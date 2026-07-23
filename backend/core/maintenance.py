"""Retention cleanup and periodic consistent SQLite backups."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path

from backend.config import settings
from backend.core.logger import get_logger
from backend.db import crud
from backend.db.models import Job
from backend.db.session import SessionLocal, backup_database


log = get_logger("bambubabu.maintenance")


class MaintenanceWorker:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_backup = 0.0

    def start(self) -> None:
        self.run_once(force_backup=True)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="maintenance"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(settings.MAINTENANCE_INTERVAL_SECONDS):
            try:
                self.run_once()
            except Exception as exc:
                log.error(f"Maintenance pass failed: {exc}")

    def run_once(self, *, force_backup: bool = False) -> None:
        self._cleanup_terminal_files()
        self._cleanup_partial_uploads()
        self._cleanup_orphan_files()
        interval = settings.DB_BACKUP_INTERVAL_HOURS * 3600
        if force_backup or time.monotonic() - self._last_backup >= interval:
            backup = backup_database()
            if backup:
                log.info(f"SQLite backup created: {backup.name}")
            self._last_backup = time.monotonic()

    def _cleanup_terminal_files(self) -> None:
        cutoff = crud.utcnow() - timedelta(days=settings.TERMINAL_FILE_RETENTION_DAYS)
        removed = 0
        with SessionLocal.begin() as db:
            protected_ids = {
                state.current_job_id
                for state in crud.get_all_printer_states(db)
                if state.current_job_id
            }
            jobs = (
                db.query(Job)
                .filter(
                    Job.status.in_(crud.TERMINAL_JOB_STATUSES),
                    Job.print_ended_at < cutoff,
                )
                .all()
            )
            for job in jobs:
                if job.id in protected_ids:
                    continue
                for value in (job.stl_path, job.sliced_path):
                    if value:
                        path = Path(value)
                        if path.is_file():
                            path.unlink(missing_ok=True)
                            removed += 1
        if removed:
            log.info(f"Retention cleanup removed {removed} stored print files")

    def _cleanup_partial_uploads(self) -> None:
        cutoff = time.time() - settings.PARTIAL_UPLOAD_RETENTION_HOURS * 3600
        for path in settings.UPLOAD_DIR.glob("*.part"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue

    def _cleanup_orphan_files(self) -> None:
        """Remove crash/cancellation leftovers only after an ample safety window."""
        cutoff = time.time() - settings.ORPHAN_FILE_RETENTION_HOURS * 3600
        with SessionLocal() as db:
            referenced = {
                value
                for pair in db.query(Job.stl_path, Job.sliced_path).all()
                for value in pair
                if value
            }
        for directory, pattern in (
            (settings.UPLOAD_DIR, "*.stl"),
            (settings.SLICED_DIR, "*.3mf"),
        ):
            for path in directory.glob(pattern):
                try:
                    if str(path) not in referenced and path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except FileNotFoundError:
                    continue


maintenance_worker = MaintenanceWorker()
