"""Command palette (Ctrl+K).

Fuzzy-searchable launcher for every action, open document, recent file,
bookmark, and setting. Keyboard-first: arrows + Enter, Esc to dismiss.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)

try:
    from rapidfuzz import fuzz
    def _score(query: str, text: str) -> float:
        if not text:
            return 0.0
        q = query.lower()
        t = text.lower()
        if q == t:
            return 100.0
        if t.startswith(q):
            return 90.0
        if q in t:
            return 75.0
        return float(fuzz.partial_ratio(q, t))
except ImportError:  # pragma: no cover
    def _score(query: str, text: str) -> float:
        q, t = query.lower(), text.lower()
        if q == t:
            return 100.0
        if t.startswith(q):
            return 90.0
        if q in t:
            return 75.0
        return 0.0


@dataclass
class Command:
    title: str
    subtitle: str = ""
    icon_name: str = "command"
    category: str = "Command"
    payload: object = None
    keywords: str = ""

    def searchable(self) -> str:
        return f"{self.title} {self.subtitle} {self.keywords}"


class CommandPalette(QFrame):
    """Overlay palette parented to the main window."""

    command_run = Signal(object)   # Command

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("paletteFrame")
        self.setWindowFlags(Qt.Widget)
        self.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or search…")
        self._input.installEventFilter(self)
        lay.addWidget(self._input)

        self._list = QListWidget()
        self._list.setObjectName("paletteList")
        # Backwards-compatible aliases used by scripts and tests.
        self.search = self._input
        self.list = self._list
        self._list.itemActivated.connect(self._run_item)
        self._list.itemClicked.connect(self._run_item)
        lay.addWidget(self._list)

        self._input.textChanged.connect(self._filter)
        self._commands: list[Command] = []
        self._filtered: list[Command] = []

    # ------------------------------------------------------------------ public
    def set_commands(self, commands: list[Command]) -> None:
        self._commands = commands

    def open_palette(self) -> None:
        """Show the palette overlay and focus its input."""
        self._input.clear()
        self._filter("")
        parent = self.parentWidget()
        if parent:
            w = min(560, parent.width() - 80)
            self.setGeometry((parent.width() - w) // 2, 60, w, 420)
        self.show()
        self.raise_()
        self._input.setFocus()

    def close_palette(self) -> None:
        """Hide the palette overlay."""
        self.hide()
        parent = self.parentWidget()
        if parent:
            parent.setFocus()

    def is_open(self) -> bool:
        return self.isVisible()

    # ------------------------------------------------------------------ internals
    def _filter(self, text: str) -> None:
        text = text.strip()
        self._list.clear()
        if text:
            scored = []
            for cmd in self._commands:
                s = _score(text, cmd.searchable())
                if s > 30:
                    scored.append((s, cmd))
            scored.sort(key=lambda t: -t[0])
            self._filtered = [cmd for _, cmd in scored[:40]]
        else:
            self._filtered = self._commands[:40]
        for cmd in self._filtered:
            item = QListWidgetItem(
                f"  {cmd.title}\n      {cmd.subtitle}" if cmd.subtitle
                else f"  {cmd.title}")
            item.setData(Qt.UserRole, cmd)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _run_item(self, item) -> None:
        cmd = item.data(Qt.UserRole)
        self.close_palette()
        if cmd is not None:
            self.command_run.emit(cmd)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.close_palette()
                return True
            if key in (Qt.Key_Down, Qt.Key_Up):
                row = self._list.currentRow()
                delta = 1 if key == Qt.Key_Down else -1
                new_row = max(0, min(self._list.count() - 1, row + delta))
                self._list.setCurrentRow(new_row)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                item = self._list.currentItem()
                if item:
                    self._run_item(item)
                return True
        return super().eventFilter(obj, event)
