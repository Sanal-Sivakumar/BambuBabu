from __future__ import annotations

import pytest

from backend.config import Settings, settings
from backend.db import crud
from backend.db.models import JobStatus, PrinterID, PrinterStatus
from backend.db.session import SessionLocal
from tests.helpers import binary_stl, create_job


def upload(client, content: bytes | None = None, email: str = "member@example.com"):
    return client.post(
        "/api/jobs",
        files={"file": ("part.stl", content or binary_stl(), "model/stl")},
        data={"user_name": "Member", "user_email": email, "description": "fixture"},
    )


def test_streamed_upload_creates_pending_job(client):
    response = upload(client)
    assert response.status_code == 201
    with SessionLocal() as db:
        job = crud.get_job(db, response.json()["job_id"])
        assert job.status == JobStatus.PENDING
        assert job.user_email == "member@example.com"
        assert job.stl_path.endswith(".stl")


def test_upload_rejects_invalid_and_bad_email(client):
    assert upload(client, b"x" * 100).status_code == 400
    assert upload(client, email="not-an-email").status_code == 422


def test_active_job_quota(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ACTIVE_JOBS", 1)
    assert upload(client).status_code == 201
    assert upload(client).status_code == 429


def test_stream_limit_and_public_response_privacy(client, monkeypatch):
    from backend.api import jobs as jobs_api

    monkeypatch.setattr(jobs_api, "MAX_BYTES", 100)
    assert upload(client).status_code == 413

    monkeypatch.setattr(jobs_api, "MAX_BYTES", settings.MAX_STL_SIZE_MB * 1024 * 1024)
    response = client.post(
        "/api/jobs",
        files={"file": ("part.stl", binary_stl(), "model/stl")},
        data={
            "user_name": "Member\r\nInjected",
            "user_email": "private@example.com",
            "description": "fixture",
        },
    )
    assert response.status_code == 201
    public_job = client.get(f"/api/jobs/{response.json()['job_id']}").json()
    assert "user_email" not in public_job
    assert public_job["user_name"] == "Member Injected"


def test_cancel_is_atomic_and_worker_cannot_revive_job(client):
    with SessionLocal.begin() as db:
        path = settings.UPLOAD_DIR / "cancel.stl"
        path.write_bytes(binary_stl())
        job = create_job(db, str(path))
        crud.transition_job_status(db, job.id, JobStatus.PENDING, JobStatus.ANALYSING)
        job_id = job.id

    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    with SessionLocal.begin() as db:
        job = crud.get_job(db, job_id)
        assert job.status == JobStatus.CANCELLED
        try:
            crud.transition_job_status(
                db, job_id, JobStatus.ANALYSING, JobStatus.SLICING
            )
        except crud.JobTransitionError:
            pass
        else:
            raise AssertionError("A cancelled job was revived")


def test_logs_api_matches_frontend_array_contract(client):
    with SessionLocal.begin() as db:
        crud.add_log(db, "TEST_EVENT", "structured log")
    response = client.get("/api/logs/all")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert response.json()[0]["event"] == "TEST_EVENT"


def test_plate_clear_records_completed_job_and_releases_printer(client):
    with SessionLocal.begin() as db:
        job = create_job(
            db, "stored.stl", status=JobStatus.COMPLETED, printer=PrinterID.P1S
        )
        crud.update_printer_state(
            db,
            PrinterID.P1S,
            status=PrinterStatus.IDLE,
            plate_cleared=False,
            current_job_id=job.id,
        )
        job_id = job.id

    response = client.post("/api/printers/p1s/plate-cleared")
    assert response.status_code == 200
    with SessionLocal() as db:
        state = crud.get_printer_state(db, PrinterID.P1S)
        job = crud.get_job(db, job_id)
        assert state.plate_cleared is True
        assert state.current_job_id is None
        assert job.plate_cleared_at is not None


def test_plate_clear_refuses_active_print(client):
    with SessionLocal.begin() as db:
        job = create_job(
            db, "stored.stl", status=JobStatus.PRINTING, printer=PrinterID.P1S
        )
        crud.update_printer_state(
            db,
            PrinterID.P1S,
            status=PrinterStatus.PRINTING,
            plate_cleared=False,
            current_job_id=job.id,
        )
    assert client.post("/api/printers/p1s/plate-cleared").status_code == 409


def test_idle_acknowledgement_requires_jobless_clear_failed_printer(client, monkeypatch):
    class FailedPrinter:
        def acknowledge_physically_idle(self):
            return {"status": "idle", "gcode_state": "IDLE"}

    from backend.api import printers as printers_api

    monkeypatch.setattr(
        printers_api.printer_manager,
        "get_printer",
        lambda _printer_id: FailedPrinter(),
    )
    with SessionLocal.begin() as db:
        crud.update_printer_state(
            db,
            PrinterID.A1_MINI,
            status=PrinterStatus.ERROR,
            plate_cleared=True,
            current_job_id=None,
        )

    response = client.post(
        "/api/printers/a1_mini/acknowledge-idle",
        json={"physically_idle": True},
    )
    assert response.status_code == 200
    assert response.json()["gcode_state"] == "IDLE"

    assert client.post(
        "/api/printers/a1_mini/acknowledge-idle",
        json={"physically_idle": False},
    ).status_code == 422


def test_security_boundary_is_explicit_and_cors_is_not_wildcard(client):
    response = client.get("/api/health")
    assert response.json()["authentication"] == "external-pending"
    assert settings.HOST in {"127.0.0.1", "localhost", "::1"}
    assert "*" not in settings.cors_origins


def test_pending_auth_mode_refuses_network_exposure(monkeypatch):
    monkeypatch.setattr(settings, "HOST", "0.0.0.0")
    with pytest.raises(RuntimeError, match="loopback"):
        settings.validate_runtime()


def test_wildcard_cors_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="Wildcard CORS"):
        settings.validate_runtime()


def test_mock_slice_output_cannot_reach_live_printers(monkeypatch):
    monkeypatch.setattr(settings, "PRINTERS_ENABLED", True)
    monkeypatch.setattr(settings, "MOCK_SLICER", True)
    with pytest.raises(RuntimeError, match="MOCK_SLICER"):
        settings.validate_runtime()


def test_live_printer_config_requires_valid_transport_pin(tmp_path):
    certificate = tmp_path / "printer.pem"
    certificate.write_text("test certificate fixture")
    valid_pin = "sha256//AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    config = Settings(
        _env_file=None,
        PRINTERS_ENABLED=True,
        MOCK_SLICER=False,
        P1S_IP="192.168.1.10",
        P1S_SERIAL="P1S-SERIAL",
        P1S_ACCESS_CODE="12345678",
        P1S_MQTT_CERT_PATH=str(certificate),
        P1S_FTPS_PIN="not-a-pin",
        A1_MINI_IP="192.168.1.11",
        A1_MINI_SERIAL="A1-SERIAL",
        A1_MINI_ACCESS_CODE="12345678",
        A1_MINI_MQTT_CERT_PATH=str(certificate),
        A1_MINI_FTPS_PIN=valid_pin,
    )
    with pytest.raises(RuntimeError, match="P1S_FTPS_PIN"):
        config.validate_runtime()
