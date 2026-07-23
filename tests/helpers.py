from __future__ import annotations

import struct

from backend.db import crud
from backend.db.models import JobStatus


def binary_stl() -> bytes:
    header = b"BambuBabu test STL".ljust(80, b"\0")
    triangle = struct.pack("<12fH", *(0.0 for _ in range(12)), 0)
    return header + struct.pack("<I", 1) + triangle


def create_job(db, path: str, *, status: JobStatus = JobStatus.PENDING, printer=None):
    job = crud.create_job(
        db,
        "Test User",
        "test@example.com",
        None,
        "part.stl",
        path,
    )
    if printer:
        job.assigned_printer = printer
    if status != JobStatus.PENDING:
        # Seed durable scenarios without pretending they followed a production path.
        job.status = status
    db.flush()
    return job
