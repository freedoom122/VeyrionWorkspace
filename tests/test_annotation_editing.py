"""Phase 2: editing annotations that already exist.

Two layers are covered:

* the engine mutation API — hit testing, move, resize, restyle, geometry
  snapshots — exercised across every annotation subtype and page rotation;
* the view gestures that drive it — click-select, Ctrl-drag marquee,
  drag-to-move, corner-handle resize, Delete, and group delete whose undo
  restores the original geometry.

A few regressions are pinned here too: the eraser used to call an API that
does not exist in this PyMuPDF build, sticky-note hit testing compared scene
coords against local ones, and note painting built QColor from (hex, alpha).
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

fitz = pytest.importorskip("fitz")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _settle(app, seconds=0.12):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture()
def engine(sample_pdf):
    from veyrion_workspace.core.documents.pdf_engine import PdfEngine
    e = PdfEngine(sample_pdf)
    yield e
    try:
        e.close()
    except Exception:
        pass


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


def _rect(engine, xref):
    return fitz.Rect(engine.get_annot(xref).rect)


def _visual(engine, xref):
    return fitz.Rect(engine.annot_visual_rect(xref))


def _make_all(engine):
    """One of every editable annotation type, keyed by display name."""
    page = engine._doc[0]
    return {
        "Highlight": engine.add_highlight(
            0, page.search_for("Page 1"), "#E5B25D", 0.4),
        "Underline": engine.add_text_markup(
            0, page.search_for("Alpha"), "underline", "#C7522A", 1.0),
        "Ink": engine.add_ink(
            0, [[(80, 300), (200, 320), (260, 305)]], "#222222", 2.0),
        "Square": engine.add_shape(
            0, "rectangle", (80, 350, 220, 400), "#C7522A", 1.5),
        "Circle": engine.add_shape(
            0, "ellipse", (250, 350, 380, 400), "#C7522A", 1.5),
        "Line": engine.add_shape(
            0, "line", (80, 430, 220, 470), "#C7522A", 1.5),
        "FreeText": engine.add_free_text(
            0, (250, 430, 400, 480), "hello", "#C7522A", 11),
    }


def _send(view, kind, scene_pos, buttons=None, mods=None):
    """Deliver a synthetic mouse event to the canvas viewport."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    gv = view._view
    vp = gv.viewport()
    local = QPointF(gv.mapFromScene(scene_pos))
    gp = QPointF(vp.mapToGlobal(local.toPoint()))
    if buttons is None:
        buttons = Qt.LeftButton
    if mods is None:
        mods = Qt.NoModifier
    QApplication.sendEvent(vp, QMouseEvent(kind, local, gp, Qt.LeftButton,
                                           buttons, mods))


def _drag(view, qapp, start, end, mods=None):
    from PySide6.QtCore import QEvent, Qt
    _send(view, QEvent.MouseButtonPress, start, mods=mods)
    _send(view, QEvent.MouseMove, end, mods=mods)
    _send(view, QEvent.MouseButtonRelease, end, buttons=Qt.NoButton, mods=mods)
    _settle(qapp, 0.05)


def _center(view, page, xref):
    return view._scene_rect_for(page, xref).center()


# ------------------------------------------------------------------ engine


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_move_is_exact_for_every_subtype(engine, rotation):
    """A page-space (dx, dy) must translate every subtype by exactly that."""
    if rotation:
        engine.rotate_page(0, rotation)
    for name, xref in _make_all(engine).items():
        before = _rect(engine, xref)
        assert engine.move_annot(xref, 30.0, 20.0) is True, name
        after = _rect(engine, xref)
        assert after.x0 - before.x0 == pytest.approx(30.0, abs=0.05), name
        assert after.y0 - before.y0 == pytest.approx(20.0, abs=0.05), name


def test_move_with_zero_delta_is_a_noop(engine):
    xref = _make_all(engine)["Square"]
    assert engine.move_annot(xref, 0.0, 0.0) is False


