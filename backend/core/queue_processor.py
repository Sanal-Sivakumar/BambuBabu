"""Transactional background orchestration for analysis, slicing, and printing."""

from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from backend.config import settings
from backend.core import complexity, slicer
from backend.core.logger import get_logger
from backend.core.printer import PrintStartRejected, PrintStartUnconfirmed
from backend.db import crud
from backend.db.models import Job, JobStatus, LogLevel, PrinterID, PrinterStatus
from backend.db.session import SessionLocal

if TYPE_CHECKING:
    from backend.core.printer_manager import PrinterManager


log = get_logger("bambubabu.queue")


class QueueProcessor:
    def __init__(self, printer_manager: "PrinterManager"):
        self.pm = printer_manager
        self._running = False
        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._slicer_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="slicer"
        )
        self._dispatch_executor = ThreadPoolExecutor(
            max_workers=max(1, settings.MAX_CONCURRENT_JOBS),
            thread_name_prefix="printer-dispatch",
        )
        self._slicing_jobs: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        self.reconcile_interrupted_jobs()
        self._running = True
        self._stop_event.clear()
        self._loop_thread = threading.Thread(
            target=self._loop, daemon=True, name="queue-processor"
        )
        self._loop_thread.start()
        log.info(
            f"Queue processor polling every {settings.QUEUE_POLL_INTERVAL_SECONDS}s"
        )

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
        self._slicer_executor.shutdown(wait=False, cancel_futures=True)
        self._dispatch_executor.shutdown(wait=False, cancel_futures=True)

    def reconcile_interrupted_jobs(self) -> None:
        """Recover safe work and quarantine ambiguous physical operations after restart."""
        with SessionLocal.begin() as db:
            retryable = crud.get_jobs_in_statuses(
                db, {JobStatus.ANALYSING, JobStatus.SLICING}
            )
            for job in retryable:
                previous = job.status
                crud.transition_job_status(db, job.id, previous, JobStatus.PENDING)
                crud.add_log(
                    db,
                    "JOB_RECOVERED",
                    f"Restart recovered {previous.value} job; analysis/slicing will retry",
                    job_id=job.id,
                    level=LogLevel.WARNING,
                )

            ambiguous = crud.get_jobs_in_statuses(
                db, {JobStatus.UPLOADING, JobStatus.STARTING}
            )
            for job in ambiguous:
                previous = job.status
                crud.transition_job_status(
                    db,
                    job.id,
                    previous,
                    JobStatus.ATTENTION,
                    error_message=(
                        "Service restarted during printer handoff. Verify the physical printer "
                        "before clearing the plate or cancelling this job."
                    ),
                )
                if job.assigned_printer:
                    crud.update_printer_state(
                        db,
                        job.assigned_printer,
                        current_job_id=job.id,
                        plate_cleared=False,
                        status=PrinterStatus.ERROR,
                    )
                crud.add_log(
                    db,
                    "JOB_REQUIRES_ATTENTION",
                    f"Restart interrupted {previous.value}; dispatch is blocked",
                    job_id=job.id,
                    printer_id=job.assigned_printer.value
                    if job.assigned_printer
                    else None,
                    level=LogLevel.ERROR,
                )

            for job in crud.get_jobs_by_status(db, JobStatus.PRINTING):
                if job.assigned_printer:
                    crud.update_printer_state(
                        db,
                        job.assigned_printer,
                        current_job_id=job.id,
                        plate_cleared=False,
                        status=PrinterStatus.PRINTING,
                    )

            # Firmware commonly reports 99 immediately before FINISH. A job
            # already committed as completed is authoritative and must not
            # retain misleading historical progress after a restart.
            for job in crud.get_jobs_by_status(db, JobStatus.COMPLETED):
                if job.print_progress != 100:
                    crud.update_job_progress(db, job.id, 100)

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                log.error(f"Queue processor error:\n{traceback.format_exc()}")
            self._stop_event.wait(settings.QUEUE_POLL_INTERVAL_SECONDS)

    def _tick(self) -> None:
        with SessionLocal() as db:
            pending_ids = [
                job.id for job in crud.get_jobs_by_status(db, JobStatus.PENDING)
            ]
        for job_id in pending_ids:
            self._submit_slice(job_id, self._slice_pipeline)

        for printer_id in (PrinterID.A1_MINI, PrinterID.P1S):
            dispatched = self._try_dispatch(printer_id)
            if not dispatched:
                self._try_schedule_fallback(printer_id)

    def _submit_slice(self, job_id: str, worker, *args) -> bool:
        with self._lock:
            if job_id in self._slicing_jobs:
                return False
            self._slicing_jobs.add(job_id)
        self._slicer_executor.submit(worker, job_id, *args)
        return True

    def _slice_pipeline(self, job_id: str) -> None:
        try:
            with SessionLocal.begin() as db:
                job = crud.transition_job_status(
                    db, job_id, JobStatus.PENDING, JobStatus.ANALYSING
                )
                crud.add_log(
                    db,
                    "ANALYSIS_STARTED",
                    f"Analysing {job.original_filename}",
                    job_id=job_id,
                )
                stl_path = job.stl_path

            analysis = complexity.analyse_stl(stl_path)
            printer_id, rejection = complexity.select_printer(analysis)

            with SessionLocal.begin() as db:
                job = crud.get_job(db, job_id)
                if not job or job.status != JobStatus.ANALYSING:
                    return
                if rejection:
                    crud.transition_job_status(
                        db,
                        job_id,
                        JobStatus.ANALYSING,
                        JobStatus.REJECTED,
                        rejection_reason=rejection,
                    )
                    crud.add_log(
                        db,
                        "JOB_REJECTED",
                        rejection,
                        job_id=job_id,
                        level=LogLevel.WARNING,
                    )
                    return
                if printer_id is None:
                    raise RuntimeError(
                        "Printer selection returned no printer or rejection"
                    )
                crud.update_job_analysis(db, job_id, analysis, printer_id)
                crud.transition_job_status(
                    db, job_id, JobStatus.ANALYSING, JobStatus.SLICING
                )
                crud.add_log(
                    db,
                    "ANALYSIS_DONE",
                    f"Score={analysis['complexity_score']:.1f}; preferred={printer_id.value}",
                    job_id=job_id,
                    extra=analysis,
                )

            sliced_path, estimated_minutes = slicer.slice_stl(
                stl_path, printer_id, settings.SLICED_DIR
            )
            with SessionLocal.begin() as db:
                job = crud.get_job(db, job_id)
                if not job or job.status != JobStatus.SLICING:
                    return
                crud.update_job_sliced(db, job_id, str(sliced_path), estimated_minutes)
                crud.transition_job_status(
                    db, job_id, JobStatus.SLICING, JobStatus.QUEUED
                )
                crud.add_log(
                    db,
                    "JOB_QUEUED",
                    f"Queued for {printer_id.value}; estimate={estimated_minutes or 'unknown'} min",
                    job_id=job_id,
                    printer_id=printer_id.value,
                )
        except crud.JobTransitionError:
            # Cancellation or another worker won the compare-and-swap; never revive the job.
            return
        except Exception as exc:
            self._fail_job(job_id, f"Analysis/slicing error: {exc}")
        finally:
            with self._lock:
                self._slicing_jobs.discard(job_id)

    def _reslice_for_fallback(self, job_id: str, printer_id: PrinterID) -> None:
        try:
            with SessionLocal() as db:
                job = crud.get_job(db, job_id)
                if not job or job.status != JobStatus.SLICING:
                    return
                stl_path = job.stl_path
            sliced_path, estimated_minutes = slicer.slice_stl(
                stl_path, printer_id, settings.SLICED_DIR
            )
            with SessionLocal.begin() as db:
                job = crud.get_job(db, job_id)
                if not job or job.status != JobStatus.SLICING:
                    return
                crud.update_job_assignment(db, job_id, printer_id)
                crud.update_job_sliced(db, job_id, str(sliced_path), estimated_minutes)
                crud.transition_job_status(
                    db, job_id, JobStatus.SLICING, JobStatus.QUEUED
                )
                crud.add_log(
                    db,
                    "JOB_REROUTED",
                    f"Re-sliced for available {printer_id.value}",
                    job_id=job_id,
                    printer_id=printer_id.value,
                )
        except crud.JobTransitionError:
            return
        except Exception as exc:
            with SessionLocal.begin() as db:
                job = crud.get_job(db, job_id)
                if job and job.status == JobStatus.SLICING:
                    message = (
                        "Fallback slicing failed; retained the original printer queue "
                        f"without retrying fallback automatically: {exc}"
                    )
                    crud.transition_job_status(
                        db,
                        job_id,
                        JobStatus.SLICING,
                        JobStatus.QUEUED,
                        error_message=message,
                    )
                    crud.add_log(
                        db,
                        "FALLBACK_ABORTED",
                        message,
                        job_id=job_id,
                        printer_id=(
                            job.assigned_printer.value if job.assigned_printer else None
                        ),
                        level=LogLevel.WARNING,
                    )
        finally:
            with self._lock:
                self._slicing_jobs.discard(job_id)

    def _try_dispatch(self, printer_id: PrinterID) -> bool:
        with SessionLocal.begin() as db:
            state = crud.get_printer_state(db, printer_id)
            if (
                not state
                or state.status != PrinterStatus.IDLE
                or not state.plate_cleared
                or state.current_job_id
            ):
                return False
            job = crud.get_next_queued_job_for_printer(db, printer_id)
            if not job:
                return False
            try:
                crud.transition_job_status(
                    db, job.id, JobStatus.QUEUED, JobStatus.UPLOADING
                )
            except crud.JobTransitionError:
                return False
            crud.update_printer_state(db, printer_id, current_job_id=job.id)
            crud.add_log(
                db,
                "UPLOADING",
                f"Uploading to {printer_id.value}",
                job_id=job.id,
                printer_id=printer_id.value,
            )
            job_id = job.id
            sliced_path = job.sliced_path

        self._dispatch_executor.submit(
            self._upload_start_and_confirm, job_id, printer_id, sliced_path
        )
        return True

    def _upload_start_and_confirm(
        self, job_id: str, printer_id: PrinterID, sliced_path: str | None
    ) -> None:
        try:
            printer = self.pm.require_printer(printer_id)
            if not sliced_path:
                raise RuntimeError("Job has no sliced file")
            filename = Path(sliced_path).name
            printer.upload_file(sliced_path, filename)
            with SessionLocal.begin() as db:
                crud.transition_job_status(
                    db, job_id, JobStatus.UPLOADING, JobStatus.STARTING
                )
                crud.add_log(
                    db,
                    "PRINT_START_REQUESTED",
                    f"Requesting start on {printer_id.value}",
                    job_id=job_id,
                    printer_id=printer_id.value,
                )

            printer.start_print_and_confirm(filename, job_name=job_id[:8])

            with SessionLocal.begin() as db:
                job = crud.get_job(db, job_id)
                if not job:
                    return
                if job.status == JobStatus.STARTING:
                    crud.transition_job_status(
                        db, job_id, JobStatus.STARTING, JobStatus.PRINTING
                    )
                    crud.add_log(
                        db,
                        "PRINT_CONFIRMED",
                        f"Printer confirmed start on {printer_id.value}",
                        job_id=job_id,
                        printer_id=printer_id.value,
                    )
                elif job.status != JobStatus.PRINTING:
                    return
                crud.update_printer_state(db, printer_id, plate_cleared=False)
                started_job = job

            try:
                from backend.email.mailer import send_print_started

                send_print_started(started_job, printer_id.value)
            except Exception as exc:
                log.warning(f"Could not send start email for {job_id[:8]}: {exc}")

        except (PrintStartUnconfirmed, PrintStartRejected) as exc:
            with SessionLocal.begin() as db:
                job = crud.get_job(db, job_id)
                if job and job.status == JobStatus.STARTING:
                    crud.transition_job_status(
                        db,
                        job_id,
                        JobStatus.STARTING,
                        JobStatus.ATTENTION,
                        error_message=str(exc),
                    )
                    crud.update_printer_state(
                        db,
                        printer_id,
                        status=PrinterStatus.ERROR,
                        plate_cleared=False,
                        current_job_id=job_id,
                    )
                    crud.add_log(
                        db,
                        "PRINT_START_UNCONFIRMED",
                        str(exc),
                        job_id=job_id,
                        printer_id=printer_id.value,
                        level=LogLevel.ERROR,
                    )
        except Exception as exc:
            self._fail_job(job_id, f"Printer handoff failed: {exc}")
            with SessionLocal.begin() as db:
                state = crud.get_printer_state(db, printer_id)
                job = crud.get_job(db, job_id)
                # Clear only when no ambiguous start remains.
                if (
                    state
                    and state.current_job_id == job_id
                    and job
                    and job.status == JobStatus.FAILED
                ):
                    crud.update_printer_state(
                        db,
                        printer_id,
                        current_job_id=None,
                        plate_cleared=True,
                        status=PrinterStatus.IDLE,
                    )

    def _try_schedule_fallback(self, target: PrinterID) -> bool:
        with SessionLocal.begin() as db:
            target_state = crud.get_printer_state(db, target)
            if (
                not target_state
                or target_state.status != PrinterStatus.IDLE
                or not target_state.plate_cleared
                or target_state.current_job_id
            ):
                return False

            candidates = (
                db.query(Job)
                .filter(
                    Job.status == JobStatus.QUEUED,
                    Job.assigned_printer != target,
                )
                .order_by(
                    Job.estimated_minutes.asc().nulls_last(),
                    Job.submitted_at.asc(),
                )
                .all()
            )
            for job in candidates:
                # A fallback profile failure must not create a 10-second retry
                # storm. The job remains eligible for its original printer,
                # whose already-produced 3MF is still valid.
                if job.error_message and job.error_message.startswith(
                    "Fallback slicing failed;"
                ):
                    continue
                source_state = crud.get_printer_state(db, job.assigned_printer)
                source_available = bool(
                    source_state
                    and source_state.status == PrinterStatus.IDLE
                    and source_state.plate_cleared
                    and not source_state.current_job_id
                )
                bbox = {"x": job.bbox_x, "y": job.bbox_y, "z": job.bbox_z}
                if (
                    source_available
                    or None in bbox.values()
                    or not complexity.can_fit_on_printer(bbox, target)
                ):
                    continue
                with self._lock:
                    if job.id in self._slicing_jobs:
                        continue
                    self._slicing_jobs.add(job.id)
                try:
                    crud.transition_job_status(
                        db, job.id, JobStatus.QUEUED, JobStatus.SLICING
                    )
                except crud.JobTransitionError:
                    with self._lock:
                        self._slicing_jobs.discard(job.id)
                    continue
                crud.add_log(
                    db,
                    "FALLBACK_SCHEDULED",
                    f"{job.assigned_printer.value} unavailable; preparing {target.value}",
                    job_id=job.id,
                    printer_id=target.value,
                )
                job_id = job.id
                break
            else:
                return False

        self._slicer_executor.submit(self._reslice_for_fallback, job_id, target)
        return True

    def _fail_job(self, job_id: str, message: str) -> None:
        with SessionLocal.begin() as db:
            job = crud.get_job(db, job_id)
            if not job or job.status in crud.TERMINAL_JOB_STATUSES:
                return
            try:
                crud.transition_job_status(
                    db, job_id, job.status, JobStatus.FAILED, error_message=message
                )
            except crud.JobTransitionError:
                return
            crud.add_log(db, "JOB_FAILED", message, job_id=job_id, level=LogLevel.ERROR)
        log.error(f"Job {job_id[:8]} failed: {message}")
