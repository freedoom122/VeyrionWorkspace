"""Annotations panel legend chips (promoted from the session runtime check).

Covers: chip construction with live counts, click-to-filter and click-to-clear,
multi-chip unions, composition with the type dropdown and search box,
restart persistence, corrupt-value clamping, and settings-less operation.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def panel_env(qapp, data_dir, db):
    """AnnotationsPanel over a real service, with seeded DB annotations."""
    from veyrion_workspace.core.annotations.service import AnnotationService
    from veyrion_workspace.storage.repositories import AnnotationRepository
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.ui.panels.info_panels import AnnotationsPanel

    settings = Settings()
    svc = AnnotationService(AnnotationRepository(db), settings)
    doc = "C:/books/sample.pdf"
    svc.create(doc, 0, "highlight", text="important line")
    svc.create(doc, 1, "highlight", text="second highlight")
    svc.create(doc, 2, "note", text="remember this")
    svc.create(doc, 3, "ink", text="scribble")
    svc.create(doc, 3, "ink", text="todo item")
    svc.create(doc, 4, "rectangle", text="box")
    panel = AnnotationsPanel(svc, settings)
    panel.set_document_filter(doc)
    panel.reload()
    yield panel, settings, svc, doc


def _rows(panel) -> int:
    return panel._list.count()


def test_chips_built_with_counts(panel_env):
    panel, _s, _svc, _doc = panel_env
    from veyrion_workspace.ui.panels.info_panels import CHIP_GROUPS
    assert set(panel._chip_buttons) == {label for label, *_ in CHIP_GROUPS}
    assert _rows(panel) == 6
    assert panel._count_badge.text() == "6"
    assert panel._chip_counts.get("Highlights") == 2
    assert panel._chip_counts.get("Notes") == 1
    assert panel._chip_counts.get("Ink") == 2
    assert panel._chip_counts.get("Shapes") == 1


def test_click_filters_and_click_clears(panel_env):
    panel, settings, _svc, _doc = panel_env
    panel._chip_buttons["Highlights"].click()
    assert panel._chips == {"Highlights"}
    assert _rows(panel) == 2
    assert panel._count_badge.text() == "2"
    assert settings.get("annotations", "panel_chips", []) == ["Highlights"]
    # Second click returns to the unfiltered legend state.
    panel._chip_buttons["Highlights"].click()
    assert panel._chips == set()
    assert _rows(panel) == 6
    assert settings.get("annotations", "panel_chips", []) == []


def test_multi_chip_union(panel_env):
    panel, _s, _svc, _doc = panel_env
    panel._chip_buttons["Highlights"].click()
    panel._chip_buttons["Ink"].click()
    assert panel._chips == {"Highlights", "Ink"}
    assert _rows(panel) == 4  # 2 highlights + 2 ink


def test_chips_compose_with_dropdown_and_search(panel_env):
    panel, _s, _svc, _doc = panel_env
    panel._chip_buttons["Highlights"].click()
    # Dropdown "Shapes" intersects the Highlights chip -> nothing matches.
    panel._type_combo.setCurrentIndex(4)
    assert _rows(panel) == 0
    assert panel._count_badge.text() == "0"
    panel._type_combo.setCurrentIndex(0)
    # Search box still applies on top of the chip filter.
    panel._chip_buttons["Ink"].click()
    panel._filter.setText("todo")
    assert _rows(panel) == 1
    panel._filter.clear()


def test_restart_restores_active_chips(panel_env, data_dir):
    panel, _s, svc, doc = panel_env
    panel._chip_buttons["Ink"].click()
    # Fresh Settings + fresh panel over the same data dir: the active chip
    # must come back and re-apply its filter.
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.ui.panels.info_panels import AnnotationsPanel
    panel2 = AnnotationsPanel(svc, Settings())
    assert panel2._chips == {"Ink"}
    panel2.set_document_filter(doc)
    panel2.reload()
    assert _rows(panel2) == 2


def test_corrupt_chip_values_clamp(panel_env, db):
    from veyrion_workspace.core.annotations.service import AnnotationService
    from veyrion_workspace.storage.repositories import AnnotationRepository
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.ui.panels.info_panels import AnnotationsPanel
    svc = AnnotationService(AnnotationRepository(db), Settings())
    settings = Settings()
    settings.set("annotations", "panel_chips", "not-a-list")
    panel = AnnotationsPanel(svc, settings)
    assert panel._chips == set()
    settings.set("annotations", "panel_chips", ["Ink", "Bogus", 42])
    panel2 = AnnotationsPanel(svc, Settings())
    assert panel2._chips == {"Ink"}


def test_settings_less_panel_still_works(qapp, db):
    from veyrion_workspace.core.annotations.service import AnnotationService
    from veyrion_workspace.storage.repositories import AnnotationRepository
    from veyrion_workspace.ui.panels.info_panels import AnnotationsPanel
    svc = AnnotationService(AnnotationRepository(db), None)
    svc.create("C:/x/a.pdf", 0, "ink", text="stroke")
    panel = AnnotationsPanel(svc, None)
    panel.set_document_filter("C:/x/a.pdf")
    panel.reload()
    assert _rows(panel) == 1
    panel._chip_buttons["Ink"].click()   # must not crash without settings
    assert _rows(panel) == 1
    panel._chip_buttons["Ink"].click()