def test_move_keeps_the_xref_stable(engine):
    """Moves rewrite the raw keys in place, so no annot is recreated."""
    made = _make_all(engine)
    before = sorted(a["pdf_xref"] for a in engine.annotations())
    engine.move_annot(made["Ink"], 12.0, 8.0)
    engine.move_annot(made["Highlight"], 5.0, 5.0)
    engine.move_annot(made["Line"], 5.0, 5.0)
    assert sorted(a["pdf_xref"] for a in engine.annotations()) == before


@pytest.mark.parametrize("rotation", [0, 90])
def test_resize_scales_the_drawn_box(engine, rotation):
    if rotation:
        engine.rotate_page(0, rotation)
    for name, xref in _make_all(engine).items():
        before = _visual(engine, xref)
        target = (before.x0, before.y0,
                  before.x0 + before.width * 2,
                  before.y0 + before.height * 2)
        assert engine.resize_annot_rect(xref, target) is True, name
        after = _visual(engine, xref)
        assert after.width == pytest.approx(before.width * 2, rel=0.03), name
        assert after.height == pytest.approx(before.height * 2, rel=0.03), name


def test_resize_rejects_degenerate_rect(engine):
    xref = _make_all(engine)["Square"]
    assert engine.resize_annot_rect(xref, (10, 10, 10, 10)) is False


def test_popup_note_moves_on_an_unrotated_page(engine):
    xref = engine.add_note(0, (400, 120), "note text", "#E5B25D", "Me")
    before = _rect(engine, xref)
    assert engine.move_annot(xref, 30.0, 20.0) is True
    after = _rect(engine, xref)
    assert after.x0 - before.x0 == pytest.approx(30.0, abs=0.05)
    assert after.y0 - before.y0 == pytest.approx(20.0, abs=0.05)


def test_popup_note_icons_report_not_resizable(engine):
    """MuPDF keeps note icons a fixed size, so resize must say so."""
    xref = engine.add_note(0, (400, 120), "note text", "#E5B25D", "Me")
    r = _visual(engine, xref)
    assert engine.resize_annot_rect(
        xref, (r.x0, r.y0, r.x0 + 80, r.y0 + 80)) is False


def test_recolor_changes_every_subtype(engine):
    """Text boxes are excluded: see the dedicated FreeText test below."""
    made = {k: v for k, v in _make_all(engine).items() if k != "FreeText"}
    for name, xref in made.items():
        assert engine.set_annot_color(xref, "#1E6FB8") is True, name
        rec = next(a for a in engine.annotations() if a["pdf_xref"] == xref)
        assert rec["color"] == "#1e6fb8", name


def test_freetext_reports_that_it_cannot_be_recoloured(engine):
    """A text box's colour is baked into its appearance stream."""
    xref = engine.add_free_text(0, (250, 430, 400, 480), "hi", "#C7522A", 11)
    assert engine.set_annot_color(xref, "#1E6FB8") is False


def test_recolouring_a_missing_xref_is_false(engine):
    assert engine.set_annot_color(99999, "#1E6FB8") is False
    assert engine.set_annot_opacity(99999, 0.5) is False
    assert engine.move_annot(99999, 1.0, 1.0) is False
    assert engine.resize_annot_rect(99999, (0, 0, 10, 10)) is False


def test_opacity_and_border_width(engine):
    xref = _make_all(engine)["Square"]
    assert engine.set_annot_opacity(xref, 0.25) is True
    assert engine.set_annot_border_width(xref, 4.0) is True
    rec = next(a for a in engine.annotations() if a["pdf_xref"] == xref)
    assert rec["opacity"] == pytest.approx(0.25, abs=0.02)
    assert engine.annot_stroke_width(xref) == pytest.approx(4.0, abs=0.1)


