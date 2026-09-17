"""DocumentView: the base class for every tab's content widget.

A DocumentView wraps a DocumentEngine and exposes a uniform interface for
the main window: navigation, zoom, search, state persistence, actions.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from veyrion_workspace.core.documents.base import (
    Capability, DocumentEngine, DocumentMetadata, PageInfo, TocEntry,
)

logger = logging.getLogger("veyrion.docview")


class DocumentView(QWidget):
    """Abstract-ish base; concrete formats subclass this."""

    # Emitted whenever the page/zoom/scroll changes (for status bar + save).
    state_changed = Signal()
    # Emitted when the document's data changed (needs save / reindex).
    content_changed = Signal()
    # Emitted to show transient feedback in the main window.
    request_toast = Signal(str, str)  # message, kind
    # Emitted when back/forward navigation availability changes.
    nav_state_changed = Signal()

    view_id: str = "base"

    NAV_HISTORY_MAX = 100

    def __init__(self, engine: DocumentEngine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self._current_page = 0
        self._zoom = 1.0
        # Reading history (browser-style back/forward over page jumps).
        self._nav_back: list[int] = []
        self._nav_forward: list[int] = []
        self._nav_programmatic = False

    # -- identity -----------------------------------------------------------
    @property
    def path(self) -> Path:
        return self.engine.path

    @property
    def display_name(self) -> str:
        md_title = ""
        try:
            md_title = self.engine.metadata().title
        except Exception:
            pass
        return md_title or self.engine.path.name

    @property
    def is_modified(self) -> bool:
        return bool(getattr(self.engine, "modified", False))

    # -- capabilities ---------------------------------------------------------
    def has_capability(self, cap: Capability) -> bool:
        return cap in self.engine.capabilities

    # -- navigation ------------------------------------------------------------
    @property
    def current_page(self) -> int:
        return self._current_page

    @property
    def page_count(self) -> int:
        try:
            return self.engine.page_count
        except Exception:
            return 0

    def go_to_page(self, index: int, *, _record: bool = True) -> bool:
        index = max(0, min(self.page_count - 1, index))
        if index != self._current_page:
            if _record and not self._nav_programmatic:
                self._nav_back.append(self._current_page)
                if len(self._nav_back) > self.NAV_HISTORY_MAX:
                    self._nav_back.pop(0)
                self._nav_forward.clear()
                self.nav_state_changed.emit()
            self._current_page = index
            self.state_changed.emit()
            return True
        return False

    # -- reading history (browser-style back/forward) -------------------------
    def nav_back(self) -> bool:
        """Return to the previous page in the jump history."""
        if not self._nav_back:
            return False
        prev = self._nav_back.pop()
        self._nav_forward.append(self._current_page)
        self._nav_programmatic = True
        try:
            self.go_to_page(prev, _record=False)
        finally:
            self._nav_programmatic = False
        self.nav_state_changed.emit()
        return True

    def nav_forward(self) -> bool:
        """Re-apply the next page in the jump history."""
        if not self._nav_forward:
            return False
        nxt = self._nav_forward.pop()
        self._nav_back.append(self._current_page)
        self._nav_programmatic = True
        try:
            self.go_to_page(nxt, _record=False)
        finally:
            self._nav_programmatic = False
        self.nav_state_changed.emit()
        return True

    def nav_can_back(self) -> bool:
        return bool(self._nav_back)

    def nav_can_forward(self) -> bool:
        return bool(self._nav_forward)

    def next_page(self) -> bool:
        # Sequential paging is not a "jump"; don't pollute history.
        return self.go_to_page(self._current_page + 1, _record=False)

    def previous_page(self) -> bool:
        # Sequential paging is not a "jump"; don't pollute history.
        return self.go_to_page(self._current_page - 1, _record=False)

    # -- zoom ---------------------------------------------------------------
    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, min(8.0, zoom))
        self.state_changed.emit()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def fit_width(self) -> None:
        pass

    def fit_page(self) -> None:
        pass

    # -- text / search --------------------------------------------------------
    def page_text(self, page: int = -1) -> str:
        try:
            idx = self._current_page if page < 0 else page
            return self.engine.page_text(idx)
        except Exception:
            return ""

    def selected_text(self) -> str:
        return ""

    def find_in_view(self, query: str, case_sensitive: bool = False,
                     whole_word: bool = False, regex: bool = False,
                     backwards: bool = False) -> int:
        return 0

    def clear_find(self) -> None:
        pass

    def goto_search_hit(self, hit: dict) -> None:
        if "page" in hit:
            self.go_to_page(int(hit["page"]))

    # -- state persistence ------------------------------------------------------
    def save_state(self) -> dict:
        return {"page": self._current_page, "zoom": self._zoom}

    def restore_state(self, state: dict) -> None:
        if "page" in state:
            self._current_page = int(state.get("page", 0))
        if "zoom" in state:
            self._zoom = float(state.get("zoom", 1.0))

    # -- lifecycle -----------------------------------------------------------
    def save(self) -> Path:
        return self.engine.save()

    def save_as(self, target: Path) -> Path:
        return self.engine.save(Path(target))

    def close_view(self) -> None:
        try:
            self.engine.close()
        except Exception:
            logger.exception("engine close failed")

    def context_actions(self) -> list:
        """QActions contributed to the viewer context menu."""
        return []

    # -- convenience for subclasses -------------------------------------------
    def _metadata(self) -> DocumentMetadata:
        try:
            return self.engine.metadata()
        except Exception:
            return DocumentMetadata()

    def toc(self) -> list[TocEntry]:
        try:
            return self.engine.toc()
        except Exception:
            return []
