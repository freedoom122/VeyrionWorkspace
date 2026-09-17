"""Phase 1 reading experience (promoted from session runtime checks).

Covers: two-page gap tuning (#10), cover offset, RTL manga pairing (#59),
per-view themes (#77), autoscroll (#7), slideshow (#8), the reading-time
ticker (#81), the customizable toolbar registry (#78), distraction-free
mode (#79), and the floating quick-toolbar (#84).
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


# ---------------------------------------------------------------- #10 gap

def test_two_page_gap_pairing_ltr(view):
    from veyrion_workspace.ui.views.pdf_view import PdfView
    v = view
    v.set_mode(PdfView.MODE_TWO)
    v.set_gap(40)
    assert v.gap() == 40
    margin = 12.0
    w0 = v._page_items[0].page_width
    h0 = v._page_items[0].page_height
    h1 = v._page_items[1].page_height
    # Pairs are (0,1), (2,3), … with the configured gap between them.
    assert v._page_items[0].pos().x() == pytest.approx(margin)
    assert v._page_items[1].pos().x() == pytest.approx(margin + w0 + 40)
    assert v._page_items[2].pos().x() == pytest.approx(margin)
    assert v._page_items[2].pos().y() == pytest.approx(
        margin + max(h0, h1) + 40)


def test_two_cover_offset_pairs_shift(view):
    from veyrion_workspace.ui.views.pdf_view import PdfView
    v = view
    v.set_mode(PdfView.MODE_TWO_COVER)
    v.set_gap(40)
    assert v.cover_offset() is True
    margin = 12.0
    w1 = v._page_items[1].page_width
    h1 = v._page_items[1].page_height
    h2 = v._page_items[2].page_height
    # Page 0 sits alone on its own row; then pairs are (1,2), (3,4), …
    h0 = v._page_items[0].page_height
    assert v._page_items[0].pos().x() == pytest.approx(margin)
    assert v._page_items[1].pos().x() == pytest.approx(margin)
    assert v._page_items[2].pos().x() == pytest.approx(margin + w1 + 40)
    row2_y = margin + h0 + 40
    assert v._page_items[1].pos().y() == pytest.approx(row2_y)
    assert v._page_items[2].pos().y() == pytest.approx(row2_y)
    assert v._page_items[3].pos().y() == pytest.approx(
        row2_y + max(h1, h2) + 40)
    # Turning cover offset off returns to straight two-page mode.
    v.set_cover_offset(False)
    assert v.cover_offset() is False
    assert v._mode == PdfView.MODE_TWO


def test_rtl_manga_pairing_mirrors(view):
    from veyrion_workspace.ui.views.pdf_view import PdfView
    v = view
    v.set_mode(PdfView.MODE_TWO)
    v.set_rtl(True)
    v.set_gap(40)
    assert v.rtl() is True
    margin = 12.0
    w0 = v._page_items[0].page_width
    w1 = v._page_items[1].page_width
    # Even index is the RIGHT-hand page; odd index the left.
    assert v._page_items[0].pos().x() == pytest.approx(margin + w1 + 40)
    assert v._page_items[1].pos().x() == pytest.approx(margin)
    # Next pair lands on the following row.
    h0 = v._page_items[0].page_height
    h1 = v._page_items[1].page_height
    assert v._page_items[2].pos().y() == pytest.approx(
        margin + max(h0, h1) + 40)
    v.set_rtl(False)


# ---------------------------------------------------------------- #77 themes

def test_per_view_theme_changes_canvas(view):
    from veyrion_workspace.ui.views.pdf_view import VIEW_THEMES
    view.set_theme("sepia")
    assert view.theme() == "sepia"
    name = view._view.backgroundBrush().color().name().upper()
    assert name == VIEW_THEMES["sepia"].upper()
    view.set_theme("")
    assert view.theme() == ""
    name = view._view.backgroundBrush().color().name().upper()
    assert name == VIEW_THEMES[""].upper()


def test_gap_clamps_to_zero(view):
    view.set_gap(-5)
    assert view.gap() == 0.0


# ---------------------------------------------------------------- #7 / #8 / #81

def test_autoscroll_toggle_and_speed_clamp(view):
    view.start_autoscroll(2.0)
    assert view._autoscroll_timer.isActive()
    assert view.autoscroll_speed() == 2.0
    view.set_autoscroll_speed(99)
    assert view.autoscroll_speed() == 10.0
    view.set_autoscroll_speed(0.01)
    assert view.autoscroll_speed() == 0.1
    view.toggle_autoscroll()
    assert not view._autoscroll_timer.isActive()


def test_slideshow_advances_and_stops_at_end(view):
    view.start_slideshow(0.5)
    assert view.slideshow_active()
    view._slideshow_next()
    assert view.current_page == 1
    view.go_to_page(view.page_count - 1)
    view._slideshow_next()
    assert not view.slideshow_active(), "slideshow must stop at the last page"
    view.stop_slideshow()


def test_reading_tick_counts_seconds(view):
    hits = []
    view.reading_tick.connect(hits.append)
    before = view.reading_seconds()
    view._reading_tick()
    assert view.reading_seconds() == before + 1
    assert hits and hits[-1] == before + 1


# ---------------------------------------------------------------- #78 toolbar

def test_toolbar_default_registry(main_window):
    w, settings = main_window
    assert w.main_toolbar is not None
    # Default layout includes the Open action.
    assert w.act_open in w.main_toolbar.actions()
    # No persisted layout yet.
    assert settings.get("reading", "toolbar_items", []) == []


def test_toolbar_hide_and_append_semantics(main_window):
    w, settings = main_window
    # '-open' hides Open; keys missing from the list stay visible defaults.
    settings.set("reading", "toolbar_items", ["-open", "save"])
    w._render_toolbar_items()
    assert w.act_open not in w.main_toolbar.actions()
    assert w.act_save in w.main_toolbar.actions()


def test_toolbar_garbage_keys_clamp(main_window):
    w, settings = main_window
    settings.set("reading", "toolbar_items", "not-a-list")
    w._render_toolbar_items()          # must not raise
    assert w.act_open in w.main_toolbar.actions()
    settings.set("reading", "toolbar_items", ["totally_bogus_key"])
    w._render_toolbar_items()          # unknown keys are skipped safely
    assert w.act_open in w.main_toolbar.actions()


def test_customized_toolbar_survives_full_relaunch(
        main_window, app_launcher, monkeypatch):
    """#78: a layout customized in the real dialog comes back after a relaunch.

    The relaunch builds fresh ``Settings`` (re-read from ``settings.json``) plus
    a brand-new ``MainWindow``, so this exercises the whole persistence path
    rather than any in-memory state.
    """
    import json

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QListWidget

    from veyrion_workspace.app import paths

    w, settings = main_window

    def fake_exec(self):
        # Stand in for the user: reorder one item to the front and untick
        # another, then accept the dialog.
        lst = self.findChild(QListWidget)
        assert lst is not None and lst.count() > 0, "dialog has no item list"
        by_key = {lst.item(i).data(Qt.UserRole): lst.item(i)
                  for i in range(lst.count())}
        print_item = by_key["print"]
        lst.takeItem(lst.row(print_item))
        lst.insertItem(0, print_item)
        by_key["ocr"].setCheckState(Qt.Unchecked)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    w.action_customize_toolbar()

    saved = settings.get("reading", "toolbar_items")
    assert saved[0] == "print"
    assert "-ocr" in saved
    assert w.main_toolbar.actions()[0] is w.act_print
    assert w.act_ocr not in w.main_toolbar.actions()

    # The layout reached the disk — not merely this window's memory.
    raw = json.loads((paths.settings_dir() / "settings.json").read_text("utf-8"))
    assert raw["reading"]["toolbar_items"] == saved

    # ---- full relaunch: fresh services over the same data dir ----
    w.close()
    w2, settings2 = app_launcher()
    assert settings2.get("reading", "toolbar_items") == saved
    actions = w2.main_toolbar.actions()
    assert actions and actions[0] is w2.act_print, \
        "reordered item did not survive relaunch"
    assert w2.act_ocr not in actions, "hidden item reappeared after relaunch"
    assert w2.act_open in actions, "untouched default vanished after relaunch"


def test_distraction_free_persists_and_toggles(main_window):
    w, settings = main_window
    w.action_distraction_free()
    assert settings.get("reading", "distraction_free", False) is True
    assert w.act_distraction.isChecked() is True
    w.action_distraction_free()
    assert settings.get("reading", "distraction_free", False) is False
    assert w.act_distraction.isChecked() is False


# ---------------------------------------------------------------- #84 quick bar

def test_quick_bar_style_sync_and_signal(view):
    from veyrion_workspace.ui.views.pdf_view import QuickAnnotBar
    bar = QuickAnnotBar()
    try:
        bar.sync_from_style("#33557A", 0.8, 3.5)
        assert bar._swatches["#33557A"].isChecked()
        assert bar._thick.value() == 35          # width x10
        assert bar._trans.value() == 20          # (1-0.8)*100
        seen = []
        bar.style_changed.connect(lambda c, o, w_: seen.append((c, o, w_)))
        bar._pick_color("#C7522A")
        assert seen and seen[0][0] == "#C7522A"
    finally:
        bar.deleteLater()


def test_quick_bar_pops_on_annotation_press_and_hides(view):
    from PySide6.QtCore import QPointF

    class _FakeEvent:
        def __init__(self, p):
            self._p = p

        def position(self):
            return self._p

    view.set_annotation_tool("ink")
    view._pop_quick_bar(_FakeEvent(QPointF(30, 30)))
    assert view._quick_bar is not None, "quick bar did not appear"
    assert view._quick_bar.isVisible()
    # Style changes flow back into the view.
    view._quick_bar._pick_color("#3E7C4F")
    assert view._annot_color == "#3E7C4F"
    # Leaving annotation mode hides the bar.
    view.set_annotation_tool("")
    assert not view._quick_bar.isVisible()


def test_quick_bar_syncs_from_view_style(view):
    from PySide6.QtCore import QPointF

    class _FakeEvent:
        def __init__(self, p):
            self._p = p

        def position(self):
            return self._p

    view.set_annotation_tool("ink")
    try:
        view._pop_quick_bar(_FakeEvent(QPointF(30, 30)))
        bar = view._quick_bar
        view.set_annotation_style(color="#6A4BB8", opacity=0.5, width=4.0)
        assert bar._swatches["#6A4BB8"].isChecked()
        assert bar._thick.value() == 40
        assert bar._trans.value() == 50
    finally:
        if view._quick_bar is not None:
            view._quick_bar.hide()


def test_quick_bar_pop_clamps_to_screen(view):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication

    class _FakeEvent:
        def __init__(self, p):
            self._p = p

        def position(self):
            return self._p

    view.set_annotation_tool("ink")
    from PySide6.QtCore import QPointF
    view._pop_quick_bar(_FakeEvent(QPointF(20000, 20000)))
    bar = view._quick_bar
    try:
        assert bar is not None
        screen = bar.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            assert bar.x() + bar.width() <= avail.right() + 1
            assert bar.y() + bar.height() <= avail.bottom() + 1
    finally:
        bar.hide()