def test_hit_test_finds_each_subtype(engine):
    made = _make_all(engine)
    note = engine.add_note(0, (500, 120), "note", "#E5B25D", "Me")
    made["Note"] = note
    for name, xref in made.items():
        box = _visual(engine, xref)
        hit = engine.annot_at(0, box.x0 + box.width / 2,
                              box.y0 + box.height / 2)
        assert hit is not None and hit[0] == xref, name
    assert engine.annot_at(0, 5, 5) is None


def test_hit_test_ignores_gaps_between_markup_quads(engine):
    """Highlight hit testing is per quad, not per bounding box."""
    import fitz as f
    xref = engine.add_highlight(
        0, [f.Rect(50, 50, 150, 60), f.Rect(50, 200, 150, 210)],
        "#E5B25D", 0.4)
    box = _visual(engine, xref)
    assert box.y1 - box.y0 > 140          # the bbox spans both quads
    assert (engine.annot_at(0, 100, 55) or (None,))[0] == xref
    assert (engine.annot_at(0, 100, 205) or (None,))[0] == xref
    # ... but the empty space between the two marked lines does not hit.
    assert engine.annot_at(0, 100, 130) is None


def test_geometry_snapshot_then_restore_is_faithful(engine):
    made = _make_all(engine)
    for name, xref in made.items():
        before = _visual(engine, xref)
        snap = engine.annot_geometry_snapshot(xref)
        rec = next(a for a in engine.annotations() if a["pdf_xref"] == xref)
        engine.delete_annot_by_xref(xref)
        assert engine.get_annot(xref) is None, name

        if rec["type"] in ("Highlight", "Underline", "StrikeOut", "Squiggly"):
            new = engine.add_markup_quads(0, rec["type"], snap["quads"],
                                          rec["color"], rec["opacity"])
        elif rec["type"] == "Ink":
            strokes = [[(s[i], s[i + 1]) for i in range(0, len(s) - 1, 2)]
                       for s in snap["strokes"]]
            new = engine.add_ink(0, strokes, rec["color"],
                                 engine.annot_stroke_width(xref) or 2.0)
        elif rec["type"] == "Line":
            ln = snap["line"]
            new = engine.add_line_between(0, (ln[0], ln[1]), (ln[2], ln[3]),
                                          rec["color"], 1.5)
        else:
            continue
        after = _visual(engine, new)
        assert after.x0 == pytest.approx(before.x0, abs=0.6), name
        assert after.y0 == pytest.approx(before.y0, abs=0.6), name
        assert after.width == pytest.approx(before.width, abs=0.6), name
        assert after.height == pytest.approx(before.height, abs=0.6), name


def test_snapshot_carries_subtype_geometry(engine):
    made = _make_all(engine)
    assert engine.annot_geometry_snapshot(made["Highlight"])["quads"]
    assert engine.annot_geometry_snapshot(made["Ink"])["strokes"]
    assert engine.annot_geometry_snapshot(made["Line"])["line"]


# -------------------------------------------------------------------- view


def test_selection_overlay_tracks_the_selection(view):
    x1 = view.pdf.add_highlight(0, view.pdf._doc[0].search_for("Page 1"),
                                "#E5B25D", 0.4)
    x2 = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                            "#C7522A", 1.5)
    assert view.selection_count() == 0

    view.select_annotations([(0, x1), (0, x2)])
    assert view.selection_count() == 2
    assert len(view._sel_outline_items) == 2
    # Handles only make sense for a single annotation.
    assert view._sel_handles == []

    view.select_annotations([(0, x1)])
    assert len(view._sel_handles) == 4

    view.clear_selection()
    assert view.selection_count() == 0
    assert view._sel_outline_items == [] and view._sel_handles == []


