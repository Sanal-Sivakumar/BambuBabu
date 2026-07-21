"""
BambuBabu — Queue Processor
Background thread that drives the full automation pipeline:
  PENDING → analyse → slice → QUEUED → PRINTING → COMPLETED
"""
from __future__ import annotations
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from backend.config import settings
from backend.core.logger import get_logger
from backend.db.models import JobStatus, PrinterID, LogLevel
from backend.db.session import SessionLocal
from backend.db import crud
from backend.core import complexity, slicer

if TYPE_CHECKING:
    from backend.core.printer_manager import PrinterManager

log = get_logger("bambububu.queue")


class QueueProcessor:
    """
    Runs a polling loop every N seconds to:
    1. Pick up PENDING jobs → analyse → assign printer → slice → QUEUED
    2. Pick up QUEUED jobs → start printing on idle + plate-cleared printer
    """

    def __init__(self, printer_manager: "PrinterManager"):
        self.pm = printer_manager
        self._running = False
        self._loop_thread: threading.Thread | None = None
        self._slicer_executor = ThreadPoolExecutor(max_workers=1,
                                                   thread_name_prefix="slicer")
        # Track job IDs currently being sliced (avoids double-scheduling)
        self._slicing_jobs: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self._loop_thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="queue-processor",
        )
        self._loop_thread.start()
        log.info(f"QueueProcessor started — polling every {settings.QUEUE_POLL_INTERVAL_SECONDS}s")

    def stop(self) -> None:
        self._running = False
        self._slicer_executor.shutdown(wait=False)
        log.info("QueueProcessor stopped")

    # ── Main loop ───────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                log.error(f"Queue processor error:\n{traceback.format_exc()}")
            time.sleep(settings.QUEUE_POLL_INTERVAL_SECONDS)

    def _tick(self) -> None:
        with SessionLocal() as db:
            # ── 1. Submit pending jobs for slicing ────────────────────────
            pending = crud.get_jobs_by_status(db, JobStatus.PENDING)
            for job in pending:
                with self._lock:
                    if job.id not in self._slicing_jobs:
                        self._slicing_jobs.add(job.id)
                        self._slicer_executor.submit(self._slice_pipeline, job.id)
                        log.info(f"Submitted job {job.id[:8]} for slicing")

            # ── 2. Dispatch queued jobs to idle printers ───────────────────
            # Check A1 Mini first (default, lower power)
            for pid in [PrinterID.A1_MINI, PrinterID.P1S]:
                self._try_dispatch(db, pid)

    def _try_dispatch(self, db, printer_id: PrinterID) -> None:
        state = crud.get_printer_state(db, printer_id)
        if not state:
            return
        if state.status.value not in ("idle",) or not state.plate_cleared:
            return
        if state.current_job_id:
            return

        next_job = crud.get_next_queued_job_for_printer(db, printer_id)
        if not next_job:
            return

        log.info(f"Dispatching job {next_job.id[:8]} to {printer_id}")
        self._start_print(db, next_job, printer_id)

    # ── Slicing pipeline (runs in thread pool) ──────────────────────────────

    def _slice_pipeline(self, job_id: str) -> None:
        """Analyse → assign printer → slice → mark QUEUED. Runs in a worker thread."""
        try:
            with SessionLocal() as db:
                job = crud.get_job(db, job_id)
                if not job or job.status != JobStatus.PENDING:
                    return

                # ── Analyse ──────────────────────────────────────────────
                crud.update_job_status(db, job_id, JobStatus.ANALYSING)
                crud.add_log(db, "ANALYSIS_STARTED",
                             f"Analysing {job.original_filename}", job_id=job_id)
                db.commit()

                try:
                    analysis = complexity.analyse_stl(job.stl_path)
                except Exception as e:
                    self._fail_job(job_id, f"STL analysis error: {e}")
                    return

                # ── Select printer ───────────────────────────────────────
                printer_id, rejection = complexity.select_printer(analysis)
                if rejection:
                    with SessionLocal() as db2:
                        crud.reject_job(db2, job_id, rejection)
                        crud.add_log(db2, "JOB_REJECTED", rejection,
                                     job_id=job_id, level=LogLevel.WARNING)
                        db2.commit()
                    log.warning(f"Job {job_id[:8]} rejected: {rejection}")
                    return

                with SessionLocal() as db2:
                    crud.update_job_analysis(db2, job_id, analysis, printer_id)
                    crud.add_log(db2, "ANALYSIS_DONE",
                                 f"Score={analysis['complexity_score']:.1f} → {printer_id}",
                                 job_id=job_id,
                                 extra=analysis)
                    db2.commit()

                # ── Slice ────────────────────────────────────────────────
                with SessionLocal() as db3:
                    crud.update_job_status(db3, job_id, JobStatus.SLICING)
                    crud.add_log(db3, "SLICING_STARTED",
                                 f"Slicing for {printer_id}", job_id=job_id)
                    db3.commit()

                try:
                    sliced_path, est_minutes = slicer.slice_stl(
                        job.stl_path, printer_id, settings.SLICED_DIR
                    )
                except Exception as e:
                    self._fail_job(job_id, f"Slicing error: {e}")
                    return

                # ── Queue ────────────────────────────────────────────────
                with SessionLocal() as db4:
                    crud.update_job_sliced(db4, job_id, str(sliced_path), est_minutes)
                    crud.update_job_status(db4, job_id, JobStatus.QUEUED)
                    crud.add_log(db4, "JOB_QUEUED",
                                 f"Queued for {printer_id}, est. {est_minutes} min",
                                 job_id=job_id, printer_id=printer_id.value)
                    db4.commit()
                log.info(f"Job {job_id[:8]} queued for {printer_id} (~{est_minutes} min)")

        except Exception:
            log.error(f"Slice pipeline crashed for {job_id}:\n{traceback.format_exc()}")
            self._fail_job(job_id, "Internal error during slice pipeline")
        finally:
            with self._lock:
                self._slicing_jobs.discard(job_id)

    # ── Print dispatch ──────────────────────────────────────────────────────

    def _start_print(self, db, job, printer_id: PrinterID) -> None:
        """Upload .3mf and issue the print command."""
        try:
            crud.update_job_status(db, job.id, JobStatus.UPLOADING)
            crud.update_printer_state(db, printer_id, current_job_id=job.id)
            crud.add_log(db, "UPLOADING", f"Uploading to {printer_id}", job_id=job.id,
                         printer_id=printer_id.value)
            db.commit()

            self.pm.upload_and_print(printer_id, job.sliced_path, job.id)

            with SessionLocal() as db2:
                crud.update_job_status(db2, job.id, JobStatus.PRINTING)
                crud.update_printer_state(db2, printer_id, plate_cleared=False)
                crud.add_log(db2, "PRINT_STARTED",
                             f"Printing started on {printer_id}", job_id=job.id,
                             printer_id=printer_id.value)
                db2.commit()

            log.info(f"Job {job.id[:8]} is now PRINTING on {printer_id}")

            # Send "print started" email
            try:
                from backend.email.mailer import send_print_started
                send_print_started(job, printer_id.value)
            except Exception as e:
                log.warning(f"Could not send print-started email: {e}")

        except Exception as e:
            log.error(f"Failed to start print for {job.id[:8]}: {e}")
            self._fail_job(job.id, f"Print start error: {e}")
            with SessionLocal() as db_err:
                crud.update_printer_state(db_err, printer_id, current_job_id=None)
                db_err.commit()

    def _fail_job(self, job_id: str, message: str) -> None:
        with SessionLocal() as db:
            crud.update_job_status(db, job_id, JobStatus.FAILED, error_message=message)
            crud.add_log(db, "JOB_FAILED", message, job_id=job_id, level=LogLevel.ERROR)
            db.commit()
        log.error(f"Job {job_id[:8]} FAILED: {message}")
