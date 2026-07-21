"""
BambuBabu — Database Models
SQLAlchemy ORM models for jobs, printer state, and logs
"""
import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean,
    DateTime, Enum, Text, JSON, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ── Enums ──────────────────────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    PENDING    = "pending"    # Uploaded, waiting for analysis + slicing
    ANALYSING  = "analysing"  # STL being analysed
    SLICING    = "slicing"    # OrcaSlicer running
    QUEUED     = "queued"     # Ready to print, waiting for printer
    UPLOADING  = "uploading"  # .3mf being sent to printer
    PRINTING   = "printing"   # Currently printing
    COMPLETED  = "completed"  # Done, waiting for plate clearance
    FAILED     = "failed"     # Something went wrong
    REJECTED   = "rejected"   # File too large or invalid
    CANCELLED  = "cancelled"  # Cancelled by user


class PrinterID(str, enum.Enum):
    P1S     = "p1s"
    A1_MINI = "a1_mini"


class PrinterStatus(str, enum.Enum):
    IDLE     = "idle"
    PRINTING = "printing"
    PAUSED   = "paused"
    ERROR    = "error"
    OFFLINE  = "offline"


class LogLevel(str, enum.Enum):
    DEBUG   = "DEBUG"
    INFO    = "INFO"
    WARNING = "WARNING"
    ERROR   = "ERROR"


# ── Models ─────────────────────────────────────────────────────────────────

class Job(Base):
    __tablename__ = "jobs"

    id                   = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_name            = Column(String(128), nullable=False)
    user_email           = Column(String(256), nullable=False)
    description          = Column(Text, nullable=True)
    original_filename    = Column(String(256), nullable=False)
    stl_path             = Column(String(512), nullable=False)
    sliced_path          = Column(String(512), nullable=True)

    # Status
    status               = Column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    error_message        = Column(Text, nullable=True)
    rejection_reason     = Column(Text, nullable=True)

    # STL analysis results
    complexity_score     = Column(Float, nullable=True)
    face_count           = Column(Integer, nullable=True)
    volume_cm3           = Column(Float, nullable=True)
    overhang_ratio       = Column(Float, nullable=True)
    bbox_x               = Column(Float, nullable=True)  # mm
    bbox_y               = Column(Float, nullable=True)
    bbox_z               = Column(Float, nullable=True)

    # Printer assignment
    assigned_printer     = Column(Enum(PrinterID), nullable=True)
    estimated_minutes    = Column(Integer, nullable=True)  # from slicer
    print_progress       = Column(Integer, default=0)       # 0–100 %

    # Timestamps
    submitted_at         = Column(DateTime, default=datetime.utcnow, nullable=False)
    analysis_started_at  = Column(DateTime, nullable=True)
    slicing_started_at   = Column(DateTime, nullable=True)
    slicing_done_at      = Column(DateTime, nullable=True)
    print_started_at     = Column(DateTime, nullable=True)
    print_ended_at       = Column(DateTime, nullable=True)
    plate_cleared_at     = Column(DateTime, nullable=True)

    # Relationship
    logs = relationship("LogEntry", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Job id={self.id[:8]} file={self.original_filename} status={self.status}>"


class PrinterState(Base):
    __tablename__ = "printer_state"

    printer_id       = Column(Enum(PrinterID), primary_key=True)
    status           = Column(Enum(PrinterStatus), default=PrinterStatus.OFFLINE, nullable=False)
    current_job_id   = Column(String, ForeignKey("jobs.id"), nullable=True)
    plate_cleared    = Column(Boolean, default=True, nullable=False)
    print_progress   = Column(Integer, default=0)
    last_seen        = Column(DateTime, nullable=True)
    nozzle_temp      = Column(Float, nullable=True)
    bed_temp         = Column(Float, nullable=True)

    def __repr__(self):
        return f"<PrinterState id={self.printer_id} status={self.status}>"


class LogEntry(Base):
    __tablename__ = "log_entries"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    timestamp  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    level      = Column(Enum(LogLevel), default=LogLevel.INFO, nullable=False)
    event      = Column(String(64), nullable=False, index=True)  # e.g. "JOB_QUEUED"
    message    = Column(Text, nullable=False)
    job_id     = Column(String, ForeignKey("jobs.id"), nullable=True, index=True)
    printer_id = Column(String, nullable=True)
    extra      = Column(JSON, nullable=True)

    job = relationship("Job", back_populates="logs")

    def __repr__(self):
        return f"<Log [{self.level}] {self.event}: {self.message[:60]}>"
