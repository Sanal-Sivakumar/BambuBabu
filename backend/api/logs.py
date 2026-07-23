"""Structured database-backed event log API used by the dashboard."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.db import crud
from backend.db.session import get_db_dep


router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/all")
def get_logs(
    limit: int = Query(80, ge=1, le=500), db: Session = Depends(get_db_dep)
) -> list[dict]:
    return [
        {
            "id": entry.id,
            "timestamp": entry.timestamp.isoformat(),
            "level": entry.level.value,
            "event": entry.event,
            "message": entry.message,
            "job_id": entry.job_id,
            "printer_id": entry.printer_id,
            "extra": entry.extra,
        }
        for entry in crud.get_logs(db, limit=limit)
    ]
