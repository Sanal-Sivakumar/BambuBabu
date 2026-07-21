"""
BambuBabu — Application Configuration
Loads settings from .env file via pydantic-settings
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # ── App ────────────────────────────────────────────────────────
    APP_NAME: str = "BambuBabu"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── Storage paths ──────────────────────────────────────────────
    UPLOAD_DIR: Path = BASE_DIR / "storage" / "uploads"
    SLICED_DIR: Path = BASE_DIR / "storage" / "sliced"
    LOG_DIR: Path = BASE_DIR / "logs"
    DB_PATH: Path = BASE_DIR / "bambububu.db"

    # ── Bambu Lab P1S ──────────────────────────────────────────────
    P1S_IP: str = "192.168.10.116"
    P1S_SERIAL: str = "01P09C552500636"
    P1S_ACCESS_CODE: str = "dd4b4e51"
    P1S_MAX_X: int = 256  # mm
    P1S_MAX_Y: int = 256
    P1S_MAX_Z: int = 256

    # ── Bambu Lab A1 Mini ──────────────────────────────────────────
    A1_MINI_IP: str = "192.168.10.115"
    A1_MINI_SERIAL: str = "0300DA610705389"
    A1_MINI_ACCESS_CODE: str = "c9fd869a"
    A1_MINI_MAX_X: int = 180  # mm
    A1_MINI_MAX_Y: int = 180
    A1_MINI_MAX_Z: int = 180

    # ── Email ──────────────────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "sanalsiva2005@gmail.com"
    SMTP_PASSWORD: str = ""
    ADMIN_EMAIL: str = "sanalsiva2005@gmail.com"

    # ── Slicer ─────────────────────────────────────────────────────
    ORCA_SLICER_PATH: str = "/usr/bin/OrcaSlicer"
    MOCK_SLICER: bool = False  # True = skip real slicing (for testing)

    # ── Queue logic ────────────────────────────────────────────────
    COMPLEXITY_THRESHOLD: float = 50.0
    MAX_STL_SIZE_MB: int = 100
    MAX_CONCURRENT_JOBS: int = 2
    QUEUE_POLL_INTERVAL_SECONDS: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure required directories exist
for d in [settings.UPLOAD_DIR, settings.SLICED_DIR, settings.LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)
