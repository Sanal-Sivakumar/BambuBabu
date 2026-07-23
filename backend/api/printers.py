"""
BambuBabu — Printer API Routes
GET  /api/printers               Live status of both printers
POST /api/printers/{id}/plate-cleared   Admin clears the plate
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from backend.core.printer_manager import printer_manager
from backend.db.session import get_db_dep
from backend.db import crud
from backend.db.models import Job, JobStatus, PrinterID, PrinterStatus
from backend.core.logger import get_logger

log = get_logger("bambubabu.api.printers")
router = APIRouter(prefix="/api/printers", tags=["printers"])

VALID_PRINTER_IDS = {p.value for p in PrinterID}


@router.get("")
def get_printers(db: Session = Depends(get_db_dep)):
    """
    Return live status of both printers — combines MQTT live data
    with DB state (plate_cleared, current_job_id).
    """
    live = printer_manager.get_snapshot()
    result = []

    for pid in PrinterID:
        db_state = crud.get_printer_state(db, pid)
        live_data = live.get(pid.value, {})

        result.append(
            {
                "printer_id": pid.value,
                "name": "Bambu Lab P1S"
                if pid == PrinterID.P1S
                else "Bambu Lab A1 Mini",
                "status": live_data.get("status", "offline"),
                "gcode_state": live_data.get("gcode_state", "OFFLINE"),
                "progress": live_data.get("progress", 0),
                "nozzle_temp": live_data.get("nozzle_temp", 0),
                "bed_temp": live_data.get("bed_temp", 0),
                "connected": live_data.get("connected", False),
                "last_seen": live_data.get("last_seen"),
                "plate_cleared": db_state.plate_cleared if db_state else True,
                "current_job_id": db_state.current_job_id if db_state else None,
            }
        )

    return result


@router.post("/{printer_id}/plate-cleared")
def mark_plate_cleared(
    printer_id: str,
    db: Session = Depends(get_db_dep),
):
    """
    Admin endpoint — marks the plate as cleared so the next queued job
    can start printing.
    """
    if printer_id not in VALID_PRINTER_IDS:
        raise HTTPException(
            400, f"Unknown printer: {printer_id}. Valid: {list(VALID_PRINTER_IDS)}"
        )

    pid = PrinterID(printer_id)
    state = crud.get_printer_state(db, pid)

    if not state:
        raise HTTPException(404, "Printer state not found")

    if state.plate_cleared:
        return {"message": "Plate was already marked as cleared"}

    if state.status in {PrinterStatus.PRINTING, PrinterStatus.PAUSED}:
        raise HTTPException(409, "Cannot clear the plate while the printer is active")

    current_job = (
        crud.get_job(db, state.current_job_id) if state.current_job_id else None
    )
    if current_job and current_job.status in {JobStatus.PRINTING, JobStatus.STARTING}:
        raise HTTPException(409, "Cannot clear the plate while the job is active")

    if current_job and current_job.status == JobStatus.ATTENTION:
        crud.transition_job_status(
            db,
            current_job.id,
            JobStatus.ATTENTION,
            JobStatus.FAILED,
            error_message=(
                "Admin inspected the ambiguous printer state and cleared the plate; "
                "the interrupted job was not automatically retried."
            ),
        )

    # Mark cleared
    crud.set_plate_cleared(db, pid)

    # If there was a completed job on this printer, record the timestamp
    if current_job:
        crud.update_job_plate_cleared(db, current_job.id)

    # Clear current_job_id so queue processor can pick next job
    crud.update_printer_state(db, pid, current_job_id=None)

    crud.add_log(
        db,
        "PLATE_CLEARED",
        f"Plate cleared on {printer_id} by admin",
        printer_id=printer_id,
    )
    db.commit()

    log.info(f"Plate cleared on {printer_id} — next job can now start")
    return {"message": f"Plate cleared on {printer_id}. Next job will start shortly."}


@router.get("/{printer_id}/history")
def printer_history(
    printer_id: str,
    db: Session = Depends(get_db_dep),
):
    """Return recent completed/failed jobs for a printer."""
    if printer_id not in VALID_PRINTER_IDS:
        raise HTTPException(400, f"Unknown printer: {printer_id}")

    from sqlalchemy import desc

    jobs = (
        db.query(Job)
        .filter_by(assigned_printer=PrinterID(printer_id))
        .filter(Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]))
        .order_by(desc(Job.print_ended_at))
        .limit(20)
        .all()
    )

    return [
        {
            "id": j.id,
            "original_filename": j.original_filename,
            "status": j.status,
            "print_started_at": j.print_started_at.isoformat()
            if j.print_started_at
            else None,
            "print_ended_at": j.print_ended_at.isoformat()
            if j.print_ended_at
            else None,
        }
        for j in jobs
    ]
