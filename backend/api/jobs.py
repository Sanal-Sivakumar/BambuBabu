"""
BambuBabu — Job API Routes
POST /api/jobs        Upload STL, create job
GET  /api/jobs        List all jobs
GET  /api/jobs/{id}   Get single job
DELETE /api/jobs/{id} Cancel a job
GET  /api/logs        Get log entries
"""
from __future__ import annotations
import uuid
import shutil
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.session import get_db_dep
from backend.db import crud
from backend.db.models import JobStatus
from backend.core.logger import get_logger

log = get_logger("bambububu.api.jobs")
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

ALLOWED_EXTENSIONS = {".stl"}
MAX_BYTES = settings.MAX_STL_SIZE_MB * 1024 * 1024


# ── Helpers ─────────────────────────────────────────────────────────────────

def _validate_stl(file: UploadFile, data: bytes) -> None:
    """Basic STL validation — extension + magic bytes."""
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Only .stl files are accepted (got {suffix})")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"File too large (max {settings.MAX_STL_SIZE_MB} MB)")
    if len(data) < 84:
        raise HTTPException(400, "File too small to be a valid STL")
    # ASCII STL starts with "solid", binary STL is 80-byte header + 4-byte count
    is_ascii  = data[:5].lower() == b"solid"
    is_binary = len(data) >= 84  # binary always >= 84 bytes
    if not (is_ascii or is_binary):
        raise HTTPException(400, "File does not appear to be a valid STL")


def _job_to_dict(job) -> dict:
    return {
        "id":                job.id,
        "user_name":         job.user_name,
        "user_email":        job.user_email,
        "description":       job.description,
        "original_filename": job.original_filename,
        "status":            job.status,
        "assigned_printer":  job.assigned_printer,
        "complexity_score":  job.complexity_score,
        "face_count":        job.face_count,
        "volume_cm3":        job.volume_cm3,
        "overhang_ratio":    job.overhang_ratio,
        "bbox":              {"x": job.bbox_x, "y": job.bbox_y, "z": job.bbox_z}
                             if job.bbox_x else None,
        "estimated_minutes": job.estimated_minutes,
        "print_progress":    job.print_progress,
        "error_message":     job.error_message,
        "rejection_reason":  job.rejection_reason,
        "submitted_at":      job.submitted_at.isoformat() if job.submitted_at else None,
        "slicing_done_at":   job.slicing_done_at.isoformat() if job.slicing_done_at else None,
        "print_started_at":  job.print_started_at.isoformat() if job.print_started_at else None,
        "print_ended_at":    job.print_ended_at.isoformat() if job.print_ended_at else None,
        "plate_cleared_at":  job.plate_cleared_at.isoformat() if job.plate_cleared_at else None,
    }


# ── Routes ──────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def upload_job(
    file:        UploadFile = File(..., description="STL file to print"),
    user_name:   str        = Form(..., description="Your name"),
    user_email:  str        = Form(..., description="Your email address"),
    description: str        = Form("",  description="What is this model?"),
    db: Session             = Depends(get_db_dep),
):
    """Upload an STL file — creates a new print job."""
    data = await file.read()
    _validate_stl(file, data)

    # Save file with UUID name to prevent collisions / path traversal
    job_id   = str(uuid.uuid4())
    ext      = Path(file.filename).suffix.lower()
    saved_fn = f"{job_id}{ext}"
    saved_path = settings.UPLOAD_DIR / saved_fn

    with open(saved_path, "wb") as f:
        f.write(data)

    job = crud.create_job(
        db,
        user_name        = user_name.strip(),
        user_email       = user_email.strip().lower(),
        description      = description.strip() or None,
        original_filename = file.filename,
        stl_path         = str(saved_path),
    )
    # Override the auto-generated ID with our pre-made one so file name matches
    job.id = job_id

    crud.add_log(db, "JOB_UPLOADED",
                 f"{file.filename} uploaded by {user_name}",
                 job_id=job_id)
    db.commit()

    log.info(f"New job {job_id[:8]} — {file.filename} by {user_name}")
    return {"job_id": job_id, "message": "Job created — slicing will begin shortly"}


@router.get("")
def list_jobs(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db_dep),
):
    """List all print jobs, newest first."""
    jobs = crud.get_all_jobs(db, limit=limit)
    return [_job_to_dict(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db_dep)):
    """Get a single print job by ID."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_to_dict(job)


@router.delete("/{job_id}", status_code=200)
def cancel_job(job_id: str, db: Session = Depends(get_db_dep)):
    """Cancel a job (only if PENDING or QUEUED)."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in (JobStatus.PENDING, JobStatus.QUEUED, JobStatus.ANALYSING):
        raise HTTPException(409, f"Cannot cancel a job in '{job.status}' state")

    # Remove the STL file
    try:
        Path(job.stl_path).unlink(missing_ok=True)
        if job.sliced_path:
            Path(job.sliced_path).unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Could not delete files for job {job_id[:8]}: {e}")

    crud.update_job_status(db, job_id, JobStatus.CANCELLED)
    crud.add_log(db, "JOB_CANCELLED", "Cancelled by user", job_id=job_id)
    db.commit()
    return {"message": "Job cancelled"}


@router.get("/{job_id}/logs")
def job_logs(
    job_id: str,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db_dep),
):
    """Get log entries for a specific job."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    logs = crud.get_logs(db, job_id=job_id, limit=limit)
    return [
        {
            "id":         l.id,
            "timestamp":  l.timestamp.isoformat(),
            "level":      l.level,
            "event":      l.event,
            "message":    l.message,
            "printer_id": l.printer_id,
            "extra":      l.extra,
        }
        for l in logs
    ]


@router.get("/logs/all")
def all_logs(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db_dep),
):
    """Get all log entries (newest first)."""
    logs = crud.get_logs(db, limit=limit)
    return [
        {
            "id":         l.id,
            "timestamp":  l.timestamp.isoformat(),
            "level":      l.level,
            "event":      l.event,
            "message":    l.message,
            "job_id":     l.job_id,
            "printer_id": l.printer_id,
            "extra":      l.extra,
        }
        for l in logs
    ]
