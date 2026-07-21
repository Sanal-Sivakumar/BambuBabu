"""
BambuBabu — Printer Manager
Singleton that owns both BambuPrinter instances and syncs their state to the DB.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from backend.config import settings
from backend.core.printer import BambuPrinter
from backend.core.logger import get_logger
from backend.db.models import PrinterID, PrinterStatus
from backend.db.session import SessionLocal
from backend.db import crud

log = get_logger("bambububu.printer_manager")


class PrinterManager:
    """
    Manages both printers. Acts as the bridge between MQTT callbacks
    and the rest of the application.
    """

    def __init__(self):
        self._printers: dict[PrinterID, BambuPrinter] = {}
        self._initialized = False

    def init(self) -> None:
        """Create and connect both printer clients."""
        if self._initialized:
            return

        self._printers[PrinterID.P1S] = BambuPrinter(
            printer_id   = PrinterID.P1S,
            ip           = settings.P1S_IP,
            serial       = settings.P1S_SERIAL,
            access_code  = settings.P1S_ACCESS_CODE,
            on_status_update = self._on_status_update,
        )

        self._printers[PrinterID.A1_MINI] = BambuPrinter(
            printer_id   = PrinterID.A1_MINI,
            ip           = settings.A1_MINI_IP,
            serial       = settings.A1_MINI_SERIAL,
            access_code  = settings.A1_MINI_ACCESS_CODE,
            on_status_update = self._on_status_update,
        )

        for pid, printer in self._printers.items():
            log.info(f"Connecting to {pid} at {printer.ip} …")
            printer.connect()

        self._initialized = True
        log.info("PrinterManager initialised — both printers connecting")

    def shutdown(self) -> None:
        for printer in self._printers.values():
            printer.disconnect()
        log.info("PrinterManager shut down")

    # ── Public helpers ──────────────────────────────────────────────────────

    def get_printer(self, printer_id: PrinterID) -> Optional[BambuPrinter]:
        return self._printers.get(printer_id)

    def is_idle(self, printer_id: PrinterID) -> bool:
        p = self._printers.get(printer_id)
        return p is not None and p.is_idle()

    def is_plate_cleared(self, printer_id: PrinterID) -> bool:
        with SessionLocal() as db:
            state = crud.get_printer_state(db, printer_id)
            return state.plate_cleared if state else False

    def get_snapshot(self) -> dict:
        """Return a dict of live printer data for the API."""
        snap = {}
        for pid, p in self._printers.items():
            snap[pid.value] = {
                "printer_id":   pid.value,
                "status":       p.status,
                "gcode_state":  p.gcode_state,
                "progress":     p.progress,
                "nozzle_temp":  p.nozzle_temp,
                "bed_temp":     p.bed_temp,
                "connected":    p.is_online(),
                "last_seen":    p.last_seen.isoformat() if p.last_seen else None,
            }
        return snap

    def start_print(self, printer_id: PrinterID,
                    filename_3mf: str, job_name: str) -> None:
        p = self._printers[printer_id]
        p.start_print(filename_3mf, job_name)

    def upload_and_print(self, printer_id: PrinterID,
                         local_3mf_path: str, job_id: str) -> None:
        """Upload 3MF then send print command."""
        import os
        filename = os.path.basename(local_3mf_path)
        p = self._printers[printer_id]
        p.upload_file(local_3mf_path, filename)
        p.start_print(filename, job_name=job_id[:8])

    # ── MQTT callback (called from printer's thread) ────────────────────────

    def _on_status_update(self, printer_id: str, snapshot: dict) -> None:
        """
        Called by BambuPrinter when an MQTT status message arrives.
        Syncs the live state to the DB and triggers job completion logic.
        """
        pid = PrinterID(printer_id)

        status_str = snapshot["status"]
        status_map = {
            "idle":     PrinterStatus.IDLE,
            "printing": PrinterStatus.PRINTING,
            "paused":   PrinterStatus.PAUSED,
            "error":    PrinterStatus.ERROR,
            "offline":  PrinterStatus.OFFLINE,
            "finished": PrinterStatus.IDLE,   # treat 'finished' as idle after handling
        }
        db_status = status_map.get(status_str, PrinterStatus.OFFLINE)

        with SessionLocal() as db:
            state = crud.get_printer_state(db, pid)
            if not state:
                return

            prev_status = state.status

            crud.update_printer_state(db, pid,
                status       = db_status,
                print_progress = snapshot["progress"],
                nozzle_temp  = snapshot["nozzle_temp"],
                bed_temp     = snapshot["bed_temp"],
            )

            # ── Job progress update ────────────────────────────────────────
            if state.current_job_id and status_str == "printing":
                crud.update_job_progress(db, state.current_job_id, snapshot["progress"])

            # ── Print finished ─────────────────────────────────────────────
            if snapshot["gcode_state"] == "FINISH" and state.current_job_id:
                self._handle_print_finished(db, pid, state.current_job_id)

            # ── Print failed ───────────────────────────────────────────────
            elif snapshot["gcode_state"] == "FAILED" and state.current_job_id:
                self._handle_print_failed(db, pid, state.current_job_id)

            db.commit()

    def _handle_print_finished(self, db, printer_id: PrinterID, job_id: str) -> None:
        from backend.db.models import JobStatus
        log.info(f"[{printer_id}] Print FINISHED for job {job_id[:8]}")
        crud.update_job_status(db, job_id, JobStatus.COMPLETED)
        # Plate not cleared — block next job until admin clears
        crud.update_printer_state(db, printer_id,
            status         = PrinterStatus.IDLE,
            plate_cleared  = False,
            current_job_id = None,
        )
        crud.add_log(db, "PRINT_FINISHED",
                     f"Print completed on {printer_id}", job_id=job_id,
                     printer_id=printer_id.value)

        # Send completion email (import here to avoid circular)
        self._send_completion_email(db, job_id, printer_id)

    def _handle_print_failed(self, db, printer_id: PrinterID, job_id: str) -> None:
        from backend.db.models import JobStatus
        log.error(f"[{printer_id}] Print FAILED for job {job_id[:8]}")
        crud.update_job_status(db, job_id, JobStatus.FAILED,
                               error_message="Printer reported FAILED via MQTT")
        crud.update_printer_state(db, printer_id,
            status         = PrinterStatus.IDLE,
            plate_cleared  = False,
            current_job_id = None,
        )
        crud.add_log(db, "PRINT_FAILED",
                     f"Print failed on {printer_id}", job_id=job_id,
                     printer_id=printer_id.value,
                     level=__import__("backend.db.models", fromlist=["LogLevel"]).LogLevel.ERROR)
        self._send_failure_email(db, job_id, printer_id)

    def _send_completion_email(self, db, job_id: str, printer_id: PrinterID) -> None:
        try:
            from backend.email.mailer import send_print_complete
            job = crud.get_job(db, job_id)
            if job:
                send_print_complete(job, printer_id.value)
        except Exception as e:
            log.error(f"Failed to send completion email: {e}")

    def _send_failure_email(self, db, job_id: str, printer_id: PrinterID) -> None:
        try:
            from backend.email.mailer import send_print_failed
            job = crud.get_job(db, job_id)
            if job:
                send_print_failed(job, printer_id.value)
        except Exception as e:
            log.error(f"Failed to send failure email: {e}")


# ── Global singleton ────────────────────────────────────────────────────────
printer_manager = PrinterManager()