def test_click_selects_and_ctrl_toggles(view, qapp):
    from PySide6.QtCore import QEvent, Qt
    x1 = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                            "#C7522A", 1.5)
    x2 = view.pdf.add_shape(0, "ellipse", (250, 250, 380, 300),
                            "#3E7C4F", 1.5)
    _send(view, QEvent.MouseButtonPress, _center(view, 0, x1))
    _send(view, QEvent.MouseButtonRelease, _center(view, 0, x1),
          buttons=Qt.NoButton)
    assert view.selection() == [(0, x1)]

    # Ctrl+click adds the second, then removes it again.
    _send(view, QEvent.MouseButtonPress, _center(view, 0, x2),
          mods=Qt.ControlModifier)
    _send(view, QEvent.MouseButtonRelease, _center(view, 0, x2),
          buttons=Qt.NoButton, mods=Qt.ControlModifier)
    assert set(view.selection()) == {(0, x1), (0, x2)}
    _send(view, QEvent.MouseButtonPress, _center(view, 0, x2),
          mods=Qt.ControlModifier)
    _send(view, QEvent.MouseButtonRelease, _center(view, 0, x2),
          buttons=Qt.NoButton, mods=Qt.ControlModifier)
    assert view.selection() == [(0, x1)]


def test_ctrl_drag_marquee_selects_annotations(view, qapp):
    from PySide6.QtCore import QEvent, QPointF, Qt
    x1 = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                            "#C7522A", 1.5)
    x2 = view.pdf.add_shape(0, "ellipse", (250, 250, 380, 300),
                            "#3E7C4F", 1.5)
    view.pdf.add_shape(0, "rectangle", (80, 600, 120, 640), "#6A4BB8", 1.5)
    box = view._scene_rect_for(0, x1).united(view._scene_rect_for(0, x2))
    _drag(view, qapp,
          QPointF(box.left() - 25, box.top() - 25),
          QPointF(box.right() + 25, box.bottom() + 25),
          mods=Qt.ControlModifier)
    assert set(view.selection()) == {(0, x1), (0, x2)}


def test_drag_moves_the_selection_and_undo_restores(view, qapp):
    from PySide6.QtCore import QPointF
    xref = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                              "#C7522A", 1.5)
    before = _visual(view.pdf, xref)
    _drag(view, qapp, _center(view, 0, xref),
          _center(view, 0, xref) + QPointF(60, 40))
    after = _visual(view.pdf, xref)
    zoom = max(0.01, view.zoom)
    assert after.x0 - before.x0 == pytest.approx(60.0 / zoom, abs=0.6)
    assert after.y0 - before.y0 == pytest.approx(40.0 / zoom, abs=0.6)

    view.undo()
    restored = _visual(view.pdf, xref)
    assert restored.x0 == pytest.approx(before.x0, abs=0.05)
    assert restored.y0 == pytest.approx(before.y0, abs=0.05)


def test_drag_moves_every_selected_annotation(view, qapp):
    from PySide6.QtCore import QPointF
    x1 = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                            "#C7522A", 1.5)
    x2 = view.pdf.add_shape(0, "ellipse", (250, 250, 380, 300),
                            "#3E7C4F", 1.5)
    view.select_annotations([(0, x1), (0, x2)])
    b1, b2 = _visual(view.pdf, x1), _visual(view.pdf, x2)
    _drag(view, qapp, _center(view, 0, x1),
          _center(view, 0, x1) + QPointF(45, 30))
    a1, a2 = _visual(view.pdf, x1), _visual(view.pdf, x2)
    assert a1.x0 - b1.x0 == pytest.approx(a2.x0 - b2.x0, abs=0.05)
    assert a1.y0 - b1.y0 == pytest.approx(a2.y0 - b2.y0, abs=0.05)


def test_corner_handle_resizes_the_selection(view, qapp):
    from PySide6.QtCore import QPointF
    xref = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                              "#C7522A", 1.5)
    view.select_annotations([(0, xref)])
    box = view._scene_rect_for(0, xref)
    corner = QPointF(box.right(), box.bottom())
    before = _visual(view.pdf, xref)
    _drag(view, qapp, corner, corner + QPointF(50, 30))
    after = _visual(view.pdf, xref)
    assert after.width > before.width
    assert after.height > before.height
    assert after.x0 == pytest.approx(before.x0, abs=0.6)

    view.undo()
    undone = _visual(view.pdf, xref)
    assert undone.width == pytest.approx(before.width, abs=0.05)


