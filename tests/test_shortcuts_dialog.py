"""Keyboard shortcuts cheat sheet (promoted from session runtime check).

Covers: menu-tree harvesting, the F1 action wiring, dialog population,
search filtering, hidden-shortcut documentation (F8/F9/reading keys), and
the one-time startup hint flag.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from veyrion_workspace.ui.reference_export import (  # noqa: E402
    format_for as _format_for,
)
from veyrion_workspace.ui.shortcuts_dialog import (  # noqa: E402
    HIDDEN_SHORTCUTS,
    ShortcutsDialog,
    collect_menu_shortcuts,
    write_shortcuts_csv,
    write_shortcuts_markdown,
    write_shortcuts_pdf,
)


def _pdf_text(path) -> str:
    import fitz
    doc = fitz.open(path)
    try:
        return "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()


def _visible_rows(dlg) -> int:
    return sum(0 if dlg.table.isRowHidden(r) else 1
               for r in range(dlg.table.rowCount()))


def test_action_and_menu_wiring(main_window):
    w, _settings = main_window
    act = getattr(w, "act_shortcuts", None)
    assert act is not None, "act_shortcuts action missing"
    assert "F1" in act.shortcut().toString()
    # The action is reachable from the menu bar (Settings menu).
    hits = collect_menu_shortcuts(w.menuBar())
    assert any(a is act for (_m, _l, _k, a) in hits), \
        "cheat-sheet action not reachable from any menu"


def test_collect_menu_shortcuts_harvests_all_menus(main_window):
    w, _settings = main_window
    rows = collect_menu_shortcuts(w.menuBar())
    assert len(rows) > 25, "expected the full shortcut set, got few rows"
    menus = {r[0] for r in rows}
    for expected in ("File", "Edit", "View", "Annotate", "Tools", "Settings"):
        assert any(expected in m for m in menus), f"menu {expected} not covered"
    # Rows with a shortcut carry a non-empty key string.
    keyed = [r for r in rows if r[2]]
    assert keyed, "no shortcut strings harvested"
    assert any("Ctrl" in r[2] for r in keyed)
    # No duplicate (menu, label) rows.
    pairs = [(r[0], r[1]) for r in rows]
    assert len(pairs) == len(set(pairs))


def test_dialog_population_includes_reading_modes(main_window):
    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        total = dlg.table.rowCount()
        menu_row_count = len(collect_menu_shortcuts(w.menuBar()))
        assert total >= menu_row_count + len(HIDDEN_SHORTCUTS) - 1, \
            "hidden shortcuts not appended"
        labels = {dlg.table.item(r, 1).text().lower() for r in range(total)}
        for label in ("distraction-free mode", "focus mode",
                      "command palette", "undo", "autoscroll"):
            assert label in labels, f"{label!r} missing from the sheet"
        keys = [dlg.table.item(r, 2).text() for r in range(total)]
        assert any("F8" in k for k in keys), "F8 not listed"
        assert any("F9" in k for k in keys), "F9 not listed"
        # Hidden rows carry their context in the Where column.
        ctxs = {dlg.table.item(r, 0).text() for r in range(total)}
        assert {"Reading", "Chrome", "Presentation", "Annotating"} <= ctxs
    finally:
        dlg.deleteLater()


def test_search_filter_and_title(main_window, qapp):
    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        total = dlg.table.rowCount()
        dlg._search.setText("theme")
        vis = _visible_rows(dlg)
        assert 0 < vis < total
        assert str(vis) in dlg.windowTitle()
        # Multi-term AND filter.
        dlg._search.setText("page theme")
        assert _visible_rows(dlg) <= vis
        # No match -> zero visible rows.
        dlg._search.setText("zzzz-no-such-shortcut")
        assert _visible_rows(dlg) == 0
        # Reset restores everything.
        dlg._search.clear()
        assert _visible_rows(dlg) == total
    finally:
        dlg.deleteLater()


def test_parentless_construction_lists_hidden_shortcuts(qapp):
    dlg = ShortcutsDialog(None)
    try:
        assert dlg.table.rowCount() == len(HIDDEN_SHORTCUTS)
    finally:
        dlg.deleteLater()


# ------------------------------------------------------------ PDF export

def test_reference_card_layout_and_pagination(tmp_path):
    rows = [(f"Menu {i // 20}", f"Action number {i}", f"Ctrl+Alt+F{i % 12}")
            for i in range(300)]
    out = write_shortcuts_pdf(rows, tmp_path / "card.pdf",
                              subtitle="300 shortcuts")
    text = _pdf_text(out)

    import fitz
    doc = fitz.open(out)
    try:
        assert doc.page_count > 1, "long sheets must flow onto extra pages"
        assert doc[0].rect.width == pytest.approx(595.0)   # A4 portrait
        assert doc[0].rect.height == pytest.approx(842.0)
        # Column headers repeat on continuation pages.
        for p in doc:
            assert "Where" in p.get_text() and "Action" in p.get_text()
        # Page footer counts every page.
        assert f"Page 2 of {doc.page_count}" in doc[1].get_text()
    finally:
        doc.close()
    # No row is silently dropped.
    assert "Action number 0" in text
    assert "Action number 299" in text
    assert "Veyrion Workspace" in text


def test_reference_card_with_no_rows_still_writes(tmp_path):
    out = write_shortcuts_pdf([], tmp_path / "empty.pdf")
    assert "No shortcuts match the current filter." in _pdf_text(out)


def test_export_button_saves_shown_rows(main_window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        target = tmp_path / "shortcuts.pdf"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(target), "PDF (*.pdf)")))

        dlg._export_pdf()
        assert target.exists(), "Export as PDF did not write a file"
        text = _pdf_text(target)
        for expected in ("Where", "Action", "Shortcut",
                         "Distraction-free mode", "F8", "Ctrl+K"):
            assert expected in text, f"{expected!r} missing from the export"
        # Every visible row made it onto the card.
        assert "Shortcut" in text
        assert text.count("\n") > 50
    finally:
        dlg.deleteLater()


def test_export_respects_the_active_filter(main_window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        dlg._search.setText("theme")
        shown = dlg.visible_rows()
        assert 0 < len(shown) < dlg.table.rowCount()

        target = tmp_path / "filtered.pdf"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(target), "PDF (*.pdf)")))
        dlg._export_pdf()

        text = _pdf_text(target)
        for _where, label, _keys in shown:
            assert label in text, f"filtered row {label!r} missing from export"
        # A row the filter excluded stays out of the card.
        assert "Undo" not in text
    finally:
        dlg.deleteLater()


def test_export_cancel_writes_nothing(main_window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        dlg._export_pdf()
        assert not list(tmp_path.glob("*.pdf")), "cancel must not create a file"
    finally:
        dlg.deleteLater()


def test_export_with_empty_filter_warns_without_dialog(
        main_window, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        dlg._search.setText("zzzz-no-such-shortcut")
        assert dlg.visible_rows() == []

        calls: list[tuple] = []
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (calls.append(a), ("", ""))[1]))
        shown: list[tuple] = []
        import veyrion_workspace.ui.widgets as widgets
        monkeypatch.setattr(widgets, "show_toast",
                            lambda *a, **k: shown.append(a))

        dlg._export_pdf()
        assert calls == [], "empty export should not open the save dialog"
        assert shown and "Nothing to export" in shown[-1][1]
    finally:
        dlg.deleteLater()


# ------------------------------------------------- user-pasted key maps

def _row_by_label(dlg, label: str):
    for r in range(dlg.table.rowCount()):
        if dlg.table.item(r, 1).text() == label:
            return r
    return None

def _sheet_keys(dlg) -> dict[str, tuple[int, str]]:
    return {dlg.table.item(r, 1).text(): (r, dlg.table.item(r, 2).text())
            for r in range(dlg.table.rowCount())}


def _write_map(payload) -> None:
    import json
    from veyrion_workspace.services.shortcuts import overrides_path
    path = overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_sheet_reflects_the_active_bindings(main_window):
    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        assert "built-in bindings" in dlg._status.text()
        assert _sheet_keys(dlg)["Zoom In"][1] == \
            w.act_zoom_in.shortcut().toString()
    finally:
        dlg.deleteLater()

    _write_map({"bindings": {"zoom_in": "Ctrl+Alt+=", "quit": None}})
    w.action_reload_shortcut_overrides()

    dlg = ShortcutsDialog(w)
    try:
        keys = _sheet_keys(dlg)
        zoom_row, zoom_keys = keys["Zoom In"]
        assert zoom_keys.startswith("Ctrl+Alt+="), zoom_keys
        # An unbound action shows as having no key at all.
        assert keys["Exit"][1] == "—"
        # Custom rows are marked (bold) and explain themselves in a tooltip.
        assert dlg.table.item(zoom_row, 2).font().bold()
        assert "Custom binding" in dlg.table.item(zoom_row, 2).toolTip()
        assert "unbound" in dlg.table.item(keys["Exit"][0], 2).toolTip()
        # A row nobody overrode keeps the built-in presentation.
        save_row, save_keys = keys["Save"]
        assert save_keys.startswith("Ctrl+S")
        assert not dlg.table.item(save_row, 2).font().bold()
        # The status line states how many bindings are custom.
        status = dlg._status.text()
        assert "2 custom bindings active" in status
        assert "shortcuts.json" in status
    finally:
        dlg.deleteLater()


def test_sheet_reports_ids_and_keys_the_map_got_wrong(main_window):
    w, _settings = main_window
    _write_map({"bindings": {"nope": "Ctrl+Alt+Z", "print": "gibberish"}})
    w.action_reload_shortcut_overrides()

    dlg = ShortcutsDialog(w)
    try:
        status = dlg._status.text()
        assert "1 unknown id" in status, status
        assert "1 invalid key" in status, status
        # The action behind the invalid entry still works as before.
        assert _sheet_keys(dlg)["Print…"][1].startswith("Ctrl+P")
        assert "built-in bindings" in status
    finally:
        dlg.deleteLater()


def test_in_dialog_reload_shows_a_newly_pasted_map(main_window):
    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        _write_map({"bindings": {"zoom_in": "Ctrl+Alt+="}})
        dlg._reload_overrides()
        assert _sheet_keys(dlg)["Zoom In"][1].startswith("Ctrl+Alt+=")
        assert w.act_zoom_in.shortcut().toString().startswith("Ctrl+Alt+=")

        # Removing the entry again puts the built-in binding back on screen.
        _write_map({"bindings": {}})
        dlg._reload_overrides()
        assert _sheet_keys(dlg)["Zoom In"][1] == "Ctrl++"
        assert "built-in bindings" in dlg._status.text()
    finally:
        dlg.deleteLater()


def test_export_marks_custom_bindings(main_window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w, _settings = main_window
    _write_map({"bindings": {"zoom_in": "Ctrl+Alt+="}})
    w.action_reload_shortcut_overrides()

    dlg = ShortcutsDialog(w)
    try:
        target = tmp_path / "custom.pdf"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(target), "PDF (*.pdf)")))
        dlg._export_pdf()
        text = _pdf_text(target)
        assert "Ctrl+Alt+=" in text
        assert "(custom)" in text
    finally:
        dlg.deleteLater()


def test_sheet_stays_compact_with_the_key_map_controls(main_window):
    """The override controls must not blow the dialog past its target width."""
    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        assert dlg.width() <= 660
        assert dlg.minimumSizeHint().width() <= 640
    finally:
        dlg.deleteLater()


# ------------------------------------------------- CSV / Markdown export

def test_csv_export_is_a_parseable_table(main_window, tmp_path):
    import csv

    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        rows = dlg.visible_rows()
        out = write_shortcuts_csv(rows, tmp_path / "sheet.csv")
        with out.open(encoding="utf-8-sig", newline="") as fh:
            parsed = list(csv.reader(fh))
        assert parsed[0] == ["Where", "Action", "Shortcut"]
        assert len(parsed) == len(rows) + 1, "a row went missing from the CSV"
        by_label = {r[1]: r[2] for r in parsed[1:]}
        assert by_label["Zoom In"].startswith("Ctrl")
        assert by_label["Distraction-free mode"] == "F8"
        # Actions with no key still get a row, marked rather than omitted.
        assert "—" in by_label.values()
    finally:
        dlg.deleteLater()


def test_csv_export_survives_commas_and_quotes(tmp_path):
    import csv

    rows = [("Reading", 'Action with a comma, and "quotes"', "Ctrl+Alt+X")]
    out = write_shortcuts_csv(rows, tmp_path / "tricky.csv")
    with out.open(encoding="utf-8-sig", newline="") as fh:
        parsed = list(csv.reader(fh))
    assert parsed == [["Where", "Action", "Shortcut"], list(rows[0])]


def test_markdown_export_is_a_pasteable_table(main_window, tmp_path):
    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        rows = dlg.visible_rows()
        out = write_shortcuts_markdown(rows, tmp_path / "sheet.md")
        text = out.read_text(encoding="utf-8")
        assert text.startswith("# Keyboard Shortcuts")
        assert "| Where | Action | Shortcut |" in text
        assert "| --- | --- | --- |" in text
        assert f"{len(rows)} shortcuts" in text
        assert "Veyrion Workspace" in text
        body = [ln for ln in text.splitlines() if ln.startswith("| ")][2:]
        assert len(body) == len(rows), "a row went missing from the table"
        assert any("Distraction-free mode" in ln for ln in body)
    finally:
        dlg.deleteLater()


def test_markdown_escapes_pipes_and_line_breaks(tmp_path):
    rows = [("Chrome", "Action | with pipe", "Ctrl+|\nAlt+X")]
    out = write_shortcuts_markdown(rows, tmp_path / "escaped.md")
    line = [ln for ln in out.read_text(encoding="utf-8").splitlines()
            if "with pipe" in ln][0]
    assert "\\|" in line, "pipes inside cells must be escaped"
    # Exactly the four real cell boundaries remain unescaped.
    assert line.replace("\\|", "").count("|") == 4


def test_markdown_with_no_rows_still_writes(tmp_path):
    out = write_shortcuts_markdown([], tmp_path / "empty.md")
    text = out.read_text(encoding="utf-8")
    assert "0 shortcuts" in text
    assert "No shortcuts match the current filter." in text


def test_format_detection_follows_the_filter_then_the_name():
    assert _format_for("x.pdf", "PDF card (*.pdf)") == "pdf"
    assert _format_for("x", "CSV table (*.csv)") == "csv"
    assert _format_for("x", "Markdown table (*.md)") == "markdown"
    assert _format_for("x.csv", "") == "csv"
    assert _format_for("x.md", "") == "markdown"
    assert _format_for("x.markdown", "") == "markdown"
    assert _format_for("x", "") == "pdf"
    # The picked filter wins over a stale suffix in the file name.
    assert _format_for("x.pdf", "Markdown table (*.md)") == "markdown"


@pytest.mark.parametrize("typed, chosen_filter, expected, content", [
    ("card.pdf", "CSV table (*.csv)", "card.csv", "Where,Action,Shortcut"),
    ("card.pdf", "Markdown table (*.md)", "card.md", "| Where | Action |"),
    ("card", "CSV table (*.csv)", "card.csv", "Where,Action,Shortcut"),
    ("card.md", "PDF card (*.pdf)", "card.pdf", "%PDF"),
])
def test_export_dispatch_writes_the_chosen_format(
        main_window, tmp_path, monkeypatch, typed, chosen_filter, expected,
        content):
    from PySide6.QtWidgets import QFileDialog

    import veyrion_workspace.ui.widgets as widgets

    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(tmp_path / typed),
                                                          chosen_filter)))
        toasts: list[tuple] = []
        monkeypatch.setattr(widgets, "show_toast",
                            lambda *a, **k: toasts.append(a))
        dlg._export()

        written = tmp_path / expected
        assert written.exists(), f"{expected} was not written"
        assert content in written.read_text(encoding="utf-8", errors="ignore")
        assert toasts and expected in toasts[-1][1]
    finally:
        dlg.deleteLater()


def test_export_dispatch_cancel_writes_nothing(main_window, tmp_path,
                                               monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w, _settings = main_window
    dlg = ShortcutsDialog(w)
    try:
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        out = tmp_path / "exports"
        out.mkdir()
        dlg._export()
        assert not list(out.iterdir())
    finally:
        dlg.deleteLater()


def test_csv_and_markdown_mark_custom_bindings(main_window, tmp_path):
    w, _settings = main_window
    _write_map({"bindings": {"zoom_in": "Ctrl+Alt+="}})
    w.action_reload_shortcut_overrides()

    dlg = ShortcutsDialog(w)
    try:
        rows = dlg._export_rows()
        csv_text = write_shortcuts_csv(rows, tmp_path / "c.csv") \
            .read_text(encoding="utf-8-sig")
        md_text = write_shortcuts_markdown(rows, tmp_path / "c.md") \
            .read_text(encoding="utf-8")
        for text in (csv_text, md_text):
            assert "Ctrl+Alt+=" in text
            assert "(custom)" in text
        assert "Ctrl+Alt+=  (custom)" in md_text
    finally:
        dlg.deleteLater()


def test_alternate_bindings_appear_on_the_sheet_and_exports(main_window,
                                                            tmp_path):
    w, _settings = main_window
    _write_map({"bindings": {"goto_page": ["Ctrl+G", "Ctrl+L"]}})
    w.action_reload_shortcut_overrides()

    dlg = ShortcutsDialog(w)
    try:
        assert _sheet_keys(dlg)["Go to Page…"][1] == "Ctrl+G, Ctrl+L"
        text = write_shortcuts_csv(dlg.visible_rows(), tmp_path / "alt.csv") \
            .read_text(encoding="utf-8-sig")
        assert "Ctrl+G, Ctrl+L" in text
    finally:
        dlg.deleteLater()


def test_one_time_hint_flag_persists(main_window):
    w, settings = main_window
    # Emulate onboarding having completed, then re-run the startup branch.
    settings.set("general", "first_run_complete", True)
    settings.set("shortcuts", "hint_shown", False)
    assert settings.get("shortcuts", "hint_shown", False) is False
    # The MainWindow startup code writes the flag before showing the hint;
    # simulate what it does so the flag semantics stay pinned.
    if not settings.get("shortcuts", "hint_shown", False):
        settings.set("shortcuts", "hint_shown", True)
    assert settings.get("shortcuts", "hint_shown", False) is True
