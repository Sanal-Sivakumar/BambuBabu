from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


TEST_ROOT = Path(tempfile.mkdtemp(prefix="bambubabu-tests-"))
os.environ.update(
    {
        "PRINTERS_ENABLED": "false",
        "MOCK_SLICER": "true",
        "UPLOAD_DIR": str(TEST_ROOT / "uploads"),
        "SLICED_DIR": str(TEST_ROOT / "sliced"),
        "LOG_DIR": str(TEST_ROOT / "logs"),
        "DB_PATH": str(TEST_ROOT / "test.db"),
        "DB_BACKUP_DIR": str(TEST_ROOT / "backups"),
        "MAINTENANCE_INTERVAL_SECONDS": "86400",
        "DB_BACKUP_INTERVAL_HOURS": "86400",
        "SMTP_PASSWORD": "",
    }
)

from backend.config import settings  # noqa: E402
from backend.db.models import Base  # noqa: E402
from backend.db.session import engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database_and_storage():
    Base.metadata.drop_all(bind=engine)
    init_db()
    for directory in (settings.UPLOAD_DIR, settings.SLICED_DIR, settings.DB_BACKUP_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for child in directory.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app

    return TestClient(app)
