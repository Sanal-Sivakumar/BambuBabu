"""Application configuration loaded from environment variables and ``.env``."""

from __future__ import annotations

import re
from ipaddress import ip_address
from pathlib import Path
from shutil import which

from pydantic import EmailStr, Field, SecretStr, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application and deployment boundary
    APP_NAME: str = "BambuBabu"
    DEBUG: bool = False
    # Authentication is intentionally deferred; keep the service local to the Pi
    # until the parent project supplies an authenticated reverse-proxy boundary.
    HOST: str = "127.0.0.1"
    PORT: int = Field(8000, ge=1, le=65535)
    CORS_ORIGINS: str = ""  # comma-separated; empty means same-origin only
    AUTHENTICATION_MODE: str = "external-pending"

    # Storage
    UPLOAD_DIR: Path = BASE_DIR / "storage" / "uploads"
    SLICED_DIR: Path = BASE_DIR / "storage" / "sliced"
    LOG_DIR: Path = BASE_DIR / "logs"
    DB_PATH: Path = BASE_DIR / "bambubabu.db"
    DB_BACKUP_DIR: Path = BASE_DIR / "backups"

    # Printers. Credentials intentionally have no usable defaults.
    PRINTERS_ENABLED: bool = True
    P1S_IP: str = ""
    P1S_SERIAL: str = ""
    P1S_ACCESS_CODE: SecretStr = SecretStr("")
    P1S_MQTT_CERT_PATH: str = ""
    P1S_FTPS_PIN: str = ""
    P1S_MAX_X: int = Field(256, gt=0)
    P1S_MAX_Y: int = Field(256, gt=0)
    P1S_MAX_Z: int = Field(256, gt=0)

    A1_MINI_IP: str = ""
    A1_MINI_SERIAL: str = ""
    A1_MINI_ACCESS_CODE: SecretStr = SecretStr("")
    A1_MINI_MQTT_CERT_PATH: str = ""
    A1_MINI_FTPS_PIN: str = ""
    A1_MINI_MAX_X: int = Field(180, gt=0)
    A1_MINI_MAX_Y: int = Field(180, gt=0)
    A1_MINI_MAX_Z: int = Field(180, gt=0)

    MQTT_PUBLISH_TIMEOUT_SECONDS: float = Field(10.0, gt=0)
    PRINT_START_CONFIRM_TIMEOUT_SECONDS: float = Field(60.0, gt=0)

    # Email
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = Field(587, ge=1, le=65535)
    SMTP_USER: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    ADMIN_EMAIL: str = ""

    # Slicer. The installer extracts the complete profile inheritance tree here.
    ORCA_SLICER_PATH: Path = Path("/opt/bambubabu/orca/appimage-root/AppRun")
    SLICER_PROFILES_DIR: Path = Path(
        "/opt/bambubabu/orca/appimage-root/resources/profiles/BBL"
    )
    MOCK_SLICER: bool = False

    # Queue, admission control, and maintenance
    COMPLEXITY_THRESHOLD: float = Field(50.0, ge=0, le=100)
    MAX_STL_SIZE_MB: int = Field(100, gt=0)
    MAX_ACTIVE_JOBS: int = Field(50, gt=0)
    MAX_STORAGE_MB: int = Field(2048, gt=0)
    MAX_CONCURRENT_JOBS: int = Field(2, gt=0, le=2)
    QUEUE_POLL_INTERVAL_SECONDS: int = Field(10, gt=0)
    TERMINAL_FILE_RETENTION_DAYS: int = Field(30, gt=0)
    PARTIAL_UPLOAD_RETENTION_HOURS: int = Field(1, gt=0)
    ORPHAN_FILE_RETENTION_HOURS: int = Field(24, gt=0)
    MAINTENANCE_INTERVAL_SECONDS: int = Field(3600, gt=0)
    DB_BACKUP_INTERVAL_HOURS: int = Field(24, gt=0)
    DB_BACKUP_KEEP: int = Field(14, gt=0)

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()
        ]

    def printer_access_code(self, printer_id: str) -> str:
        secret = (
            self.P1S_ACCESS_CODE if printer_id == "p1s" else self.A1_MINI_ACCESS_CODE
        )
        return secret.get_secret_value()

    def printer_mqtt_cert_path(self, printer_id: str) -> Path:
        value = (
            self.P1S_MQTT_CERT_PATH
            if printer_id == "p1s"
            else self.A1_MINI_MQTT_CERT_PATH
        )
        return Path(value)

    def printer_ftps_pin(self, printer_id: str) -> str:
        return self.P1S_FTPS_PIN if printer_id == "p1s" else self.A1_MINI_FTPS_PIN

    def smtp_password(self) -> str:
        return self.SMTP_PASSWORD.get_secret_value()

    def validate_runtime(self) -> None:
        """Fail startup when a live integration is configured unsafely or incompletely."""
        if "*" in self.cors_origins:
            raise RuntimeError("Wildcard CORS is not permitted")
        if self.AUTHENTICATION_MODE == "external-pending" and self.HOST not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise RuntimeError(
                "Unauthenticated mode must bind to loopback; use an authenticated "
                "reverse proxy before exposing the service"
            )

        if self.PRINTERS_ENABLED:
            if self.MOCK_SLICER:
                raise RuntimeError("MOCK_SLICER cannot be used with live printers")
            missing = []
            for key, value in {
                "P1S_IP": self.P1S_IP,
                "P1S_SERIAL": self.P1S_SERIAL,
                "P1S_ACCESS_CODE": self.P1S_ACCESS_CODE.get_secret_value(),
                "P1S_MQTT_CERT_PATH": self.P1S_MQTT_CERT_PATH,
                "P1S_FTPS_PIN": self.P1S_FTPS_PIN,
                "A1_MINI_IP": self.A1_MINI_IP,
                "A1_MINI_SERIAL": self.A1_MINI_SERIAL,
                "A1_MINI_ACCESS_CODE": self.A1_MINI_ACCESS_CODE.get_secret_value(),
                "A1_MINI_MQTT_CERT_PATH": self.A1_MINI_MQTT_CERT_PATH,
                "A1_MINI_FTPS_PIN": self.A1_MINI_FTPS_PIN,
            }.items():
                if not value or value.lower() in {"replace_me", "changeme", "example"}:
                    missing.append(key)
            if missing:
                raise RuntimeError(
                    "Live printer mode requires fresh credentials in .env; missing/placeholder: "
                    + ", ".join(missing)
                )

            for printer, access_code in (
                ("P1S", self.P1S_ACCESS_CODE.get_secret_value()),
                ("A1_MINI", self.A1_MINI_ACCESS_CODE.get_secret_value()),
            ):
                if any(
                    ord(character) < 32 or ord(character) == 127
                    for character in access_code
                ):
                    raise RuntimeError(
                        f"{printer}_ACCESS_CODE contains control characters"
                    )

            for printer, address, cert_path, pin in (
                (
                    "P1S",
                    self.P1S_IP,
                    self.P1S_MQTT_CERT_PATH,
                    self.P1S_FTPS_PIN,
                ),
                (
                    "A1_MINI",
                    self.A1_MINI_IP,
                    self.A1_MINI_MQTT_CERT_PATH,
                    self.A1_MINI_FTPS_PIN,
                ),
            ):
                try:
                    parsed = ip_address(address)
                except ValueError as exc:
                    raise RuntimeError(
                        f"{printer}_IP is not a valid IP address"
                    ) from exc
                if not (parsed.is_private or parsed.is_link_local):
                    raise RuntimeError(f"{printer}_IP must be a private LAN address")
                if not Path(cert_path).is_file():
                    raise RuntimeError(
                        f"{printer}_MQTT_CERT_PATH must reference the trusted printer certificate"
                    )
                if not re.fullmatch(r"sha256//[A-Za-z0-9+/]{43}=", pin):
                    raise RuntimeError(
                        f"{printer}_FTPS_PIN must be a curl sha256// SPKI pin"
                    )

        if self.smtp_password():
            try:
                TypeAdapter(EmailStr).validate_python(self.SMTP_USER)
                TypeAdapter(EmailStr).validate_python(self.ADMIN_EMAIL)
            except ValidationError as exc:
                raise RuntimeError(
                    "SMTP_USER and ADMIN_EMAIL must be valid when email is enabled"
                ) from exc

        if not self.MOCK_SLICER:
            if not self.ORCA_SLICER_PATH.is_file():
                raise RuntimeError(f"OrcaSlicer not found: {self.ORCA_SLICER_PATH}")
            if not self.SLICER_PROFILES_DIR.is_dir():
                raise RuntimeError(
                    f"Complete OrcaSlicer BBL profile tree not found: {self.SLICER_PROFILES_DIR}"
                )
            if which("xvfb-run") is None:
                raise RuntimeError("xvfb-run is required for headless slicing")


settings = Settings()

for directory in (
    settings.UPLOAD_DIR,
    settings.SLICED_DIR,
    settings.LOG_DIR,
    settings.DB_BACKUP_DIR,
    settings.DB_PATH.parent,
):
    directory.mkdir(parents=True, exist_ok=True)
