"""PDF annotation undo/redo and sticky notes (promoted from session checks).

Covers: add-annotation undo/redo, delete-annotation restore (fresh-xref
semantics), sticky-note add/undo/redo, the delete -> actionable-toast ->
Undo-click -> restore loop (#13 + #82), save/restore state round-trips
(#10/#77), and reading-history recording.
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _settle(app, seconds=0.15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture()
def view(qapp, sample_pdf):
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.ui.views.pdf_view import PdfView
    result = open_document(sample_pdf)
    v = PdfView(result.engine)
    v.resize(900, 700)
    v.show()
    _settle(qapp)
    yield v
    v.close_view()
    v.deleteLater()


def test_add_annotation_undo_redo_roundtrip(view):
    res = view.add_annotation_at(1, "highlight", (72, 100, 200, 120))
    assert res and res.get("xref"), "highlight creation failed"
    assert view.can_undo()

    assert view.undo() is True
    raw = [a for a in view.pdf.annotations() if a.get("type") == "Highlight"]
    assert len(raw) == 0, "undo did not remove the annotation"

    assert view.redo() is True
    raw = [a for a in view.pdf.annotations() if a.get("type") == "Highlight"]
    assert len(raw) == 1, "redo did not re-create the annotation"


def test_delete_annotation_undo_restores_with_fresh_xref(view):
    """The eraser flow: delete, push a delete command, then undo restores.

    Undo re-creates the markup with a NEW xref, so assertions must check the
    markup was restored, never that the xref is unchanged.
    """
    from veyrion_workspace.ui.views.pdf_view import _DeleteAnnotCommand
    res = view.add_annotation_at(1, "highlight", (72, 100, 200, 120))
    assert res
    xref = res["xref"]
    snaps = [a for a in view.pdf.annotations() if a.get("type") == "Highlight"]
    assert len(snaps) == 1
    snapshot = snaps[0]

    view.delete_annotation_xref(xref)
    assert not [a for a in view.pdf.annotations()
                if a.get("type") == "Highlight"]

    view.undo_stack.push(_DeleteAnnotCommand(view, xref, snapshot))
    assert view.undo() is True
    restored = [a for a in view.pdf.annotations()
                if a.get("type") == "Highlight"]
    assert len(restored) == 1, "undo of delete did not restore the markup"
    if "xref" in restored[0]:
        assert restored[0]["xref"] != xref, "restore should use a fresh xref"

    assert view.redo() is True  # redo deletes it again
    assert not [a for a in view.pdf.annotations()
                if a.get("type") == "Highlight"]


def test_sticky_note_add_undo_redo(view):
    from PySide6.QtCore import QPointF
    item = view.add_sticky_note_on_page(0, QPointF(30, 30))
    assert item in view._sticky_notes
    assert view.can_undo()

    assert view.undo() is True
    assert view._sticky_notes == [], "undo of add did not remove the note"

    assert view.redo() is True
    assert len(view._sticky_notes) == 1, "redo did not re-create the note"
    assert view._sticky_notes[0] is not item  # fresh widget instance


def test_sticky_note_delete_toast_undo_loop(view):
    """Delete flow emits action_toast with the view's undo as the action."""
    from PySide6.QtCore import QPointF
    from veyrion_workspace.ui.views.pdf_view import _StickyNoteCommand
    item = view.add_sticky_note_on_page(0, QPointF(30, 30))
    assert item.page_index == 0

    captured = []
    view.action_toast.connect(
        lambda m, k, cb: captured.append((m, k, cb)))

    # Mirror the real X-button path in _on_sticky_note_finished.
    view.undo_stack.push(_StickyNoteCommand(
        view, "delete", item=item, page=item.page_index,
        scene_pos=item.pos()))
    view.remove_sticky_note(item)
    view.offer_undo_delete("Sticky note deleted")

    assert captured, "action_toast was not emitted"
    message, kind, callback = captured[-1]
    assert "deleted" in message.lower()
    assert kind == "info"
    assert callback == view.undo

    assert callback() is True, "Undo button callback failed"
    assert len(view._sticky_notes) == 1, "undo did not restore the note"
    assert view._sticky_notes[0].text() == item.text()


def test_save_restore_state_gap_and_theme(view):
    original = view.save_state()
    assert original["gap"] == 14
    assert original["theme"] == ""

    view.set_gap(40)
    view.set_theme("sepia")
    changed = view.save_state()
    assert changed["gap"] == 40
    assert changed["theme"] == "sepia"

    view.restore_state(original)
    assert view.gap() == 14
    assert view.theme() == ""
    # Invalid values must not corrupt state.
    view.restore_state({"gap": "junk", "theme": None})
    assert view.gap() == 14
    assert view.theme() == ""


def test_gap_clamping(view):
    view.set_gap(-5)
    assert view.gap() == 0.0
    view.set_gap(40)
    assert view.gap() == 40.0


def test_nav_history_records_jumps(view):
    assert view._current_page == 0
    view.go_to_page(3)
    assert view._current_page == 3
    assert view._nav_back[-1] == 0
    view.go_to_page(5)
    assert len(view._nav_back) >= 2
    # Cap is respected.
    for i in range(view.NAV_HISTORY_MAX + 5):
        view.go_to_page(i % view.page_count)
    assert len(view._nav_back) <= view.NAV_HISTORY_MAX
