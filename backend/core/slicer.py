"""
BambuBabu — OrcaSlicer CLI Wrapper
Slices STL files using OrcaSlicer in headless mode (via xvfb-run).
Falls back to mock mode (for testing without OrcaSlicer installed).

Profile system:
  Each printer needs 3 JSON files in config/slicer_profiles/:
    <printer>_machine.json   — printer dimensions, nozzle, speeds
    <printer>_process.json   — layer height, infill, supports
    <printer>_filament.json  — temperatures, retraction, material
  These are extracted from the OrcaSlicer AppImage bundled profiles.
"""
from __future__ import annotations
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.core.logger import get_logger
from backend.db.models import PrinterID

log = get_logger("bambububu.slicer")

# Slicer profile directory (relative to project root)
PROFILES_DIR = Path(__file__).resolve().parents[2] / "config" / "slicer_profiles"

# Each printer has 3 profile files: machine + process + filament
# OrcaSlicer loads them with --load-settings and --load-filaments
MACHINE_PROFILES = {
    PrinterID.P1S:     PROFILES_DIR / "p1s_machine.json",
    PrinterID.A1_MINI: PROFILES_DIR / "a1mini_machine.json",
}
PROCESS_PROFILES = {
    PrinterID.P1S:     PROFILES_DIR / "p1s_process.json",
    PrinterID.A1_MINI: PROFILES_DIR / "a1mini_process.json",
}
FILAMENT_PROFILES = {
    PrinterID.P1S:     PROFILES_DIR / "p1s_filament.json",
    PrinterID.A1_MINI: PROFILES_DIR / "a1mini_filament.json",
}


def slice_stl(stl_path: str | Path, printer_id: PrinterID,
              output_dir: str | Path) -> tuple[Path, Optional[int]]:
    """
    Slice an STL file for the given printer.

    Returns:
        (path_to_3mf, estimated_minutes_or_None)

    Raises:
        RuntimeError if slicing fails
    """
    stl_path   = Path(stl_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / (stl_path.stem + ".3mf")

    if settings.MOCK_SLICER:
        return _mock_slice(stl_path, output_file)

    return _orca_slice(stl_path, output_file, printer_id)


# ── Real OrcaSlicer slice ───────────────────────────────────────────────────

def _orca_slice(stl_path: Path, output_file: Path,
                printer_id: PrinterID) -> tuple[Path, Optional[int]]:
    """Run OrcaSlicer headlessly via xvfb-run with real Bambu profiles."""

    machine  = MACHINE_PROFILES.get(printer_id)
    process  = PROCESS_PROFILES.get(printer_id)
    filament = FILAMENT_PROFILES.get(printer_id)

    # Validate profiles exist
    missing = [str(p) for p in [machine, process, filament]
               if p is None or not p.exists()]
    if missing:
        log.warning(
            f"[{printer_id}] Missing slicer profiles: {missing}. "
            "Slicing without profiles (may fail). "
            "Copy profiles to config/slicer_profiles/ on this machine."
        )

    # Build OrcaSlicer command
    # xvfb-run provides a virtual X11 display (OrcaSlicer needs it even in CLI)
    cmd = [
        "xvfb-run", "--auto-servernum",
        "--server-args=-screen 0 1024x768x24",
        settings.ORCA_SLICER_PATH,
        "--slice", "0",
        "--no-check",                        # Skip G-code validation checks
        "--export-3mf", str(output_file),    # Output sliced 3MF with embedded gcode
    ]

    # Load machine + process settings (semicolon-separated)
    settings_files = [str(p) for p in [machine, process]
                      if p is not None and p.exists()]
    if settings_files:
        cmd += ["--load-settings", ";".join(settings_files)]

    # Load filament settings
    if filament and filament.exists():
        cmd += ["--load-filaments", str(filament)]

    cmd.append(str(stl_path))

    log.info(f"[{printer_id}] Slicing: {stl_path.name} → {output_file.name}")
    log.info(f"[{printer_id}] Command: {' '.join(cmd)}")
    t0 = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,   # 10 min max — Pi 5 is slower than desktop
        )
    except FileNotFoundError as exc:
        if "xvfb-run" in str(exc):
            raise RuntimeError(
                "xvfb-run not found. Run: sudo apt install xvfb -y"
            )
        raise RuntimeError(
            f"OrcaSlicer not found at '{settings.ORCA_SLICER_PATH}'. "
            "Check ORCA_SLICER_PATH in your .env file."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("OrcaSlicer timed out after 10 minutes")

    elapsed = round(time.time() - t0, 1)
    log.info(f"[{printer_id}] OrcaSlicer finished in {elapsed}s "
             f"(exit={result.returncode})")

    if result.stdout:
        log.debug(f"[{printer_id}] stdout: {result.stdout[:400]}")
    if result.stderr:
        log.debug(f"[{printer_id}] stderr: {result.stderr[:400]}")

    if result.returncode != 0:
        log.error(f"[{printer_id}] OrcaSlicer failed:\n{result.stderr[:600]}")
        raise RuntimeError(
            f"OrcaSlicer failed (exit {result.returncode}): "
            f"{result.stderr[:200]}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"OrcaSlicer produced no output file: {output_file}"
        )

    estimated_minutes = _parse_estimated_time(result.stdout + result.stderr)
    log.info(f"[{printer_id}] ✅ Sliced → {output_file.name} "
             f"(~{estimated_minutes or '?'} min)")
    return output_file, estimated_minutes


def _parse_estimated_time(output: str) -> Optional[int]:
    """Try to extract estimated print time from slicer stdout."""
    # OrcaSlicer outputs something like: "Estimated printing time: 2h 34m"
    patterns = [
        r"(\d+)h\s*(\d+)m",      # "2h 34m"
        r"(\d+)\s*minutes?",      # "154 minutes"
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
