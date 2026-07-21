"""
BambuBabu — Database Session
SQLite connection + table initialisation
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

from backend.config import settings
from backend.db.models import Base, PrinterState, PrinterID, PrinterStatus

DATABASE_URL = f"sqlite:///{settings.DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # needed for SQLite + threads
    echo=settings.DEBUG,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create all tables and seed initial printer state rows."""
    Base.metadata.create_all(bind=engine)

    # Seed printer state if not present
    with SessionLocal() as session:
        for pid in PrinterID:
            existing = session.get(PrinterState, pid)
            if not existing:
                session.add(PrinterState(
                    printer_id=pid,
                    status=PrinterStatus.OFFLINE,
                    plate_cleared=True,
                ))
        session.commit()


@contextmanager
def get_db():
    """Context manager for database sessions."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_dep():
    """FastAPI dependency — yields a DB session."""
    with get_db() as db:
        yield db
