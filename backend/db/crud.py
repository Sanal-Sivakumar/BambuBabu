"""Database operations, including compare-and-swap job state transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import desc, func, update
from sqlalchemy.orm import Session

from backend.db.models import (
    Job,
    JobStatus,
    LogEntry,
    LogLevel,
    PrinterID,
    PrinterState,
)


ACTIVE_JOB_STATUSES = {
    JobStatus.PENDING,
    JobStatus.ANALYSING,
    JobStatus.SLICING,
    JobStatus.QUEUED,
    JobStatus.UPLOADING,
    JobStatus.STARTING,
    JobStatus.PRINTING,
    JobStatus.ATTENTION,
}
TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.REJECTED,
    JobStatus.CANCELLED,
}
ALLOWED_JOB_TRANSITIONS = {
    JobStatus.PENDING: {JobStatus.ANALYSING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.ANALYSING: {
        JobStatus.PENDING,
        JobStatus.SLICING,
        JobStatus.REJECTED,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.SLICING: {
        JobStatus.PENDING,
        JobStatus.QUEUED,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.QUEUED: {
        JobStatus.SLICING,
        JobStatus.UPLOADING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.UPLOADING: {
        JobStatus.STARTING,
        JobStatus.ATTENTION,
        JobStatus.FAILED,
    },
    JobStatus.STARTING: {
        JobStatus.PRINTING,
        JobStatus.ATTENTION,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    },
    JobStatus.PRINTING: {
        JobStatus.COMPLETED,
        JobStatus.ATTENTION,
        JobStatus.FAILED,
    },
    JobStatus.ATTENTION: {
        JobStatus.PRINTING,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.REJECTED: set(),
    JobStatus.CANCELLED: set(),
}


class JobTransitionError(RuntimeError):
    """Raised when another actor changed a job before our transition committed."""


def utcnow() -> datetime:
    # Existing SQLite rows use naive UTC, so keep the storage representation compatible.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_job(
    db: Session,
    user_name: str,
    user_email: str,
    description: Optional[str],
    original_filename: str,
    stl_path: str,
    job_id: str | None = None,
) -> Job:
    job = Job(
        id=job_id,
        user_name=user_name,
        user_email=user_email,
        description=description,
        original_filename=original_filename,
        stl_path=str(stl_path),
    )
    db.add(job)
    db.flush()
    return job


def get_job(db: Session, job_id: str) -> Optional[Job]:
    return db.get(Job, job_id)


def get_all_jobs(db: Session, limit: int = 100) -> list[Job]:
    return db.query(Job).order_by(desc(Job.submitted_at)).limit(limit).all()


def get_jobs_by_status(db: Session, status: JobStatus) -> list[Job]:
    return db.query(Job).filter(Job.status == status).order_by(Job.submitted_at).all()


def get_jobs_in_statuses(db: Session, statuses: Iterable[JobStatus]) -> list[Job]:
    return (
        db.query(Job)
        .filter(Job.status.in_(list(statuses)))
        .order_by(Job.submitted_at)
        .all()
    )


def count_active_jobs(db: Session) -> int:
    return int(
        db.query(func.count(Job.id))
        .filter(Job.status.in_(ACTIVE_JOB_STATUSES))
        .scalar()
        or 0
    )


def get_next_queued_job_for_printer(
    db: Session, printer_id: PrinterID
) -> Optional[Job]:
    return (
        db.query(Job)
        .filter(Job.status == JobStatus.QUEUED, Job.assigned_printer == printer_id)
        .order_by(Job.estimated_minutes.asc().nulls_last(), Job.submitted_at.asc())
        .first()
    )


def transition_job_status(
    db: Session,
    job_id: str,
    expected: JobStatus | Iterable[JobStatus],
    new_status: JobStatus,
    *,
    error_message: str | None = None,
    rejection_reason: str | None = None,
) -> Job:
    """Atomically transition only when the persisted status matches ``expected``."""
    expected_statuses = (
        [expected] if isinstance(expected, JobStatus) else list(expected)
    )
    invalid_sources = [
        status
        for status in expected_statuses
        if new_status not in ALLOWED_JOB_TRANSITIONS[status]
    ]
    if invalid_sources:
        sources = ", ".join(status.value for status in invalid_sources)
        raise JobTransitionError(
            f"Illegal job transition {sources} -> {new_status.value}"
        )
    values: dict = {"status": new_status}
    now = utcnow()
    if error_message is not None:
        values["error_message"] = error_message
    if rejection_reason is not None:
        values["rejection_reason"] = rejection_reason
    if new_status == JobStatus.ANALYSING:
        values["analysis_started_at"] = now
    elif new_status == JobStatus.SLICING:
        values["slicing_started_at"] = now
    elif new_status == JobStatus.QUEUED:
        values["slicing_done_at"] = now
    elif new_status == JobStatus.PRINTING:
        values["print_started_at"] = now
    elif new_status in TERMINAL_JOB_STATUSES:
        values["print_ended_at"] = now

    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(expected_statuses))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        current = db.get(Job, job_id)
        actual = current.status.value if current else "missing"
        wanted = ", ".join(status.value for status in expected_statuses)
        raise JobTransitionError(
            f"Job {job_id} transition {wanted} -> {new_status.value} rejected; current={actual}"
        )
    db.flush()
    # Bulk UPDATE deliberately bypasses ORM synchronization for compare-and-swap
    # semantics. Refresh the identity-map object so later decisions in this same
    # transaction cannot observe the pre-transition status.
    current = db.get(Job, job_id)
    if current is None:
        raise JobTransitionError(f"Job {job_id} disappeared after transition")
    db.refresh(current)
    return current


def update_job_status(
    db: Session,
    job_id: str,
    status: JobStatus,
    error_message: Optional[str] = None,
) -> Optional[Job]:
    """Compatibility helper for non-concurrent recovery/admin code."""
    job = db.get(Job, job_id)
    if job is None:
        return None
    return transition_job_status(
        db, job_id, job.status, status, error_message=error_message
    )


def update_job_analysis(
    db: Session, job_id: str, analysis: dict, assigned_printer: PrinterID
) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.complexity_score = analysis["complexity_score"]
        job.face_count = analysis["face_count"]
        job.volume_cm3 = analysis["volume_cm3"]
        job.overhang_ratio = analysis["overhang_ratio"]
        job.bbox_x = analysis["bbox"]["x"]
        job.bbox_y = analysis["bbox"]["y"]
        job.bbox_z = analysis["bbox"]["z"]
        job.assigned_printer = assigned_printer
        db.flush()
    return job


def update_job_assignment(
    db: Session, job_id: str, printer_id: PrinterID
) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.assigned_printer = printer_id
        db.flush()
    return job


def update_job_sliced(
    db: Session, job_id: str, sliced_path: str, estimated_minutes: Optional[int]
) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.sliced_path = str(sliced_path)
        job.estimated_minutes = estimated_minutes
        db.flush()
    return job


def update_job_progress(db: Session, job_id: str, progress: int) -> None:
    job = db.get(Job, job_id)
    if job:
        job.print_progress = max(0, min(int(progress), 100))
        db.flush()


def update_job_plate_cleared(db: Session, job_id: str) -> Optional[Job]:
    job = db.get(Job, job_id)
    if job:
        job.plate_cleared_at = utcnow()
        db.flush()
    return job


def get_printer_state(db: Session, printer_id: PrinterID) -> Optional[PrinterState]:
    return db.get(PrinterState, printer_id)


def get_all_printer_states(db: Session) -> list[PrinterState]:
    return db.query(PrinterState).all()


def update_printer_state(
    db: Session, printer_id: PrinterID, **kwargs
) -> Optional[PrinterState]:
    state = db.get(PrinterState, printer_id)
    if state:
        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)
        state.last_seen = utcnow()
        db.flush()
    return state


def set_plate_cleared(db: Session, printer_id: PrinterID) -> Optional[PrinterState]:
    state = db.get(PrinterState, printer_id)
    if state:
        state.plate_cleared = True
        db.flush()
    return state


def add_log(
    db: Session,
    event: str,
    message: str,
    level: LogLevel = LogLevel.INFO,
    job_id: Optional[str] = None,
    printer_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> LogEntry:
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


def get_logs(
    db: Session, job_id: Optional[str] = None, limit: int = 100
) -> list[LogEntry]:
    query = db.query(LogEntry).order_by(desc(LogEntry.timestamp))
    if job_id:
        query = query.filter(LogEntry.job_id == job_id)
    return query.limit(limit).all()
