"""Fail-closed Bambu LAN client using MQTT for control and curl for FTPS."""

from __future__ import annotations

import json
import ssl

# Subprocesses use fixed argv with shell disabled; no command text is user supplied.
import subprocess  # nosec B404
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import paho.mqtt.client as mqtt

from backend.config import settings
from backend.core.logger import get_logger


log = get_logger("bambubabu.printer")
MQTT_PORT = 8883
MQTT_USER = "bblp"
PRINT_START_STATES = {"PREPARE", "RUNNING"}


class PrintStartUnconfirmed(RuntimeError):
    """The command was published, but physical printer start was not observed."""


class PrintStartRejected(RuntimeError):
    """The printer reported FAILED after a start request was published."""


class BambuPrinter:
    def __init__(
        self,
        printer_id: str,
        ip: str,
        serial: str,
        access_code: str,
        mqtt_cert_path: str | Path,
        ftps_pin: str,
        on_status_update: Callable,
    ):
        self.printer_id = printer_id
        self.ip = ip
        self.serial = serial
        self.access_code = access_code
        self.mqtt_cert_path = Path(mqtt_cert_path)
        self.ftps_pin = ftps_pin
        self.on_status_update = on_status_update

        self.status = "offline"
        self.gcode_state = "OFFLINE"
        self.progress = 0
        self.nozzle_temp = 0.0
        self.bed_temp = 0.0
        self.last_seen: Optional[datetime] = None

        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._condition = threading.Condition(threading.RLock())
        self._reconnect_thread: Optional[threading.Thread] = None
        self._shutdown = False
        self._report_version = 0
        self._failed_report_acknowledged = False

    def connect(self) -> None:
        self._shutdown = False
        self._connect_mqtt()
        if not self._reconnect_thread or not self._reconnect_thread.is_alive():
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop,
                daemon=True,
                name=f"reconnect-{self.printer_id}",
            )
            self._reconnect_thread.start()

    def disconnect(self) -> None:
        self._shutdown = True
        client = self._client
        if client:
            # Ask the broker to close first. Paho's loop_stop waits for the
            # network thread, which can otherwise remain blocked on an open
            # connection until systemd's stop timeout kills the service.
            try:
                client.disconnect()
            finally:
                client.loop_stop()
        with self._condition:
            self._connected = False
            self.status = "offline"
            self._condition.notify_all()

    def upload_file(self, local_path: str, remote_filename: str) -> None:
        """Upload via implicit FTPS without exposing the access code in argv."""
        source = Path(local_path)
        if not source.is_file() or source.stat().st_size == 0:
            raise RuntimeError(f"Sliced file is missing or empty: {source}")

        url_host = f"[{self.ip}]" if ":" in self.ip else self.ip
        url = f"ftps://{url_host}:990/{quote(remote_filename, safe='')}"
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--insecure",  # Bambu LAN mode presents a device-local self-signed certificate.
            "--pinnedpubkey",
            self.ftps_pin,
            "--ftp-pasv",
            "--connect-timeout",
            "15",
            "--max-time",
            "120",
            "--config",
            "-",
            "--upload-file",
            str(source),
            url,
        ]
        escaped_access_code = self.access_code.replace("\\", "\\\\").replace('"', '\\"')
        curl_config = f'user = "{MQTT_USER}:{escaped_access_code}"\n'
        log.info(f"[{self.printer_id}] Uploading {remote_filename} via FTPS")

        for attempt in range(1, 4):
            try:
                # Fixed curl argv; the remote filename is URL-quoted.
                result = subprocess.run(  # nosec B603
                    command,
                    input=curl_config,
                    capture_output=True,
                    text=True,
                    timeout=130,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("curl is required for Bambu FTPS uploads") from exc
            except subprocess.TimeoutExpired:
                result = None

            if result is not None and result.returncode == 0:
                log.info(f"[{self.printer_id}] Upload complete: {remote_filename}")
                return

            detail = "timed out" if result is None else result.stderr.strip()[:300]
            log.warning(
                f"[{self.printer_id}] FTPS attempt {attempt}/3 failed: {detail}"
            )
            if attempt < 3:
                time.sleep(3)
        raise RuntimeError("FTPS upload failed after three attempts")

    def start_print_and_confirm(
        self, filename_3mf: str, job_name: str, timeout: float | None = None
    ) -> dict:
        """Publish a start request and wait for PREPARE/RUNNING before returning."""
        with self._condition:
            if not self._connected:
                raise RuntimeError(f"{self.printer_id} MQTT is not connected")
            if self.status != "idle":
                raise RuntimeError(
                    f"{self.printer_id} is not idle (status={self.status}, "
                    f"gcode_state={self.gcode_state})"
                )
            baseline_report = self._report_version
            # A human acknowledgement only clears a stale, jobless FAILED
            # report. Once a new physical handoff begins, every newer FAILED
            # report is authoritative again.
            self._failed_report_acknowledged = False

        payload = {
            "print": {
                "sequence_id": str(int(time.time() * 1000)),
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "project_id": "0",
                "profile_id": "0",
                "task_id": "0",
                "subtask_id": "0",
                "subtask_name": job_name,
                "file": filename_3mf,
                "url": f"ftp:///{filename_3mf}",
                "md5": "",
                "plate_idx": 0,
                "bed_type": "auto",
                "timelapse": False,
                "bed_leveling": True,
                "flow_cali": False,
                "vibration_cali": True,
                "layer_inspect": False,
                "use_ams": False,
                "ams_mapping": [],
            }
        }
        try:
            self._publish(payload)
        except RuntimeError as exc:
            # A successful Paho publish call followed by a missing QoS 1 PUBACK
            # is physically ambiguous: the printer may already be acting on the
            # start request. Quarantine the slot rather than mark the job failed
            # and risk a second dispatch.
            if "not acknowledged" in str(exc):
                raise PrintStartUnconfirmed(
                    "MQTT start command may have reached the printer, but its QoS 1 "
                    "acknowledgement was not received"
                ) from exc
            raise
        log.info(f"[{self.printer_id}] Start request published for {filename_3mf}")

        deadline = time.monotonic() + (
            timeout
            if timeout is not None
            else settings.PRINT_START_CONFIRM_TIMEOUT_SECONDS
        )
        with self._condition:
            while True:
                if self._report_version > baseline_report and (
                    self.gcode_state in PRINT_START_STATES or self.status == "printing"
                ):
                    return self._snapshot_unlocked()
                if self.gcode_state == "FAILED" or self.status == "error":
                    raise PrintStartRejected("Printer reported FAILED while starting")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PrintStartUnconfirmed(
                        f"Printer did not report PREPARE/RUNNING within "
                        f"{settings.PRINT_START_CONFIRM_TIMEOUT_SECONDS:g}s"
                    )
                self._condition.wait(timeout=remaining)

    def stop_print(self) -> None:
        self._publish(
            {"print": {"sequence_id": str(int(time.time() * 1000)), "command": "stop"}}
        )

    def request_status(self) -> None:
        # A status refresh is non-mutating. Bambu LAN brokers commonly accept it
        # at QoS 0 but do not acknowledge the QoS 1 variant. Start commands keep
        # the stricter QoS 1 acknowledgement requirement.
        self._publish(
            {"pushing": {"sequence_id": "0", "command": "pushall"}},
            qos=0,
            require_ack=False,
        )

    def is_idle(self) -> bool:
        with self._condition:
            return self._connected and self.status == "idle"

    def is_online(self) -> bool:
        with self._condition:
            return self._connected

    def acknowledge_physically_idle(self) -> dict:
        """Clear a stale jobless FAILED report after physical inspection."""
        with self._condition:
            if not self._connected:
                raise RuntimeError(f"{self.printer_id} MQTT is not connected")
            if self.status != "error" or self.gcode_state != "FAILED":
                raise RuntimeError(
                    f"{self.printer_id} is not in FAILED state "
                    f"(status={self.status}, gcode_state={self.gcode_state})"
                )
            self._failed_report_acknowledged = True
            self.status = "idle"
            self.gcode_state = "IDLE"
            self._condition.notify_all()
            snapshot = self._snapshot_unlocked()
        self.on_status_update(self.printer_id, snapshot)
        return snapshot

    def snapshot(self) -> dict:
        with self._condition:
            return self._snapshot_unlocked()

    def _connect_mqtt(self) -> None:
        with self._condition:
            previous_client = self._client
            self._client = None
            self._connected = False
        if previous_client is not None:
            previous_client.loop_stop()
            try:
                previous_client.disconnect()
            except Exception as exc:
                log.debug(
                    f"[{self.printer_id}] Previous MQTT client cleanup: {type(exc).__name__}"
                )

        client_id = f"bambubabu_{self.printer_id}_{int(time.time())}"
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(MQTT_USER, self.access_code)
        context = ssl.create_default_context(cafile=str(self.mqtt_cert_path))
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        client.tls_set_context(context)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        with self._condition:
            self._client = client
        try:
            client.connect(self.ip, MQTT_PORT, keepalive=60)
            client.loop_start()
            log.info(f"[{self.printer_id}] MQTT connecting to {self.ip}:{MQTT_PORT}")
        except Exception as exc:
            with self._condition:
                if self._client is client:
                    self._client = None
                self._connected = False
                self.status = "offline"
            log.warning(f"[{self.printer_id}] MQTT connect error: {exc}")

    def _reconnect_loop(self) -> None:
        while not self._shutdown:
            if self._shutdown:
                break
            time.sleep(30)
            if not self.is_online() and not self._shutdown:
                self._connect_mqtt()

    def _on_connect(self, client, _userdata, _flags, rc) -> None:
        with self._condition:
            if client is not self._client:
                return
        if rc != 0:
            with self._condition:
                self._connected = False
                self.status = "offline"
                self._condition.notify_all()
            log.warning(
                f"[{self.printer_id}] MQTT authentication/connection failed, rc={rc}"
            )
            return

        with self._condition:
            self._connected = True
            self._condition.notify_all()
        topic = f"device/{self.serial}/report"
        client.subscribe(topic, qos=1)
        log.info(f"[{self.printer_id}] MQTT connected; subscribed to {topic}")
        # Do not wait for a QoS acknowledgement inside Paho's network callback.
        # The callback thread must remain free to receive the PUBACK itself.
        threading.Thread(
            target=self._request_status_after_connect,
            args=(client,),
            daemon=True,
            name=f"status-request-{self.printer_id}",
        ).start()

    def _request_status_after_connect(self, client) -> None:
        """Request a report outside Paho's network callback thread."""
        time.sleep(0.05)
        with self._condition:
            if client is not self._client or not self._connected:
                return
        try:
            self.request_status()
        except RuntimeError as exc:
            log.warning(f"[{self.printer_id}] Initial status request failed: {exc}")

    def _on_disconnect(self, client, _userdata, rc) -> None:
        with self._condition:
            if client is not self._client:
                return
            self._connected = False
            self.status = "offline"
            self._condition.notify_all()
            snapshot = self._snapshot_unlocked()
        log.warning(f"[{self.printer_id}] MQTT disconnected (rc={rc})")
        self.on_status_update(self.printer_id, snapshot)

    def _on_message(self, _client, _userdata, message) -> None:
        try:
            data = json.loads(message.payload.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if "print" in data:
            self._handle_print(data["print"])

    def _handle_print(self, report: dict) -> None:
        state_map = {
            "IDLE": "idle",
            "PREPARE": "printing",
            "RUNNING": "printing",
            "PAUSE": "paused",
            "FAILED": "error",
            "FINISH": "finished",
        }
        with self._condition:
            has_reported_state = "gcode_state" in report
            reported_state = report.get("gcode_state", self.gcode_state)
            if reported_state == "FAILED" and self._failed_report_acknowledged:
                # Bambu firmware can retain the last FAILED state after the
                # printer has physically returned to idle. Keep the explicit
                # operator acknowledgement until a new non-FAILED state or a
                # new start attempt occurs.
                gcode_state = "IDLE"
            else:
                gcode_state = reported_state
                if has_reported_state and reported_state != "FAILED":
                    self._failed_report_acknowledged = False
            # SQLite persistence uses naive UTC for compatibility with existing rows.
            self.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)
            self.gcode_state = gcode_state
            self.status = state_map.get(gcode_state, self.status)
            self.progress = int(report.get("mc_percent", self.progress))
            self.nozzle_temp = float(report.get("nozzle_temper", self.nozzle_temp))
            self.bed_temp = float(report.get("bed_temper", self.bed_temp))
            self._report_version += 1
            self._condition.notify_all()
            snapshot = self._snapshot_unlocked()
        self.on_status_update(self.printer_id, snapshot)

    def _snapshot_unlocked(self) -> dict:
        return {
            "status": self.status,
            "gcode_state": self.gcode_state,
            "progress": self.progress,
            "nozzle_temp": self.nozzle_temp,
            "bed_temp": self.bed_temp,
            "connected": self._connected,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }

    def _publish(
        self, payload: dict, *, qos: int = 1, require_ack: bool = True
    ) -> None:
        with self._condition:
            client = self._client
            connected = self._connected
        if client is None or not connected:
            raise RuntimeError(f"{self.printer_id} MQTT is not connected")

        topic = f"device/{self.serial}/request"
        info = client.publish(topic, json.dumps(payload), qos=qos)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish rejected with rc={info.rc}")
        if require_ack:
            info.wait_for_publish(timeout=settings.MQTT_PUBLISH_TIMEOUT_SECONDS)
            if not info.is_published():
                raise RuntimeError("MQTT publish was not acknowledged by the broker")
