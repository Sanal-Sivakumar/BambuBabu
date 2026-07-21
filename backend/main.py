"""
BambuBabu — FastAPI Application Entry Point
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from backend.config import settings
from backend.core.logger import log
from backend.db.session import init_db
from backend.core.printer_manager import printer_manager
from backend.core.queue_processor import QueueProcessor
from backend.api import jobs, printers

# ── Queue processor (initialised after lifespan startup) ───────────────────
_queue_processor: QueueProcessor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    global _queue_processor

    log.info("=" * 60)
    log.info(f"  🐼 {settings.APP_NAME} starting up")
    log.info("=" * 60)

    # Init database
    init_db()
    log.info("Database initialised")

    # Connect to both printers
    printer_manager.init()
    log.info("Printer connections initiated")

    # Start background queue processor
    _queue_processor = QueueProcessor(printer_manager)
    _queue_processor.start()
    log.info("Queue processor started")

    yield  # ← app is running

    # ── Shutdown ──────────────────────────────────────────────────────
    log.info("Shutting down …")
    if _queue_processor:
        _queue_processor.stop()
    printer_manager.shutdown()
    log.info("BambuBabu stopped cleanly")


# ── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="BambuBabu API",
    description="Automated 3D print management for Bambu Lab P1S + A1 Mini",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow all origins for local network use
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routers ─────────────────────────────────────────────────────────────
app.include_router(jobs.router)
app.include_router(printers.router)


@app.get("/api/health")
def health():
    return {
        "status":  "ok",
        "app":     settings.APP_NAME,
        "version": "1.0.0",
        "mock_slicer": settings.MOCK_SLICER,
    }


@app.get("/api/stats")
def stats(db=None):
    from backend.db.session import get_db
    from backend.db import crud
    from backend.db.models import JobStatus
    with get_db() as db:
        all_jobs = crud.get_all_jobs(db, limit=1000)
    counts = {}
    for s in JobStatus:
        counts[s.value] = sum(1 for j in all_jobs if j.status == s)
    return {"job_counts": counts, "total": len(all_jobs)}


# ── Serve frontend static files ─────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    log.info(f"Serving frontend from {FRONTEND_DIR}")
else:
    log.warning(f"Frontend directory not found: {FRONTEND_DIR}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )
