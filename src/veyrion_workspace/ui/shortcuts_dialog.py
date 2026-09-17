"""Keyboard shortcuts cheat sheet (roadmap: discoverability for F8/F9 and
the reading modes).

The table is auto-populated by walking the main window's live menu tree, so
every action registered through QAction.setShortcut (or listed in a menu)
appears here automatically. A small static table documents shortcuts that
live inside document views and never reach the menu bar (presentation
controls, autoscroll pacing, reading-mode keys).

Because the rows are read live, a user-pasted key map (see
``services.shortcuts``) is reflected the moment it is applied: the Shortcut
column shows the active binding, rows the map changed are marked, and the
status line reports anything the map got wrong. The shown rows can be
exported three ways: a printable A4 PDF card, a CSV table, or a Markdown
table for pasting into documentation.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from veyrion_workspace.services.shortcuts import collect_actions
from veyrion_workspace.ui.reference_export import (
    GENERATED_BY,
    copy_table_markdown,
    export_dialog,
    export_table,
    print_table,
    write_table_csv,
    write_table_markdown,
    write_table_pdf,
)


def _key_text(seq: QKeySequence | None) -> str:
    """Human-friendly shortcut string ('' when the action has none)."""
    if seq is None or seq.isEmpty():
        return ""
    return seq.toString(QKeySequence.NativeText) or seq.toString()


def _action_keys(action) -> str:
    """Every binding of an action, primary first ('Ctrl+G, Ctrl+L')."""
    return ", ".join(text for text in (_key_text(s) for s in action.shortcuts())
                     if text)


def _strip_mnemonic(text: str) -> str:
    return (text or "").replace("&&", "\x00").replace("&", "").replace("\x00", "&")


def collect_menu_shortcuts(menubar) -> list[tuple[str, str, object]]:
    """Walk a QMenuBar and return (menu, label, shortcut, action) rows.

    Only actions with an assigned shortcut are listed; separators and
    submenus are skipped. The raw action is carried so the dialog can
    trigger it directly from a row.
    """
    rows: list[tuple[str, str, object]] = []
    seen: set[int] = set()

    def _walk(menu, menu_title: str) -> None:
        for act in menu.actions():
            if act.menu() is not None:
                _walk(act.menu(), _strip_mnemonic(act.menu().title()))
                continue
            if act.isSeparator():
                continue
            key = _action_keys(act)
            label = _strip_mnemonic(act.text()) or act.toolTip()
            if act not in seen and label:
                seen.add(act)
                rows.append((menu_title, label, key, act))

    for top in menubar.actions():
        m = top.menu()
        if m is not None:
            _walk(m, _strip_mnemonic(m.title()))
    return rows


# View-internal shortcuts that never appear in a menu: (context, label, keys)
HIDDEN_SHORTCUTS: list[tuple[str, str, str]] = [
    ("Reading", "Toggle autoscroll", "Space (press again to stop)"),
    ("Reading", "Next page / slide", "Page Down"),
    ("Reading", "Previous page / slide", "Page Up"),
    ("Reading", "First page", "Home"),
    ("Reading", "Last page", "End"),
    ("Presentation", "Start presentation", "F5"),
    ("Presentation", "Leave presentation", "Esc"),
    ("Chrome", "Distraction-free mode", "F8"),
    ("Chrome", "Focus mode", "F9"),
    ("Chrome", "Fullscreen", "F11"),
    ("Chrome", "Command palette", "Ctrl+K"),
    ("Annotating", "Back to Select/Pan", "Esc"),
    ("Annotating", "Select an annotation", "Click it"),
    ("Annotating", "Marquee-select many", "Ctrl+drag on the page"),
    ("Annotating", "Add/remove one from selection", "Ctrl+click"),
    ("Annotating", "Move selected", "Drag the annotation"),
    ("Annotating", "Resize selected", "Drag a corner handle"),
    ("Annotating", "Recolour / opacity / width", "Right-click selection"),
    ("Annotating", "Delete selected", "Delete"),
    ("Annotating", "Clear selection", "Esc"),
]

# The sheet is one of several "reference surfaces" in the app; the shared
# engine in reference_export owns the card/CSV/Markdown/print behaviour.
SHORTCUT_COLUMNS = (("Where", 0.26), ("Action", 0.48), ("Shortcut", 0.26))
SHORTCUT_EMPTY = "No shortcuts match the current filter."


def write_shortcuts_pdf(rows, out_path, *, title: str = "Keyboard Shortcuts",
                        subtitle: str = "",
                        generated_by: str = GENERATED_BY) -> Path:
    """Render ``(where, action, keys)`` rows as a printable A4 reference card."""
    return write_table_pdf(rows, out_path, title=title, subtitle=subtitle,
                           columns=SHORTCUT_COLUMNS, generated_by=generated_by,
                           empty_message=SHORTCUT_EMPTY)


def write_shortcuts_csv(rows, out_path) -> Path:
    """Write ``(where, action, keys)`` rows as a plain three-column CSV."""
    return write_table_csv(rows, out_path, columns=SHORTCUT_COLUMNS)


def write_shortcuts_markdown(rows, out_path, *,
                             title: str = "Keyboard Shortcuts",
                             generated_by: str = GENERATED_BY) -> Path:
    """Write the rows as a Markdown table ready to paste into docs."""
    count = len(list(rows))
    return write_table_markdown(list(rows), out_path, title=title,
                                subtitle=f"{count} shortcut"
                                         f"{'s' if count != 1 else ''}",
                                columns=SHORTCUT_COLUMNS,
                                generated_by=generated_by,
                                empty_message=SHORTCUT_EMPTY)


def export_shortcuts(rows, out_path, fmt: str = "pdf", **kwargs) -> Path:
    """Dispatch to the shared PDF card, CSV or Markdown writer."""
    return export_table(rows, out_path, fmt,
                        title=kwargs.get("title", "Keyboard Shortcuts"),
                        subtitle=kwargs.get("subtitle", ""),
                        columns=SHORTCUT_COLUMNS,
                        generated_by=kwargs.get("generated_by", GENERATED_BY),
                        empty_message=SHORTCUT_EMPTY)


class ShortcutsDialog(QDialog):
    """Searchable cheat sheet of every window-level action and its key."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setModal(True)
        self.resize(640, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter shortcuts… (try “zoom”, “theme”, “page”)")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search, 1)
        self._reset_btn = QPushButton("Reset filter")
        self._reset_btn.setToolTip("Clear the filter and show every shortcut")
        self._reset_btn.clicked.connect(self._search.clear)
        search_row.addWidget(self._reset_btn)
        lay.addLayout(search_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self._status = QLabel("")
        self._status.setObjectName("dimLabel")
        self._status.setWordWrap(True)
        status_row.addWidget(self._status, 1)
        # Compact helpers for the paste-a-map workflow, kept next to the
        # status text so the main button row stays narrow.
        self._reload_btn = QPushButton("Reload keys")
        self._reload_btn.setFlat(True)
        self._reload_btn.setToolTip(
            "Re-read shortcuts.json and show the bindings it produces")
        self._reload_btn.clicked.connect(self._reload_overrides)
        status_row.addWidget(self._reload_btn, 0, Qt.AlignTop)
        self._file_btn = QPushButton("Key map file…")
        self._file_btn.setFlat(True)
        self._file_btn.setToolTip(
            "Create and open shortcuts.json so you can paste a key map")
        self._file_btn.clicked.connect(self._open_overrides_file)
        status_row.addWidget(self._file_btn, 0, Qt.AlignTop)
        lay.addLayout(status_row)

        hint = QLabel("Double-click a row to run the action.")
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Where", "Action", "Shortcut"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.resizeSection(0, 110)
        header.resizeSection(2, 170)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._trigger_row)
        lay.addWidget(self.table, 1)

        hint_row = QHBoxLayout()
        hint_row.addStretch(1)
        self._copy_btn = QPushButton("Copy as Markdown")
        self._copy_btn.setFlat(True)
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.setStyleSheet(
            "QPushButton { border: none; text-decoration: none; }"
            " QPushButton:hover { text-decoration: underline; }")
        self._copy_btn.setToolTip(
            "Copy the rows currently shown to the clipboard as a Markdown "
            "table, ready to paste into documentation")
        self._copy_btn.clicked.connect(self._copy_markdown)
        hint_row.addWidget(self._copy_btn)
        self._export_btn = QPushButton("Export…")
        self._export_btn.setToolTip(
            "Save the rows currently shown as a PDF card, CSV or Markdown")
        self._export_btn.clicked.connect(self._export)
        hint_row.addWidget(self._export_btn)
        self._print_btn = QPushButton("Print…")
        self._print_btn.setToolTip(
            "Send the rows currently shown to your printer as a card")
        self._print_btn.clicked.connect(self._print)
        hint_row.addWidget(self._print_btn)
        lay.addLayout(hint_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(lambda *_: self.accept())
        lay.addWidget(buttons)

        self._rows: list[tuple[str, str, object]] = []
        self._overrides: dict[str, str] = {}
        self._action_ids: dict[int, str] = {}
        self._collect()
        self._populate()

    # ------------------------------------------------------------------
    def _collect(self) -> None:
        """(Re)build the rows and the decoration for user-overridden keys."""
        parent = self.parent()
        self._rows = []
        self._overrides = {}
        self._action_ids = {}

        if parent is not None and hasattr(parent, "menuBar"):
            self._rows = list(collect_menu_shortcuts(parent.menuBar()))
            try:
                self._action_ids = {id(a): aid
                                    for aid, a in collect_actions(parent).items()}
            except Exception:
                self._action_ids = {}
        self._rows += [(ctx, label, keys, None)
                       for (ctx, label, keys) in HIDDEN_SHORTCUTS]

        report = getattr(parent, "shortcut_report", None)
        if report is not None:
            self._overrides.update(getattr(report, "applied", None) or {})
            for aid in getattr(report, "unbound", None) or []:
                self._overrides.setdefault(aid, "")
        self._status.setText(self._status_text())

    def _status_text(self) -> str:
        """Explain which bindings are custom and what the map got wrong."""
        count = len(self._overrides)
        plural = "s" if count != 1 else ""
        head = (f"{count} custom binding{plural} active." if count
                else "Using the built-in bindings.")
        report = getattr(self.parent(), "shortcut_report", None)
        problems: list[str] = []
        if report is not None:
            for label, items in (
                    ("unknown id", getattr(report, "unknown", None) or []),
                    ("invalid key", getattr(report, "invalid", None) or []),
                    ("conflicting binding", getattr(report, "conflicts", None) or [])):
                if items:
                    problems.append(f"{len(items)} {label}{'s' if len(items) != 1 else ''}")
        tail = f" Ignored: {', '.join(problems)}." if problems else ""
        error = getattr(report, "error", None) if report is not None else None
        if error:
            tail += f" {error}"
        return (head + " Paste a key map into shortcuts.json, then Reload "
                "keys." + tail)

    def refresh(self) -> None:
        """Re-read the menus and overrides, keeping the current filter."""
        query = self._search.text()
        self._collect()
        self._populate()
        self._search.setText(query)

    def _reload_overrides(self) -> None:
        """Ask the window to re-read the file, then show the result."""
        parent = self.parent()
        reload_cb = getattr(parent, "action_reload_shortcut_overrides", None)
        if callable(reload_cb):
            try:
                reload_cb()
            except Exception:
                pass
        self.refresh()

    def _open_overrides_file(self) -> None:
        parent = self.parent()
        open_cb = getattr(parent, "action_edit_shortcuts_file", None)
        if callable(open_cb):
            open_cb()
        self.refresh()
    def _populate(self) -> None:
        self.table.setRowCount(len(self._rows))
        for r, (where, label, keys, _act) in enumerate(self._rows):
            w_item = QTableWidgetItem(where)
            a_item = QTableWidgetItem(label)
            k_item = QTableWidgetItem(keys or "—")
            k_item.setData(Qt.UserRole, keys)
            aid = self._action_ids.get(id(_act)) if _act is not None else None
            custom = self._overrides.get(aid) if aid else None
            if not keys:
                from PySide6.QtGui import QColor
                for it in (w_item, a_item, k_item):
                    it.setForeground(QColor("#8A8F98"))
            if aid and aid in self._overrides:
                font = k_item.font()
                font.setBold(True)
                for it in (w_item, a_item, k_item):
                    it.setFont(font)
                k_item.setToolTip(
                    "Custom binding from your key map"
                    + (f": {custom}" if custom else " (unbound)"))
            elif custom is not None or aid:
                k_item.setToolTip("Built-in binding")
            self.table.setItem(r, 0, w_item)
            self.table.setItem(r, 1, a_item)
            self.table.setItem(r, 2, k_item)
        self._apply_filter(self._search.text())

    def _apply_filter(self, text: str) -> None:
        text = (text or "").strip().lower()
        terms = text.split()
        shown = 0
        for r in range(self.table.rowCount()):
            where = self.table.item(r, 0).text().lower()
            label = self.table.item(r, 1).text().lower()
            keys = self.table.item(r, 2).text().lower()
            hay = f"{where} {label} {keys}"
            match = all(t in hay for t in terms) if terms else True
            self.table.setRowHidden(r, not match)
            if match:
                shown += 1
        self.setWindowTitle(f"Keyboard Shortcuts — {shown} of {self.table.rowCount()}")

    def _trigger_row(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._rows):
            act = self._rows[row][3]
            if act is not None:
                self.accept()
                act.trigger()

    # ----------------------------------------------------------------- export
    def _shown_rows(self) -> list[tuple[str | None, str, str, str]]:
        """Visible rows with the id of the action each one belongs to."""
        rows: list[tuple[str | None, str, str, str]] = []
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            where, label, keys, act = self._rows[r]
            aid = self._action_ids.get(id(act)) if act is not None else None
            rows.append((aid, where, label, keys or "—"))
        return rows

    def visible_rows(self) -> list[tuple[str, str, str]]:
        """The ``(where, action, shortcut)`` rows currently on screen."""
        return [(where, label, keys) for _aid, where, label, keys in self._shown_rows()]

    def _export_rows(self) -> list[tuple[str, str, str]]:
        """Visible rows, with user-remapped bindings marked as custom."""
        return [(where, label,
                 keys + "  (custom)" if aid in self._overrides else keys)
                for aid, where, label, keys in self._shown_rows()]

    def _export(self, forced: str | None = None) -> None:
        """Write the shown rows to a PDF card, CSV or Markdown file.

        The format follows the filter the user picks in the save dialog (or
        ``forced``), and the file name is corrected to match it.
        """
        rows = self._export_rows()
        export_dialog(self, rows, title="Export shortcuts",
                      default_name="veyrion-shortcuts.pdf",
                      columns=SHORTCUT_COLUMNS, fmt=forced,
                      empty_warning="Nothing to export — the filter matched "
                                    "no rows",
                      empty_message=SHORTCUT_EMPTY)

    def _export_pdf(self) -> None:
        """Export a printable PDF reference card (PDF-only entry point)."""
        self._export("pdf")

    def _copy_markdown(self) -> bool:
        """Copy the shown rows to the clipboard as a Markdown table."""
        return copy_table_markdown(
            self, self._export_rows(), title="Keyboard Shortcuts",
            empty_warning="Nothing to copy — the filter matched no rows")

    def _print(self) -> None:
        """Send the rows currently shown to the printer as a card."""
        print_table(self, self._export_rows(), title="Keyboard Shortcuts",
                    columns=SHORTCUT_COLUMNS,
                    empty_warning="Nothing to print — the filter matched no "
                                  "rows")
