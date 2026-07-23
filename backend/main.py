"""FastAPI application entry point and managed service lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from shutil import which

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.api import jobs, logs, printers
from backend.config import settings
from backend.core.logger import log
from backend.core.maintenance import maintenance_worker
from backend.core.printer_manager import printer_manager
from backend.core.queue_processor import QueueProcessor
from backend.db.session import database_is_ready, init_db


_queue_processor: QueueProcessor | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _queue_processor
    settings.validate_runtime()
    init_db()
    printer_manager.init()
    _queue_processor = QueueProcessor(printer_manager)
    _queue_processor.start()
    maintenance_worker.start()
    log.info(f"{settings.APP_NAME} started")
    try:
        yield
    finally:
        maintenance_worker.stop()
        if _queue_processor:
            _queue_processor.stop()
        printer_manager.shutdown()
        log.info("BambuBabu stopped cleanly")


app = FastAPI(
    title="BambuBabu API",
    description="Local automated 3D print management for Bambu Lab printers",
    version="1.1.0",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

app.include_router(jobs.router)
app.include_router(printers.router)
app.include_router(logs.router)


@app.get("/api/health")
def health():
    live = printer_manager.get_snapshot()
    printers_connected = all(item["connected"] for item in live.values())
    slicer_ready = settings.MOCK_SLICER or (
        settings.ORCA_SLICER_PATH.is_file()
        and settings.SLICER_PROFILES_DIR.is_dir()
        and which("xvfb-run") is not None
        and which("xauth") is not None
    )
    checks = {
        "database": database_is_ready(),
        "slicer": slicer_ready,
        "curl": which("curl") is not None if settings.PRINTERS_ENABLED else None,
        "printers_connected": printers_connected if settings.PRINTERS_ENABLED else None,
    }
    required = [value for value in checks.values() if value is not None]
    return {
        "status": "ok" if all(required) else "degraded",
        "app": settings.APP_NAME,
        "version": "1.1.0",
        "mock_slicer": settings.MOCK_SLICER,
        "authentication": settings.AUTHENTICATION_MODE,
        "checks": checks,
    }


@app.get("/api/stats")
def stats():
    from backend.db import crud
    from backend.db.models import JobStatus
    from backend.db.session import get_db

    with get_db() as db:
        all_jobs = crud.get_all_jobs(db, limit=1000)
    return {
        "job_counts": {
            status.value: sum(1 for job in all_jobs if job.status == status)
            for status in JobStatus
        },
        "total": len(all_jobs),
    }


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )
