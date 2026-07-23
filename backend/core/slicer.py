"""
BambuBabu — OrcaSlicer CLI Wrapper
Slices STL files using OrcaSlicer in headless mode (via xvfb-run).
Falls back to mock mode (for testing without OrcaSlicer installed).

Profile system:
  Machine, process, and filament presets are resolved inside the complete BBL
  profile tree extracted from the pinned OrcaSlicer AppImage. Keeping the full
  tree preserves OrcaSlicer's inherited base presets on every installation.
"""

from __future__ import annotations
import re
import shutil
import tempfile
import zipfile

# Subprocesses use fixed argv with shell disabled; no command text is user supplied.
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.core.logger import get_logger
from backend.db.models import PrinterID

log = get_logger("bambubabu.slicer")


def _profile_paths(printer_id: PrinterID) -> tuple[Path, Path, Path]:
    """Resolve profiles from the complete, configurable Orca inheritance tree."""
    root = Path(settings.SLICER_PROFILES_DIR)
    machine = {
        PrinterID.P1S: root / "machine/Bambu Lab P1S 0.4 nozzle.json",
        PrinterID.A1_MINI: root / "machine/Bambu Lab A1 mini 0.4 nozzle.json",
    }[printer_id]
    process = {
        PrinterID.P1S: root / "process/0.20mm Standard @BBL P1P.json",
        PrinterID.A1_MINI: root / "process/0.20mm Standard @BBL A1M.json",
    }[printer_id]
    filament = {
        PrinterID.P1S: root / "filament/Bambu PLA Basic @base.json",
        PrinterID.A1_MINI: root / "filament/Bambu PLA Basic @BBL A1M.json",
    }[printer_id]
    return machine, process, filament


