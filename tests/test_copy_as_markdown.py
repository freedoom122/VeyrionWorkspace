"""Copy as Markdown: the clipboard route of the shared reference engine.

Every reference surface (shortcut sheet, annotations list, reading summary,
library panel) can put its rows on the clipboard as the exact Markdown table
its Export writes to disk. These tests pin the clipboard payload, the
file/clipboard equivalence, the empty-row guard, and each surface's wiring.
"""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from veyrion_workspace.ui.reference_export import (  # noqa: E402
    ANNOTATION_COLUMNS,
    LIBRARY_COLUMNS,
    SUMMARY_COLUMNS,
    build_markdown_table,
    copy_table_markdown,
    write_table_markdown,
)

STAMP_RE = re.compile(r"exported [\d-]+ [\d:]+ from")


def _norm(text: str) -> str:
    """Neutralise the export timestamp so two builds can be compared."""
    return STAMP_RE.sub("exported STAMP from", text)


@pytest.fixture()
def clipboard(qapp):
    """The app clipboard, cleared before and after each test."""
    clip = qapp.clipboard()
    clip.clear()
    yield clip
    clip.clear()


# --------------------------------------------------------------- engine ----

def test_empty_rows_are_refused_without_touching_the_clipboard(clipboard, qapp):
    clipboard.setText("sentinel")
    assert copy_table_markdown(None, [], title="X") is False
    assert clipboard.text() == "sentinel"


def test_clipboard_carries_both_flavors(clipboard):
    rows = [("Reading", "Next page", "Page Down")]
    cols = (("Where", 0.3), ("Action", 0.4), ("Shortcut", 0.3))
    assert copy_table_markdown(None, rows, title="Keyboard Shortcuts",
                               columns=cols) is True
    md = clipboard.mimeData()
    assert "text/markdown" in md.formats()
    assert "text/plain" in md.formats()
    # Both flavors carry the same characters.
    assert md.data("text/plain") == md.data("text/markdown")
    assert "Page Down" in clipboard.text()


def test_clipboard_text_equals_the_markdown_file(clipboard, tmp_path):
    rows = [("p.1", "Highlight", "Me", "a | tricky <cell>"),
            ("p.2", "Ink", "Me", "multi\nline")]
    cols = (("Page", .1), ("Type", .2), ("Author", .2), ("Annotation", .5))
    assert copy_table_markdown(None, rows, title="Annotations",
                               columns=cols) is True
    written = write_table_markdown(rows, tmp_path / "card.md",
                                   title="Annotations", columns=cols)
    assert _norm(clipboard.text()) == _norm(written.read_text(encoding="utf-8"))


def test_cell_escaping_survives_the_clipboard(clipboard):
    rows = [("a | b", "c\nd")]
    assert copy_table_markdown(None, rows, title="T",
                               columns=(("A", .5), ("B", .5))) is True
    body = [ln for ln in clipboard.text().splitlines() if ln.startswith("| a")]
    assert len(body) == 1, "a pipe or newline must not split the table row"


def test_build_matches_the_writer(clipboard, tmp_path):
    """build_markdown_table is the shared source of both routes."""
    rows = [("Where", "Action", "Shortcut")]
    cols = (("Where", .3), ("Action", .4), ("Shortcut", .3))
    built = build_markdown_table(rows, title="T", columns=cols)
    written = write_table_markdown(rows, tmp_path / "w.md", title="T",
                                   columns=cols)
    assert _norm(built) == _norm(written.read_text(encoding="utf-8"))


# -------------------------------------------------- shortcut sheet ----

def test_sheet_copy_button_copies_the_shown_rows(clipboard):
    from veyrion_workspace.ui.shortcuts_dialog import ShortcutsDialog

    dlg = ShortcutsDialog(None)          # parentless: hidden rows only
    try:
        assert dlg._copy_btn.text() == "Copy as Markdown"
        assert dlg._copy_markdown() is True
        text = clipboard.text()
        assert "# Keyboard Shortcuts" in text
        assert "Distraction-free mode" in text      # a hidden F8 row
    finally:
        dlg.deleteLater()


def test_sheet_copy_with_no_visible_rows_is_a_noop(clipboard):
    from veyrion_workspace.ui.shortcuts_dialog import ShortcutsDialog

    dlg = ShortcutsDialog(None)
    try:
        dlg._search.setText("zzz-no-such-shortcut")
        clipboard.setText("sentinel")
        assert dlg._copy_markdown() is False
        assert clipboard.text() == "sentinel"
    finally:
        dlg.deleteLater()


# --------------------------------------------- reading summary ----

def test_summary_copy_carries_the_report(clipboard, qapp, db):
    from veyrion_workspace.ui.reading_summary import ReadingSummaryDialog

    dlg = ReadingSummaryDialog(None, view=None, db=db)
    try:
        assert dlg.copy_markdown() is True
        text = clipboard.text()
        assert "# Reading Summary" in text
        assert "| Metric | Value |" in text
        assert "No document open" in text   # view-less: the one honest row
    finally:
        dlg.deleteLater()


# --------------------------------------------- annotations panel ----

@pytest.fixture()
def annotations_panel(qapp, data_dir, db):
    from veyrion_workspace.core.annotations.service import AnnotationService
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.storage.repositories import AnnotationRepository
    from veyrion_workspace.ui.panels.info_panels import AnnotationsPanel

    service = AnnotationService(AnnotationRepository(db), Settings())
    doc = "C:/books/field-notes.pdf"
    service.create(doc, 0, "highlight", text="important line")
    panel = AnnotationsPanel(service, Settings())
    panel.set_document_filter(doc)
    panel.reload()
    return panel


def test_annotations_copy_includes_the_marked_text(clipboard, annotations_panel):
    panel = annotations_panel
    try:
        assert panel.copy_card_markdown() is True
        text = clipboard.text()
        assert "important line" in text
        assert ANNOTATION_COLUMNS[0][0] in text          # "Page" header
    finally:
        pass


def test_annotations_copy_respects_the_filter(clipboard, annotations_panel):
    panel = annotations_panel
    panel._filter.setText("zzz-nothing")
    panel.reload()
    clipboard.setText("sentinel")
    assert panel.copy_card_markdown() is False
    assert clipboard.text() == "sentinel"


# ------------------------------------------------- library panel ----

def _library_panel(qapp, db):
    from veyrion_workspace.storage.repositories import LibraryRepository
    from veyrion_workspace.storage.repositories import DocumentRecord
    from veyrion_workspace.ui.panels.library_panel import LibraryPanel

    repo = LibraryRepository(db)
    rec = DocumentRecord(path="C:/books/alpha.pdf", title="Alpha",
                         author="A. Author", page_count=120, size_bytes=1)
    repo.upsert(rec)
    panel = LibraryPanel(repo, None, None)
    panel._refresh()
    return panel


def test_library_copy_includes_the_selection(clipboard, qapp, db):
    panel = _library_panel(qapp, db)
    try:
        assert panel.copy_selection_markdown() is True
        text = clipboard.text()
        assert "Alpha" in text
        assert LIBRARY_COLUMNS[0][0] in text             # "Title" header
    finally:
        panel.deleteLater()


def test_library_copy_on_empty_view_is_refused(clipboard, qapp, db):
    panel = _library_panel(qapp, db)
    try:
        panel._search.setText("zzz-no-such-book")
        panel.reload()
        clipboard.setText("sentinel")
        assert panel.copy_selection_markdown() is False
        assert clipboard.text() == "sentinel"
    finally:
        panel.deleteLater()
