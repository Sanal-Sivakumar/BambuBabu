"""SQLite engine, safety pragmas, sessions, initialization, and online backups."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.db.models import Base, PrinterID, PrinterState, PrinterStatus


DATABASE_URL = f"sqlite:///{settings.DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=settings.DEBUG,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
)


def init_db() -> None:
    """Create tables and seed the two durable printer-state rows."""
    Base.metadata.create_all(bind=engine)
    with SessionLocal.begin() as session:
        for printer_id in PrinterID:
            if session.get(PrinterState, printer_id) is None:
                session.add(
                    PrinterState(
                        printer_id=printer_id,
                        status=PrinterStatus.OFFLINE,
                        plate_cleared=True,
                    )
                )
    for suffix in ("", "-wal", "-shm"):
        sqlite_file = Path(f"{settings.DB_PATH}{suffix}")
        if sqlite_file.exists():
            os.chmod(sqlite_file, 0o600)


@contextmanager
def get_db():
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
    with get_db() as db:
        yield db


def database_is_ready() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def backup_database() -> Path | None:
    """Create a consistent SQLite backup without stopping the application."""
    db_path = Path(settings.DB_PATH)
    if not db_path.exists():
        return None

    settings.DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = settings.DB_BACKUP_DIR / f"bambubabu-{stamp}.db"
    with sqlite3.connect(db_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    os.chmod(destination, 0o600)

    backups = sorted(settings.DB_BACKUP_DIR.glob("bambubabu-*.db"), reverse=True)
    for expired in backups[settings.DB_BACKUP_KEEP :]:
        expired.unlink(missing_ok=True)
    return destination
