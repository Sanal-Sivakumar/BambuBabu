from __future__ import annotations

from backend.config import settings
from backend.core.printer import PrintStartUnconfirmed
from backend.core.printer_manager import PrinterManager
from backend.core.queue_processor import QueueProcessor
from backend.db import crud
from backend.db.models import JobStatus, PrinterID, PrinterStatus
from backend.db.session import SessionLocal
from tests.helpers import binary_stl, create_job


class FakePrinter:
    def __init__(self, start_error=None):
        self.start_error = start_error

    def upload_file(self, _path, _filename):
        return None

    def start_print_and_confirm(self, _filename, job_name):
        if self.start_error:
            raise self.start_error
        return {"gcode_state": "RUNNING"}


class FakeManager:
    def __init__(self, printer=None):
        self.printer = printer or FakePrinter()

    def require_printer(self, _printer_id):
        return self.printer


class UnavailableManager:
    def require_printer(self, _printer_id):
        raise RuntimeError("printer integration unavailable")


def queued_job(printer_id=PrinterID.P1S):
    source = settings.UPLOAD_DIR / "queue.stl"
    sliced = settings.SLICED_DIR / "queue.3mf"
    source.write_bytes(binary_stl())
    sliced.write_bytes(b"sliced")
    with SessionLocal.begin() as db:
        job = create_job(db, str(source), printer=printer_id)
        job.sliced_path = str(sliced)
        job.bbox_x = job.bbox_y = job.bbox_z = 10
        crud.transition_job_status(db, job.id, JobStatus.PENDING, JobStatus.ANALYSING)
        crud.transition_job_status(db, job.id, JobStatus.ANALYSING, JobStatus.SLICING)
        crud.transition_job_status(db, job.id, JobStatus.SLICING, JobStatus.QUEUED)
        crud.update_printer_state(db, printer_id, status=PrinterStatus.IDLE)
        return job.id


def test_printer_error_before_confirm_fails_job_and_releases_slot():
    job_id = queued_job()
    processor = QueueProcessor(FakeManager(FakePrinter(RuntimeError("offline"))))
    assert processor._try_dispatch(PrinterID.P1S) is True
    processor._dispatch_executor.shutdown(wait=True)
    with SessionLocal() as db:
        assert crud.get_job(db, job_id).status == JobStatus.FAILED
        state = crud.get_printer_state(db, PrinterID.P1S)
        assert state.current_job_id is None
        assert state.plate_cleared is True
    processor._slicer_executor.shutdown(wait=False, cancel_futures=True)


def test_missing_printer_client_fails_job_and_releases_slot():
    job_id = queued_job()
    processor = QueueProcessor(UnavailableManager())
    assert processor._try_dispatch(PrinterID.P1S) is True
    processor._dispatch_executor.shutdown(wait=True)
    with SessionLocal() as db:
        assert crud.get_job(db, job_id).status == JobStatus.FAILED
        assert crud.get_printer_state(db, PrinterID.P1S).current_job_id is None
    processor._slicer_executor.shutdown(wait=False, cancel_futures=True)


def test_unconfirmed_start_blocks_printer_for_inspection():
    job_id = queued_job()
    error = PrintStartUnconfirmed("no running report")
    processor = QueueProcessor(FakeManager(FakePrinter(error)))
    processor._try_dispatch(PrinterID.P1S)
    processor._dispatch_executor.shutdown(wait=True)
    with SessionLocal() as db:
        assert crud.get_job(db, job_id).status == JobStatus.ATTENTION
        state = crud.get_printer_state(db, PrinterID.P1S)
        assert state.current_job_id == job_id
        assert state.plate_cleared is False
    processor._slicer_executor.shutdown(wait=False, cancel_futures=True)


def test_restart_retries_safe_states_and_quarantines_handoff():
    with SessionLocal.begin() as db:
        analysing = create_job(db, "a.stl", status=JobStatus.ANALYSING)
        starting = create_job(
            db, "b.stl", status=JobStatus.STARTING, printer=PrinterID.P1S
        )
        analysing_id, starting_id = analysing.id, starting.id
    processor = QueueProcessor(FakeManager())
    processor.reconcile_interrupted_jobs()
    with SessionLocal() as db:
        assert crud.get_job(db, analysing_id).status == JobStatus.PENDING
        assert crud.get_job(db, starting_id).status == JobStatus.ATTENTION
        assert crud.get_printer_state(db, PrinterID.P1S).current_job_id == starting_id
    processor._slicer_executor.shutdown(wait=False, cancel_futures=True)
    processor._dispatch_executor.shutdown(wait=False, cancel_futures=True)


