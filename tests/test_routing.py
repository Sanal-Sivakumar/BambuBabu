from __future__ import annotations

from backend.config import settings
from backend.core import complexity, slicer
from backend.db.models import PrinterID
from tests.helpers import binary_stl


def analysis(*, bbox, score):
    return {"bbox": bbox, "complexity_score": score}


def test_threshold_boundary_prefers_a1_mini():
    selected, rejection = complexity.select_printer(
        analysis(bbox={"x": 100, "y": 100, "z": 100}, score=50)
    )
    assert selected == PrinterID.A1_MINI
    assert rejection is None


def test_complex_model_prefers_p1s():
    selected, rejection = complexity.select_printer(
        analysis(bbox={"x": 100, "y": 100, "z": 100}, score=50.01)
    )
    assert selected == PrinterID.P1S
    assert rejection is None


def test_model_too_large_for_a1_is_forced_to_p1s():
    selected, rejection = complexity.select_printer(
        analysis(bbox={"x": 181, "y": 100, "z": 100}, score=1)
    )
    assert selected == PrinterID.P1S
    assert rejection is None


def test_model_too_large_for_both_is_rejected():
    selected, rejection = complexity.select_printer(
        analysis(bbox={"x": 257, "y": 100, "z": 100}, score=1)
    )
    assert selected is None
    assert "exceeds P1S build volume" in rejection


def test_fit_check_uses_target_dimensions():
    bbox = {"x": 200, "y": 100, "z": 100}
    assert complexity.can_fit_on_printer(bbox, PrinterID.P1S) is True
    assert complexity.can_fit_on_printer(bbox, PrinterID.A1_MINI) is False


def test_mock_slice_names_output_for_target_printer(tmp_path, monkeypatch):
    source = tmp_path / "job-id.stl"
    source.write_bytes(binary_stl())
    monkeypatch.setattr(settings, "MOCK_SLICER", True)
    monkeypatch.setattr("backend.core.slicer.time.sleep", lambda _seconds: None)

    output, estimate = slicer.slice_stl(source, PrinterID.A1_MINI, tmp_path / "out")
    assert output.name == "job-id-a1_mini.3mf"
    assert output.read_bytes() == source.read_bytes()
    assert estimate == 30