def slice_stl(
    stl_path: str | Path, printer_id: PrinterID, output_dir: str | Path
) -> tuple[Path, Optional[int]]:
    """
    Slice an STL file for the given printer.

    Returns:
        (path_to_3mf, estimated_minutes_or_None)

    Raises:
        RuntimeError if slicing fails
    """
    stl_path = Path(stl_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{stl_path.stem}-{printer_id.value}.gcode.3mf"

    if settings.MOCK_SLICER:
        return _mock_slice(stl_path, output_file)

    return _orca_slice(stl_path, output_file, printer_id)


# ── Real OrcaSlicer slice ───────────────────────────────────────────────────


def _orca_slice(
    stl_path: Path, output_file: Path, printer_id: PrinterID
) -> tuple[Path, Optional[int]]:
    """Run OrcaSlicer headlessly via xvfb-run with real Bambu profiles."""

    machine, process, filament = _profile_paths(printer_id)

    # Validate profiles exist
    missing = [str(path) for path in (machine, process, filament) if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Required OrcaSlicer profiles or their inheritance tree are missing: "
            + ", ".join(missing)
        )

    # Build OrcaSlicer command
    # xvfb-run provides a virtual X11 display (OrcaSlicer needs it even in CLI)
    cmd = [
        "xvfb-run",
        "--auto-servernum",
        "--server-args=-screen 0 1024x768x24",
        str(settings.ORCA_SLICER_PATH),
        "--slice",
        "0",
        "--export-3mf",
        str(output_file),  # Output sliced 3MF with embedded gcode
    ]

    # Load machine + process settings (semicolon-separated)
    settings_files = [str(machine), str(process)]
    cmd += ["--load-settings", ";".join(settings_files)]

    # Load filament settings
    cmd += ["--load-filaments", str(filament)]

    cmd.append(str(stl_path))

    log.info(f"[{printer_id}] Slicing: {stl_path.name} → {output_file.name}")

    log.info(f"[{printer_id}] OrcaSlicer command prepared with configured profiles")
    t0 = time.time()
    # OrcaSlicer writes numbered diagnostic files relative to its current
    # directory. The systemd unit intentionally makes the application checkout
    # read-only. More importantly, an invocation must never inherit another
    # invocation's diagnostic files or lock state: Orca can abort with EEXIST
    # (reported as ``return -17``) when a shared working directory is reused.
    work_root = settings.LOG_DIR / "orca"
    work_root.mkdir(parents=True, exist_ok=True)

    try:
        # A private, automatically removed workspace makes retries and
        # printer-specific fallback slices independent as well.
        with tempfile.TemporaryDirectory(
            prefix=f"{printer_id.value}-", dir=work_root
        ) as work_dir:
            # Fixed executable and separated argv; no shell interpretation occurs.
            result = subprocess.run(  # nosec B603
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                cwd=work_dir,
                timeout=600,  # 10 min max — Pi 5 is slower than desktop
            )
    except FileNotFoundError as exc:
        if "xvfb-run" in str(exc):
            raise RuntimeError("xvfb-run not found. Run: sudo apt install xvfb -y")
        if "xauth" in str(exc):
            raise RuntimeError("xauth not found. Run: sudo apt install xauth -y")
        raise RuntimeError(
            f"OrcaSlicer not found at '{settings.ORCA_SLICER_PATH}'. "
            "Check ORCA_SLICER_PATH in your .env file."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("OrcaSlicer timed out after 10 minutes")

    elapsed = round(time.time() - t0, 1)
    log.info(
        f"[{printer_id}] OrcaSlicer finished in {elapsed}s (exit={result.returncode})"
    )

    if result.stdout:
        log.debug(f"[{printer_id}] stdout: {result.stdout[:400]}")
    if result.stderr:
        log.debug(f"[{printer_id}] stderr: {result.stderr[:400]}")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:600]
        if not detail:
            detail = "no diagnostic output; verify xvfb-run, xauth, and the AppRun CLI manually"
        log.error(f"[{printer_id}] OrcaSlicer failed:\n{detail}")
        raise RuntimeError(
            f"OrcaSlicer failed (exit {result.returncode}): {detail[:200]}"
        )

    if not output_file.exists():
        raise RuntimeError(f"OrcaSlicer produced no output file: {output_file}")
    _validate_printable_project(output_file)

    estimated_minutes = _parse_estimated_time(result.stdout + result.stderr)
    log.info(
        f"[{printer_id}] ✅ Sliced → {output_file.name} "
        f"(~{estimated_minutes or '?'} min)"
    )
    return output_file, estimated_minutes


def _validate_printable_project(output_file: Path) -> None:
    """Reject archives that cannot be launched by Bambu ``project_file``."""
    try:
        with zipfile.ZipFile(output_file) as project:
            if "Metadata/plate_1.gcode" not in project.namelist():
                raise RuntimeError(
                    "Slicer output has no Metadata/plate_1.gcode; refusing upload"
                )
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Slicer output is not a valid printable 3MF archive") from exc


def _parse_estimated_time(output: str) -> Optional[int]:
    """Try to extract estimated print time from slicer stdout."""
    # OrcaSlicer outputs something like: "Estimated printing time: 2h 34m"
    patterns = [
        r"(\d+)h\s*(\d+)m",  # "2h 34m"
        r"(\d+)\s*minutes?",  # "154 minutes"
        r"print\s*time.*?(\d+)",  # generic fallback
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            groups = m.groups()
            if len(groups) == 2:
                return int(groups[0]) * 60 + int(groups[1])
            elif len(groups) == 1:
                return int(groups[0])
    return None


# ── Mock slicer (for testing) ───────────────────────────────────────────────


def _mock_slice(stl_path: Path, output_file: Path) -> tuple[Path, Optional[int]]:
    """
    Simulate slicing by copying the STL and renaming it .3mf.
    Returns a fake 30-minute estimate.
    Used when MOCK_SLICER=true in .env.
    """
    log.warning("MOCK_SLICER=true — skipping real slicing, copying STL as fake .3mf")
    shutil.copy2(str(stl_path), str(output_file))
    time.sleep(2)  # simulate slicing delay
    return output_file, 30
