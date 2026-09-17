"""Shared reference-export engine (`ui.reference_export`).

The shortcut sheet, the annotations list and the reading summary all export
through this module, so its layout, escaping and error handling are pinned
here rather than three times over.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from veyrion_workspace.ui.reference_export import (  # noqa: E402
    SUMMARY_COLUMNS,
    canonical_path,
    export_dialog,
    export_table,
    format_for,
    headers_for,
    print_table,
    write_table_csv,
    write_table_markdown,
    write_table_pdf,
)


def _pdf_text(path) -> str:
    import fitz
    doc = fitz.open(path)
    try:
        return "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()


def _long_text(words: int = 160) -> str:
    return " ".join(f"word{i}" for i in range(words))


@pytest.fixture()
def parent(qapp):
    """A real widget to parent the dialogs/toasts the helpers create."""
    from PySide6.QtWidgets import QWidget

    widget = QWidget()
    yield widget
    widget.deleteLater()


# ----------------------------------------------------------------- PDF card

def test_card_layout_pagination_and_footers(tmp_path):
    rows = [("Group " + str(i // 25), f"Item number {i}", f"Ctrl+Alt+F{i % 12}")
            for i in range(300)]
    out = write_table_pdf(rows, tmp_path / "card.pdf", title="Everything",
                          subtitle="300 rows")

    import fitz
    doc = fitz.open(out)
    try:
        assert doc.page_count > 1, "long tables must flow onto extra pages"
        assert doc[0].rect.width == pytest.approx(595.0)   # A4 portrait
        assert doc[0].rect.height == pytest.approx(842.0)
        for page in doc:
            text = page.get_text()
            assert "Where" in text and "Action" in text and "Shortcut" in text
            assert "Veyrion Workspace" in text
        assert "Page 1 of" in doc[0].get_text()
        assert f"Page 2 of {doc.page_count}" in doc[1].get_text()
    finally:
        doc.close()

    text = _pdf_text(out)
    assert "Item number 0" in text
    assert "Item number 299" in text, "the last row fell off the card"
    assert "Everything" in text and "300 rows" in text


def test_card_wraps_long_cells_inside_their_column(tmp_path):
    body = _long_text(200)
    out = write_table_pdf([("p.1", body)], tmp_path / "wrap.pdf",
                          title="Wrap", columns=(("Page", 0.15),
                                                 ("Annotation", 0.85)))
    text = _pdf_text(out)
    assert "word0" in text and "word199" in text
    # No line may run off the page: every wrapped line fits the text area.
    import fitz
    doc = fitz.open(out)
    try:
        page = doc[0]
        for block in page.get_text("blocks"):
            assert block[2] <= 595.0 - 40, "a column overflowed the margin"
    finally:
        doc.close()


def test_card_headers_follow_the_column_spec(tmp_path):
    out = write_table_pdf([("Pages", "12")], tmp_path / "summary.pdf",
                          columns=SUMMARY_COLUMNS)
    text = _pdf_text(out)
    assert "Metric" in text and "Value" in text
    assert "Shortcut" not in text


def test_card_with_no_rows_still_writes(tmp_path):
    out = write_table_pdf([], tmp_path / "empty.pdf",
                          empty_message="Nothing here yet.")
    text = _pdf_text(out)
    assert "Nothing here yet." in text
    assert "Page 1 of 1" in text


def test_card_survives_rows_with_missing_or_extra_cells(tmp_path):
    rows = [("only-one",), ("three", "cells", "here"), ("", "", "")]
    out = write_table_pdf(rows, tmp_path / "ragged.pdf")
    text = _pdf_text(out)
    assert "only-one" in text and "three" in text and "here" in text


# ------------------------------------------------------------- CSV / Markdown

def test_csv_uses_the_column_headers(tmp_path):
    import csv
    out = write_table_csv([("Pages", "12")], tmp_path / "s.csv",
                          columns=SUMMARY_COLUMNS)
    with out.open(encoding="utf-8-sig", newline="") as fh:
        parsed = list(csv.reader(fh))
    assert parsed == [["Metric", "Value"], ["Pages", "12"]]
    assert headers_for(None) == ["Where", "Action", "Shortcut"]


def test_markdown_uses_headers_subtitle_and_empty_note(tmp_path):
    out = write_table_markdown([("Pages", "12")], tmp_path / "s.md",
                               title="Reading Summary",
                               subtitle="field-notes.pdf · 58% read",
                               columns=SUMMARY_COLUMNS)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Reading Summary")
    assert "field-notes.pdf · 58% read" in text
    assert "| Metric | Value |" in text
    assert "| --- | --- |" in text
    assert "| Pages | 12 |" in text

    empty = write_table_markdown([], tmp_path / "e.md",
                                 columns=SUMMARY_COLUMNS,
                                 empty_message="No data.")
    assert "No data." in empty.read_text(encoding="utf-8")


def test_export_table_dispatches_on_format(tmp_path):
    rows = [("Pages", "12")]
    pdf = export_table(rows, tmp_path / "x.pdf", "pdf")
    assert pdf.read_bytes().startswith(b"%PDF")
    csv_path = export_table(rows, tmp_path / "x.csv", "csv")
    assert csv_path.read_text(encoding="utf-8-sig").startswith("Where,Action")
    md_path = export_table(rows, tmp_path / "x.md", "markdown")
    assert md_path.read_text(encoding="utf-8").startswith("# Reference")
    # An unknown format falls back to the card rather than raising.
    assert export_table(rows, tmp_path / "y.zzz", "nonsense") \
        .read_bytes().startswith(b"%PDF")


# ------------------------------------------------------------- format helpers

@pytest.mark.parametrize("path, selected, expected", [
    ("x.pdf", "PDF card (*.pdf)", "pdf"),
    ("x", "CSV table (*.csv)", "csv"),
    ("x", "Markdown table (*.md)", "markdown"),
    ("x.csv", "", "csv"),
    ("x.md", "", "markdown"),
    ("x.markdown", "", "markdown"),
    ("x.txt", "", "csv"),
    ("x", "", "pdf"),
    ("x.pdf", "Markdown table (*.md)", "markdown"),
])
def test_format_detection(path, selected, expected):
    assert format_for(path, selected) == expected


def test_canonical_path_corrects_the_suffix():
    assert canonical_path("card.pdf", "csv").name == "card.csv"
    assert canonical_path("card", "csv").name == "card.csv"
    assert canonical_path("card.md", "pdf").name == "card.pdf"
    assert canonical_path("a.b/card.pdf", "pdf").name == "card.pdf"
    # Already correct: untouched.
    assert str(canonical_path("card.csv", "csv")) == "card.csv"


# --------------------------------------------------------------- save dialog

def test_export_dialog_writes_and_toasts(tmp_path, monkeypatch, parent):
    from PySide6.QtWidgets import QFileDialog

    import veyrion_workspace.ui.widgets as widgets

    toasts: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast",
                        lambda *a, **k: toasts.append(a))
    target = tmp_path / "card.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target),
                                                      "CSV table (*.csv)")))
    out = export_dialog(parent, [("Pages", "12")], title="Summary",
                        default_name="card.pdf", columns=SUMMARY_COLUMNS)
    written = tmp_path / "card.csv"
    assert out == written and written.exists()
    assert written.read_text(encoding="utf-8-sig").startswith("Metric,Value")
    assert toasts and "card.csv" in toasts[-1][1]


def test_export_dialog_forced_format_ignores_the_filter(tmp_path, monkeypatch,
                                                       qapp):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "card.pdf"),
                                                      "CSV table (*.csv)")))
    out = export_dialog(None, [("a", "b")], title="t",
                        default_name="card.pdf", fmt="pdf")
    assert out is not None
    assert out is not None and out.read_bytes().startswith(b"%PDF")


def test_export_dialog_empty_warns_without_a_dialog(monkeypatch, parent):
    from PySide6.QtWidgets import QFileDialog

    import veyrion_workspace.ui.widgets as widgets

    calls: list = []
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (calls.append(a), ("", ""))[1]))
    toasts: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast",
                        lambda *a, **k: toasts.append(a))

    assert export_dialog(parent, [], title="t", default_name="x.pdf") is None
    assert calls == [], "an empty export must not open the save dialog"
    assert toasts and "Nothing to export" in toasts[-1][1]


def test_export_dialog_cancel_writes_nothing(tmp_path, monkeypatch, parent):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    out = export_dialog(parent, [("a", "b")], title="t",
                        default_name="x.pdf")
    assert out is None
    assert not list(tmp_path.iterdir())


def test_export_dialog_reports_a_write_failure(monkeypatch, parent):
    from PySide6.QtWidgets import QFileDialog

    import veyrion_workspace.ui.widgets as widgets

    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("/nope/denied/x.pdf",
                                                      "PDF card (*.pdf)")))
    toasts: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast",
                        lambda *a, **k: toasts.append(a))
    assert export_dialog(parent, [("a", "b")], title="t",
                         default_name="x.pdf") is None
    assert toasts and "Could not export" in toasts[-1][1]


def test_export_helpers_tolerate_a_parentless_surface(monkeypatch, qapp):
    """No parent widget (a standalone panel) must not raise."""
    assert export_dialog(None, [], title="t", default_name="x.pdf") is None
    assert print_table(None, [], title="t") is False


# ------------------------------------------------------------------- printing

def test_print_table_builds_a_card_and_hands_it_over(data_dir, monkeypatch,
                                                     parent):
    from PySide6.QtWidgets import QDialog

    import veyrion_workspace.core.printing.printing as printing
    import veyrion_workspace.ui.widgets as widgets

    # Auto-accept the print preview so the send path runs.
    monkeypatch.setattr(
        "PySide6.QtWidgets.QDialog.exec",
        staticmethod(lambda *a, **k: QDialog.DialogCode.Accepted))
    sent: list[str] = []
    monkeypatch.setattr(printing, "send_to_printer",
                        lambda path, *a, **k: bool(sent.append(str(path)))
                        or True)
    toasts: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast",
                        lambda *a, **k: toasts.append(a))

    assert print_table(parent, [("Pages", "12")], title="Reading Summary",
                       columns=SUMMARY_COLUMNS) is True
    assert sent and Path(sent[0]).read_bytes().startswith(b"%PDF")
    text = _pdf_text(sent[0])
    assert "Metric" in text and "12" in text
    assert toasts and "Print job sent" in toasts[-1][1]
    # The temp card stays inside the app's own data directory.
    assert str(data_dir) in sent[0]


def test_print_table_warns_when_no_printer_is_reachable(data_dir, monkeypatch,
                                                        parent):
    from PySide6.QtWidgets import QDialog

    import veyrion_workspace.core.printing.printing as printing

    monkeypatch.setattr(
        "PySide6.QtWidgets.QDialog.exec",
        staticmethod(lambda *a, **k: QDialog.DialogCode.Accepted))
    warnings: list[tuple] = []
    monkeypatch.setattr(printing, "send_to_printer", lambda *a, **k: False)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        staticmethod(lambda *a, **k: warnings.append(a)))

    assert print_table(parent, [("a", "b")], title="t") is False
    assert warnings, "a failed print must tell the user"


def test_print_table_with_no_rows_never_touches_a_printer(data_dir, monkeypatch,
                                                          parent):
    import veyrion_workspace.core.printing.printing as printing
    import veyrion_workspace.ui.widgets as widgets

    calls: list = []
    monkeypatch.setattr(printing, "send_to_printer",
                        lambda *a, **k: calls.append(a) or True)
    toasts: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast",
                        lambda *a, **k: toasts.append(a))

    assert print_table(parent, [], title="t") is False
    assert calls == []
    assert toasts and "Nothing to print" in toasts[-1][1]


# ------------------------------------------------- print preview

def test_preview_shows_exactly_what_will_print(parent, monkeypatch):
    """The preview renders the very file that approval hands to the printer."""
    from PySide6.QtWidgets import QDialog

    import veyrion_workspace.core.printing.printing as printing
    import veyrion_workspace.ui.reference_export as rx

    sent: list[str] = []

    # Drive print_table with a preview that captures its card path.
    captured: list = []

    class _Spy(rx.ReferencePrintPreview):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            captured.append(self)

    monkeypatch.setattr(rx, "ReferencePrintPreview", _Spy)
    monkeypatch.setattr(printing, "send_to_printer",
                        lambda path, *a, **k: bool(sent.append(str(path)))
                        or True)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QDialog.exec",
        staticmethod(lambda *a, **k: QDialog.DialogCode.Accepted))

    rows = [(f"Item number {i}", "key") for i in range(120)]  # multi-page
    assert print_table(parent, rows, title="Big Card") is True
    assert len(captured) == 1 and sent, "approval must send the previewed card"
    pv = captured[0]
    assert pv.windowTitle() == "Print preview — Big Card"
    # The printed file is the previewed file: same path, real card content.
    assert Path(sent[0]) == pv._card_path
    text = _pdf_text(pv._card_path)
    assert "Big Card" in text and "Item number 119" in text
    # The preview label holds a rendered image of the current page.
    assert not pv._preview.pixmap().isNull()


def test_preview_pager_flows_through_multi_page_cards(parent):
    import veyrion_workspace.ui.reference_export as rx

    rows = [(f"Item number {i}", "key") for i in range(300)]
    card = _tmp_card(parent)
    assert rx.write_table_pdf(rows, card, title="Pager")
    pv = rx.ReferencePrintPreview(parent, card, title="Pager")
    try:
        total = pv._doc.page_count
        assert total > 1
        assert pv._page_label.text() == "Page 1 of %d" % total
        assert not pv._prev_btn.isEnabled()
        pv._go_next()
        assert pv._page_label.text() == "Page 2 of %d" % total
        assert pv._prev_btn.isEnabled()
        assert not pv._next_btn.isEnabled() if total == 2 else True
        pv._go_prev()
        assert pv._page_label.text() == "Page 1 of %d" % total
    finally:
        pv._doc.close()
        pv.deleteLater()


def test_preview_cancel_sends_nothing(parent, monkeypatch):
    from PySide6.QtWidgets import QDialog

    import veyrion_workspace.core.printing.printing as printing

    monkeypatch.setattr(
        "PySide6.QtWidgets.QDialog.exec",
        staticmethod(lambda *a, **k: QDialog.DialogCode.Rejected))
    calls: list = []
    monkeypatch.setattr(printing, "send_to_printer",
                        lambda *a, **k: calls.append(a) or True)

    assert print_table(parent, [("a", "b")], title="t") is False
    assert calls == [], "cancelling the preview must not print"


def test_print_table_still_toasts_success_and_warns_without_a_printer(
        parent, monkeypatch):
    """The preview sits in front of the same toast/warn send block."""
    from PySide6.QtWidgets import QDialog

    import veyrion_workspace.core.printing.printing as printing
    import veyrion_workspace.ui.widgets as widgets

    monkeypatch.setattr(
        "PySide6.QtWidgets.QDialog.exec",
        staticmethod(lambda *a, **k: QDialog.DialogCode.Accepted))

    toasts: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast", lambda *a, **k: toasts.append(a))
    monkeypatch.setattr(printing, "send_to_printer", lambda *a, **k: True)
    assert print_table(parent, [("a", "b")], title="t") is True
    assert toasts and "Print job sent" in toasts[-1][1]

    warnings: list[tuple] = []
    monkeypatch.setattr(printing, "send_to_printer", lambda *a, **k: False)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        staticmethod(lambda *a, **k: warnings.append(a)))
    assert print_table(parent, [("a", "b")], title="t") is False
    assert warnings


def _tmp_card(parent):
    from veyrion_workspace.utils.safeio import make_temp_file

    return Path(make_temp_file(suffix="_reference.pdf"))
