"""Job upload, listing, status, cancellation, and per-job event endpoints."""

from __future__ import annotations

import asyncio
import os
import struct
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.core.logger import get_logger
from backend.db import crud
from backend.db.models import JobStatus
from backend.db.session import get_db_dep


log = get_logger("bambubabu.api.jobs")
router = APIRouter(prefix="/api/jobs", tags=["jobs"])
UPLOAD_LOCK = asyncio.Lock()
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_BYTES = settings.MAX_STL_SIZE_MB * 1024 * 1024
CANCELLABLE = {
    JobStatus.PENDING,
    JobStatus.ANALYSING,
    JobStatus.SLICING,
    JobStatus.QUEUED,
}


def _job_to_dict(job) -> dict:
    return {
        "id": job.id,
        "user_name": job.user_name,
        "description": job.description,
        "original_filename": job.original_filename,
        "status": job.status.value,
        "assigned_printer": job.assigned_printer.value
        if job.assigned_printer
        else None,
        "complexity_score": job.complexity_score,
        "face_count": job.face_count,
        "volume_cm3": job.volume_cm3,
        "overhang_ratio": job.overhang_ratio,
        "bbox": (
            {"x": job.bbox_x, "y": job.bbox_y, "z": job.bbox_z}
            if job.bbox_x is not None
            else None
        ),
        "estimated_minutes": job.estimated_minutes,
        "print_progress": job.print_progress,
        "error_message": job.error_message,
        "rejection_reason": job.rejection_reason,
        "submitted_at": job.submitted_at.isoformat() if job.submitted_at else None,
        "slicing_done_at": job.slicing_done_at.isoformat()
        if job.slicing_done_at
        else None,
        "print_started_at": job.print_started_at.isoformat()
        if job.print_started_at
        else None,
        "print_ended_at": job.print_ended_at.isoformat()
        if job.print_ended_at
        else None,
        "plate_cleared_at": job.plate_cleared_at.isoformat()
        if job.plate_cleared_at
        else None,
    }


def _single_line(value: str, limit: int) -> str:
    """Remove control characters from values that reach logs, HTML, or headers."""
    return " ".join(value.split())[:limit].strip()


def _storage_bytes() -> int:
    total = 0
    for directory in (settings.UPLOAD_DIR, settings.SLICED_DIR):
        for path in directory.glob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
    return total


def _validate_stl_file(path: Path) -> None:
    size = path.stat().st_size
    if size < 84:
        raise HTTPException(400, "File is too small to be a valid STL")
    with path.open("rb") as handle:
        header = handle.read(84)
        triangle_count = struct.unpack("<I", header[80:84])[0]
        expected_binary_size = 84 + triangle_count * 50
        if expected_binary_size == size:
            return
        if header[:5].lower() == b"solid":
            handle.seek(max(0, size - 4096))
            if b"endsolid" in handle.read().lower():
                return
    raise HTTPException(400, "STL structure is invalid or truncated")


async def _stream_upload(file: UploadFile, destination: Path) -> int:
    written = 0
    try:
        with destination.open("xb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_BYTES:
                    raise HTTPException(
                        413, f"File exceeds the {settings.MAX_STL_SIZE_MB} MB limit"
                    )
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        return written
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("", status_code=201)
async def upload_job(
    file: UploadFile = File(..., description="Binary or ASCII STL file"),
    user_name: str = Form(..., min_length=1, max_length=128),
    user_email: str = Form(..., min_length=3, max_length=256),
    description: str = Form("", max_length=2000),
    db: Session = Depends(get_db_dep),
):
    original_filename = _single_line(Path(file.filename or "").name, 256)
    if not original_filename or Path(original_filename).suffix.lower() != ".stl":
        raise HTTPException(400, "Only .stl files are accepted")
    try:
        normalized_email = str(
            TypeAdapter(EmailStr).validate_python(user_email)
        ).lower()
    except ValidationError as exc:
        raise HTTPException(422, "A valid email address is required") from exc

    safe_name = _single_line(user_name, 128)
    safe_description = _single_line(description, 2000)
    if not safe_name:
        raise HTTPException(422, "A non-empty name is required")

    job_id = str(uuid.uuid4())
    partial_path = settings.UPLOAD_DIR / f"{job_id}.stl.part"
    final_path = settings.UPLOAD_DIR / f"{job_id}.stl"
    storage_limit = settings.MAX_STORAGE_MB * 1024 * 1024

    async with UPLOAD_LOCK:
        if crud.count_active_jobs(db) >= settings.MAX_ACTIVE_JOBS:
            raise HTTPException(429, "The print queue is at capacity; try again later")
        current_storage = _storage_bytes()
        if current_storage >= storage_limit:
            raise HTTPException(507, "Print storage quota is exhausted")

        written = await _stream_upload(file, partial_path)
        if current_storage + written > storage_limit:
            partial_path.unlink(missing_ok=True)
            raise HTTPException(507, "Upload would exceed the print storage quota")
        try:
            _validate_stl_file(partial_path)
            os.replace(partial_path, final_path)
            job = crud.create_job(
                db,
                user_name=safe_name,
                user_email=normalized_email,
                description=safe_description or None,
                original_filename=original_filename,
                stl_path=str(final_path),
                job_id=job_id,
            )
            crud.add_log(
                db,
                "JOB_UPLOADED",
                f"{original_filename} uploaded by {job.user_name}",
                job_id=job_id,
            )
            db.commit()
        except Exception:
            partial_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            db.rollback()
            raise

    return {"job_id": job_id, "message": "Job accepted for analysis"}


@router.get("")
def list_jobs(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db_dep)):
    return [_job_to_dict(job) for job in crud.get_all_jobs(db, limit=limit)]


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db_dep)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_to_dict(job)


@router.delete("/{job_id}")
def cancel_job(job_id: str, db: Session = Depends(get_db_dep)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    try:
        crud.transition_job_status(db, job_id, CANCELLABLE, JobStatus.CANCELLED)
    except crud.JobTransitionError as exc:
        db.rollback()
        current = crud.get_job(db, job_id)
        status = current.status.value if current else "missing"
        raise HTTPException(409, f"Cannot cancel a job in '{status}' state") from exc
    crud.add_log(db, "JOB_CANCELLED", "Cancelled by user", job_id=job_id)
    db.commit()
    # Files are intentionally retained until the maintenance worker; a slicer may still hold them.
    return {"message": "Job cancelled"}


@router.get("/{job_id}/logs")
def job_logs(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db_dep),
):
    if not crud.get_job(db, job_id):
        raise HTTPException(404, "Job not found")
    return [
        {
            "id": entry.id,
            "timestamp": entry.timestamp.isoformat(),
            "level": entry.level.value,
            "event": entry.event,
            "message": entry.message,
            "printer_id": entry.printer_id,
            "extra": entry.extra,
        }
        for entry in crud.get_logs(db, job_id=job_id, limit=limit)
    ]
