"""
BambuBabu — Structured Logger
File-based rotating logger + DB log helper
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.config import settings


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger that writes to file + stdout."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Rotating file handler (10 MB × 5 files) ──
    log_file = settings.LOG_DIR / "bambububu.log"
    fh = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # ── Console handler ──
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger


# Module-level root logger for the app
log = get_logger("bambububu")
