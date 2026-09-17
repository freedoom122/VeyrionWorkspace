"""Reference surfaces: the annotations list and the reading summary.

Both now export through the shared engine
(:mod:`veyrion_workspace.ui.reference_export`) — as a PDF card, CSV or
Markdown — and both can hand the card to a printer. These tests pin what each
surface reports and that a printed/exported copy matches what is on screen.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from veyrion_workspace.ui.reading_summary import (  # noqa: E402
    TITLE,
    default_export_name,
    format_duration,
    format_stamp,
    gather_summary,
    summary_subtitle,
)


@pytest.fixture()
def pdf_view(qapp, data_dir, sample_pdf):
    """A real PdfView over the sample document (closed on teardown)."""
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.ui.views.factory import create_view

    result = open_document(sample_pdf)
    view = create_view(result, Settings())
    view.resize(900, 700)
    yield view
    view.close_view()


@pytest.fixture()
def annotations_panel(qapp, data_dir, db):
    """AnnotationsPanel with a handful of DB records on a stable doc path."""
    from veyrion_workspace.core.annotations.service import AnnotationService
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.storage.repositories import AnnotationRepository
    from veyrion_workspace.ui.panels.info_panels import AnnotationsPanel

    settings = Settings()
    service = AnnotationService(AnnotationRepository(db), settings)
    doc = "C:/books/field-notes.pdf"
    service.create(doc, 0, "highlight", text="important line")
    service.create(doc, 1, "highlight", text="second highlight")
    service.create(doc, 4, "note", text="remember this")
    service.create(doc, 4, "ink", text="todo item")
    panel = AnnotationsPanel(service, settings)
    panel.set_document_filter(doc)
    panel.reload()
    return panel


def _capture_toasts(monkeypatch) -> list[tuple]:
    import veyrion_workspace.ui.widgets as widgets

    seen: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast",
                        lambda *a, **k: seen.append(a))
    return seen


def _capture_print(monkeypatch) -> list[str]:
    import veyrion_workspace.core.printing.printing as printing

    from PySide6.QtWidgets import QDialog as _QDialog

    # Auto-accept the print preview so the send path runs.
    monkeypatch.setattr(
        "PySide6.QtWidgets.QDialog.exec",
        staticmethod(lambda *a, **k: _QDialog.DialogCode.Accepted))
    sent: list[str] = []
    monkeypatch.setattr(printing, "send_to_printer",
                        lambda path, *a, **k: bool(sent.append(str(path))) or True)
    return sent


def _pdf_text(path) -> str:
    import fitz
    doc = fitz.open(path)
    try:
        return "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()


# ------------------------------------------------------- annotations surface

def test_panel_card_rows_describe_what_is_listed(annotations_panel):
    rows = annotations_panel.visible_rows()
    assert len(rows) == 4
    assert all(len(row) == 4 for row in rows)
    pages = [row[0] for row in rows]
    types = [row[1] for row in rows]
    assert pages == ["p.1", "p.2", "p.5", "p.5"]
    assert types == ["highlight", "highlight", "note", "ink"]
    assert all(row[2] == "Me" for row in rows)
    assert "important line" in rows[0][3]


def test_panel_card_follows_the_active_filters(annotations_panel):
    panel = annotations_panel
    panel._type_combo.setCurrentIndex(3)          # Ink
    assert [r[1] for r in panel.visible_rows()] == ["ink"]
    panel._type_combo.setCurrentIndex(0)
    panel._filter.setText("second")
    assert [r[3] for r in panel.visible_rows()] == ["second highlight"]
    panel._filter.clear()
    assert len(panel.visible_rows()) == 4


def test_panel_card_names_the_document_in_the_all_documents_scope(
        annotations_panel):
    panel = annotations_panel
    assert panel.visible_rows()[0][0] == "p.1"
    panel._scope_combo.setCurrentIndex(1)         # All documents
    cells = [row[0] for row in panel.visible_rows()]
    assert all("field-notes.pdf" in cell for cell in cells), cells
    assert "all documents" in panel._card_subtitle(len(cells))


def test_panel_card_export_writes_the_rows_shown(annotations_panel, tmp_path,
                                                 monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    toasts = _capture_toasts(monkeypatch)
    target = tmp_path / "annots.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target),
                                                      "CSV table (*.csv)")))
    out = annotations_panel.export_card("csv")

    written = tmp_path / "annots.csv"
    assert out == written and written.exists()
    with written.open(encoding="utf-8-sig", newline="") as fh:
        parsed = list(csv.reader(fh))
    assert parsed[0] == ["Page", "Type", "Author", "Annotation"]
    assert [row[3] for row in parsed[1:]] == [
        "important line", "second highlight", "remember this", "todo item"]
    assert toasts and "annots.csv" in toasts[-1][1]


def test_panel_card_export_honors_the_filter(annotations_panel, tmp_path,
                                             monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    _capture_toasts(monkeypatch)
    panel = annotations_panel
    panel._filter.setText("important")
    target = tmp_path / "filtered.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target),
                                                      "Markdown table (*.md)")))
    panel.export_card("markdown")
    text = target.read_text(encoding="utf-8")
    assert "important line" in text
    assert "second highlight" not in text, "the export ignored the search box"


def test_panel_print_card_sends_a_pdf_card(annotations_panel, data_dir,
                                           monkeypatch):
    sent = _capture_print(monkeypatch)
    toasts = _capture_toasts(monkeypatch)
    assert annotations_panel.print_card() is True
    assert sent and Path(sent[0]).read_bytes().startswith(b"%PDF")
    text = _pdf_text(sent[0])
    assert "Annotation" in text and "important line" in text
    assert toasts and "Print job sent" in toasts[-1][1]


def test_panel_export_menu_offers_card_data_and_print(annotations_panel,
                                                      monkeypatch):
    panel = annotations_panel
    labels = [a.text() for a in panel._export_btn.menu().actions() if a.text()]
    for expected in ("PDF card…", "CSV table…", "Markdown table…",
                     "Markdown…", "HTML…", "CSV…", "JSON…", "PDF…"):
        assert expected in labels, expected
    assert panel._print_btn.text() == "Print…"

    # The data entries still route to the library exporter.
    calls: list[str] = []
    monkeypatch.setattr(panel, "_export", lambda fmt: calls.append(fmt))
    for action in panel._export_btn.menu().actions():
        if action.text() == "JSON…":
            action.trigger()
    assert calls == ["json"]


def test_panel_card_includes_live_markups_and_sticky_notes(
        annotations_panel, pdf_view):
    from PySide6.QtCore import QPointF

    import fitz

    panel = annotations_panel
    view = pdf_view
    view.engine.add_highlight(0, [fitz.Rect(70, 95, 300, 110).quad], "#E5B25D")
    note = view.add_sticky_note_on_page(1, QPointF(90, 90))
    note.init_elements()
    note._text_item.setPlainText("double-check the numbers")

    panel.set_document_filter(str(view.path), view)
    panel.reload()
    rows = panel.visible_rows()
    bodies = [row[3] for row in rows]
    assert any(row[1] == "highlight" for row in rows)
    assert any(body.startswith("sticky note") for body in bodies), bodies
    live = [a for a in view.live_annotations() if a.get("sticky")]
    assert live and live[0]["text"] == "double-check the numbers"


# --------------------------------------------------------- reading summary

def test_gather_summary_reports_the_document(pdf_view):
    pdf_view.go_to_page(3)
    rows = dict(gather_summary(pdf_view))
    assert rows["Document"] == pdf_view.path.name
    assert rows["Pages"] == str(pdf_view.page_count)
    assert rows["Current page"] == f"4 of {pdf_view.page_count}"
    assert rows["Progress"] == "67% read"
    assert rows["Pages remaining"] == "2"
    assert "Report generated" in rows


def test_gather_summary_counts_markups(pdf_view):
    import fitz

    pdf_view.engine.add_highlight(0, [fitz.Rect(70, 95, 300, 110).quad],
                                  "#E5B25D")
    pdf_view.engine.add_note(1, (400, 120), "note", "#C7522A", "Me")
    rows = dict(gather_summary(pdf_view))
    assert rows["Markups"] == "2"


def test_gather_summary_without_a_view_is_honest():
    assert gather_summary(None) == [("Document", "No document open")]


def test_gather_summary_reports_library_facts(pdf_view, db):
    path = str(pdf_view.path)
    db.execute("INSERT INTO bookmarks (doc_path, page, title, created_at) "
               "VALUES (?,?,?,?)", (path, 2, "chapter", 1_700_000_000.0))
    db.execute("INSERT INTO bookmarks (doc_path, page, title, created_at) "
               "VALUES (?,?,?,?)", (path, 5, "another", 1_700_000_100.0))
    db.execute("INSERT INTO reading_progress (doc_path, page, scroll, zoom, "
               "mode, updated_at) VALUES (?,?,?,?,?,?)",
               (path, 2, 0.0, 1.0, "", 1_700_000_200.0))
    db.execute("INSERT INTO reading_sessions (doc_path, started_at, ended_at, "
               "pages_read) VALUES (?,?,?,?)", (path, 1.0, 2.0, 7))
    db.execute("INSERT INTO reading_sessions (doc_path, started_at, ended_at, "
               "pages_read) VALUES (?,?,?,?)", (path, 3.0, 4.0, 5))
    db.execute("INSERT INTO documents (path, title, author, kind, added_at, "
               "last_opened) VALUES (?,?,?,?,?,?)",
               (path, "Field Notes", "A. Author", "pdf",
                1_700_000_000.0, 1_700_000_300.0))

    rows = dict(gather_summary(pdf_view, db))
    assert rows["Bookmarks"] == "2"
    assert rows["Saved position"] == "page 3"
    assert rows["Position last saved"].startswith("2023-")
    assert rows["Reading sessions recorded"] == "2"
    assert rows["Pages read (recorded)"] == "12"
    assert rows["Library title"] == "Field Notes"
    assert rows["Author"] == "A. Author"
    assert rows["Kind"] == "pdf"


def test_gather_summary_survives_a_broken_database(pdf_view):
    class Broken:
        def query_one(self, *a, **k):
            raise RuntimeError("no database today")

    rows = dict(gather_summary(pdf_view, Broken()))
    assert rows["Document"] == pdf_view.path.name
    assert "Bookmarks" not in rows


def test_duration_and_stamp_formatting():
    assert format_duration(0) == "—"
    assert format_duration(45) == "45s"
    assert format_duration(63) == "1m 03s"
    assert format_duration(3725) == "1h 02m 05s"
    assert format_duration(None) == "—"
    assert format_stamp(0) == "—"
    assert format_stamp(None) == "—"
    assert format_stamp(1_700_000_000.0).startswith("2023-")


def test_summary_subtitle_and_default_name(pdf_view):
    pdf_view.go_to_page(3)
    subtitle = summary_subtitle(pdf_view)
    assert pdf_view.path.name in subtitle
    assert "67% read" in subtitle
    assert subtitle.endswith(f"page 4 of {pdf_view.page_count}")
    assert default_export_name(pdf_view) == \
        f"{pdf_view.path.stem}.reading-summary.pdf"
    assert summary_subtitle(None) == "No document open"


def test_summary_dialog_shows_rows_and_refreshes(pdf_view, db):
    from veyrion_workspace.ui.reading_summary import ReadingSummaryDialog

    dialog = ReadingSummaryDialog(None, view=pdf_view, db=db)
    try:
        assert dialog.table.rowCount() == len(gather_summary(pdf_view, db))
        metrics = [dialog.table.item(r, 0).text()
                   for r in range(dialog.table.rowCount())]
        assert metrics[0] == "Document"
        assert "Progress" in metrics
        assert pdf_view.path.name in dialog.windowTitle()

        pdf_view.go_to_page(1)
        fresh = dict(dialog.rows_for_export())
        assert fresh["Current page"] == f"2 of {pdf_view.page_count}"
    finally:
        dialog.deleteLater()


def test_summary_dialog_exports_and_prints(pdf_view, db, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from veyrion_workspace.ui.reading_summary import ReadingSummaryDialog

    sent = _capture_print(monkeypatch)
    toasts = _capture_toasts(monkeypatch)
    dialog = ReadingSummaryDialog(None, view=pdf_view, db=db)
    try:
        target = tmp_path / "summary.pdf"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(target),
                                                          "CSV table (*.csv)")))
        out = dialog.export()
        written = tmp_path / "summary.csv"
        assert out == written
        text = written.read_text(encoding="utf-8-sig")
        assert text.startswith("Metric,Value")
        assert pdf_view.path.name in text

        assert dialog.print_report() is True
        assert sent and Path(sent[0]).read_bytes().startswith(b"%PDF")
        assert _pdf_text(sent[0]).count("Metric") >= 1
        assert toasts and "Print job sent" in toasts[-1][1]
    finally:
        dialog.deleteLater()


def test_summary_dialog_export_cancel_is_a_noop(pdf_view, db, tmp_path,
                                                monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from veyrion_workspace.ui.reading_summary import ReadingSummaryDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    dialog = ReadingSummaryDialog(None, view=pdf_view, db=db)
    try:
        assert dialog.export() is None
        assert not list(tmp_path.glob("*.pdf"))
    finally:
        dialog.deleteLater()


# ------------------------------------------------------------ main window

def test_reading_summary_is_reachable_and_the_status_segment_opens_it(
        main_window, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    from veyrion_workspace.ui.reading_summary import ReadingSummaryDialog
    from veyrion_workspace.ui.shortcuts_dialog import collect_menu_shortcuts

    w, _settings = main_window
    assert w.act_reading_summary is not None
    labels = [row[1] for row in collect_menu_shortcuts(w.menuBar())]
    assert "Reading Summary…" in labels, "not reachable from the View menu"
    assert any(c.title == "Reading Summary…" for c in w._palette_commands())

    opened: list = []
    monkeypatch.setattr(ReadingSummaryDialog, "exec",
                        lambda self: opened.append(self) or 0)
    assert w._status_progress.toolTip()

    click = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPoint(4, 4),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    w._status_progress.mouseReleaseEvent(click)
    assert len(opened) == 1, "clicking the progress segment must open it"
    assert opened[0].windowTitle().startswith(TITLE)

    # The action itself opens it too (menu, palette, programmatic).
    w.action_reading_summary()
    assert len(opened) == 2
