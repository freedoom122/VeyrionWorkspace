"""Grind batch-1 regression tests.

Covers: the single zoom-clamp helper (ZOOM_MIN/ZOOM_MAX in one place),
the argument-vector lp command builder (no shell joining, space-safe
printer names and paths), and LibraryRepository.upsert preserving
metadata_json on the UPDATE branch.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------- zoom clamp
def test_clamp_zoom_bounds_and_identity():
    from veyrion_workspace.ui.views.pdf_view import ZOOM_MAX, ZOOM_MIN, clamp_zoom

    assert clamp_zoom(1.0) == 1.0
    assert clamp_zoom(99.0) == ZOOM_MAX
    assert clamp_zoom(0.0001) == ZOOM_MIN
    assert ZOOM_MIN < 1.0 < ZOOM_MAX


def test_set_zoom_clamps_extremes(qapp, sample_pdf):
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.ui.views.pdf_view import PdfView
    result = open_document(sample_pdf)
    v = PdfView(result.engine)
    try:
        v.set_zoom(99.0)
        assert v.zoom == pytest.approx(8.0)
        v.set_zoom(0.0001)
        assert v.zoom == pytest.approx(0.1)
        v.set_zoom(1.25)
        assert v.zoom == pytest.approx(1.25)
    finally:
        v.deleteLater()


def test_zoom_bounds_constant_single_source():
    """No other clamp of the old magic pair may exist in pdf_view."""
    import re
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[1] / \
        "src/veyrion_workspace/ui/views/pdf_view.py"
    text = p.read_text(encoding="utf-8")
    hits = re.findall(r"max\(0\.1, min\(8\.0,", text)
    assert hits == [], f"raw zoom clamp literals leaked: {hits}"


# ------------------------------------------------------------- lp arguments
def test_build_lp_command_default():
    from veyrion_workspace.core.printing.printing import build_lp_command
    import pathlib
    p = pathlib.Path("/tmp/out with space.pdf")
    cmd = build_lp_command(p)
    assert cmd[0] == "lp"
    assert cmd[-1] == str(p)
    assert len(cmd) == 2


def test_build_lp_command_named_printer_with_spaces():
    from veyrion_workspace.core.printing.printing import build_lp_command
    import pathlib
    p = pathlib.Path("C:/cards/o.pdf")
    cmd = build_lp_command(p, "Office HP LaserJet 400")
    assert cmd == ["lp", "-d", "Office HP LaserJet 400", str(p)]


def test_send_to_printer_uses_argument_vector(monkeypatch, tmp_path):
    """The shell-joined string form must be gone: run() gets a list."""
    from veyrion_workspace.core.printing import printing
    captured = {}

    def fake_run(cmd, check):
        captured["cmd"] = cmd
        captured["check"] = check

    monkeypatch.setattr(printing.subprocess, "run", fake_run)
    monkeypatch.setattr(printing.sys, "platform", "linux")
    assert printing.send_to_printer(tmp_path / "card.pdf", "My Printer") is True
    assert captured["cmd"] == ["lp", "-d", "My Printer", str(tmp_path / "card.pdf")]


# -------------------------------------------------------- upsert metadata
def _rec(**kw):
    from veyrion_workspace.storage.repositories import DocumentRecord
    return DocumentRecord(**kw)


def test_upsert_insert_then_update_preserves_metadata(db):
    from veyrion_workspace.storage.repositories import LibraryRepository
    repo = LibraryRepository(db)

    rid = repo.upsert(_rec(path="P:/book.pdf", title="Book"))
    db.execute("UPDATE documents SET metadata_json=? WHERE id=?",
               ('{"custom": true}', rid))

    # A later rescan upserts the same path with fresh scan data.
    repo.upsert(_rec(path="P:/book.pdf", title="Book", page_count=42))

    row = db.query_one("SELECT metadata_json, page_count FROM documents "
                       "WHERE id=?", (rid,))
    assert row["page_count"] == 42
    assert row["metadata_json"] == '{"custom": true}'


def test_upsert_update_never_writes_metadata_json(db):
    from veyrion_workspace.storage.repositories import LibraryRepository
    repo = LibraryRepository(db)
    rid = repo.upsert(_rec(path="P:/a.pdf", title="A"))
    db.execute("UPDATE documents SET metadata_json=? WHERE id=?",
               ('{"keep": 1}', rid))
    repo.upsert(_rec(path="P:/a.pdf", title="A2", author="X"))
    row = db.query_one("SELECT metadata_json FROM documents WHERE id=?", (rid,))
    assert row["metadata_json"] == '{"keep": 1}'
