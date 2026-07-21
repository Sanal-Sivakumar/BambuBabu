"""
BambuBabu — Bambu Printer Client
MQTT (TLS) + FTPS communication with a single Bambu Lab printer.

Tested with: P1S, A1 Mini
Protocol: Bambu LAN mode (must be enabled on printer)
"""
from __future__ import annotations
import json
import ssl
import socket
import ftplib
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import paho.mqtt.client as mqtt

from backend.core.logger import get_logger

log = get_logger("bambububu.printer")

MQTT_PORT = 8883
FTP_PORT  = 990
MQTT_USER = "bblp"


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """
    FTP_TLS subclass that handles Bambu's implicit FTPS (port 990).
    Python's built-in FTP_TLS only does explicit STARTTLS — this wraps
    the socket in TLS immediately on connect, as Bambu requires.
    """

    def __init__(self, context: ssl.SSLContext, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.context = context
        self._sock: Optional[ssl.SSLSocket] = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=None)
        self._sock = value

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if port == 0:
            port = FTP_PORT
        self.host = host
        self.port = port
        self.timeout = self.timeout if timeout == -999 else timeout
        raw_sock = socket.create_connection(
            (host, port), self.timeout, source_address
        )
        self.sock = raw_sock  # triggers TLS wrap via setter
        self.af = raw_sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


class BambuPrinter:
    """
    Manages the connection and control of one Bambu Lab printer.

    Usage:
        printer = BambuPrinter("p1s", "192.168.10.116", "01P09C...", "dd4b4e51", on_update)
        printer.connect()
    """

    def __init__(
        self,
        printer_id: str,
        ip: str,
        serial: str,
        access_code: str,
        on_status_update: Callable,  # called whenever status changes
    ):
        self.printer_id   = printer_id
        self.ip           = ip
        self.serial       = serial
        self.access_code  = access_code
        self.on_status_update = on_status_update

        # Runtime state (updated via MQTT)
        self.status       = "offline"
        self.gcode_state  = "OFFLINE"
        self.progress     = 0
        self.nozzle_temp  = 0.0
        self.bed_temp     = 0.0
        self.last_seen: Optional[datetime] = None

        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._lock = threading.Lock()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._shutdown = False

    # ── Public API ─────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Connect to the printer's MQTT broker."""
        self._shutdown = False
        self._connect_mqtt()
        # Start reconnect watcher
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True, name=f"reconnect-{self.printer_id}"
        )
        self._reconnect_thread.start()

    def disconnect(self) -> None:
        """Gracefully disconnect."""
        self._shutdown = True
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    def start_print(self, filename_3mf: str, job_name: str) -> None:
        """Send the print command via MQTT."""
        payload = {
            "print": {
                "sequence_id": str(int(time.time())),
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "subtask_name": job_name,
                "url": f"ftp:///{filename_3mf}",
                "bed_type": "auto",
                "timelapse": False,
                "bed_leveling": True,
                "flow_cali": False,
                "vibration_cali": True,
                "layer_inspect": False,
                "use_ams": False,
            }
        }
        self._publish(payload)
        log.info(f"[{self.printer_id}] Print command sent for {filename_3mf}")

    def stop_print(self) -> None:
        """Stop current print."""
        self._publish({"print": {"sequence_id": str(int(time.time())), "command": "stop"}})
        log.info(f"[{self.printer_id}] Stop command sent")

    def upload_file(self, local_path: str, remote_filename: str) -> None:
        """Upload a .3mf file to the printer via implicit FTPS (port 990)."""
        log.info(f"[{self.printer_id}] Uploading {remote_filename} via FTPS …")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE

        ftp = ImplicitFTP_TLS(context=ctx)
        try:
            ftp.connect(self.ip, FTP_PORT, timeout=30)
            ftp.login("bblp", self.access_code)
            ftp.prot_p()  # Encrypt data channel

            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR /{remote_filename}", f)

            ftp.quit()
            log.info(f"[{self.printer_id}] Upload complete: {remote_filename}")
        except Exception as e:
            log.error(f"[{self.printer_id}] FTP upload failed: {e}")
            raise

    def request_status(self) -> None:
        """Request a full status push from the printer."""
        self._publish({"pushing": {"sequence_id": "0", "command": "pushall"}})

    def is_idle(self) -> bool:
        return self.status == "idle"

    def is_online(self) -> bool:
        return self._connected

    # ── MQTT internals ─────────────────────────────────────────────────────

    def _connect_mqtt(self) -> None:
        client_id = f"bambububu_{self.printer_id}_{int(time.time())}"
        client = mqtt.Client(client_id=client_id)
        client.username_pw_set(MQTT_USER, self.access_code)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        client.tls_set_context(ctx)

        client.on_connect    = self._on_connect
        client.on_message    = self._on_message
        client.on_disconnect = self._on_disconnect

        try:
            client.connect(self.ip, MQTT_PORT, keepalive=60)
            client.loop_start()
            self._client = client
            log.info(f"[{self.printer_id}] MQTT connecting to {self.ip}:{MQTT_PORT}")
        except Exception as e:
            log.warning(f"[{self.printer_id}] MQTT connect error: {e}")
            self.status = "offline"

    def _reconnect_loop(self) -> None:
        """Background thread: try to reconnect if disconnected."""
        while not self._shutdown:
            time.sleep(30)
            if not self._connected and not self._shutdown:
                log.info(f"[{self.printer_id}] Attempting reconnect …")
                self._connect_mqtt()

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self._connected = True
            self.status = "idle"
            topic = f"device/{self.serial}/report"
            client.subscribe(topic)
            log.info(f"[{self.printer_id}] MQTT connected — subscribed to {topic}")
            self.request_status()
        else:
            log.warning(f"[{self.printer_id}] MQTT auth failed, rc={rc}")
            self.status = "offline"

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        self.status = "offline"
        log.warning(f"[{self.printer_id}] MQTT disconnected (rc={rc})")
        self.on_status_update(self.printer_id, self._snapshot())

    def _on_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return

        self.last_seen = datetime.utcnow()

        if "print" in data:
            self._handle_print(data["print"])

    def _handle_print(self, p: dict) -> None:
        gcode_state = p.get("gcode_state", "")
        mc_percent  = p.get("mc_percent", self.progress)

        state_map = {
            "IDLE":    "idle",
            "RUNNING": "printing",
            "PAUSE":   "paused",
            "FAILED":  "error",
            "FINISH":  "finished",   # will be handled specially
        }

        if gcode_state:
            prev = self.status
            self.status = state_map.get(gcode_state, self.status)

        self.progress    = int(mc_percent)
        self.gcode_state = gcode_state
        self.nozzle_temp = float(p.get("nozzle_temper", self.nozzle_temp))
        self.bed_temp    = float(p.get("bed_temper", self.bed_temp))

        self.on_status_update(self.printer_id, self._snapshot())

    def _snapshot(self) -> dict:
        return {
            "status":      self.status,
            "gcode_state": self.gcode_state,
            "progress":    self.progress,
            "nozzle_temp": self.nozzle_temp,
            "bed_temp":    self.bed_temp,
            "last_seen":   self.last_seen.isoformat() if self.last_seen else None,
        }

    def _publish(self, payload: dict) -> None:
        if self._client and self._connected:
            topic = f"device/{self.serial}/request"
            self._client.publish(topic, json.dumps(payload))
        else:
            log.warning(f"[{self.printer_id}] Cannot publish — not connected")
