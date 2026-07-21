"""
BambuBabu — STL Complexity Analyser
Scores an STL file (0–100) and determines which printer to use.
"""
from __future__ import annotations
import numpy as np
import trimesh
from pathlib import Path

from backend.config import settings
from backend.core.logger import get_logger
from backend.db.models import PrinterID

log = get_logger("bambububu.complexity")


def analyse_stl(stl_path: str | Path) -> dict:
    """
    Analyse an STL file and return metrics + complexity score.

    Returns:
        {
            face_count: int,
            volume_cm3: float,
            overhang_ratio: float,      # 0.0 – 1.0
            bbox: {x, y, z},            # mm
            complexity_score: float,    # 0 – 100
        }
    """
    log.info(f"Analysing STL: {stl_path}")

    mesh = trimesh.load(str(stl_path), force="mesh")

    if not isinstance(mesh, trimesh.Trimesh):
        # Handle scenes (multi-mesh STLs)
        if isinstance(mesh, trimesh.Scene):
            mesh = trimesh.util.concatenate(list(mesh.geometry.values()))
        else:
            raise ValueError("Could not load STL as a valid mesh")

    # ── Metrics ────────────────────────────────────────────────────────────

    face_count = len(mesh.faces)

    # Volume: STL units are typically mm³ → convert to cm³
    raw_volume = abs(float(mesh.volume))
    volume_cm3 = raw_volume / 1000.0

    # Bounding box (mm)
    extents = mesh.bounding_box.extents  # [x, y, z]
    bbox = {"x": round(float(extents[0]), 2),
            "y": round(float(extents[1]), 2),
            "z": round(float(extents[2]), 2)}

    # Overhang ratio: faces whose downward normal exceeds 45°
    # A face normal z-component < -cos(45°) = -0.707 means it faces downward
    normals = mesh.face_normals
    overhang_mask = normals[:, 2] < -0.707
    overhang_ratio = float(np.sum(overhang_mask)) / max(len(normals), 1)

    # ── Complexity Score (0–100) ────────────────────────────────────────────
    face_score     = min(face_count / 500_000, 1.0) * 100
    overhang_score = overhang_ratio * 100
    volume_score   = min(volume_cm3 / 300.0, 1.0) * 100

    complexity_score = (
        face_score     * 0.40
        + overhang_score * 0.40
        + volume_score   * 0.20
    )

    result = {
        "face_count":       int(face_count),
        "volume_cm3":       round(volume_cm3, 3),
        "overhang_ratio":   round(overhang_ratio, 4),
        "bbox":             bbox,
        "complexity_score": round(complexity_score, 2),
    }

    log.info(
        f"Analysis done — faces={face_count}, vol={volume_cm3:.1f}cm³, "
        f"overhangs={overhang_ratio:.1%}, score={complexity_score:.1f}"
    )
    return result


def select_printer(analysis: dict) -> tuple[PrinterID, str | None]:
    """
    Apply the printer selection algorithm.

    Returns:
        (printer_id, rejection_reason_or_None)
    """
    bbox = analysis["bbox"]
    score = analysis["complexity_score"]

    # ── Hard size check ────────────────────────────────────────────────────
    if (bbox["x"] > settings.P1S_MAX_X
            or bbox["y"] > settings.P1S_MAX_Y
            or bbox["z"] > settings.P1S_MAX_Z):
        return None, (
            f"Object ({bbox['x']}×{bbox['y']}×{bbox['z']} mm) "
            f"exceeds P1S build volume ({settings.P1S_MAX_X}×"
            f"{settings.P1S_MAX_Y}×{settings.P1S_MAX_Z} mm). "
            "Cannot print on any available printer."
        )

    fits_a1_mini = (
        bbox["x"] <= settings.A1_MINI_MAX_X
        and bbox["y"] <= settings.A1_MINI_MAX_Y
        and bbox["z"] <= settings.A1_MINI_MAX_Z
    )

    # ── Forced to P1S (doesn't fit A1 Mini) ───────────────────────────────
    if not fits_a1_mini:
        log.info(f"Object forced to P1S — too large for A1 Mini")
        return PrinterID.P1S, None

    # ── Complexity routing ─────────────────────────────────────────────────
    if score > settings.COMPLEXITY_THRESHOLD:
        log.info(f"Score {score:.1f} > threshold {settings.COMPLEXITY_THRESHOLD} → P1S")
        return PrinterID.P1S, None
    else:
        log.info(f"Score {score:.1f} ≤ threshold {settings.COMPLEXITY_THRESHOLD} → A1 Mini (default)")
        return PrinterID.A1_MINI, None


def can_fit_on_printer(bbox: dict, printer_id: PrinterID) -> bool:
    """Check if a bounding box fits on the given printer."""
    if printer_id == PrinterID.P1S:
        return (bbox["x"] <= settings.P1S_MAX_X
                and bbox["y"] <= settings.P1S_MAX_Y
                and bbox["z"] <= settings.P1S_MAX_Z)
    else:
        return (bbox["x"] <= settings.A1_MINI_MAX_X
                and bbox["y"] <= settings.A1_MINI_MAX_Y
                and bbox["z"] <= settings.A1_MINI_MAX_Z)
