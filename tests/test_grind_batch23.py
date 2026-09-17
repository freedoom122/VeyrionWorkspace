"""Grind batch 2+3 regression tests.

Covers: middle-click tab close, double-click-empty opens a document,
empty-area context-menu hooks, the crash-journal heartbeat timer parented
to the window, the live window title, and the zoom-animation mutex
(manual wheel zoom freezes a running animation).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# ------------------------------------------------------------- tabwidget
def _bar_with_tabs(qapp):
    from veyrion_workspace.ui.tabwidget import DocumentTabWidget
    w = DocumentTabWidget()
    for i in range(2):
        page = object.__new__(type("P", (object,), {}))
        from PySide6.QtWidgets import QWidget
        page = QWidget()
        page.path = f"P:/doc{i}.pdf"
        w.addTab(page, f"Doc {i}")
    return w


def test_middle_click_closes_tab(qapp):
    w = _bar_with_tabs(qapp)
    assert w.count() == 2
    # Simulate a middle-click on the first tab.
    rect = w.tabBar().tabRect(0)
    ev = QMouseEvent(QEvent.MouseButtonPress, rect.center(),
                     Qt.MiddleButton, Qt.MiddleButton, Qt.NoModifier)
    handled = w.eventFilter(w.tabBar(), ev)
    assert handled is True
    assert w._closed_stack, "closed tab must be remembered for reopen"


def test_double_click_empty_area_requests_open(qapp):
    w = _bar_with_tabs(qapp)
    opened = []
    wnd = type("W", (), {"action_open": staticmethod(lambda: opened.append(True))})()
    wnd.setParent = lambda *a, **k: None
    try:
        w.window = lambda: wnd  # type: ignore[assignment]
    except Exception:
        pass
    w._on_tab_double_clicked(-1)
    assert opened == [True]


def test_double_click_tab_emits_detach(qapp):
    w = _bar_with_tabs(qapp)
    got = []
    w.tab_detach_requested.connect(got.append)
    w._on_tab_double_clicked(1)
    assert got == [1]


# ------------------------------------------------------------ main window
def test_journal_timer_parented_to_window(main_window):
    w, _ = main_window
    timer = getattr(w, "_journal_timer", None)
    assert timer is not None
    assert timer.parent() is w
    assert timer.isActive()


def test_window_title_tracks_document(main_window, sample_pdf):
    w, _ = main_window
    w.show()
    w.open_path(str(sample_pdf))
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    name = str(w.current_view().display_name)
    assert w.windowTitle().startswith(name)
    assert w.windowTitle().endswith("Veyrion Workspace")
    # With no document: tagline form.
    w._update_window_title(None)
    assert w.windowTitle() == "Veyrion Workspace — READ. EDIT. ORGANIZE. CREATE."


# ------------------------------------------------------------- library
def test_library_empty_area_menu_actions(qapp):
    from veyrion_workspace.ui.panels.library_panel import LibraryPanel
    assert hasattr(LibraryPanel, "_empty_area_menu")
    import inspect
    src = inspect.getsource(LibraryPanel._empty_area_menu)
    assert "Add folder" in src


# ------------------------------------------------------------- pdf zoom
def test_cancel_zoom_anim_freezes_current_zoom(qapp, sample_pdf):
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.ui.views.pdf_view import PdfView
    result = open_document(sample_pdf)
    v = PdfView(result.engine)
    try:
        v.resize(900, 700)
        v.show()
        for _ in range(6):
            qapp.processEvents()
        v.zoom_in()
        assert v._zoom_anim.isActive()
        frozen = v.zoom
        v._cancel_zoom_anim()
        assert not v._zoom_anim.isActive()
        assert v._zoom_anim_anchor is None
        assert v.zoom == pytest.approx(frozen)
    finally:
        v.deleteLater()