def test_busy_preferred_printer_reroutes_and_reslices(monkeypatch):
    job_id = queued_job(PrinterID.P1S)
    with SessionLocal.begin() as db:
        crud.update_printer_state(
            db, PrinterID.P1S, status=PrinterStatus.PRINTING, plate_cleared=False
        )
        crud.update_printer_state(db, PrinterID.A1_MINI, status=PrinterStatus.IDLE)

    def fake_slice(_stl, printer_id, _output):
        assert printer_id == PrinterID.A1_MINI
        return settings.SLICED_DIR / "fallback.3mf", 5

    monkeypatch.setattr("backend.core.queue_processor.slicer.slice_stl", fake_slice)
    processor = QueueProcessor(FakeManager())
    assert processor._try_schedule_fallback(PrinterID.A1_MINI) is True
    processor._slicer_executor.shutdown(wait=True)
    processor._dispatch_executor.shutdown(wait=False, cancel_futures=True)
    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        assert job.assigned_printer == PrinterID.A1_MINI
        assert job.status == JobStatus.QUEUED


def test_fallback_does_not_steal_when_preferred_printer_is_available():
    queued_job(PrinterID.P1S)
    with SessionLocal.begin() as db:
        crud.update_printer_state(db, PrinterID.P1S, status=PrinterStatus.IDLE)
        crud.update_printer_state(db, PrinterID.A1_MINI, status=PrinterStatus.IDLE)
    processor = QueueProcessor(FakeManager())
    assert processor._try_schedule_fallback(PrinterID.A1_MINI) is False
    processor._slicer_executor.shutdown(wait=False, cancel_futures=True)
    processor._dispatch_executor.shutdown(wait=False, cancel_futures=True)


def test_status_transition_refreshes_identity_map():
    with SessionLocal.begin() as db:
        job = create_job(db, "stored.stl")
        transitioned = crud.transition_job_status(
            db, job.id, JobStatus.PENDING, JobStatus.ANALYSING
        )
        assert transitioned is job
        assert job.status == JobStatus.ANALYSING


def test_illegal_terminal_transition_is_rejected():
    with SessionLocal.begin() as db:
        job = create_job(db, "stored.stl", status=JobStatus.COMPLETED)
        try:
            crud.transition_job_status(
                db, job.id, JobStatus.COMPLETED, JobStatus.PRINTING
            )
        except crud.JobTransitionError as exc:
            assert "Illegal job transition" in str(exc)
        else:
            raise AssertionError("A terminal job was revived")


def test_finish_keeps_plate_blocked_until_clearance():
    with SessionLocal.begin() as db:
        job = create_job(
            db, "stored.stl", status=JobStatus.STARTING, printer=PrinterID.P1S
        )
        crud.update_printer_state(
            db,
            PrinterID.P1S,
            status=PrinterStatus.IDLE,
            current_job_id=job.id,
        )
        job_id = job.id

    manager = PrinterManager()
    base = {
        "progress": 100,
        "nozzle_temp": 200,
        "bed_temp": 60,
    }
    manager._on_status_update(
        PrinterID.P1S.value,
        {**base, "status": "printing", "gcode_state": "RUNNING"},
    )
    manager._on_status_update(
        PrinterID.P1S.value,
        {**base, "status": "finished", "gcode_state": "FINISH"},
    )
    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        state = crud.get_printer_state(db, PrinterID.P1S)
        assert job.status == JobStatus.COMPLETED
        assert state.current_job_id == job_id
        assert state.plate_cleared is False


def test_idle_without_finish_quarantines_physical_state():
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
        job_id = job.id

    PrinterManager()._on_status_update(
        PrinterID.P1S.value,
        {
            "status": "idle",
            "gcode_state": "IDLE",
            "progress": 25,
            "nozzle_temp": 40,
            "bed_temp": 35,
        },
    )
    with SessionLocal() as db:
        assert crud.get_job(db, job_id).status == JobStatus.ATTENTION
        state = crud.get_printer_state(db, PrinterID.P1S)
        assert state.current_job_id == job_id
        assert state.plate_cleared is False
        assert state.status == PrinterStatus.ERROR
