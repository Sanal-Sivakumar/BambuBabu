"""Own printer clients and reconcile authoritative MQTT reports with durable state."""

from __future__ import annotations

from typing import Optional

from backend.config import settings
from backend.core.logger import get_logger
from backend.core.printer import BambuPrinter
from backend.db import crud
from backend.db.models import JobStatus, LogLevel, PrinterID, PrinterStatus
from backend.db.session import SessionLocal


log = get_logger("bambubabu.printer_manager")


class PrinterManager:
    def __init__(self):
        self._printers: dict[PrinterID, BambuPrinter] = {}
        self._initialized = False

    def init(self) -> None:
        if self._initialized or not settings.PRINTERS_ENABLED:
            self._initialized = True
            return

        self._printers = {
            PrinterID.P1S: BambuPrinter(
                printer_id=PrinterID.P1S,
                ip=settings.P1S_IP,
                serial=settings.P1S_SERIAL,
                access_code=settings.printer_access_code("p1s"),
                mqtt_cert_path=settings.printer_mqtt_cert_path("p1s"),
                ftps_pin=settings.printer_ftps_pin("p1s"),
                on_status_update=self._on_status_update,
            ),
            PrinterID.A1_MINI: BambuPrinter(
                printer_id=PrinterID.A1_MINI,
                ip=settings.A1_MINI_IP,
                serial=settings.A1_MINI_SERIAL,
                access_code=settings.printer_access_code("a1_mini"),
                mqtt_cert_path=settings.printer_mqtt_cert_path("a1_mini"),
                ftps_pin=settings.printer_ftps_pin("a1_mini"),
                on_status_update=self._on_status_update,
            ),
        }
        for printer_id, printer in self._printers.items():
            log.info(f"Connecting to {printer_id.value} at {printer.ip}")
            printer.connect()
        self._initialized = True

    def shutdown(self) -> None:
        for printer in self._printers.values():
            printer.disconnect()
        self._initialized = False

    def get_printer(self, printer_id: PrinterID) -> Optional[BambuPrinter]:
        return self._printers.get(printer_id)

    def require_printer(self, printer_id: PrinterID) -> BambuPrinter:
        printer = self.get_printer(printer_id)
        if printer is None:
            raise RuntimeError(
                f"Printer integration is disabled or unavailable: {printer_id.value}"
            )
        return printer

    def get_snapshot(self) -> dict:
        snapshot = {}
        for printer_id in PrinterID:
            printer = self._printers.get(printer_id)
            data = (
                printer.snapshot()
                if printer
                else {
                    "status": "offline",
                    "gcode_state": "OFFLINE",
                    "progress": 0,
                    "nozzle_temp": 0,
                    "bed_temp": 0,
                    "connected": False,
                    "last_seen": None,
                }
            )
            snapshot[printer_id.value] = {"printer_id": printer_id.value, **data}
        return snapshot

    def _on_status_update(self, printer_id: str, snapshot: dict) -> None:
        printer = PrinterID(printer_id)
        status_map = {
            "idle": PrinterStatus.IDLE,
            "printing": PrinterStatus.PRINTING,
            "paused": PrinterStatus.PAUSED,
            "error": PrinterStatus.ERROR,
            "offline": PrinterStatus.OFFLINE,
            "finished": PrinterStatus.IDLE,
        }
        notification: tuple[str, str, PrinterID] | None = None

        with SessionLocal.begin() as db:
            state = crud.get_printer_state(db, printer)
            if state is None:
                return
            crud.update_printer_state(
                db,
                printer,
                status=status_map.get(snapshot["status"], PrinterStatus.OFFLINE),
                print_progress=snapshot["progress"],
                nozzle_temp=snapshot["nozzle_temp"],
                bed_temp=snapshot["bed_temp"],
            )

            job = (
                crud.get_job(db, state.current_job_id) if state.current_job_id else None
            )
            gcode_state = snapshot["gcode_state"]

            if job and snapshot["status"] == "printing":
                if job.status in {JobStatus.STARTING, JobStatus.ATTENTION}:
                    previous = job.status
                    try:
                        crud.transition_job_status(
                            db, job.id, previous, JobStatus.PRINTING
                        )
                        crud.add_log(
                            db,
                            "PRINT_CONFIRMED"
                            if previous == JobStatus.STARTING
                            else "PRINT_RECOVERED",
                            f"Printer confirmed {gcode_state} on {printer.value}",
                            job_id=job.id,
                            printer_id=printer.value,
                        )
                    except crud.JobTransitionError:
                        pass
                crud.update_job_progress(db, job.id, snapshot["progress"])

            if job and gcode_state == "IDLE" and job.status == JobStatus.PRINTING:
                crud.transition_job_status(
                    db,
                    job.id,
                    JobStatus.PRINTING,
                    JobStatus.ATTENTION,
                    error_message=(
                        "Printer became IDLE without a FINISH report. Inspect the printer "
                        "and plate before resolving this job."
                    ),
                )
                crud.update_printer_state(
                    db,
                    printer,
                    status=PrinterStatus.ERROR,
                    plate_cleared=False,
                    current_job_id=job.id,
                )
                crud.add_log(
                    db,
                    "PRINT_COMPLETION_UNCONFIRMED",
                    "Printer reported IDLE without FINISH; dispatch remains blocked",
                    job_id=job.id,
                    printer_id=printer.value,
                    level=LogLevel.ERROR,
                )

            if (
                job
                and gcode_state == "FINISH"
                and job.status
                in {
                    JobStatus.STARTING,
                    JobStatus.PRINTING,
                    JobStatus.ATTENTION,
                }
            ):
                self._handle_print_finished(db, printer, job.id, job.status)
                notification = ("complete", job.id, printer)
            elif (
                job
                and gcode_state == "FAILED"
                and job.status
                in {
                    JobStatus.STARTING,
                    JobStatus.PRINTING,
                    JobStatus.ATTENTION,
                }
            ):
                self._handle_print_failed(db, printer, job.id, job.status)
                notification = ("failed", job.id, printer)

        if notification:
            kind, job_id, printer = notification
            self._send_lifecycle_email(kind, job_id, printer)

    def _handle_print_finished(
        self, db, printer_id: PrinterID, job_id: str, expected: JobStatus
    ) -> None:
        crud.transition_job_status(db, job_id, expected, JobStatus.COMPLETED)
        crud.update_job_progress(db, job_id, 100)
        # Keep current_job_id until physical clearance so the completed job is traceable.
        crud.update_printer_state(
            db,
            printer_id,
            status=PrinterStatus.IDLE,
            plate_cleared=False,
            current_job_id=job_id,
        )
        crud.add_log(
            db,
            "PRINT_FINISHED",
            f"Print completed on {printer_id.value}; plate clearance required",
            job_id=job_id,
            printer_id=printer_id.value,
        )

    def _handle_print_failed(
        self, db, printer_id: PrinterID, job_id: str, expected: JobStatus
    ) -> None:
        crud.transition_job_status(
            db,
            job_id,
            expected,
            JobStatus.FAILED,
            error_message="Printer reported FAILED via MQTT",
        )
        crud.update_printer_state(
            db,
            printer_id,
            status=PrinterStatus.ERROR,
            plate_cleared=False,
            current_job_id=job_id,
        )
        crud.add_log(
            db,
            "PRINT_FAILED",
            f"Print failed on {printer_id.value}; inspection required",
            job_id=job_id,
            printer_id=printer_id.value,
            level=LogLevel.ERROR,
        )

    def _send_lifecycle_email(
        self, kind: str, job_id: str, printer_id: PrinterID
    ) -> None:
        try:
            from backend.email.mailer import send_print_complete, send_print_failed

            with SessionLocal() as db:
                job = crud.get_job(db, job_id)
                if not job:
                    return
                if kind == "complete":
                    send_print_complete(job, printer_id.value)
                else:
                    send_print_failed(job, printer_id.value)
        except Exception as exc:
            log.error(f"Failed to send {kind} email for {job_id[:8]}: {exc}")


printer_manager = PrinterManager()
