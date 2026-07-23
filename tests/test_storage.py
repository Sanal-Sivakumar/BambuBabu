from __future__ import annotations

import os
from datetime import timedelta

from backend.config import settings
from backend.core.maintenance import MaintenanceWorker
from backend.db import crud
from backend.db.models import JobStatus
from backend.db.session import SessionLocal, backup_database, engine
from tests.helpers import binary_stl, create_job


def test_sqlite_uses_wal_and_backup_is_consistent():
    with engine.connect() as connection:
        mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        assert mode.lower() == "wal"
    assert os.stat(settings.DB_PATH).st_mode & 0o077 == 0
    backup = backup_database()
    assert backup is not None and backup.is_file() and backup.stat().st_size > 0
    assert os.stat(backup).st_mode & 0o077 == 0


def test_retention_removes_terminal_files(monkeypatch):
    monkeypatch.setattr(settings, "TERMINAL_FILE_RETENTION_DAYS", 1)
    source = settings.UPLOAD_DIR / "expired.stl"
    sliced = settings.SLICED_DIR / "expired.3mf"
    source.write_bytes(binary_stl())
    sliced.write_bytes(b"sliced")
    with SessionLocal.begin() as db:
        job = create_job(db, str(source), status=JobStatus.FAILED)
        job.sliced_path = str(sliced)
        job.print_ended_at = crud.utcnow() - timedelta(days=2)

    MaintenanceWorker()._cleanup_terminal_files()
    assert not source.exists()
    assert not sliced.exists()


def test_orphan_cleanup_handles_cancelled_slicer_leftovers(monkeypatch):
    monkeypatch.setattr(settings, "ORPHAN_FILE_RETENTION_HOURS", 1)
    orphan = settings.SLICED_DIR / "cancelled-worker.3mf"
    orphan.write_bytes(b"orphan")
    old = crud.utcnow().timestamp() - 7200
    os.utime(orphan, (old, old))

    MaintenanceWorker()._cleanup_orphan_files()
    assert not orphan.exists()
