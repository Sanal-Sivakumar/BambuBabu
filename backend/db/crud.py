"""
BambuBabu — CRUD Operations
All database read/write operations — no business logic here
"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc

from backend.db.models import Job, PrinterState, LogEntry, JobStatus, PrinterID, PrinterStatus, LogLevel


# ── Job CRUD ───────────────────────────────────────────────────────────────

def create_job(db: Session, user_name: str, user_email: str,
               description: Optional[str], original_filename: str, stl_path: str) -> Job:
    job = Job(
        user_name=user_name,
        user_email=user_email,
        description=description,
        original_filename=original_filename,
        stl_path=str(stl_path),
    )
    db.add(job)
    db.flush()  # get the ID without committing
    return job


def get_job(db: Session, job_id: str) -> Optional[Job]:
    return db.get(Job, job_id)


def get_all_jobs(db: Session, limit: int = 100) -> list[Job]:
    return db.query(Job).order_by(desc(Job.submitted_at)).limit(limit).all()


def get_jobs_by_status(db: Session, status: JobStatus) -> list[Job]:
    return db.query(Job).filter(Job.status == status).all()


def get_next_queued_job_for_printer(db: Session, printer_id: PrinterID) -> Optional[Job]:
    """
    Return the next QUEUED job assigned to this printer.
    Priority: shortest estimated_minutes first, then earliest submission.
    """
    return (
        db.query(Job)
        .filter(Job.status == JobStatus.QUEUED, Job.assigned_printer == printer_id)
        .order_by(Job.estimated_minutes.asc().nulls_last(), Job.submitted_at.asc())
        .first()
    )


def update_job_status(db: Session, job_id: str, status: JobStatus,
                      error_message: Optional[str] = None) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.status = status
        if error_message:
            job.error_message = error_message
        # Set timestamps automatically
        now = datetime.utcnow()
        if status == JobStatus.ANALYSING:
            job.analysis_started_at = now
        elif status == JobStatus.SLICING:
            job.slicing_started_at = now
        elif status == JobStatus.QUEUED:
            job.slicing_done_at = now
        elif status == JobStatus.PRINTING:
            job.print_started_at = now
        elif status in (JobStatus.COMPLETED, JobStatus.FAILED):
            job.print_ended_at = now
        db.flush()
    return job


def update_job_analysis(db: Session, job_id: str, analysis: dict,
                        assigned_printer: PrinterID) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.complexity_score  = analysis["complexity_score"]
        job.face_count        = analysis["face_count"]
        job.volume_cm3        = analysis["volume_cm3"]
        job.overhang_ratio    = analysis["overhang_ratio"]
        job.bbox_x            = analysis["bbox"]["x"]
        job.bbox_y            = analysis["bbox"]["y"]
        job.bbox_z            = analysis["bbox"]["z"]
        job.assigned_printer  = assigned_printer
        db.flush()
    return job


def update_job_sliced(db: Session, job_id: str, sliced_path: str,
                      estimated_minutes: Optional[int]) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.sliced_path       = str(sliced_path)
        job.estimated_minutes = estimated_minutes
        db.flush()
    return job


def update_job_progress(db: Session, job_id: str, progress: int) -> None:
    job = db.get(Job, job_id)
    if job:
        job.print_progress = progress
        db.flush()


def update_job_plate_cleared(db: Session, job_id: str) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.plate_cleared_at = datetime.utcnow()
        db.flush()
    return job


def reject_job(db: Session, job_id: str, reason: str) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.status           = JobStatus.REJECTED
        job.rejection_reason = reason
        db.flush()
    return job


# ── PrinterState CRUD ──────────────────────────────────────────────────────

def get_printer_state(db: Session, printer_id: PrinterID) -> Optional[PrinterState]:
    return db.get(PrinterState, printer_id)


def get_all_printer_states(db: Session) -> list[PrinterState]:
    return db.query(PrinterState).all()


def update_printer_state(db: Session, printer_id: PrinterID, **kwargs) -> Optional[PrinterState]:
    state = db.get(PrinterState, printer_id)
    if state:
        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)
        state.last_seen = datetime.utcnow()
        db.flush()
    return state


def set_plate_cleared(db: Session, printer_id: PrinterID) -> Optional[PrinterState]:
    state = db.get(PrinterState, printer_id)
    if state:
        state.plate_cleared = True
        db.flush()
    return state


# ── Log CRUD ───────────────────────────────────────────────────────────────

def add_log(db: Session, event: str, message: str,
            level: LogLevel = LogLevel.INFO,
            job_id: Optional[str] = None,
            printer_id: Optional[str] = None,
            extra: Optional[dict] = None) -> LogEntry:
    entry = LogEntry(
        level=level,
        event=event,
        message=message,
        job_id=job_id,
        printer_id=printer_id,
        extra=extra,
    )
    db.add(entry)
    db.flush()
    return entry


def get_logs(db: Session, job_id: Optional[str] = None,
             limit: int = 100) -> list[LogEntry]:
    q = db.query(LogEntry).order_by(desc(LogEntry.timestamp))
    if job_id:
        q = q.filter(LogEntry.job_id == job_id)
    return q.limit(limit).all()
