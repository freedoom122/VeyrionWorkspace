"""Custom widgets used across the application.

Small set of polished, reusable controls — nav rail, toggle switches,
toasts, empty states, section headers — that keep the UI coherent without
default-Qt blandness.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
import logging

logger = logging.getLogger("veyrion.widgets")

from veyrion_workspace.ui.icons import icon, pixmap
from veyrion_workspace.ui.theme import Palette


class FlowLayout(QLayout):
    """Wrapping flow layout (used by tag chips and toolbar extras)."""

    def __init__(self, parent=None, margin=0, spacing=6) -> None:
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: list = []

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return QSize(size.width() + 12, size.height() + 12)

    def _do_layout(self, rect, test_only) -> int:
        x, y = rect.x(), rect.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - rect.x() > rect.width() and line_height > 0:
                x = rect.x()
                y = y + line_height + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + 12


class NavRail(QFrame):
    """Vertical navigation rail — the app's primary mode switcher."""

    action_selected = Signal(str)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(52)
        self._palette = palette
        self._buttons: dict[str, NavRailButton] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 8, 6, 8)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)

    def add_action(self, action_id: str, label: str, icon_name: str,
                   tooltip: str = "") -> "NavRailButton":
        btn = NavRailButton(icon_name, label, tooltip or label, self._palette)
        btn.clicked.connect(lambda: self.action_selected.emit(action_id))
        self._buttons[action_id] = btn
        self._layout.insertWidget(self._layout.count() - 1, btn)
        return btn

    def select(self, action_id: str) -> None:
        for aid, b in self._buttons.items():
            b.setChecked(aid == action_id)

    def button(self, action_id: str) -> "NavRailButton | None":
        return self._buttons.get(action_id)


class NavRailButton(QToolButton):
    def __init__(self, icon_name: str, label: str, tooltip: str,
                 palette: Palette) -> None:
        super().__init__()
        self._palette = palette
        self._icon_name = icon_name
        self._label = label
        self.setCheckable(True)
        self.setFixedSize(40, 40)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self._update_icon()

    def _update_icon(self) -> None:
        color = ("#FFFFFF" if self.isChecked()
                 else self._palette.text if self.isEnabled()
                 else self._palette.text_disabled)
        self.setIcon(icon(self._icon_name, color))
        self.setIconSize(QSize(22, 22))

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        self._update_icon()
        self.update()


