"""
BambuBabu — OrcaSlicer CLI Wrapper
Slices STL files using OrcaSlicer in headless mode.
Falls back to mock mode (for testing without OrcaSlicer installed).
"""
from __future__ import annotations
import json
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

# Slicer profile paths (relative to project root)
PROFILES_DIR = Path(__file__).resolve().parents[2] / "config" / "slicer_profiles"

PRINTER_PROFILES = {
    PrinterID.P1S:     PROFILES_DIR / "p1s_standard.json",
    PrinterID.A1_MINI: PROFILES_DIR / "a1mini_standard.json",
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
    """Run OrcaSlicer headlessly via xvfb-run."""

    profile_cfg = PRINTER_PROFILES.get(printer_id)

    # Base command — use xvfb-run so OrcaSlicer can open a virtual display
    cmd = [
        "xvfb-run", "--auto-servernum", "--server-args=-screen 0 1024x768x24",
        settings.ORCA_SLICER_PATH,
        "--slice", "0",
        "-g",                           # embed G-code inside the 3MF
        "--output", str(output_file),
    ]

    # Load printer-specific config if it exists
    if profile_cfg and profile_cfg.exists():
        cmd += ["--load", str(profile_cfg)]
    else:
        log.warning(
            f"No slicer profile found for {printer_id} at {profile_cfg}. "
            "Slicing with OrcaSlicer defaults."
        )

    cmd.append(str(stl_path))

    log.info(f"Slicing with OrcaSlicer [{printer_id}]: {' '.join(cmd)}")
    t0 = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,   # 10 min max — Pi is slower than desktop
        )
    except FileNotFoundError as exc:
        if "xvfb-run" in str(exc):
            raise RuntimeError(
                "xvfb-run not found. On Pi run: sudo apt install xvfb -y"
            )
        raise RuntimeError(
            f"OrcaSlicer not found at '{settings.ORCA_SLICER_PATH}'. "
            "Check ORCA_SLICER_PATH in your .env file."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("OrcaSlicer timed out after 10 minutes")

    elapsed = round(time.time() - t0, 1)
    log.info(f"OrcaSlicer finished in {elapsed}s (exit={result.returncode})")

    if result.stdout:
        log.debug(f"OrcaSlicer stdout: {result.stdout[:300]}")
    if result.stderr:
        log.debug(f"OrcaSlicer stderr: {result.stderr[:300]}")

    if result.returncode != 0:
        log.error(f"OrcaSlicer failed:\n{result.stderr[:600]}")
        raise RuntimeError(
            f"OrcaSlicer failed (exit {result.returncode}): {result.stderr[:200]}"
        )

    if not output_file.exists():
        raise RuntimeError(f"OrcaSlicer did not produce output: {output_file}")

    estimated_minutes = _parse_estimated_time(result.stdout + result.stderr)
    log.info(f"Sliced {stl_path.name} → {output_file.name} "
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