def test_delete_key_removes_the_selection_and_undo_restores(view, qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    x1 = view.pdf.add_highlight(0, view.pdf._doc[0].search_for("Page 1"),
                                "#E5B25D", 0.4)
    x2 = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                            "#C7522A", 1.5)
    view._view.setFocus()
    view.select_annotations([(0, x1), (0, x2)])
    QApplication.sendEvent(view._view.viewport(),
                           QKeyEvent(QEvent.KeyPress, Qt.Key_Delete,
                                     Qt.NoModifier))
    assert view.pdf.annotations() == []
    assert view.selection_count() == 0

    view.undo()
    assert {a["type"] for a in view.pdf.annotations()} == {"Highlight",
                                                           "Square"}


def test_group_delete_restores_highlight_geometry(view):
    xref = view.pdf.add_highlight(0, view.pdf._doc[0].search_for("Page 1"),
                                  "#E5B25D", 0.4)
    before = _visual(view.pdf, xref)
    view.select_annotations([(0, xref)])
    assert view.delete_selection() == 1
    view.undo()
    restored = [a for a in view.pdf.annotations() if a["type"] == "Highlight"]
    assert len(restored) == 1
    after = fitz.Rect(restored[0]["rect"])
    assert after.width == pytest.approx(before.width, abs=1.0)


def test_restyle_is_a_single_undo_step(view):
    x1 = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                            "#C7522A", 1.5)
    x2 = view.pdf.add_shape(0, "ellipse", (250, 250, 380, 300),
                            "#3E7C4F", 1.5)
    view.select_annotations([(0, x1), (0, x2)])
    assert view.set_selection_style(color="#1E6FB8", opacity=0.5) == 2
    colors = {a["pdf_xref"]: a["color"] for a in view.pdf.annotations()}
    assert colors[x1] == "#1e6fb8" and colors[x2] == "#1e6fb8"

    # One undo reverts *both* annotations, so the restyle was one step.
    view.undo()
    colors = {a["pdf_xref"]: a["color"] for a in view.pdf.annotations()}
    assert colors[x1] == "#c7522a" and colors[x2] == "#3e7c4f"


def test_restyle_without_selection_is_a_noop(view):
    assert view.set_selection_style(color="#1E6FB8") == 0
    assert view.delete_selection() == 0


def test_styling_a_markup_ignores_border_width(view):
    xref = view.pdf.add_highlight(0, view.pdf._doc[0].search_for("Page 1"),
                                  "#E5B25D", 0.4)
    view.select_annotations([(0, xref)])
    # Markups have no stroke width, so only the colour change is recorded.
    assert view.set_selection_style(color="#3E7C4F", width=4.0) == 1


def test_select_all_on_page(view):
    view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300), "#C7522A", 1.5)
    view.pdf.add_shape(0, "ellipse", (250, 250, 380, 300), "#3E7C4F", 1.5)
    assert view.select_all_on_page(0) == 2
    assert view.selection_count() == 2
    assert view.select_all_on_page(5) == 0


def test_drawing_tool_clears_the_selection(view):
    xref = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                              "#C7522A", 1.5)
    view.select_annotations([(0, xref)])
    view.set_annotation_tool("ink")
    assert view.selection_count() == 0
    view.set_annotation_tool("")