class ToggleSwitch(QAbstractButton):
    """Animated iOS-style toggle with accessible state text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._handle_pos = 3.0
        self._anim: QPropertyAnimation | None = None
        self._accent = "#C7522A"
        self._track_off = "#B9B2A3"

    def set_palette(self, p: Palette) -> None:
        self._accent = p.accent
        self._track_off = p.border_strong
        self.update()

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QColor(self._accent if self.isChecked() else self._track_off)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRect(0, 2, 42, 20), 10, 10)
        painter.setBrush(QColor("#FFFFFF"))
        target = 21 if self.isChecked() else 3
        if self._anim is None or self._anim.state() != QPropertyAnimation.Running:
            self._handle_pos = target
        painter.drawEllipse(QRect(int(self._handle_pos), 4, 16, 16))
        painter.end()

    def hitButton(self, pos) -> bool:
        return self.contentsRect().contains(pos)

    def nextCheckState(self) -> None:
        self.setChecked(not self.isChecked())


class EmptyState(QWidget):
    """Friendly empty state with icon, title, hint, and optional action."""

    def __init__(self, icon_name: str, title: str, hint: str = "",
                 action_text: str = "", parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 36, 24, 36)
        lay.setSpacing(10)
        lay.addStretch(1)

        icon_label = QLabel()
        icon_label.setPixmap(pixmap(icon_name, "#9A948A", 44))
        icon_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setObjectName("titleLabel")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setWordWrap(True)
        lay.addWidget(title_label)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("dimLabel")
            hint_label.setAlignment(Qt.AlignCenter)
            hint_label.setWordWrap(True)
            lay.addWidget(hint_label)

        self.action_button: QPushButton | None = None
        if action_text:
            self.action_button = QPushButton(action_text)
            self.action_button.setObjectName("accentButton")
            self.action_button.setFixedWidth(180)
            wrap = QHBoxLayout()
            wrap.addStretch(1)
            wrap.addWidget(self.action_button)
            wrap.addStretch(1)
            lay.addLayout(wrap)
        lay.addStretch(1)


class ActionableToast(QFrame):
    """Toast with an action button (roadmap #82): "Annotation deleted [Undo]".

    The action button appears only when an ``on_action`` callback is given.
    The toast dismisses itself after ``duration_ms``; invoking the action
    cancels the dismissal timer first, so the callback runs cleanly.
    """

    KIND_STYLES = {
        "info": ("#3E7C4F", "info"),
        "success": ("#3E7C4F", "info"),
        "warning": ("#B07A21", "warning"),
        "error": ("#A33B2E", "warning"),
    }

    def __init__(self, parent: QWidget, message: str, kind: str = "info",
                 duration_ms: int = 6000, on_action=None,
                 action_text: str = "Undo") -> None:
        super().__init__(parent)
        color, icon_name = self.KIND_STYLES.get(kind, self.KIND_STYLES["info"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(pixmap(icon_name, "#FFFFFF", 16))
        lay.addWidget(ic)
        label = QLabel(message)
        label.setStyleSheet("color: #FFFFFF; font-weight: 500;")
        label.setWordWrap(True)
        lay.addWidget(label)
        self._button = None
        if on_action is not None:
            self._button = QPushButton(action_text)
            self._button.setCursor(Qt.PointingHandCursor)
            self._button.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.18);"
                " color: #FFFFFF; border: 1px solid rgba(255,255,255,0.55);"
                " border-radius: 4px; padding: 2px 12px; font-weight: 600; }"
                "QPushButton:hover { background: rgba(255,255,255,0.32); }")
            self._button.clicked.connect(self._on_action_clicked)
            lay.addWidget(self._button)
        self.setStyleSheet(f"background: {color}; border-radius: 6px;")
        self._on_action = on_action
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.deleteLater)
        self._dismiss_timer.start(duration_ms)
        self.adjustSize()
        self.show()
        self.raise_()

    def _on_action_clicked(self) -> None:
        self._dismiss_timer.stop()
        cb = self._on_action
        try:
            if cb is not None:
                cb()
        except Exception:
            logger.exception("toast action failed")
        self.deleteLater()

    def reposition(self, parent_size) -> None:
        self.move((parent_size.width() - self.width()) // 2,
                  parent_size.height() - self.height() - 44)


class Toast(QWidget):
    """Transient notification toast, rendered inside the main window."""

    KIND_STYLES = {
        "info": ("#3E7C4F", "info"),
        "success": ("#3E7C4F", "info"),
        "warning": ("#B07A21", "warning"),
        "error": ("#A33B2E", "warning"),
    }

    def __init__(self, parent: QWidget, message: str, kind: str = "info",
                 duration_ms: int = 3200) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        color, icon_name = self.KIND_STYLES.get(kind, self.KIND_STYLES["info"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(pixmap(icon_name, "#FFFFFF", 16))
        lay.addWidget(ic)
        label = QLabel(message)
        label.setStyleSheet("color: #FFFFFF; font-weight: 500;")
        label.setWordWrap(True)
        lay.addWidget(label)
        self.setStyleSheet(
            f"background: {color}; border-radius: 6px;")
        self.adjustSize()
        self.show()
        self.raise_()
        QTimer.singleShot(duration_ms, self.deleteLater)

    def reposition(self, parent_size) -> None:
        self.move((parent_size.width() - self.width()) // 2,
                  parent_size.height() - self.height() - 44)


class SectionHeader(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("panelHeader")


class ChipButton(QToolButton):
    """Tag chip with removable check state."""

    def __init__(self, text: str, checkable: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QToolButton {
                border: 1px solid rgba(0,0,0,0.2);
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 9pt;
            }
            QToolButton:checked { background: rgba(0,0,0,0.12); font-weight: 600; }
            QToolButton:hover { border-color: rgba(0,0,0,0.45); }
        """)


class HelpLabel(QLabel):
    """Small dim helper text."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("dimLabel")
        self.setWordWrap(True)


class ClickableLabel(QLabel):
    """A QLabel that emits ``clicked`` — used by the status-bar segments."""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton \
                and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def show_toast(parent: QWidget, message: str, kind: str = "info") -> None:
    """Attach and position a toast over ``parent`` (usually main window)."""
    toast = Toast(parent, message, kind)
    toast.reposition(parent.size())
    if parent not in _PARENT_TOASTS:
        _PARENT_TOASTS[parent] = []
    _PARENT_TOASTS[parent].append(toast)


_PARENT_TOASTS: dict[QWidget, list] = {}


def show_actionable_toast(parent: QWidget, message: str, kind: str = "info",
                          on_action=None, action_text: str = "Undo",
                          duration_ms: int = 6000) -> None:
    """Toast with an action button, e.g. 'Annotation deleted [Undo]' (#82)."""
    toast = ActionableToast(parent, message, kind, duration_ms,
                            on_action=on_action, action_text=action_text)
    toast.reposition(parent.size())
    if parent not in _PARENT_TOASTS:
        _PARENT_TOASTS[parent] = []
    _PARENT_TOASTS[parent].append(toast)
