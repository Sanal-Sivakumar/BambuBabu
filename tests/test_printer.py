from __future__ import annotations

import json
import threading
import time

import paho.mqtt.client as mqtt
import pytest

from backend.core.printer import (
    BambuPrinter,
    PrintStartRejected,
    PrintStartUnconfirmed,
)


class PublishInfo:
    rc = mqtt.MQTT_ERR_SUCCESS

    def wait_for_publish(self, timeout=None):
        return None

    def is_published(self):
        return True


class UnacknowledgedPublishInfo(PublishInfo):
    def is_published(self):
        return False


class FakeClient:
    def __init__(self, publish_info=None):
        self.publishes = []
        self.publish_info = publish_info or PublishInfo()

    def subscribe(self, *_args, **_kwargs):
        return (mqtt.MQTT_ERR_SUCCESS, 1)

    def publish(self, *args, **kwargs):
        self.publishes.append((args, kwargs))
        return self.publish_info


def make_printer():
    printer = BambuPrinter(
        "p1s",
        "192.0.2.10",
        "SERIAL",
        "ACCESS",
        "/tmp/test-printer.pem",
        "sha256//AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        lambda *_: None,
    )
    return printer


def test_publish_fails_closed_when_disconnected():
    with pytest.raises(RuntimeError, match="not connected"):
        make_printer().start_print_and_confirm("part.3mf", "job", timeout=0.01)


def test_status_request_is_nonblocking_qos_zero():
    printer = make_printer()
    client = FakeClient()
    printer._client = client
    printer._connected = True

    printer.request_status()

    assert client.publishes[0][1]["qos"] == 0


def test_ftps_keeps_access_code_out_of_process_arguments(tmp_path, monkeypatch):
    source = tmp_path / "part.3mf"
    source.write_bytes(b"sliced")
    captured = {}

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        return Result()

    monkeypatch.setattr("backend.core.printer.subprocess.run", fake_run)
    printer = make_printer()
    printer.upload_file(str(source), "part.3mf")

    assert all("ACCESS" not in argument for argument in captured["command"])
    assert "ACCESS" in captured["input"]
    assert "--pinnedpubkey" in captured["command"]


def test_start_waits_for_authoritative_running_report():
    printer = make_printer()
    client = FakeClient()
    printer._client = client
    printer._connected = True
    printer.status = "idle"
    printer.gcode_state = "IDLE"

    thread = threading.Thread(
        target=lambda: (
            time.sleep(0.02),
            printer._handle_print({"gcode_state": "RUNNING"}),
        )
    )
    thread.start()
    result = printer.start_print_and_confirm("part.3mf", "job", timeout=1)
    thread.join()
    assert result["gcode_state"] == "RUNNING"
    payload = json.loads(client.publishes[0][0][1])["print"]
    assert payload["file"] == "part.3mf"
    assert payload["url"] == "ftp:///part.3mf"
    assert payload["subtask_id"] == "0"


def test_published_but_unconfirmed_start_raises_attention_signal():
    printer = make_printer()
    printer._client = FakeClient()
    printer._connected = True
    printer.status = "idle"
    printer.gcode_state = "IDLE"
    with pytest.raises(PrintStartUnconfirmed):
        printer.start_print_and_confirm("part.3mf", "job", timeout=0.01)


def test_unacknowledged_start_publish_is_treated_as_ambiguous():
    printer = make_printer()
    printer._client = FakeClient(UnacknowledgedPublishInfo())
    printer._connected = True
    printer.status = "idle"
    printer.gcode_state = "IDLE"

    with pytest.raises(PrintStartUnconfirmed, match="may have reached"):
        printer.start_print_and_confirm("part.3mf", "job", timeout=0.01)


def test_authoritative_failure_rejects_start():
    printer = make_printer()
    printer._client = FakeClient()
    printer._connected = True
    printer.status = "idle"
    printer.gcode_state = "IDLE"
    thread = threading.Thread(
        target=lambda: (
            time.sleep(0.02),
            printer._handle_print({"gcode_state": "FAILED"}),
        )
    )
    thread.start()
    with pytest.raises(PrintStartRejected):
        printer.start_print_and_confirm("part.3mf", "job", timeout=1)
    thread.join()


def test_stale_mqtt_disconnect_cannot_overwrite_current_connection():
    printer = make_printer()
    current_client = FakeClient()
    stale_client = FakeClient()
    printer._client = current_client
    printer._connected = True
    printer.status = "idle"

    printer._on_disconnect(stale_client, None, 1)

    assert printer._connected is True
    assert printer.status == "idle"


def test_initial_status_request_runs_outside_mqtt_callback():
    printer = make_printer()
    client = FakeClient()
    requested = threading.Event()
    printer._client = client

    def request_status():
        requested.set()

    printer.request_status = request_status
    printer._on_connect(client, None, None, 0)

    assert requested.wait(timeout=1)


def test_disconnect_requests_broker_shutdown_before_stopping_loop():
    class OrderedClient:
        def __init__(self):
            self.calls = []

        def disconnect(self):
            self.calls.append("disconnect")

        def loop_stop(self):
            self.calls.append("loop_stop")

    printer = make_printer()
    client = OrderedClient()
    printer._client = client

    printer.disconnect()

    assert client.calls == ["disconnect", "loop_stop"]