def test_annotations_survive_a_save_round_trip(view, tmp_path):
    """Edits are written into the PDF, not just held in memory."""
    xref = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                              "#C7522A", 1.5)
    moved = view.pdf.move_annot(xref, 40.0, 25.0)
    assert moved
    out = tmp_path / "edited.pdf"
    view.pdf.save(out)
    check = fitz.open(out)
    try:
        page = check[0]          # hold the page: its annots must not outlive it
        rects = [fitz.Rect(a.rect) for a in page.annots()
                 if a.type[1] == "Square"]
        assert len(rects) == 1
        assert rects[0].x0 == pytest.approx(79.0 + 40.0, abs=1.0)
        assert rects[0].y0 == pytest.approx(249.0 + 25.0, abs=1.0)
    finally:
        check.close()


# ---------------------------------------------------- regressions pinned


def test_eraser_deletes_annotations_and_sticky_notes(view, qapp):
    from PySide6.QtCore import QEvent, QPointF
    xref = view.pdf.add_shape(0, "rectangle", (80, 250, 220, 300),
                              "#C7522A", 1.5)
    note = view.add_sticky_note_on_page(0, QPointF(560, 200))
    _settle(qapp)
    view.set_annotation_tool("eraser")

    _send(view, QEvent.MouseButtonPress, _center(view, 0, xref))
    assert view.pdf.annotations() == []

    note_center = note.mapRectToScene(note.boundingRect()).center()
    _send(view, QEvent.MouseButtonPress, note_center)
    assert view._sticky_notes == []

    assert view.undo()          # restore the note
    assert view.undo()          # restore the annotation
    assert len(view._sticky_notes) == 1
    assert len(view.pdf.annotations()) == 1


def test_sticky_note_hit_test_uses_scene_coordinates(view, qapp):
    from PySide6.QtCore import QPointF
    note = view.add_sticky_note_on_page(0, QPointF(560, 200))
    _settle(qapp)
    inside = note.mapRectToScene(note.boundingRect()).center()
    assert note.isUnderline(inside) is True
    assert note.isUnderline(QPointF(4, 4)) is False


def test_sticky_note_renders_without_raising(view, qapp):
    """Guards the QColor(hex, alpha) crash in the note's paint()."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QImage, QPainter
    note = view.add_sticky_note_on_page(0, QPointF(560, 200))
    _settle(qapp)
    img = QImage(320, 220, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        view._scene.render(painter, QRectF(0, 0, 320, 220),
                           note.mapRectToScene(note.boundingRect()))
    finally:
        painter.end()
    assert not img.isNull()


def test_select_all_annotations_action_is_wired(main_window):
    """The Annotate menu is the discoverable entry point for Phase 2."""
    w, _settings = main_window
    act = getattr(w, "act_select_annots", None)
    assert act is not None, "select-all-annotations action missing"
    assert "Ctrl+Shift+A" in act.shortcut().toString()
    from veyrion_workspace.ui.shortcuts_dialog import collect_menu_shortcuts
    hits = collect_menu_shortcuts(w.menuBar())
    assert any(a is act for (_m, _l, _k, a) in hits), "action is in no menu"
    assert any("Select All Annotations" in c.title
               for c in w._palette_commands())

    # No document open: a harmless no-op.
    w._select_all_annotations()

    # With a view it switches to Select/Pan then selects on the page.
    class _StubView:
        current_page = 2

        def __init__(self):
            self.tools = []
            self.pages = []

        def set_annotation_tool(self, tool):
            self.tools.append(tool)

        def select_all_on_page(self, page):
            self.pages.append(page)
            return 3

    stub = _StubView()
    w.current_view = lambda: stub        # type: ignore[assignment]
    w._select_all_annotations()
    assert stub.tools == [""]
    assert stub.pages == [2]


def test_quick_annot_bar_ink_preview_renders(view, qapp):
    """Guards the QPainterPath NameError in the live ink preview."""
    from PySide6.QtCore import QPointF
    view.set_annotation_tool("ink")
    view._drag_ink = [QPointF(20, 20), QPointF(60, 60), QPointF(90, 40)]
    view._view._draw_ink_preview()      # must not raise
    assert view._view._preview_item is not None
    view._view._remove_preview()
    view.set_annotation_tool("")
