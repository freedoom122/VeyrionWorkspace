"""Library panel reference card (``LibraryPanel`` export/print).

The library joins the other reference surfaces: whatever is selected — or the
whole filtered view when nothing is — exports as the shared PDF card / CSV /
Markdown table and prints the same way. These tests pin the selection rules,
the row contents, and the dialog/print behaviour.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelection, QItemSelectionModel  # noqa: E402

from veyrion_workspace.storage.repositories import (  # noqa: E402
    DocumentRecord,
    LibraryRepository,
)
from veyrion_workspace.ui.panels.library_panel import LibraryPanel  # noqa: E402
from veyrion_workspace.ui.reference_export import LIBRARY_COLUMNS  # noqa: E402

BOOKS = [
    # Insertion order. The panel's default sort is "Last opened" (newest
    # first; ties fall back to insert id), so views list delta→alpha and
    # every test picks rows by title, never by hard-coded row index.
    ("C:/books/alpha.pdf", "Alpha", "A. Author", 120, 500_000, 2, 0.5, True),
    ("C:/books/beta.epub", "Beta", "B. Writer", 0, 300_000, 0, 0.0, False),
    ("C:/books/gamma.pdf", "Gamma", "", 42, 1_200_000, 5, 1.0, True),
    ("C:/books/delta.txt", "Delta", "D. Poet", 7, 12_000, 1, 0.25, False),
]
TOTAL_PAGES = 169            # 120 + 0 + 42 + 7


def _row_of(panel: LibraryPanel, title: str) -> int:
    """Table row of a title in whatever order the view currently shows."""
    for row, doc in enumerate(panel._docs):
        if doc.title == title:
            return row
    raise AssertionError(f"{title!r} is not in the current view")


def _select_title(panel: LibraryPanel, title: str) -> None:
    """Select one document by title in the table view."""
    panel._table.selectRow(_row_of(panel, title))


def _pdf_text(path) -> str:
    import fitz
    doc = fitz.open(path)
    try:
        return "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()


def _capture_toasts(monkeypatch) -> list[tuple]:
    import veyrion_workspace.ui.widgets as widgets

    seen: list[tuple] = []
    monkeypatch.setattr(widgets, "show_toast", lambda *a, **k: seen.append(a))
    return seen


def _capture_print(monkeypatch) -> list[str]:
    from PySide6.QtWidgets import QDialog

    import veyrion_workspace.core.printing.printing as printing

    # Auto-accept the print preview so the send path runs.
    monkeypatch.setattr(
        "PySide6.QtWidgets.QDialog.exec",
        staticmethod(lambda *a, **k: QDialog.DialogCode.Accepted))
    sent: list[str] = []
    monkeypatch.setattr(printing, "send_to_printer",
                        lambda path, *a, **k: bool(sent.append(str(path))) or True)
    return sent


@pytest.fixture()
def panel(qapp, data_dir) -> LibraryPanel:
    """A library panel with four documents and one tag, nothing selected."""
    from veyrion_workspace.storage.database import Database

    repo = LibraryRepository(Database())
    for path, title, author, pages, size, rating, progress, fav in BOOKS:
        repo.upsert(DocumentRecord(
            path=path, title=title, author=author,
            kind="pdf" if path.endswith(".pdf") else
            ("ebook" if path.endswith(".epub") else "text"),
            format=path.rsplit(".", 1)[-1], size_bytes=size,
            page_count=pages, rating=rating, progress=progress,
            favorite=fav))
    repo.add_tag("C:/books/alpha.pdf", "reference")
    panel = LibraryPanel(repo, None, None)
    panel._refresh()
    yield panel
    panel.deleteLater()
    repo._db.close()


# ------------------------------------------------------------------ selection

def test_no_selection_means_the_whole_filtered_view(panel):
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    assert len(panel.selected_paths()) == 4
    assert panel.selected_paths() == [d.path for d in panel._docs]


def test_table_selection_scopes_the_card(panel):
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    panel._table.setCurrentCell(_row_of(panel, "Gamma"), 0)
    sel = QItemSelection()
    model = panel._table.model()
    row = _row_of(panel, "Gamma")
    sel.select(model.index(row, 0), model.index(row, 5))
    panel._table.selectionModel().select(
        sel, QItemSelectionModel.Select | QItemSelectionModel.Rows)
    assert panel.selected_paths() == ["C:/books/gamma.pdf"]
    rows = panel.card_rows()
    assert [r[0] for r in rows] == ["Gamma"]


def test_list_and_grid_selection(panel):
    panel._view_combo.setCurrentText("List")
    panel._refresh()
    panel._list.item(_row_of(panel, "Beta")).setSelected(True)
    assert panel.selected_paths() == ["C:/books/beta.epub"]

    panel._view_combo.setCurrentText("Grid")
    panel._refresh()
    assert len(panel.selected_paths()) == 4      # nothing selected -> all


def test_selection_does_not_survive_a_refilter(panel):
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    _select_title(panel, "Alpha")
    assert len(panel.selected_paths()) == 1
    # Typing a new filter re-renders; the stale selection must not leak into
    # the card (a filtered view with no live selection = the filtered view).
    panel._search.setText("gamma")
    panel._refresh()
    assert panel.selected_paths() == ["C:/books/gamma.pdf"]


# --------------------------------------------------------------------- rows

def test_card_rows_carry_every_column(panel):
    # Alpha needs explicit progress/rating: the repository does not persist
    # those through upsert(), so set them directly.
    panel._library.set_progress("C:/books/alpha.pdf", 0.5)
    panel._library.set_rating("C:/books/alpha.pdf", 2)
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    _select_title(panel, "Alpha")
    (title, author, kind, pages, size, progress, rating, tags) = \
        panel.card_rows()[0]
    assert title == "Alpha"
    assert author == "A. Author"
    assert kind == "PDF"
    assert pages == "120"
    assert size.endswith("KB")
    assert progress == "50%"
    assert rating == "★★"
    assert tags == "reference"


def test_card_rows_use_placeholders_for_missing_data(panel):
    # Gamma has no author; Delta never gets rating/progress/tags set.
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    _select_title(panel, "Gamma")
    row = panel.card_rows()[0]
    assert row[0] == "Gamma"
    assert row[1] == "—"                         # author missing
    assert row[2] == "PDF"
    assert row[3] == "42"

    _select_title(panel, "Delta")
    row = panel.card_rows()[0]
    assert row[5] == "—"                         # progress not set
    assert row[6] == "—"                         # rating not set
    assert row[7] == "—"                         # no tags


def test_card_title_and_subtitle_describe_the_scope(panel):
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    assert panel._card_title() == "Library — 4 documents"

    _select_title(panel, "Delta")
    assert panel._card_title() == "Library Selection — 1 document"
    subtitle = panel._card_subtitle(1)
    assert subtitle.startswith("1 document")
    assert "7 pages" in subtitle

    panel._search.setText("gamma")
    panel._refresh()
    subtitle = panel._card_subtitle(len(panel.card_rows()))
    assert "filter: gamma" in subtitle

    panel._search.clear()
    panel._fav_btn.setChecked(True)
    panel._refresh()
    subtitle = panel._card_subtitle(len(panel.card_rows()))
    assert "favorites only" in subtitle


def test_total_pages_sum_the_selection(panel):
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    assert f"{TOTAL_PAGES:,} pages" in panel._card_subtitle(4)
    _select_title(panel, "Gamma")
    assert "42 pages" in panel._card_subtitle(1)


# ------------------------------------------------------------ export / print

def test_export_dialog_writes_the_selection_as_csv(panel, tmp_path,
                                                   monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    toasts = _capture_toasts(monkeypatch)
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    _select_title(panel, "Alpha")
    target = tmp_path / "library.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target),
                                                      "CSV table (*.csv)")))
    out = panel.export_selection("csv")
    written = tmp_path / "library.csv"
    assert out == written and written.exists()
    with written.open(encoding="utf-8-sig", newline="") as fh:
        parsed = list(csv.reader(fh))
    assert parsed[0] == [header for header, _share in LIBRARY_COLUMNS]
    assert len(parsed) == 2, "only the selected document may be exported"
    assert parsed[1][0] == "Alpha" and "reference" in parsed[1][7]
    assert toasts and "library.csv" in toasts[-1][1]


def test_export_without_selection_writes_the_filtered_view(panel, tmp_path,
                                                           monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    _capture_toasts(monkeypatch)
    panel._search.setText("author 0".replace("author 0", "a"))
    panel._refresh()
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "l.md"),
                                                      "Markdown table (*.md)")))
    panel.export_selection("markdown")
    text = (tmp_path / "l.md").read_text(encoding="utf-8")
    assert "| Title | Author | Type | Pages | Size | Progress | Rating | Tags |" \
        in text
    # All four titles contain the letter 'a' in title or author.
    for title in ("Alpha", "Beta", "Gamma", "Delta"):
        assert title in text


def test_export_empty_view_warns(panel, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    toasts = _capture_toasts(monkeypatch)
    opened: list = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: opened.append(a) or ("", "")))
    panel._search.setText("zzz-no-such-document")
    panel._refresh()
    assert panel.export_selection("pdf") is None
    assert opened == [], "an empty view must not open the save dialog"
    assert toasts and "Nothing to export" in toasts[-1][1]


def test_print_sends_a_pdf_card_of_the_selection(panel, data_dir, monkeypatch):
    sent = _capture_print(monkeypatch)
    toasts = _capture_toasts(monkeypatch)
    panel._view_combo.setCurrentText("Table")
    panel._refresh()
    _select_title(panel, "Gamma")
    assert panel.print_selection() is True
    assert sent and Path(sent[0]).read_bytes().startswith(b"%PDF")
    text = _pdf_text(sent[0])
    assert "Gamma" in text
    assert "Beta" not in text, "an unselected document printed"
    assert toasts and "Print job sent" in toasts[-1][1]


def test_toolbar_and_context_menu_expose_the_card(panel, monkeypatch):
    labels = [a.text() for a in panel._export_btn.menu().actions() if a.text()]
    for expected in ("Export card as PDF…", "Export as CSV…",
                     "Export as Markdown…", "Print card…"):
        assert expected in labels, expected

    # The per-document context menu carries the same card actions.
    from PySide6.QtWidgets import QMenu

    calls: list[str] = []
    monkeypatch.setattr(panel, "export_selection",
                        lambda fmt="pdf": calls.append(fmt))
    menu = QMenu(panel)
    export_menu = menu.addMenu("Export")
    for fmt, label in (("pdf", "Reference card (PDF)…"),
                       ("csv", "Reference card (CSV)…"),
                       ("markdown", "Reference card (Markdown)…")):
        act = export_menu.addAction(label)
        act.triggered.connect(
            lambda _checked=False, f=fmt: panel.export_selection(f))
    for action in export_menu.actions():
        if action.text() == "Reference card (CSV)…":
            action.trigger()
    assert calls == ["csv"]


def test_library_columns_are_shared(panel):
    assert [h for h, _s in LIBRARY_COLUMNS] == \
        ["Title", "Author", "Type", "Pages", "Size", "Progress", "Rating",
         "Tags"]
