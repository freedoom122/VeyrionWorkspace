"""PDF viewer widget.

Rendering architecture:
  * QGraphicsView-based canvas with one QGraphicsItem per page slot.
  * Pages render lazily on a worker pool; a memory-aware LRU keeps recent
    bitmaps (configurable MB budget).
  * Neighbor prefetch, fit-width/page/height modes, 50%-800% zoom,
    single/continuous/two-page/two-cover modes, rotation, smooth scrolling,
    link handling, and text-selection-based annotations.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

# Zoom bounds for every path that changes magnification (set_zoom, animated
# zoom, fit modes, wheel zoom).  Keep the numbers in exactly one place.
ZOOM_MIN = 0.1
ZOOM_MAX = 8.0


def clamp_zoom(zoom: float) -> float:
    """Clamp a zoom factor into ``[ZOOM_MIN, ZOOM_MAX]``."""
    return max(ZOOM_MIN, min(ZOOM_MAX, zoom))

from PySide6.QtCore import (
    QEvent, QObject, QPoint, QPointF, QRunnable, QRectF, QSizeF, Qt,
    QThreadPool, Signal, QTimer,
)
from PySide6.QtGui import (
    QColor, QFont, QCursor, QImage, QPainter, QPainterPath, QPen, QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsTextItem, QGraphicsView, QGraphicsWidget,
    QGridLayout, QHBoxLayout,
    QLabel, QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from veyrion_workspace.core.documents.base import Capability
from veyrion_workspace.core.documents.pdf_engine import PdfEngine
from veyrion_workspace.ui.document_view import DocumentView
from veyrion_workspace.ui.icons import icon

logger = logging.getLogger("veyrion.pdfview")

# Subtype names PDF uses for quad-based text markup annotations.
_ANNOT_MARKUP_KINDS = ("Highlight", "Underline", "StrikeOut", "Squiggly")


def _qcolor(hex_color: str, alpha: int = 255) -> QColor:
    """QColor from '#rrggbb' plus an alpha channel.

    QColor(hex, alpha) is not a valid constructor, so build then set alpha.
    """
    color = QColor(hex_color)
    color.setAlpha(max(0, min(255, int(alpha))))
    return color


# -----------------------------------------------------------------------------
# Undo/redo command objects for PDF annotations and sticky notes.

class _AnnotCommand:
    """Base for reversible annotation operations on PdfView."""

    label = "annotation"

    def __init__(self, view: "PdfView") -> None:
        self.view = view

    def undo(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def redo(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError


class _AddAnnotCommand(_AnnotCommand):
    """Adding an annotation; undo removes it by xref, redo re-creates it."""

    label = "add annotation"

    def __init__(self, view: "PdfView", xref: int, page: int, kind: str,
                 rect, quads=None, points=None, text: str = "") -> None:
        super().__init__(view)
        self.xref = xref
        self.page = page
        self.kind = kind
        self.rect = rect
        self.quads = quads
        self.points = points
        self.text = text

    def _create(self) -> bool:
        try:
            import fitz
            v = self.view
            if self.kind == "highlight":
                xref = v.pdf.add_highlight(
                    self.page, [fitz.Rect(*self.rect)], v._annot_color,
                    v._annot_opacity)
            elif self.kind in ("underline", "strikeout", "squiggly"):
                xref = v.pdf.add_text_markup(
                    self.page, [fitz.Rect(*self.rect)], self.kind,
                    v._annot_color, v._annot_opacity)
            elif self.kind == "ink":
                xref = v.pdf.add_ink(
                    self.page, self.points or [], v._annot_color,
                    v._annot_width)
            elif self.kind in ("rectangle", "ellipse", "line", "arrow"):
                xref = v.pdf.add_shape(
                    self.page, self.kind, self.rect, v._annot_color,
                    v._annot_width)
            elif self.kind == "note":
                xref = v.pdf.add_note(
                    self.page, self.rect[:2], self.text or "Note",
                    v._annot_color)
            elif self.kind == "freetext":
                xref = v.pdf.add_free_text(
                    self.page, self.rect, self.text or "Text", v._annot_color)
            else:
                return False
            self.xref = xref
            v._rerender_page(self.page)
            v.content_changed.emit()
            return True
        except Exception:
            logger.exception("undo/redo re-create failed")
            return False

    def undo(self) -> bool:
        try:
            self.view.delete_annotation_xref(self.xref)
            self.view._rerender_page(self.page)
            return True
        except Exception:
            logger.exception("undo add-annotation failed")
            return False

    def redo(self) -> bool:
        return self._create()


class _DeleteAnnotCommand(_AnnotCommand):
    """Deleting an annotation by xref; undo restores its properties."""

    label = "delete annotation"

    def __init__(self, view: "PdfView", xref: int, snapshot: dict,
                 geometry: dict | None = None) -> None:
        super().__init__(view)
        self.xref = xref
        self.snapshot = snapshot  # dict from pdf.annotations() listing
        # Subtype geometry captured in page coords before deleting; lets undo
        # restore ink strokes / line endpoints / markup quads exactly rather
        # than approximating them from the bounding rect.
        self.geometry = dict(geometry or {})
        self.page = int(snapshot.get("page", 0))

    def undo(self) -> bool:
        """Re-create the deleted annotation from its snapshot."""
        try:
            import fitz
            v = self.view
            s = self.snapshot
            g = self.geometry
            kind = s.get("type", "")
            rect = s.get("rect") or [0, 0, 0, 0]
            color = s.get("color", "#E5B25D")
            opacity = float(s.get("opacity", 1.0))
            width = float(g.get("width", 0.0) or 2.0)
            if kind in _ANNOT_MARKUP_KINDS and g.get("quads"):
                xref = v.pdf.add_markup_quads(self.page, kind, g["quads"],
                                              color, opacity)
            elif kind == "Ink" and g.get("strokes"):
                strokes = [[(st[i], st[i + 1])
                            for i in range(0, len(st) - 1, 2)]
                           for st in g["strokes"]]
                xref = v.pdf.add_ink(self.page, strokes, color, width)
            elif kind == "Line" and g.get("line"):
                ln = g["line"]
                xref = v.pdf.add_line_between(
                    self.page, (ln[0], ln[1]), (ln[2], ln[3]), color, width)
            elif kind in ("Highlight",):
                xref = v.pdf.add_highlight(self.page, [fitz.Rect(*rect)],
                                           color, float(s.get("opacity", 0.4)))
            elif kind in ("Underline", "StrikeOut", "Squiggly"):
                km = {"Underline": "underline", "StrikeOut": "strikeout",
                      "Squiggly": "squiggly"}[kind]
                xref = v.pdf.add_text_markup(self.page, [fitz.Rect(*rect)],
                                             km, s.get("color", "#C7522A"),
                                             float(s.get("opacity", 1.0)))
            elif kind == "Ink":
                # No stroke geometry captured (older command): approximate a
                # single horizontal stroke across the bounding rect.
                pts = [(rect[0], (rect[1] + rect[3]) / 2),
                       (rect[2], (rect[1] + rect[3]) / 2)]
                xref = v.pdf.add_ink(self.page, [pts],
                                     s.get("color", "#222222"), width)
            elif kind in ("Square", "Circle", "Line", "Arrow"):
                km = {"Square": "rectangle", "Circle": "ellipse",
                      "Line": "line", "Arrow": "arrow"}
                xref = v.pdf.add_shape(self.page, km[kind], rect,
                                       s.get("color", "#C7522A"), 1.5)
            elif kind == "FreeText":
                xref = v.pdf.add_free_text(self.page, rect,
                                           s.get("text", ""),
                                           s.get("color", "#C7522A"))
            elif kind == "Text":
                xref = v.pdf.add_note(self.page, rect[:2], s.get("text", "Note"),
                                      s.get("color", "#E5B25D"))
            else:
                return False
            self.xref = xref
            v._rerender_page(self.page)
            v.content_changed.emit()
            return True
        except Exception:
            logger.exception("undo delete-annotation failed")
            return False

    def redo(self) -> bool:
        try:
            self.view.delete_annotation_xref(self.xref)
            self.view._rerender_page(self.page)
            return True
        except Exception:
            return False


class _StickyNoteCommand(_AnnotCommand):
    """Sticky note add / delete / text-change as reversible commands.

    For 'delete' commands the widget is destroyed shortly after (deleteLater),
    so everything needed to recreate it is snapshotted here at delete time.
    """

    def __init__(self, view: "PdfView", action: str, item=None, *,
                 page: int = 0, scene_pos=None, color: str = "",
                 old_text: str = "", new_text: str = "") -> None:
        super().__init__(view)
        self.action = action          # 'add' | 'delete' | 'text'
        self.item = item              # live widget (None after delete-undo)
        self.page = page
        self.scene_pos = scene_pos
        self.color = color
        self.old_text = old_text
        self.new_text = new_text
        self._snapshot_text = ""
        self._snapshot_size = (0.0, 0.0)
        if item is not None:
            try:
                self._snapshot_text = item.text()
                br = item.boundingRect()
                self._snapshot_size = (br.width(), br.height())
            except Exception:
                logger.exception("__init__ failed")

    @property
    def label(self) -> str:
        return {"add": "add note", "delete": "delete note",
                "text": "edit note"}.get(self.action, "note")

    def _recreate(self) -> bool:
        """Build a fresh note on the page from this command's snapshot."""
        v = self.view
        v._undoing = True
        try:
            pos = self.scene_pos or QPointF(12, 12)
            item = v.add_sticky_note_on_page(self.page, pos)
            text = self._snapshot_text or self.new_text
            if text:
                item.init_elements()
                item._restoring = True
                item._text_item.setPlainText(text)
                item._restoring = False
                item._last_persisted_text = text
                item._on_text_changed()
            w, h = self._snapshot_size
            if w > 0 and h > 0:
                item.resize(w, h)
            self.item = item   # new live widget replaces the dead one
            return True
        except Exception:
            logger.exception("sticky note recreate failed")
            return False
        finally:
            v._undoing = False

    def _delete_item(self, item) -> bool:
        try:
            self.view.remove_sticky_note(item)
            return True
        except Exception:
            logger.exception("sticky note remove failed")
            return False

    def undo(self) -> bool:
        if self.action == "add":
            return self.item is not None and self._delete_item(self.item)
        if self.action == "delete":
            return self._recreate()
        if self.action == "text":
            item = self.item
            if item is None or item._text_item is None:
                return False
            item._restoring = True
            item._text_item.setPlainText(self.old_text)
            item._restoring = False
            item._last_persisted_text = self.old_text
            item._on_text_changed()
            return True
        return False

    def redo(self) -> bool:
        if self.action == "add":
            return self._recreate()
        if self.action == "delete":
            return self.item is not None and self._delete_item(self.item)
        if self.action == "text":
            item = self.item
            if item is None or item._text_item is None:
                return False
            item._restoring = True
            item._text_item.setPlainText(self.new_text)
            item._restoring = False
            item._last_persisted_text = self.new_text
            item._on_text_changed()
            return True
        return False


class _MoveAnnotsCommand(_AnnotCommand):
    """Translate one or more annotations by a page-space delta."""

    label = "move annotation"

    def __init__(self, view: "PdfView", moves) -> None:
        super().__init__(view)
        # moves: iterable of (page, xref, dx, dy)
        self.moves = list(moves)
        if len(self.moves) > 1:
            self.label = f"move {len(self.moves)} annotations"

    def _apply(self, sign: int) -> bool:
        ok = False
        for page, xref, dx, dy in self.moves:
            if self.view.pdf.move_annot(xref, sign * dx, sign * dy):
                ok = True
        if ok:
            self.view._after_annot_edit([m[0] for m in self.moves])
        return ok

    def undo(self) -> bool:
        return self._apply(-1)

    def redo(self) -> bool:
        return self._apply(1)


class _ResizeAnnotCommand(_AnnotCommand):
    """Resize one annotation from one page-space rect to another."""

    label = "resize annotation"

    def __init__(self, view: "PdfView", page: int, xref: int,
                 old_rect, new_rect) -> None:
        super().__init__(view)
        self.page = page
        self.xref = xref
        self.old_rect = tuple(old_rect)
        self.new_rect = tuple(new_rect)

    def undo(self) -> bool:
        ok = self.view.pdf.resize_annot_rect(self.xref, self.old_rect)
        if ok:
            self.view._after_annot_edit([self.page])
        return ok

    def redo(self) -> bool:
        ok = self.view.pdf.resize_annot_rect(self.xref, self.new_rect)
        if ok:
            self.view._after_annot_edit([self.page])
        return ok


class _StyleAnnotCommand(_AnnotCommand):
    """Restyle one annotation (colour / opacity / border width)."""

    label = "restyle annotation"

    def __init__(self, view: "PdfView", page: int, xref: int, attr: str,
                 old_value, new_value) -> None:
        super().__init__(view)
        self.page = page
        self.xref = xref
        self.attr = attr              # 'color' | 'opacity' | 'width'
        self.old_value = old_value
        self.new_value = new_value

    def _apply(self, value) -> bool:
        pdf = self.view.pdf
        if self.attr == "color":
            ok = pdf.set_annot_color(self.xref, value)
        elif self.attr == "opacity":
            ok = pdf.set_annot_opacity(self.xref, value)
        elif self.attr == "width":
            ok = pdf.set_annot_border_width(self.xref, value)
            if not ok:
                # Markups carry no border; recolour instead is wrong, so
                # report failure rather than silently doing nothing.
                return False
        else:
            return False
        if ok:
            self.view._after_annot_edit([self.page])
        return ok

    def undo(self) -> bool:
        return self._apply(self.old_value)

    def redo(self) -> bool:
        return self._apply(self.new_value)


class _CompositeCommand(_AnnotCommand):
    """Runs several commands as one undo step (group operations)."""

    def __init__(self, view: "PdfView", commands, label: str = "edit") -> None:
        super().__init__(view)
        self.commands = list(commands)
        self.label = label

    def undo(self) -> bool:
        ok = False
        for cmd in reversed(self.commands):
            if cmd.undo():
                ok = True
        return ok

    def redo(self) -> bool:
        ok = False
        for cmd in self.commands:
            if cmd.redo():
                ok = True
        return ok


class UndoStack:
    """Per-view LIFO command stack (no QUndoStack dependency)."""

    def __init__(self, limit: int = 100) -> None:
        self._undo: list[_AnnotCommand] = []
        self._redo: list[_AnnotCommand] = []
        self._limit = limit

    def push(self, cmd: _AnnotCommand) -> None:
        self._undo.append(cmd)
        if len(self._undo) > self._limit:
            self._undo.pop(0)
        self._redo.clear()
    
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> str | None:
        """Undo the top command; returns its label or None if empty."""
        if not self._undo:
            return None
        cmd = self._undo.pop()
        ok = False
        try:
            ok = cmd.undo()
        except Exception:
            logger.exception("undo failed")
        if ok:
            self._redo.append(cmd)
            return cmd.label
        return None

    def redo(self) -> str | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        ok = False
        try:
            ok = cmd.redo()
        except Exception:
            logger.exception("redo failed")
        if ok:
            self._undo.append(cmd)
            return cmd.label
        return None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()



class PageCache:
    """Memory-aware LRU page bitmap cache."""

    def __init__(self, budget_mb: float = 160) -> None:
        self.budget_bytes = int(budget_mb * 1024 * 1024)
        self._entries: OrderedDict[tuple, tuple[QPixmap, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key) -> Optional[QPixmap]:
        if key not in self._entries:
            return None
        pm, size = self._entries.pop(key)
        self._entries[key] = (pm, size)
        return pm

    def put(self, key, pixmap: QPixmap) -> None:
        if key in self._entries:
            old_pm, old_size = self._entries.pop(key)
            self._bytes -= old_size
        size = pixmap.width() * pixmap.height() * 4
        self._entries[key] = (pixmap, size)
        self._bytes += size
        while self._bytes > self.budget_bytes and len(self._entries) > 1:
            _, (pm, s) = self._entries.popitem(last=False)
            self._bytes -= s

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def stats(self) -> tuple[int, float]:
        return len(self._entries), self._bytes / 1024 / 1024


# Per-view page themes (#77): canvas background tints behind the pages.
# Chrome (toolbars, panels) keeps following the app-wide theme; this only
# changes the reading surface. Page rendering itself stays original.
VIEW_THEMES = {
    "": "#F5F3EE",      # follow app theme (default canvas)
    "light": "#F5F3EE",
    "sepia": "#E8DCC4",
    "dark": "#2B2926",
    "oled": "#000000",
}


class PdfView(DocumentView):
    view_id = "pdf"

    page_rendered = Signal(int)
    action_toast = Signal(str, str, object)   # message, kind, action callback

    MODE_SINGLE = "single"
    MODE_CONTINUOUS = "continuous"
    MODE_TWO = "two-page"
    MODE_TWO_COVER = "two-cover"

    # Signals for the enhanced status bar / main-window sync (#81).
    tool_changed = Signal(str)          # current annotation tool id ("" = none)
    reading_tick = Signal(int)          # seconds spent reading this document

    # ------------------------------------------------------------------ reading modes
    def start_autoscroll(self, speed: float = 1.0) -> None:
        """Begin smooth auto-scrolling (#7). Speed is a multiplier; Space
        toggles via toggle_autoscroll(), +/- adjust via autoscroll_speed()."""
        self._autoscroll_speed = max(0.1, min(10.0, speed))
        self._autoscroll_frac = 0.0  # sub-pixel accumulator
        self._autoscroll_timer.start()

    def stop_autoscroll(self) -> None:
        self._autoscroll_timer.stop()

    def toggle_autoscroll(self) -> None:
        if self._autoscroll_timer.isActive():
            self.stop_autoscroll()
        else:
            self.start_autoscroll(self._autoscroll_speed or 1.0)

    def autoscroll_speed(self) -> float:
        return self._autoscroll_speed

    def set_autoscroll_speed(self, speed: float) -> None:
        self._autoscroll_speed = max(0.1, min(10.0, speed))

    def _autoscroll_step(self) -> None:
        """Advance scroll by speed px/frame with sub-pixel accumulation."""
        if not self._page_items:
            return
        bar = self._view.verticalScrollBar()
        distance = self._autoscroll_speed  # px per frame (16 ms)
        self._autoscroll_frac += distance
        whole = int(self._autoscroll_frac)
        if whole <= 0:
            return
        self._autoscroll_frac -= whole
        max_v = bar.maximum()
        if bar.value() >= max_v and whole > 0:
            self.stop_autoscroll()  # reached the end of the document
            self.request_toast.emit("End of document — autoscroll stopped", "info")
        else:
            bar.setValue(min(max_v, bar.value() + whole))

    def start_slideshow(self, interval_s: float = 5.0) -> None:
        """Auto-advance one page every ``interval_s`` seconds (#8)."""
        self._slideshow_interval = max(0.5, min(120.0, interval_s))
        self._slideshow_timer.setInterval(int(self._slideshow_interval * 1000))
        self._slideshow_timer.start()

    def stop_slideshow(self) -> None:
        self._slideshow_timer.stop()

    def slideshow_active(self) -> bool:
        return self._slideshow_timer.isActive()

    def _slideshow_next(self) -> None:
        if self._current_page + 1 < self.page_count:
            self.go_to_page(self._current_page + 1, _record=False)
        else:
            self.stop_slideshow()
            self.request_toast.emit("End of document — slideshow finished", "info")

    def set_rtl(self, enabled: bool) -> None:
        """Right-to-left page order (#59): pair pages right→left in two-page
        modes so manga/Japanese-style documents read correctly."""
        self._rtl = bool(enabled)
        self._relayout()
        self.state_changed.emit()

    def rtl(self) -> bool:
        return self._rtl

    def reading_seconds(self) -> int:
        return self._reading_seconds

    def _reading_tick(self) -> None:
        self._reading_seconds += 1
        self.reading_tick.emit(self._reading_seconds)

    def __init__(self, engine: PdfEngine, parent=None) -> None:
        super().__init__(engine, parent)
        self.pdf: PdfEngine = engine

        self._mode = self.MODE_CONTINUOUS
        self._rotation = 0
        self._fit_mode = "width"          # width|page|height|none
        self._cache = PageCache(budget_mb=160)
        self._pending: dict[tuple, list] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._highlights: dict[int, list] = {}   # page -> [QRectF in page coords]
        self._annot_tool: str = ""               # current annotation tool
        self._annot_color = "#E5B25D"
        self._annot_opacity = 0.4
        self._annot_width = 2.0
        self._drag_ink: list = []
        self._drag_start: QPointF | None = None
        self._rubber: QGraphicsRectItem = None  # type: ignore
        self._sticky_notes: list[StickyNoteItem] = []  # on-page editable notes
        # Per-view undo/redo for annotations + sticky notes (#13).
        self.undo_stack = UndoStack(limit=100)
        self._undoing = False
        # Phase 2 annotation editing: marquee multi-select, move, resize and
        # restyle of *existing* annotations. Entries are (page, xref) pairs.
        self._selection: list[tuple[int, int]] = []
        self._sel_outline_items: list[QGraphicsRectItem] = []
        self._sel_handles: list[QGraphicsRectItem] = []
        # Floating quick-toolbar near the cursor while annotating (#84).
        self._quick_bar: QuickAnnotBar | None = None
        # Spread tuning (#10) and per-view theme (#77) — set BEFORE the
        # first _build_scene(), which consumes these values.
        self._gap = 14.0
        self._margin = 12.0
        self._theme = ""

        self._scene = QGraphicsScene(self)
        self._view = _PdfGraphicsView(self._scene, self)
        self._view.setDragMode(QGraphicsView.ScrollHandDrag)
        self._view.setRenderHint(QPainter.Antialiasing, True)
        self._view.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self._view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self._view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._view.horizontalScrollBar().valueChanged.connect(self._on_scroll)
        self._render_signals = _RenderSignals(self)
        self._render_signals.finished.connect(self._on_render_done)
        self._render_signals.failed.connect(self._on_render_failed)

        lay = self.layout() or None
        from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._view)

        self._page_items: list[PageSlotItem] = []
        self._build_scene()

        # Palette / style controls for the annotation tools.
        self._annot_palette: AnnotationStylePalette | None = None

        # Debounced relayout on resize.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(180)
        self._resize_timer.timeout.connect(self._relayout)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(60)
        self._debounce_timer.timeout.connect(self._apply_zoom_now)

        # Animated zoom (buttons). While animating, we move page items each
        # frame so the chosen anchor point stays under the viewport center.
        self._zoom_anim = QTimer(self)
        self._zoom_anim.setSingleShot(False)
        self._zoom_anim.setInterval(16)
        self._zoom_anim.timeout.connect(self._step_zoom_anim)
        self._zoom_anim_from = 1.0
        self._zoom_anim_to = 1.0
        self._zoom_anim_anchor: QPointF | None = None
        self._zoom_anim_start_center: QPointF | None = None

        # Reading-experience timers: autoscroll (#7), slideshow (#8),
        # reading-time ticker (#81).
        self._autoscroll_speed = 1.0
        self._autoscroll_frac = 0.0
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(16)
        self._autoscroll_timer.timeout.connect(self._autoscroll_step)
        self._slideshow_interval = 5.0
        self._slideshow_timer = QTimer(self)
        self._slideshow_timer.timeout.connect(self._slideshow_next)
        self._reading_seconds = 0
        self._reading_timer = QTimer(self)
        self._reading_timer.setInterval(1000)
        self._reading_timer.timeout.connect(self._reading_tick)
        self._reading_timer.start()
        self._rtl = False

        # Annotation flash highlight (panel jump polish).
        self._flash_rect_item = None
        self._flash_cycles_left = 0
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(280)
        self._flash_timer.timeout.connect(self._flash_tick)

    # ------------------------------------------------------------------ scene
    def _build_scene(self) -> None:
        self._scene.clear()
        # scene.clear() destroys the overlay items too; drop the stale refs.
        self._sel_outline_items = []
        self._sel_handles = []
        self._page_items = []
        infos = [self.pdf.page_info(i) for i in range(self.page_count)]
        y = self._margin
        gap = self._gap
        for i, info in enumerate(infos):
            w, h = info.width, info.height
            if self._rotation % 180 == 90:
                w, h = h, w
            item = PageSlotItem(i, w, h)
            self._scene.addItem(item)
            # Layout: compute later in _relayout (mode-dependent).
            self._page_items.append(item)
        self._relayout()

    def _relayout(self) -> None:
        """Position page items per view mode.

        Two-page pairing (#10): MODE_TWO pairs (0,1),(2,3)…; MODE_TWO_COVER
        places page 0 alone as a cover, then pairs (1,2),(3,4)…  RTL (#59)
        mirrors every pair so even indices sit on the right (manga order).
        """
        if not self._page_items:
            return
        gap = self._gap
        margin = self._margin
        two = self._mode in (self.MODE_TWO, self.MODE_TWO_COVER)
        y = margin
        x = margin
        row_max_h = 0.0
        start = 1 if self._mode == self.MODE_TWO_COVER else 0
        for i, item in enumerate(self._page_items):
            w = item.page_width
            h = item.page_height
            if not two:
                item.setPos(x, y)
                y += h + gap
                continue
            if i < start:
                # Lone cover page: left slot for books, right slot for manga.
                item.setPos(margin + w + gap if self._rtl else margin, y)
                y += h + gap
                continue
            k = i - start
            if self._rtl:
                if k % 2 == 0:
                    # Right-hand page of the pair; its left partner is i+1.
                    partner = (self._page_items[i + 1]
                               if i + 1 < len(self._page_items) else None)
                    pw = partner.page_width if partner is not None else 0
                    item.setPos(margin + pw + gap, y)
                    row_max_h = max(row_max_h, h)
                else:
                    # Left-hand page; the pair is complete — advance a row.
                    item.setPos(margin, y)
                    y += row_max_h + gap
                    row_max_h = 0
                    row_max_h = max(row_max_h, h)
            else:
                if k % 2 == 0:
                    # Left-hand page; remember where the right slot starts.
                    item.setPos(margin, y)
                    x = margin + w + gap
                    row_max_h = max(row_max_h, h)
                else:
                    # Right-hand page; the pair is complete — advance a row.
                    item.setPos(x, y)
                    y += row_max_h + gap
                    row_max_h = 0
                    x = margin
                    row_max_h = max(row_max_h, h)
        self._scene.setSceneRect(0, 0, self._view.viewport().width(),
                                 max(y + 12, 400))
        self._request_visible(force=True)
        # Selection outlines live in scene coordinates, so they must be
        # redrawn whenever pages move (zoom, mode, rotation, resize).
        if self._selection:
            self._refresh_selection_overlay()

    # ------------------------------------------------------------------ zoom
    def set_zoom(self, zoom: float) -> None:
        self._fit_mode = "none"
        self._zoom = clamp_zoom(zoom)
        self._debounce_timer.start()

    def _apply_zoom_now(self) -> None:
        self._request_visible(force=True)
        self.state_changed.emit()

    def zoom_in(self) -> None:
        self._animated_zoom(1.25)

    def zoom_out(self) -> None:
        self._animated_zoom(1.0 / 1.25)

    def _animated_zoom(self, factor: float) -> None:
        """Smoothly zoom toward the viewport center (or page center if focused).

        We pick an anchor point in *scene* coords that should remain under the
        viewport center throughout the animation, then on each animation frame
        we update the zoom and shift every page item by the same delta so the
        anchor stays put.
        """
        if not self._page_items:
            return
        old_zoom = self._zoom
        new_zoom = clamp_zoom(old_zoom * factor)
        if new_zoom == old_zoom:
            return

        # Choose anchor: prefer the current page center, else viewport center.
        anchor = self._zoom_anim_anchor_target()
        if anchor is None:
            # Fallback: viewport center in scene coords (before zoom change).
            vp = self._view.viewport()
            c = vp.rect().center()
            anchor = self._view.mapToScene(c)

        # Before changing zoom, record the scene point that is currently under
        # the viewport center; after zoom we want that same scene point to sit
        # under the viewport center again.
        vp = self._view.viewport()
        center = vp.rect().center()
        center_scene = self._view.mapToScene(center)

        self._zoom_anim_from = old_zoom
        self._zoom_anim_to = new_zoom
        self._zoom_anim_anchor = center_scene
        self._zoom_anim_start_center = center_scene
        self._zoom_anim.start()

    def _cancel_zoom_anim(self) -> None:
        """Stop a running zoom animation, freezing the current zoom.

        Called when the user takes manual control (e.g. wheel zoom) so the
        animation and the manual anchor-shift never fight over page items.
        """
        if self._zoom_anim.isActive():
            self._zoom_anim.stop()
            self._zoom_anim_anchor = None
            self._zoom_anim_start_center = None

    def _zoom_anim_anchor_target(self) -> QPointF | None:
        """Scene point to keep under the viewport center during zoom."""
        if not self._page_items:
            return None
        idx = min(self._current_page, len(self._page_items) - 1)
        item = self._page_items[idx]
        cx = item.pos().x() + item.page_width * self._zoom / 2
        cy = item.pos().y() + item.page_height * self._zoom / 2
        return QPointF(cx, cy)

    def _step_zoom_anim(self) -> None:
        """Eased single frame of the zoom animation."""
        from_ = self._zoom_anim_from
        to = self._zoom_anim_to
        if abs(to - from_) < 1e-4:
            self._zoom_anim.stop()
            self._zoom = to
            self._request_visible(force=True)
            self.state_changed.emit()
            return

        # Quadratic ease toward target.
        t = min(1.0, 1.0 / 20.0)  # cap per-frame progress so it stays smooth
        progress = 1.0 - (1.0 - t) ** 2
        # We step a fixed small z delta instead of easing the whole interval,
        # which keeps each frame cheap and deterministic.
        direction = 1.0 if to > from_ else -1.0
        remaining = abs(to - from_)
        step = max(0.001, min(remaining, remaining * 0.18))
        next_zoom = from_ + direction * step
        if (direction > 0 and next_zoom >= to) or (direction < 0 and next_zoom <= to):
            next_zoom = to

        old_zoom = self._zoom
        self._zoom = clamp_zoom(next_zoom)
        if self._zoom == old_zoom:
            return

        # Keep the chosen anchor under the viewport center.
        anchor = self._zoom_anim_anchor
        if anchor is None:
            self._request_visible(force=True)
            self.state_changed.emit()
            return

        vp = self._view.viewport()
        center = vp.rect().center()
        center_scene = self._view.mapToScene(center)

        # How much to move all page items so the anchor lands on center_scene.
        dx = anchor.x() - center_scene.x()
        dy = anchor.y() - center_scene.y()

        for item in self._page_items:
            item.setPos(item.pos().x() + dx, item.pos().y() + dy)

        self._zoom_anim_from = self._zoom
        self._request_visible(force=True)
        self.state_changed.emit()

    def fit_width(self) -> None:
        self._fit_mode = "width"
        self._compute_fit()

    def fit_page(self) -> None:
        self._fit_mode = "page"
        self._compute_fit()

    def fit_height(self) -> None:
        self._fit_mode = "height"
        self._compute_fit()

    def _compute_fit(self) -> None:
        if not self._page_items:
            return
        vp_w = self._view.viewport().width() - 40
        vp_h = self._view.viewport().height() - 40
        info = self.pdf.page_info(min(self._current_page, self.page_count - 1))
        w, h = info.width, info.height
        if self._rotation % 180 == 90:
            w, h = h, w
        if self._fit_mode == "width":
            z = vp_w / max(1.0, w)
        elif self._fit_mode == "page":
            z = min(vp_w / max(1.0, w), vp_h / max(1.0, h))
        else:
            z = vp_h / max(1.0, h)
        self._zoom = clamp_zoom(z)
        self._request_visible(force=True)
        self.state_changed.emit()

    def rotate(self, degrees: int) -> None:
        self._rotation = (self._rotation + degrees) % 360
        keep = self._current_page
        self._build_scene()
        self._compute_fit()
        if self._page_items:
            self.go_to_page(min(keep, len(self._page_items) - 1))
        self.state_changed.emit()

    # ------------------------------------------------------------------ modes
    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._relayout()
        self.state_changed.emit()

    def mode(self) -> str:
        return self._mode

    # -- spread tuning (#10) -------------------------------------------------
    def set_gap(self, gap: float) -> None:
        """Spacing between pages/rows in scene units (two-page gap tuning)."""
        self._gap = max(0.0, float(gap))
        self._relayout()
        self.state_changed.emit()

    def gap(self) -> float:
        return self._gap

    def set_cover_offset(self, enabled: bool) -> None:
        """Offset the first page so page 1 pairs with page 2 in two-cover."""
        if enabled and self._mode != self.MODE_TWO_COVER:
            self._mode = self.MODE_TWO_COVER
        elif not enabled and self._mode == self.MODE_TWO_COVER:
            self._mode = self.MODE_TWO
        self._relayout()
        self.state_changed.emit()

    def cover_offset(self) -> bool:
        return self._mode == self.MODE_TWO_COVER

    # ------------------------------------------------------------------ render pipeline
    def _request_visible(self, force: bool = False) -> None:
        """Enqueue rendering for visible (and nearby) pages."""
        if not self._page_items:
            return
        viewport_rect = self._view.mapToScene(self._view.viewport().rect()).boundingRect()
        prefetch = 2
        for item in self._page_items:
            item_rect = QRectF(item.pos(), QSizeF(item.page_width * self._zoom,
                                                  item.page_height * self._zoom))
            if item_rect.intersects(viewport_rect):
                self._ensure_render(item, self._zoom)
            elif abs(item_rect.top() - viewport_rect.center().y()) < \
                    (viewport_rect.height() * (prefetch + 0.5)):
                self._ensure_render(item, self._zoom, low_priority=True)
            else:
                item.show_placeholder()

    def _ensure_render(self, item: "PageSlotItem", zoom: float,
                       low_priority: bool = False) -> None:
        key = (item.page_index, round(zoom, 2), self._rotation)
        cached = self._cache.get(key)
        if cached is not None:
            item.set_pixmap(cached, zoom)
            return
        if key in self._pending:
            return
        runnable = _PageRenderRunnable(self, item.page_index, zoom, key)
        self._pending[key] = [item.page_index]
        self._pool.start(runnable)

    def _on_render_done(self, page_index: int, zoom: float, key, image: QImage) -> None:
        self._pending.pop(key, None)
        if image.isNull():
            return
        pm = QPixmap.fromImage(image)
        self._cache.put(key, pm)
        if not self._page_items or page_index >= len(self._page_items):
            return
        item = self._page_items[page_index]
        current_key = (page_index, round(self._zoom, 2), self._rotation)
        item.set_pixmap(pm, self._zoom if key == current_key else key[1])
        self.page_rendered.emit(page_index)

    def _on_render_failed(self, key) -> None:
        self._pending.pop(key, None)

    def _on_scroll(self, value: int) -> None:
        self._request_visible()
        self._update_current_page_from_scroll()
        self.state_changed.emit()

    def _update_current_page_from_scroll(self) -> None:
        viewport_rect = self._view.mapToScene(self._view.viewport().rect()).boundingRect()
        center_y = viewport_rect.center().y()
        best, best_dist = self._current_page, 1e18
        for item in self._page_items:
            mid = item.pos().y() + item.page_height * self._zoom / 2
            dist = abs(mid - center_y)
            if dist < best_dist:
                best, best_dist = item.page_index, dist
        if best != self._current_page:
            self._current_page = best
            self.state_changed.emit()

    # ------------------------------------------------------------------ navigation
    def go_to_page(self, index: int, *, _record: bool = True) -> bool:
        if not (0 <= index < len(self._page_items)):
            return False
        if _record and not self._nav_programmatic \
                and index != self._current_page:
            self._nav_back.append(self._current_page)
            if len(self._nav_back) > self.NAV_HISTORY_MAX:
                self._nav_back.pop(0)
            self._nav_forward.clear()
            self.nav_state_changed.emit()
        self._current_page = index
        item = self._page_items[index]
        # Scroll so the top of the target page sits a little above the
        # viewport top, regardless of current zoom.
        target_scroll = int(item.pos().y())
        self._view.verticalScrollBar().setValue(target_scroll)
        self.state_changed.emit()
        return True

    def page_info(self, index: int = -1):
        idx = self._current_page if index < 0 else index
        try:
            return self.pdf.page_info(idx)
        except Exception:
            return None

    # ------------------------------------------------------------------ text
    def selected_text(self) -> str:
        return self._view.textCursorSelected()

    def find_in_view(self, query: str, case_sensitive: bool = False,
                     whole_word: bool = False, regex: bool = False,
                     backwards: bool = False) -> int:
        """Find in the PDF via PyMuPDF search; highlights all hits on pages."""
        if not query:
            self.clear_find()
            return 0
        flags = 0
        try:
            import fitz
            hits_total = 0
            self._highlights.clear()
            start_page = self._current_page if not backwards else self._current_page
            order = list(range(self.page_count))
            if backwards:
                order = order[: self._current_page + 1][::-1] + \
                    order[self._current_page + 1:][::-1]
            for pno in order:
                page = self.pdf._doc[pno]
                quads = page.search_for(query, quads=True)
                if quads:
                    self._highlights[pno] = [
                        QRectF(q.rect.x0, q.rect.y0, q.rect.width, q.rect.height)
                        for q in quads]
                    hits_total += len(quads)
                    if hits_total > 500:
                        break
            for item in self._page_items:
                item.set_search_highlights(self._highlights.get(item.page_index, []))
            if self._highlights:
                first_page = min(self._highlights.keys())
                self.go_to_page(first_page)
            return hits_total
        except Exception:
            logger.exception("find failed")
            return 0

    def clear_find(self) -> None:
        self._highlights.clear()
        for item in self._page_items:
            item.set_search_highlights([])

    def goto_search_hit(self, hit: dict) -> None:
        self.go_to_page(int(hit.get("page", 0)))

    # ------------------------------------------------------------------ annotations
    def set_annotation_tool(self, tool: str) -> None:
        self._annot_tool = tool
        # Activating any drawing tool ends annotation editing; the neutral
        # "" tool is Select/Pan, where editing lives.
        if tool:
            self.clear_selection()
        self._view.set_annotation_mode(tool)
        self.tool_changed.emit(tool)
        # Quick bar (#84) lives only while a drawing tool is active.
        if not tool or tool == "eraser":
            if self._quick_bar is not None:
                self._quick_bar.hide()
        if tool:
            if tool == "eraser":
                # Eraser needs mouse-move tracking, not NoDrag pan
                self._view.setDragMode(QGraphicsView.NoDrag)
                self._view.setCursor(Qt.PointingHandCursor)
            else:
                self._view.setDragMode(QGraphicsView.NoDrag)
                self._view.setCursor(Qt.CrossCursor)
        else:
            self._view.setDragMode(QGraphicsView.ScrollHandDrag)
            self._view.unsetCursor()

    def annotation_tool(self) -> str:
        return self._annot_tool

    def set_annotation_style(self, color: str | None = None,
                             opacity: float | None = None,
                             width: float | None = None) -> None:
        """Update annotation style; pass None to leave that attribute unchanged."""
        if color is not None:
            self._annot_color = color
        if opacity is not None:
            self._annot_opacity = opacity
        if width is not None:
            self._annot_width = width
        self._sync_style_widgets()

    def _sync_style_widgets(self) -> None:
        """Reflect the current style in the quick bar and corner palette."""
        style = (self._annot_color, self._annot_opacity, self._annot_width)
        if self._quick_bar is not None:
            self._quick_bar.sync_from_style(*style)
        palette = getattr(self, "_annot_palette", None)
        if palette is not None and palette.isVisible():
            try:
                palette._set_color(self._annot_color)
                palette._thick_slider.setValue(self._annot_width)
                palette._trans_slider.setValue(
                    int(round((1.0 - self._annot_opacity) * 100)))
            except Exception:
                logger.exception("_sync_style_widgets failed")

    def _pop_quick_bar(self, event) -> None:
        """Show the floating quick-toolbar next to the cursor (#84)."""
        if not self._annot_tool:
            return
        if self._quick_bar is None:
            self._quick_bar = QuickAnnotBar()
            self._quick_bar.style_changed.connect(
                lambda c, o, w: self.set_annotation_style(
                    color=c, opacity=o, width=w))
            self._quick_bar.sync_from_style(
                self._annot_color, self._annot_opacity, self._annot_width)
        self._quick_bar.pop_at(self._view.viewport().mapToGlobal(QPoint(0, 0)),
                               event.position().toPoint())

    def add_annotation_at(self, page: int, kind: str, page_rect, *,
                          quads=None, points=None, text: str = "") -> Optional[dict]:
        """Create a PDF annotation. Returns the annotation dict."""
        import fitz
        try:
            if kind == "highlight":
                xref = self.pdf.add_highlight(
                    page, [fitz.Rect(*page_rect)], self._annot_color,
                    self._annot_opacity)
            elif kind in ("underline", "strikeout", "squiggly"):
                xref = self.pdf.add_text_markup(
                    page, [fitz.Rect(*page_rect)], kind, self._annot_color,
                    self._annot_opacity)
            elif kind == "note":
                xref = self.pdf.add_note(
                    page, page_rect[:2], text or "Note",
                    self._annot_color)
            elif kind == "freetext":
                xref = self.pdf.add_free_text(
                    page, page_rect, text or "Text", self._annot_color)
            elif kind == "ink":
                xref = self.pdf.add_ink(
                    page, [points] if points else [], self._annot_color,
                    self._annot_width)
            elif kind in ("rectangle", "ellipse", "line", "arrow"):
                xref = self.pdf.add_shape(
                    page, kind, page_rect, self._annot_color, self._annot_width)
            elif kind == "stamp":
                xref = self.pdf.add_stamp(page, page_rect)
            else:
                return None
            self.content_changed.emit()
            self._rerender_page(page)
            cmd = _AddAnnotCommand(
                self, xref, page, kind, page_rect, quads=quads,
                points=points, text=text)
            self.undo_stack.push(cmd)
            return {"xref": xref, "page": page, "type": kind}
        except Exception:
            logger.exception("annotation failed")
            return None

    def add_sticky_note_on_page(self, page_index: int,
                                scene_pos: QPointF) -> StickyNoteItem:
        """Place a new resizable, editable sticky note widget on the page.

        The widget lives in the QGraphicsScene as a QGraphicsWidget so it can
        be dragged, resized from its corners, and edited in place.
        """
        item = StickyNoteItem(page_index, scene_pos, self._annot_color,
                              pdf_view=self)
        item.finished.connect(self._on_sticky_note_finished)
        self._scene.addItem(item)
        self._sticky_notes.append(item)
        item.setPos(scene_pos)
        item.setFlag(QGraphicsItem.ItemIsMovable)
        item.setFlag(QGraphicsItem.ItemIsSelectable)
        item.setAcceptHoverEvents(True)
        item.setZValue(100)
        item.edit_text(self._annot_color)
        self.content_changed.emit()
        # Record undoable add (#13) — unless we're inside an undo/redo restore.
        if not self._undoing:
            self.undo_stack.push(_StickyNoteCommand(
                self, "add", item=item, page=page_index,
                scene_pos=QPointF(scene_pos), color=self._annot_color))
        return item

    def _on_sticky_note_finished(self, item: StickyNoteItem) -> None:
        # Persist the note text to the PDF as a text annotation so it survives
        # save/close. We keep the on-screen widget in sync with the PDF.
        if item.page_index is None:
            return
        if getattr(item, "_deleted", False):
            # User pressed the delete button: remove the note entirely and
            # record an undoable delete (#13) with an Undo-button toast (#82).
            self.undo_stack.push(_StickyNoteCommand(
                self, "delete", item=item, page=item.page_index,
                scene_pos=item.pos()))
            self.remove_sticky_note(item)
            self.offer_undo_delete("Sticky note deleted")
            return
        try:
            import fitz
            rect = item.boundingRect()
            self.pdf.add_free_text(
                item.page_index,
                (rect.x(), rect.y(), rect.x() + rect.width(),
                 rect.y() + rect.height()),
                item.text() or "Note",
                self._annot_color,
            )
        except Exception:
            logger.exception("sticky note persist failed")
        self._rerender_page(item.page_index)

    def sticky_note_at(self, scene_pos: QPointF) -> StickyNoteItem | None:
        for n in self._sticky_notes:
            if n.isUnderline(scene_pos):
                return n
        return None

    def remove_sticky_note(self, item: StickyNoteItem) -> None:
        if item not in self._sticky_notes:
            return
        self._sticky_notes.remove(item)
        self._scene.removeItem(item)
        item.deleteLater()
        self.content_changed.emit()
        if item.page_index is not None:
            self._rerender_page(item.page_index)

    def delete_annotation_xref(self, xref: int) -> None:
        self.pdf.delete_annot_by_xref(xref)
        self.content_changed.emit()

    # -- selection + editing (Phase 2: post-creation annotation edits) ------
    def selection(self) -> list[tuple[int, int]]:
        """Selected annotations as (page, xref) pairs, in selection order."""
        return list(self._selection)

    def selection_count(self) -> int:
        return len(self._selection)

    def annotation_items(self) -> list[tuple[int, int]]:
        """Every PDF annotation here as a (page, xref) pair."""
        out: list[tuple[int, int]] = []
        try:
            for a in self.pdf.annotations():
                xref = a.get("pdf_xref")
                if xref:
                    out.append((int(a.get("page", 0)), int(xref)))
        except Exception:
            logger.exception("annotation listing failed")
        return out

    def clear_selection(self) -> None:
        if not self._selection:
            return
        self._selection = []
        self._refresh_selection_overlay()

    def select_annotations(self, items, *, add: bool = False) -> None:
        """Select (page, xref) pairs; ``add`` extends the current selection."""
        sel = list(self._selection) if add else []
        for page, xref in items:
            pair = (int(page), int(xref))
            if pair not in sel:
                sel.append(pair)
        self._selection = sel
        self._refresh_selection_overlay()

    def toggle_annotation(self, page: int, xref: int) -> None:
        """Ctrl-click behaviour: add to or remove from the selection."""
        pair = (int(page), int(xref))
        sel = list(self._selection)
        if pair in sel:
            sel.remove(pair)
        else:
            sel.append(pair)
        self._selection = sel
        self._refresh_selection_overlay()

    def select_all_on_page(self, page: int) -> int:
        """Select every annotation on ``page``; returns how many."""
        items = [p for p in self.annotation_items() if p[0] == page]
        self.select_annotations(items)
        return len(items)

    def _visual_rect(self, xref: int):
        """An annotation's drawn bounds in page coords (None if unusable)."""
        try:
            r = self.pdf.annot_visual_rect(xref)
        except Exception:
            return None
        if r is None or r.is_empty:
            return None
        return r

    def _scene_rect_for(self, page: int, xref: int) -> QRectF | None:
        """Scene-space box of an annotation (page position + zoom applied)."""
        rect = self._visual_rect(xref)
        if rect is None or not (0 <= page < len(self._page_items)):
            return None
        item = self._page_items[page]
        z = max(0.01, self.zoom)
        return QRectF(item.pos().x() + rect.x0 * z,
                      item.pos().y() + rect.y0 * z,
                      max(1.5, rect.width * z), max(1.5, rect.height * z))

    def _clear_selection_items(self) -> None:
        for it in list(self._sel_outline_items) + list(self._sel_handles):
            try:
                self._scene.removeItem(it)
            except Exception:
                logger.exception("_clear_selection_items failed")
        self._sel_outline_items = []
        self._sel_handles = []

    def _refresh_selection_overlay(self, offset: QPointF | None = None) -> None:
        """Redraw selection outlines, plus resize handles for a single pick.

        ``offset`` shifts the drawn outlines only, which is how a drag
        previews a pending move without touching the document.
        """
        self._clear_selection_items()
        if not self._selection:
            return
        offset = offset or QPointF(0.0, 0.0)
        for page, xref in self._selection:
            r = self._scene_rect_for(page, xref)
            if r is None:
                continue
            outline = QGraphicsRectItem(r.translated(offset))
            outline.setPen(QPen(QColor("#2A6FB0"), 1.4, Qt.DashLine))
            outline.setBrush(Qt.NoBrush)
            outline.setZValue(140)
            self._scene.addItem(outline)
            self._sel_outline_items.append(outline)
        if len(self._selection) == 1:
            # Corner handles: only single annotations resize (and pop-up
            # notes report no resizable rect, so no handles appear for them).
            r = self._scene_rect_for(*self._selection[0])
            if r is None:
                return
            r = r.translated(offset)
            size = 7.0
            for cx, cy in ((r.left(), r.top()), (r.right(), r.top()),
                           (r.left(), r.bottom()), (r.right(), r.bottom())):
                handle = QGraphicsRectItem(
                    QRectF(cx - size / 2, cy - size / 2, size, size))
                handle.setPen(QPen(QColor("#FFFFFF"), 1.0))
                handle.setBrush(QColor("#2A6FB0"))
                handle.setZValue(141)
                self._scene.addItem(handle)
                self._sel_handles.append(handle)

    def _after_annot_edit(self, pages) -> None:
        """Post-edit bookkeeping: re-render pages and refresh overlays."""
        for page in sorted({int(p) for p in pages}):
            self._rerender_page(page)
        self.content_changed.emit()
        self._refresh_selection_overlay()

    def _delete_command_for(self, xref: int):
        """Undoable delete command carrying faithful geometry, or None."""
        rec = None
        for a in self.pdf.annotations():
            if a.get("pdf_xref") == xref:
                rec = a
                break
        if rec is None:
            return None
        try:
            geom = self.pdf.annot_geometry_snapshot(xref)
            geom["width"] = self.pdf.annot_stroke_width(xref)
        except Exception:
            geom = {}
        return _DeleteAnnotCommand(self, xref, rec, geometry=geom)

    def delete_selection(self) -> int:
        """Delete every selected annotation as one undoable step."""
        if not self._selection:
            return 0
        cmds = []
        pages: list[int] = []
        for page, xref in list(self._selection):
            cmd = self._delete_command_for(xref)
            if cmd is None:
                continue
            cmds.append(cmd)
            pages.append(page)
        if not cmds:
            return 0
        self._selection = []
        for cmd in cmds:
            cmd.redo()
        self.undo_stack.push(_CompositeCommand(
            self, cmds, f"delete {len(cmds)} annotation(s)"))
        self._after_annot_edit(pages)
        return len(cmds)

    def set_selection_style(self, *, color: str | None = None,
                            opacity: float | None = None,
                            width: float | None = None) -> int:
        """Restyle every selected annotation; returns how many were changed."""
        if not self._selection:
            return 0
        recs = {int(a["pdf_xref"]): a for a in self.pdf.annotations()
                if a.get("pdf_xref")}
        cmds: list[_AnnotCommand] = []
        for page, xref in self._selection:
            rec = recs.get(xref)
            if rec is None:
                continue
            if color is not None and \
                    str(rec.get("color", "")).lower() != color.lower():
                cmds.append(_StyleAnnotCommand(self, page, xref, "color",
                                               rec.get("color"), color))
            if opacity is not None:
                cur = float(rec.get("opacity", 1.0) or 1.0)
                if abs(cur - float(opacity)) > 1e-6:
                    cmds.append(_StyleAnnotCommand(
                        self, page, xref, "opacity", cur, float(opacity)))
            if width is not None:
                try:
                    cur_w = self.pdf.annot_stroke_width(xref)
                except Exception:
                    cur_w = 0.0
                if cur_w > 0.0 and abs(cur_w - float(width)) > 1e-6:
                    cmds.append(_StyleAnnotCommand(
                        self, page, xref, "width", cur_w, float(width)))
        if not cmds:
            return 0
        touched = len({c.xref for c in cmds})
        cmd = _CompositeCommand(self, cmds,
                                f"restyle {touched} annotation(s)")
        cmd.redo()
        self.undo_stack.push(cmd)
        return touched

    def live_annotations(self) -> list[dict]:
        """All markups for this document: PDF-native annotations plus on-screen
        sticky notes, in a uniform dict shape for the annotations panel.

        This is the live source of truth the panel renders; it reflects
        annotations added this session even before a save/index cycle.
        """
        out: list[dict] = []
        try:
            out.extend(self.pdf.annotations())
        except Exception:
            logger.exception("live annotations: pdf listing failed")
        for note in self._sticky_notes:
            try:
                # Report the note rect in *page* coords (same space as PDF
                # annotation rects) so the panel/flash can map it back.
                rect = [0.0, 0.0, 0.0, 0.0]
                if 0 <= note.page_index < len(self._page_items):
                    page_item = self._page_items[note.page_index]
                    z = max(0.01, self.zoom)
                    br = note.mapRectToScene(note.boundingRect())
                    rect = [(br.x() - page_item.pos().x()) / z,
                            (br.y() - page_item.pos().y()) / z,
                            (br.x() + br.width() - page_item.pos().x()) / z,
                            (br.y() + br.height() - page_item.pos().y()) / z]
                out.append({
                    "pdf_xref": None,
                    "page": note.page_index,
                    "type": "note-widget",
                    "type_code": -1,
                    "rect": rect,
                    "color": note._note_color,
                    "opacity": 1.0,
                    "text": note.text(),
                    "author": "Me",
                    "date": "",
                    "sticky": True,
                })
            except Exception:
                logger.exception("live annotations: note listing failed")
        return out

    def live_annotation_count(self) -> int:
        try:
            return len(self.live_annotations())
        except Exception:
            return 0

    # -- undo/redo (#13) ------------------------------------------------------
    def undo(self) -> bool:
        """Undo the last annotation/note operation. Returns True if applied."""
        label = self.undo_stack.undo()
        if label is None:
            return False
        self.request_toast.emit(f"Undo: {label}", "info")
        return True

    def offer_undo_delete(self, message: str) -> None:
        """Toast 'deleted' with an Undo button (#82); emitted after deletes."""
        self.action_toast.emit(message, "info", self.undo)

    def redo(self) -> bool:
        """Redo the last undone annotation/note operation."""
        label = self.undo_stack.redo()
        if label is None:
            return False
        self.request_toast.emit(f"Redo: {label}", "info")
        return True

    def can_undo(self) -> bool:
        return self.undo_stack.can_undo()

    def can_redo(self) -> bool:
        return self.undo_stack.can_redo()

    def flash_annotation(self, page: int, page_rect, sticky: bool = False,
                         cycles: int = 3) -> bool:
        """Scroll to a page rect, then pulse an outline around it (#15 polish).

        ``page_rect`` is in unrotated PDF page coords (the same space the
        annotations panel reports). Returns True if the flash was started.
        """
        if not (0 <= page < len(self._page_items)):
            return False
        page_item = self._page_items[page]
        z = max(0.01, self.zoom)
        try:
            x0, y0, x1, y1 = [float(v) for v in page_rect]
        except Exception:
            return False
        if x1 <= x0 or y1 <= y0:
            # Degenerate rect (e.g. point annotations): make a visible box.
            x0, y0, x1, y1 = x0 - 20, y0 - 20, x0 + 60, y0 + 60
        scene_rect = QRectF(page_item.pos().x() + x0 * z,
                            page_item.pos().y() + y0 * z,
                            (x1 - x0) * z,
                            (y1 - y0) * z)
        self.go_to_page(page)
        self._flash_rect_item = QGraphicsRectItem(scene_rect)
        self._flash_rect_item.setPen(QPen(QColor("#E5B25D"), 2.4))
        self._flash_rect_item.setBrush(Qt.NoBrush)
        self._flash_rect_item.setZValue(120)
        self._scene.addItem(self._flash_rect_item)
        self._flash_cycles_left = max(1, int(cycles)) * 2  # on+off per cycle
        self._flash_timer.start()
        return True

    def _flash_tick(self) -> None:
        """Pulse the flash outline; solid -> dashed -> remove, per cycle."""
        item = self._flash_rect_item
        if item is None:
            self._flash_timer.stop()
            return
        self._flash_cycles_left -= 1
        if self._flash_cycles_left <= 0:
            self._flash_timer.stop()
            self._scene.removeItem(item)
            self._flash_rect_item = None
            return
        pen = item.pen()
        if self._flash_cycles_left % 2 == 0:
            pen.setStyle(Qt.SolidLine)
        else:
            pen.setStyle(Qt.NoPen)  # blink off
        item.setPen(pen)

    def _rerender_page(self, page_index: int) -> None:
        # Cache keys include page index; clear all and redraw.
        self._cache.clear()
        self._request_visible(force=True)

    # ------------------------------------------------------------------ state
    def save_state(self) -> dict:
        return {"page": self._current_page, "zoom": self._zoom,
                "mode": self._mode, "rotation": self._rotation,
                "gap": self._gap, "theme": self._theme}

    def restore_state(self, state: dict) -> None:
        self._mode = state.get("mode", self.MODE_CONTINUOUS)
        self._rotation = int(state.get("rotation", 0))
        self._current_page = int(state.get("page", 0))
        self._zoom = float(state.get("zoom", 1.0))
        try:
            self._gap = max(0.0, float(state.get("gap", self._gap)))
        except (TypeError, ValueError):
            pass
        self._theme = str(state.get("theme", self._theme) or "")
        if self._page_items:
            self._apply_theme()
            self.go_to_page(self._current_page)

    # -- per-view theme (#77) ---------------------------------------------
    def set_theme(self, theme: str) -> None:
        """Per-view page theme: '' (app), 'light', 'sepia', 'dark', 'oled'."""
        self._theme = str(theme or "")
        self._apply_theme()
        self.state_changed.emit()

    def theme(self) -> str:
        return self._theme

    def _apply_theme(self) -> None:
        """Tint the canvas behind the pages; page rendering stays original."""
        bg = VIEW_THEMES.get(self._theme, VIEW_THEMES[""])
        self._view.setBackgroundBrush(QColor(bg))
        self._request_visible(force=True)
    def delete_current_page(self) -> None:
        self.pdf.delete_pages([self._current_page])
        self.content_changed.emit()
        self._build_scene()
        self.state_changed.emit()

    def rotate_current_page(self, degrees: int) -> None:
        self.pdf.rotate_page(self._current_page, degrees)
        self.content_changed.emit()
        self._build_scene()

    # ------------------------------------------------------------------ presentation
    def enter_presentation(self) -> None:
        self._view.showFullScreen() if False else None

    def close_view(self) -> None:
        # Stop all reading-mode timers before teardown.
        self._autoscroll_timer.stop()
        self._slideshow_timer.stop()
        self._reading_timer.stop()
        self._flash_timer.stop()
        # Teardown the floating quick bar (#84).
        if self._quick_bar is not None:
            try:
                self._quick_bar.close()
                self._quick_bar.deleteLater()
            except Exception:
                logger.exception("close_view failed")
            self._quick_bar = None
        if self._flash_rect_item is not None:
            try:
                self._scene.removeItem(self._flash_rect_item)
            except Exception:
                logger.exception("close_view failed")
            self._flash_rect_item = None
        try:
            self.engine.close()
        except Exception:
            logger.exception("engine close failed")
        # Clean up the on-page sticky notes and the palette.
        for n in list(self._sticky_notes):
            try:
                self._scene.removeItem(n)
                n.deleteLater()
            except Exception:
                logger.exception("close_view failed")
        self._sticky_notes.clear()
        if getattr(self, "_annot_palette", None):
            try:
                self._annot_palette.hide()
            except Exception:
                logger.exception("close_view failed")
            self._annot_palette = None


class _PdfGraphicsView(QGraphicsView):
    """Canvas with annotation interaction and link handling."""

    def __init__(self, scene, pdf_view: PdfView) -> None:
        super().__init__(scene)
        self.pdf_view = pdf_view
        self._annot_mode = ""
        self._drawing = False
        self._last_point = None
        self._shape_start = None
        self._preview_item = None
        self._ink_page: int | None = None
        # Phase 2 editing gesture state.
        self._edit_drag: dict = {}
        self._edit_preview = None
        self._marquee_origin: QPointF | None = None
        self._marquee_item = None

    def set_annotation_mode(self, mode: str) -> None:
        self._annot_mode = mode

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            # Zoom toward the cursor.
            # Strategy: record the scene point under the cursor now, change
            # the zoom, then shift the page items in the scene so that the
            # same page-content point stays under the cursor. We move ALL
            # page items by the same delta so the relative layout is preserved.
            delta = event.angleDelta().y()
            factor = 1.15 if delta > 0 else 1 / 1.15
            # Manual wheel zoom takes over: freeze any running animation so
            # the two systems never move page items against each other.
            self._cancel_zoom_anim()
            old_zoom = self.pdf_view.zoom
            new_zoom = clamp_zoom(old_zoom * factor)
            if new_zoom == old_zoom:
                event.accept()
                return

            # Scene point under the cursor (before zoom).
            cursor = event.position().toPoint()
            scene_pt = self.mapToScene(cursor)

            # Apply the new zoom (pages will be re-rendered at the new scale).
            self.pdf_view.set_zoom(new_zoom)

            # Find the page item under the cursor (if any) to anchor on.
            anchor_item = self._page_at(scene_pt)
            if anchor_item is not None:
                # Page-local point that the cursor is on (un-scaled).
                lx = (scene_pt.x() - anchor_item.pos().x()) / max(0.01, old_zoom)
                ly = (scene_pt.y() - anchor_item.pos().y()) / max(0.01, old_zoom)
                # Desired new scene position of that page-local point.
                desired_x = anchor_item.pos().x() + lx * new_zoom
                desired_y = anchor_item.pos().y() + ly * new_zoom
                # Delta to move ALL page items so the anchor point lands on
                # the cursor's scene position.
                dx = scene_pt.x() - desired_x
                dy = scene_pt.y() - desired_y
            else:
                # No page under cursor: anchor on the scene origin (0,0).
                dx = scene_pt.x() * (1 - new_zoom / old_zoom)
                dy = scene_pt.y() * (1 - new_zoom / old_zoom)

            # Shift every page item (and the scene rect stays scaled).
            for item in self.pdf_view._page_items:
                item.setPos(item.pos() + QPointF(dx, dy))

            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._annot_mode and event.button() == Qt.LeftButton:
            self._handle_annot_press(event)
            event.accept()
            return
        # Panning / link handling.
        if event.button() == Qt.LeftButton and not self._annot_mode:
            # If the user clicked on a sticky note, forward to it.
            clicked_note = self._clicked_sticky_note(event)
            if clicked_note is not None:
                clicked_note.mousePressEvent(event)
                event.accept()
                return
            # Select/Pan mode doubles as annotation editing (Phase 2).
            if self._begin_edit_press(event):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drawing:
            self._handle_annot_move(event)
            event.accept()
            return
        if self._edit_drag:
            self._handle_edit_move(event)
            event.accept()
            return
        if self._marquee_origin is not None:
            self._update_marquee(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        # If we are hovering/moving a sticky note, let it handle.
        note = self._hover_sticky_note(event.pos())
        if note is not None and note.isSelected():
            note.mouseMoveEvent(event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drawing and event.button() == Qt.LeftButton:
            self._handle_annot_release(event)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._marquee_origin is not None:
            self._finish_marquee()
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._edit_drag:
            self._finish_edit_drag(event)
            event.accept()
            return
        if event.button() == Qt.LeftButton and not self._annot_mode:
            # Link handling
            self._try_open_link(event)
            # Also release any sticky note dragging.
            note = self._hover_sticky_note(event.pos())
            if note is not None and note.isSelected():
                note.mouseReleaseEvent(event)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    # -- annotation editing gestures (Phase 2) --------------------------------
    def event(self, ev):
        """Claim Delete/Esc while a selection exists.

        Esc is bound at window level to "Select/Pan" and would otherwise fire
        before the canvas sees the key, so a selection could never be cleared
        with the keyboard.
        """
        if ev.type() == QEvent.ShortcutOverride:
            v = self.pdf_view
            if v.selection_count() and ev.key() in (Qt.Key_Delete,
                                                    Qt.Key_Escape):
                ev.accept()
                return True
        return super().event(ev)

    def keyPressEvent(self, event) -> None:
        v = self.pdf_view
        if event.key() in (Qt.Key_Delete,
                           Qt.Key_Backspace) and v.selection_count():
            n = v.delete_selection()
            if n:
                v.request_toast.emit(f"Deleted {n} annotation(s)", "info")
            event.accept()
            return
        if event.key() == Qt.Key_Escape and v.selection_count():
            v.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _handle_at(self, scene_pos: QPointF):
        """Which resize handle (tl/tr/bl/br) is under the cursor, if any."""
        v = self.pdf_view
        if v.selection_count() != 1:
            return None
        r = v._scene_rect_for(*v.selection()[0])
        if r is None:
            return None
        tol = 8.0
        corners = (("tl", r.left(), r.top()), ("tr", r.right(), r.top()),
                   ("bl", r.left(), r.bottom()),
                   ("br", r.right(), r.bottom()))
        for name, cx, cy in corners:
            if abs(scene_pos.x() - cx) <= tol and abs(scene_pos.y() - cy) <= tol:
                return name
        return None

    def _begin_edit_press(self, event) -> bool:
        """Select / start move / start resize / start marquee.

        Returns True when the press was consumed (so no pan begins).
        """
        v = self.pdf_view
        scene_pos = self.mapToScene(event.position().toPoint())
        modifiers = event.modifiers()
        self.setFocus()
        handle = self._handle_at(scene_pos)
        if handle is not None:
            page, xref = v.selection()[0]
            rect = v._visual_rect(xref)
            if rect is None:
                return False
            self._edit_drag = {"kind": "resize", "page": page, "xref": xref,
                               "handle": handle, "origin": scene_pos,
                               "page_rect": (rect.x0, rect.y0, rect.x1, rect.y1)}
            return True
        item = self._page_at(scene_pos)
        if item is not None:
            p = self._to_page_coords(item, scene_pos)
            hit = v.pdf.annot_at(item.page_index, p.x(), p.y())
            if hit is not None:
                page, xref = item.page_index, int(hit[0])
                if modifiers & Qt.ControlModifier:
                    v.toggle_annotation(page, xref)
                elif (page, xref) not in v.selection():
                    v.select_annotations([(page, xref)])
                self._edit_drag = {"kind": "move", "origin": scene_pos,
                                   "moved": False}
                return True
        # Empty page area: Ctrl-drag draws a marquee, plain drag pans.
        if modifiers & Qt.ControlModifier:
            v.clear_selection()
            self._marquee_origin = scene_pos
        return False

    def _handle_edit_move(self, event) -> None:
        v = self.pdf_view
        scene_pos = self.mapToScene(event.position().toPoint())
        drag = self._edit_drag
        if drag.get("kind") == "move":
            delta = scene_pos - drag["origin"]
            if not drag.get("moved") and delta.manhattanLength() < 3:
                return
            drag["moved"] = True
            drag["scene_delta"] = delta
            v._refresh_selection_overlay(offset=delta)
        elif drag.get("kind") == "resize":
            self._preview_resize(scene_pos)

    def _resized_page_rect(self, drag, scene_pos: QPointF):
        """Pending page rect for the dragged resize handle, or None."""
        v = self.pdf_view
        page = drag["page"]
        if not (0 <= page < len(v._page_items)):
            return None
        p = self._to_page_coords(v._page_items[page], scene_pos)
        x0, y0, x1, y1 = drag["page_rect"]
        handle = drag["handle"]
        if handle in ("tl", "bl"):
            x0 = p.x()
        else:
            x1 = p.x()
        if handle in ("tl", "tr"):
            y0 = p.y()
        else:
            y1 = p.y()
        if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:
            return None
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _preview_resize(self, scene_pos: QPointF) -> None:
        drag = self._edit_drag
        new_rect = self._resized_page_rect(drag, scene_pos)
        if new_rect is None:
            return
        v = self.pdf_view
        page = drag["page"]
        if not (0 <= page < len(v._page_items)):
            return
        item = v._page_items[page]
        z = max(0.01, v.zoom)
        r = QRectF(item.pos().x() + new_rect[0] * z,
                   item.pos().y() + new_rect[1] * z,
                   (new_rect[2] - new_rect[0]) * z,
                   (new_rect[3] - new_rect[1]) * z)
        if self._edit_preview is None:
            self._edit_preview = self.scene().addRect(
                r, QPen(QColor("#2A6FB0"), 1.4, Qt.DashLine))
            self._edit_preview.setZValue(142)
        else:
            self._edit_preview.setRect(r)

    def _finish_edit_drag(self, event) -> None:
        v = self.pdf_view
        drag = self._edit_drag
        self._edit_drag = {}
        if self._edit_preview is not None:
            self.scene().removeItem(self._edit_preview)
            self._edit_preview = None
        if drag.get("kind") == "move":
            if not drag.get("moved"):
                return                      # plain click: selection only
            delta = drag.get("scene_delta") or QPointF(0.0, 0.0)
            z = max(0.01, v.zoom)
            dx, dy = delta.x() / z, delta.y() / z
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                v._refresh_selection_overlay()
                return
            moves = [(page, xref, dx, dy) for page, xref in v.selection()]
            cmd = _MoveAnnotsCommand(v, moves)
            if cmd.redo():
                v.undo_stack.push(cmd)
                v.request_toast.emit(cmd.label, "info")
            v._refresh_selection_overlay()
            return
        if drag.get("kind") == "resize":
            scene_pos = self.mapToScene(event.position().toPoint())
            new_rect = self._resized_page_rect(drag, scene_pos)
            old_rect = tuple(drag["page_rect"])
            if new_rect is None or new_rect == old_rect:
                v._refresh_selection_overlay()
                return
            cmd = _ResizeAnnotCommand(v, drag["page"], drag["xref"],
                                      old_rect, new_rect)
            if cmd.redo():
                v.undo_stack.push(cmd)
                v.request_toast.emit(cmd.label, "info")
            v._refresh_selection_overlay()

    def _update_marquee(self, scene_pos: QPointF) -> None:
        origin = self._marquee_origin
        if origin is None:
            return
        r = QRectF(origin, scene_pos).normalized()
        if self._marquee_item is None:
            self._marquee_item = self.scene().addRect(
                r, QPen(QColor("#2A6FB0"), 1.2, Qt.DashLine),
                QColor(42, 111, 176, 40))
            self._marquee_item.setZValue(143)
        else:
            self._marquee_item.setRect(r)

    def _finish_marquee(self) -> None:
        v = self.pdf_view
        item = self._marquee_item
        self._marquee_origin = None
        self._marquee_item = None
        if item is None:
            return
        rect = item.rect()
        self.scene().removeItem(item)
        if rect.width() < 3 and rect.height() < 3:
            return
        hits = [(page, xref) for page, xref in v.annotation_items()
                if (r := v._scene_rect_for(page, xref)) is not None
                and rect.intersects(r)]
        v.select_annotations(hits)
        if hits:
            v.request_toast.emit(f"{len(hits)} annotations selected", "info")

    def contextMenuEvent(self, event) -> None:
        v = self.pdf_view
        scene_pos = self.mapToScene(event.pos())
        item = self._page_at(scene_pos)
        if item is not None:
            p = self._to_page_coords(item, scene_pos)
            hit = v.pdf.annot_at(item.page_index, p.x(), p.y())
            if hit is not None:
                pair = (item.page_index, int(hit[0]))
                if pair not in v.selection():
                    v.select_annotations([pair])
        if not v.selection_count():
            return
        self._show_edit_menu(event.globalPos())

    def _show_edit_menu(self, global_pos) -> None:
        from PySide6.QtWidgets import QMenu
        v = self.pdf_view
        n = v.selection_count()
        menu = QMenu(self)
        color_menu = menu.addMenu("Colour" if n == 1 else f"Colour ({n})")
        for hexcode in ("#E5B25D", "#C7522A", "#3E7C4F", "#2A6FB0",
                        "#6A4BB8", "#222222", "#B03A48"):
            act = color_menu.addAction(hexcode)
            act.triggered.connect(
                lambda _=False, c=hexcode: v.set_selection_style(color=c))
        color_menu.addSeparator()
        custom = color_menu.addAction("Custom…")
        custom.triggered.connect(self._pick_custom_color)
        opacity_menu = menu.addMenu("Opacity")
        for pct in (100, 75, 50, 25):
            act = opacity_menu.addAction(f"{pct}%")
            act.triggered.connect(
                lambda _=False, o=pct / 100.0: v.set_selection_style(opacity=o))
        width_menu = menu.addMenu("Line width")
        for label, w in (("Thin", 1.0), ("Medium", 2.0), ("Thick", 4.0)):
            act = width_menu.addAction(label)
            act.triggered.connect(
                lambda _=False, x=w: v.set_selection_style(width=x))
        menu.addSeparator()
        delete = menu.addAction("Delete" if n == 1
                                else f"Delete {n} annotations")
        delete.triggered.connect(lambda: v.delete_selection())
        menu.addAction("Clear selection").triggered.connect(v.clear_selection)
        menu.addAction("Select all on this page").triggered.connect(
            lambda: v.select_all_on_page(v.current_page))
        menu.exec(global_pos)

    def _pick_custom_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(QColor(self.pdf_view._annot_color), self,
                                      "Annotation colour")
        if color.isValid():
            self.pdf_view.set_selection_style(color=color.name())

    def _clicked_sticky_note(self, event) -> StickyNoteItem | None:
        pos = event.position().toPoint()
        for n in reversed(self.pdf_view._sticky_notes):
            if n.isUnderline(self.mapToScene(pos)):
                return n
        return None

    def _hover_sticky_note(self, pos) -> StickyNoteItem | None:
        return self._note_at_scene(self.mapToScene(pos))

    def _note_at_scene(self, scene_pos: QPointF) -> StickyNoteItem | None:
        """Topmost sticky note under a *scene* point (notes are Z=100)."""
        for n in reversed(self.pdf_view._sticky_notes):
            if n.isUnderline(scene_pos):
                return n
        return None

    # -- annotation interactions ---------------------------------------------
    def _page_at(self, scene_pos: QPointF):
        """Page whose laid-out box contains a scene point.

        Deliberately geometric rather than ``scene().itemAt()``: while a page
        is still showing its (half-size) render placeholder, itemAt() misses
        points over the right-hand part of the page, and it could also return
        an overlay item sitting on top.
        """
        zoom = max(0.01, self.pdf_view.zoom)
        for item in self.pdf_view._page_items:
            box = QRectF(item.pos().x(), item.pos().y(),
                         item.page_width * zoom, item.page_height * zoom)
            if box.contains(scene_pos):
                return item
        return None

    def _handle_annot_press(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        item = self._page_at(scene_pos)
        mode = self._annot_mode
        if mode == "eraser":
            # Sticky notes sit above the page, so the eraser must run even
            # when the topmost item at the cursor is not a page slot.
            self._drawing = True
            self._erase_hit_test(scene_pos)
            return
        if item is None:
            return
        page_point = self._to_page_coords(item, scene_pos)
        self.pdf_view._pop_quick_bar(event)
        if mode in ("highlight", "underline", "strikeout", "squiggly",
                    "rectangle", "ellipse", "line", "arrow", "redact"):
            self._shape_start = (item, page_point)
            self._drawing = True
        elif mode == "ink":
            self._drawing = True
            self._ink_page = item.page_index
            self.pdf_view._drag_ink = [page_point]
            self._draw_ink_preview()
        elif mode == "note":
            # Place a resizable, editable on-page sticky note.
            self.pdf_view.add_sticky_note_on_page(
                item.page_index, scene_pos)

    def _handle_annot_move(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        item = self._page_at(scene_pos)
        if self._annot_mode == "eraser":
            self._erase_hit_test(scene_pos)
            return
        if item is None:
            return
        page_point = self._to_page_coords(item, scene_pos)
        if self._annot_mode == "ink":
            self.pdf_view._drag_ink.append(page_point)
            self._draw_ink_preview()
        elif self._shape_start:
            self._draw_shape_preview(item, page_point)

    def _handle_annot_release(self, event) -> None:
        if self._annot_mode == "ink":
            stroke = [(p.x(), p.y()) for p in self.pdf_view._drag_ink]
            self.pdf_view._drag_ink = []
            if self._ink_page is not None and len(stroke) > 1:
                self.pdf_view.add_annotation_at(
                    self._ink_page, "ink", None, points=stroke)
            self._ink_page = None
            self._remove_preview()
            self._drawing = False
            return
        if self._annot_mode == "eraser":
            self._drawing = False
            self._remove_preview()
            return
        if self._shape_start:
            item, start = self._shape_start
            scene_pos = self.mapToScene(event.position().toPoint())
            end = self._to_page_coords(item, scene_pos)
            rect = (min(start.x(), end.x()), min(start.y(), end.y()),
                    max(start.x(), end.x()), max(start.y(), end.y()))
            mode = self._annot_mode
            self._remove_preview()
            self._drawing = False
            self._shape_start = None
            if mode == "redact":
                self.pdf_view.pdf.add_redaction(item.page_index, rect)
                self.pdf_view.content_changed.emit()
            elif mode in ("highlight", "underline", "strikeout", "squiggly"):
                # Snap markup to text under the drag if any.
                quads = self._text_quads(item, rect)
                self.pdf_view.add_annotation_at(
                    item.page_index, mode, rect, quads=quads)
            else:
                self.pdf_view.add_annotation_at(item.page_index, mode, rect)

    def _to_page_coords(self, item: "PageSlotItem", scene_pos: QPointF) -> QPointF:
        """Map scene coords to unrotated PDF page coords (Y down)."""
        px = (scene_pos.x() - item.pos().x()) / max(0.01, self.pdf_view.zoom)
        py = (scene_pos.y() - item.pos().y()) / max(0.01, self.pdf_view.zoom)
        return QPointF(px, py)

    def _text_quads(self, item, rect) -> list:
        """Find text inside rect and return fitz quads (for markup tools)."""
        import fitz
        try:
            page = self.pdf_view.pdf._doc[item.page_index]
            r = fitz.Rect(*rect)
            words = page.get_text("words")
            hits = [fitz.Rect(w[:4]) for w in words
                    if r.intersects(fitz.Rect(w[:4]))]
            return [h.quad for h in hits] if hits else [r]
        except Exception:
            import fitz as _f
            return [_f.Rect(*rect)]

    def _draw_shape_preview(self, item, point: QPointF) -> None:
        scene = self.scene()
        if self._preview_item is not None:
            scene.removeItem(self._preview_item)
            self._preview_item = None
        start = self._to_page_coords(item, QPointF(*self._shape_start_page()))
        p1 = QPointF(item.pos().x() + start.x() * self.pdf_view.zoom,
                     item.pos().y() + start.y() * self.pdf_view.zoom)
        p2 = QPointF(item.pos().x() + point.x() * self.pdf_view.zoom,
                     item.pos().y() + point.y() * self.pdf_view.zoom)
        from PySide6.QtWidgets import QGraphicsRectItem
        from PySide6.QtGui import QPen, QColor
        self._preview_item = scene.addRect(
            QRectF(p1, p2).normalized(),
            QPen(QColor(self.pdf_view._annot_color), 1.4, Qt.DashLine))
        self._preview_item.setZValue(50)

    def _shape_start_page(self):
        item, point = self._shape_start
        return (point.x(), point.y())

    def _draw_ink_preview(self) -> None:
        # Live polyline preview while dragging ink.
        scene = self.scene()
        self._remove_preview()
        pts = self.pdf_view._drag_ink
        if len(pts) < 2:
            return
        zoom = self.pdf_view.zoom
        item = self._page_at(pts[0])
        if item is None:
            return
        colored = QColor(self.pdf_view._annot_color)
        pen = QPen(colored, max(1.0, self.pdf_view._annot_width),
                   Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        path = QPainterPath()
        path.moveTo(item.pos().x() + pts[0].x() * zoom,
                     item.pos().y() + pts[0].y() * zoom)
        for p in pts[1:]:
            path.lineTo(item.pos().x() + p.x() * zoom,
                        item.pos().y() + p.y() * zoom)
        self._preview_item = scene.addPath(path, pen)
        self._preview_item.setZValue(50)
        self._preview_item.setFlag(QGraphicsItem.ItemIsMovable, False)

    def _remove_preview(self) -> None:
        if self._preview_item is not None:
            self.scene().removeItem(self._preview_item)
            self._preview_item = None

    # -- eraser ------------------------------------------------------------------
    def _erase_hit_test(self, scene_pos: QPointF) -> None:
        """Delete the topmost annotation/sticky-note under the cursor."""
        # Sticky notes are Z=100, drawn last; check them first. NB: the point
        # here is already in scene coords, so do not re-map it.
        note = self._note_at_scene(scene_pos)
        if note is not None:
            v = self.pdf_view
            v.undo_stack.push(_StickyNoteCommand(
                v, "delete", item=note, page=note.page_index,
                scene_pos=note.pos()))
            v.remove_sticky_note(note)
            v.offer_undo_delete("Sticky note deleted")
            return
        item = self._page_at(scene_pos)
        if item is None:
            return
        page_point = self._to_page_coords(item, scene_pos)
        try:
            hit = self.pdf_view.pdf.annot_at(item.page_index, page_point.x(),
                                            page_point.y())
        except Exception:
            logger.exception("eraser hit test failed")
            return
        if hit is None:
            return
        xref = int(hit[0] or 0)
        if xref:
            try:
                # Snapshot the annotation before deleting so undo can
                # re-create it (#13), geometry included for faithful restore.
                cmd = self.pdf_view._delete_command_for(xref)
                self.pdf_view.delete_annotation_xref(xref)
                if cmd is not None:
                    self.pdf_view.undo_stack.push(cmd)
                self.pdf_view._after_annot_edit([item.page_index])
                self.pdf_view.offer_undo_delete("Annotation deleted")
            except Exception:
                logger.exception("eraser delete failed")

    # -- links ------------------------------------------------------------------
    def _try_open_link(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        item = self._page_at(scene_pos)
        if item is None:
            return
        page_point = self._to_page_coords(item, scene_pos)
        import fitz
        try:
            links = self.pdf_view.pdf._doc[item.page_index].get_links()
        except Exception:
            return
        for lk in links:
            r = lk.get("from")
            if r and r.contains(fitz.Point(page_point.x(), page_point.y())):
                kind = lk.get("kind")
                if kind == fitz.LINK_GOTO:
                    self.pdf_view.go_to_page(lk.get("page", 0))
                elif kind == fitz.LINK_URI:
                    from veyrion_workspace.ui.dialogs import confirm_external_link
                    confirm_external_link(self, lk.get("uri", ""))
                return


class PageSlotItem(QGraphicsPixmapItem):
    """A page placeholder that becomes a rendered pixmap."""

    def __init__(self, page_index: int, page_width: float,
                 page_height: float) -> None:
        super().__init__()
        self.page_index = page_index
        self.page_width = page_width
        self.page_height = page_height
        self._zoom = 0.0
        self._highlights: list = []
        self._placeholder_pixmap: QPixmap | None = None
        placeholder = QPixmap(max(1, int(page_width * 0.5)),
                              max(1, int(page_height * 0.5)))
        placeholder.fill(QColor(245, 243, 238))
        painter = QPainter(placeholder)
        painter.setPen(QPen(QColor(210, 205, 195), 1))
        painter.drawRect(placeholder.rect().adjusted(0, 0, -1, -1))
        painter.end()
        self._placeholder_pixmap = placeholder
        self.setPixmap(placeholder)
        self.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.setAcceptHoverEvents(True)

    def show_placeholder(self) -> None:
        if self._zoom > 0:
            self.setPixmap(self._placeholder_pixmap)
            self._zoom = 0.0

    def set_pixmap(self, pm: QPixmap, zoom: float) -> None:
        scaled = pm.scaled(
            int(self.page_width * zoom), int(self.page_height * zoom),
            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)
        self._zoom = zoom

    def set_search_highlights(self, rects) -> None:
        self._highlights = rects
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        if self._highlights and self._zoom > 0:
            painter.save()
            painter.setOpacity(0.35)
            painter.setBrush(QColor("#E5B25D"))
            painter.setPen(Qt.NoPen)
            for r in self._highlights:
                scaled = QRectF(
                    r.x() * self._zoom, r.y() * self._zoom,
                    r.width() * self._zoom, r.height() * self._zoom)
                painter.drawRect(scaled)
            painter.restore()
        # Page shadow/border
        painter.save()
        painter.setPen(QPen(QColor(180, 175, 165, 120), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.boundingRect())
        painter.restore()


class _RenderSignals(QObject):
    """Thread-safe delivery channel from render workers to the GUI thread.

    QRunnable objects cannot be QObjects, so the view owns this bridge;
    emitting a signal from a worker thread is queued to the receiver's
    thread automatically, which QTimer.singleShot(0, ...) from a pool
    thread is not (pool threads have no event loop).
    """

    finished = Signal(int, float, object, object)   # page, zoom, key, QImage
    failed = Signal(object)                          # key


class _PageRenderRunnable(QRunnable):
    def __init__(self, pdf_view: PdfView, page_index: int, zoom: float, key) -> None:
        super().__init__()
        self.pdf_view = pdf_view
        self.page_index = page_index
        self.zoom = zoom
        self.key = key
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            pix = self.pdf_view.pdf.render_page(self.page_index, zoom=self.zoom)
            fmt = QImage.Format_RGB888 if pix.n < 4 else QImage.Format_RGBA8888
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
            self.pdf_view._render_signals.finished.emit(
                self.page_index, self.zoom, self.key, img)
        except Exception:
            logger.exception("page render failed p%d", self.page_index)
            self.pdf_view._render_signals.failed.emit(self.key)


# -----------------------------------------------------------------------------
# Annotation style palette: color swatches + thickness + transparency.

# ---------------------------------------------------------------------------
# Floating quick-toolbar near the cursor while annotating (#84).

class QuickAnnotBar(QFrame):
    """Compact floating style bar that snaps next to the cursor.

    Shown while an annotation tool is active: a row of color swatches plus
    thickness/opacity spinners, so style changes never require a trip to the
    corner palette. Shares state with PdfView via set_annotation_style.
    """

    style_changed = Signal(str, float, float)  # color, opacity, width

    COLORS = ["#E5B25D", "#C7522A", "#3E7C4F", "#33557A",
              "#6A4BB8", "#000000"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("quickAnnotBar")
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint |
                            Qt.WindowStaysOnTopHint)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(5)
        self._swatches: dict[str, QToolButton] = {}
        for c in self.COLORS:
            btn = QToolButton()
            btn.setFixedSize(18, 18)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"Color {c}")
            btn.setStyleSheet(
                f"QToolButton {{ background: {c}; border: 1px solid"
                f" rgba(0,0,0,0.35); border-radius: 9px; }}"
                f"QToolButton:checked {{ border: 2px solid #2A2721; }}")
            btn.clicked.connect(lambda _=False, col=c: self._pick_color(col))
            lay.addWidget(btn)
            self._swatches[c] = btn
        self._thick = QSlider(Qt.Horizontal)
        self._thick.setRange(5, 80)          # 0.5 - 8.0 pt, x10
        self._thick.setFixedWidth(70)
        self._thick.setValue(20)
        self._thick.setToolTip("Thickness")
        self._thick.valueChanged.connect(self._emit)
        lay.addWidget(self._thick)
        self._thick_label = QLabel("2.0")
        self._thick_label.setFixedWidth(24)
        lay.addWidget(self._thick_label)
        self._trans = QSlider(Qt.Horizontal)
        self._trans.setRange(0, 100)         # transparency %
        self._trans.setFixedWidth(70)
        self._trans.setValue(40)
        self._trans.setToolTip("Transparency")
        self._trans.valueChanged.connect(self._emit)
        lay.addWidget(self._trans)
        self._trans_label = QLabel("40%")
        self._trans_label.setFixedWidth(32)
        lay.addWidget(self._trans_label)
        self.setStyleSheet(
            "QFrame#quickAnnotBar { background: #FFFDF8;"
            " border: 1px solid rgba(0,0,0,0.25); border-radius: 6px; }")
        self.adjustSize()

    def _pick_color(self, color: str) -> None:
        for c, btn in self._swatches.items():
            btn.setChecked(c == color)
        self._emit()

    def _emit(self) -> None:
        color = next(c for c, b in self._swatches.items()
                     if b.isChecked())
        width = self._thick.value() / 10.0
        opacity = 1.0 - (self._trans.value() / 100.0)
        self.style_changed.emit(color, opacity, width)

    def sync_from_style(self, color: str, opacity: float, width: float) -> None:
        """Reflect the view's current style (e.g. when switching tools)."""
        if color not in self._swatches:
            color = self.COLORS[0]
        for c, btn in self._swatches.items():
            btn.setChecked(c == color)
        self._thick.setValue(int(round(width * 10)))
        self._trans.setValue(int(round((1.0 - opacity) * 100)))

    def pop_at(self, view_global: object, local_pos) -> None:
        """Appear just right-below the cursor position in global coords."""
        g = view_global + local_pos
        self.move(g.x() + 18, g.y() + 18)
        self.show()
        self.raise_()
        # Keep the bar on screen: clamp against the primary screen geometry.
        try:
            from PySide6.QtGui import QGuiApplication
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                if self.x() + self.width() > avail.right():
                    self.move(avail.right() - self.width(), self.y())
                if self.y() + self.height() > avail.bottom():
                    self.move(self.x(), max(avail.top(),
                                            avail.bottom() - self.height() - 4))
        except Exception:
            logger.exception("pop_at failed")


class AnnotationStylePalette(QFrame):
    """Compact floating palette with a color row, thickness and opacity.

    Wired into PdfView so picking a color or changing thickness/transparency
    immediately affects the next annotation the user makes.
    """

    style_changed = Signal(str, float, float)  # color, opacity, width

    COLORS = [
        "#E5B25D",  # warm yellow
        "#C7522A",  # sienna
        "#3E7C4F",  # forest
        "#33557A",  # slate blue
        "#6A4BB8",  # purple
        "#8A8578",  # ink grey
        "#FFFFFF",  # white
        "#000000",  # black
    ]

    # Palette defaults (used by the Reset button).
    DEFAULT_COLOR = "#E5B25D"
    DEFAULT_THICKNESS = 2.0
    DEFAULT_TRANSPARENCY = 40  # percent; opacity = 1 - value/100

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("annotPalette")
        self.setFixedWidth(260)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # Title bar: draggable by the user.
        self._title = QLabel("Annotation style")
        self._title.setObjectName("panelHeader")
        lay.addWidget(self._title)

        # Color row.
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color"))
        self._color_buttons: dict[str, QToolButton] = {}
        self._color_grid = QGridLayout()
        self._color_grid.setSpacing(4)
        self._build_color_grid()
        color_row.addLayout(self._color_grid)
        color_row.addStretch(1)

        # More colors + reset.
        more_row = QHBoxLayout()
        self._more_btn = QToolButton()
        self._more_btn.setText("More…")
        self._more_btn.setAutoRaise(True)
        self._more_btn.setCursor(Qt.PointingHandCursor)
        self._more_btn.clicked.connect(self._pick_custom_color)
        more_row.addWidget(self._more_btn)
        more_row.addStretch(1)

        self._reset_btn = QToolButton()
        self._reset_btn.setText("Reset")
        self._reset_btn.setAutoRaise(True)
        self._reset_btn.setCursor(Qt.PointingHandCursor)
        self._reset_btn.clicked.connect(self._reset_to_defaults)
        more_row.addWidget(self._reset_btn)

        color_row.addLayout(more_row)
        lay.addLayout(color_row)

        # Drag state (title-bar dragging).
        self._drag_start: QPoint | None = None
        self._drag_frame: QRect | None = None

        # Thickness.
        thick_row = QHBoxLayout()
        thick_row.addWidget(QLabel("Thickness"))
        self._thick_slider = QSlider(Qt.Horizontal)
        self._thick_slider.setRange(0.5, 8.0)
        self._thick_slider.setSingleStep(0.5)
        self._thick_slider.setValue(2.0)
        self._thick_slider.setFixedWidth(140)
        self._thick_slider.valueChanged.connect(self._emit_style)
        thick_row.addWidget(self._thick_slider)
        self._thick_label = QLabel("2.0")
        self._thick_label.setFixedWidth(36)
        self._thick_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        thick_row.addWidget(self._thick_label)
        lay.addLayout(thick_row)
        self._thick_slider.valueChanged.connect(
            lambda v: self._thick_label.setText(f"{float(v):.1f}"))

        # Transparency.
        trans_row = QHBoxLayout()
        trans_row.addWidget(QLabel("Transparency"))
        self._trans_slider = QSlider(Qt.Horizontal)
        self._trans_slider.setRange(0, 100)
        self._trans_slider.setSingleStep(5)
        self._trans_slider.setValue(40)  # 0.4 opacity by default
        self._trans_slider.setFixedWidth(140)
        self._trans_slider.valueChanged.connect(self._emit_style)
        trans_row.addWidget(self._trans_slider)
        self._trans_label = QLabel("40%")
        self._trans_label.setFixedWidth(40)
        self._trans_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        trans_row.addWidget(self._trans_label)
        lay.addLayout(trans_row)
        self._trans_slider.valueChanged.connect(
            lambda v: self._trans_label.setText(f"{int(v)}%"))

    def _build_color_grid(self) -> None:
        cols = 4
        for i, color in enumerate(self.COLORS):
            btn = QToolButton()
            btn.setFixedSize(22, 22)
            btn.setAutoRaise(True)
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"QToolButton {{ background: {color}; border: 1px solid rgba(0,0,0,0.25); border-radius: 3px; }}"
                f"QToolButton:checked {{ border: 2px solid #2A2721; }}"
            )
            if color == self.COLORS[0]:
                btn.setChecked(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, c=color: self._pick_color(c))
            self._color_buttons[color] = btn
            self._color_grid.addWidget(btn, i // cols, i % cols)

    def _pick_color(self, color: str) -> None:
        for c, btn in self._color_buttons.items():
            btn.setChecked(c == color)
        self._emit_style()

    def _emit_style(self) -> None:
        color = self.current_color()
        opacity = 1.0 - (int(self._trans_slider.value()) / 100.0)
        width = float(self._thick_slider.value())
        self.style_changed.emit(color, opacity, width)

    def current_color(self) -> str:
        for c, btn in self._color_buttons.items():
            if btn.isChecked():
                return c
        return self.COLORS[0]

    # -- dragging ----------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        # Dragging is allowed only when the user grabs the title label.
        title_rect = self._title.geometry()
        if event.button() == Qt.LeftButton and title_rect.contains(
            event.pos() - self.frameGeometry().topLeft()):
            self._drag_start = event.globalPos()
            self._drag_frame = self.frameGeometry()
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - self._drag_start
            new_geo = self._drag_frame.translated(delta.x(), delta.y())
            self.move(new_geo.topLeft())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_start is not None:
            self._drag_start = None
            self._drag_frame = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- custom color -----------------------------------------------------------
    def _pick_custom_color(self) -> None:
        cur = QColor(self.current_color())
        dlg = QColorDialog(cur, self)
        dlg.setOption(QColorDialog.ShowAlphaChannel, False)
        dlg.setWindowTitle("Annotation color")
        if dlg.exec() == QDialog.Accepted:
            c = dlg.selectedColor()
            if c.isValid():
                self._set_color(c.name(QColor.HexRgb))

    def _set_color(self, color: str) -> None:
        """Set the current annotation color, even if it isn't a preset swatch."""
        # Uncheck preset swatches (a custom color won't match any swatch).
        for btn in self._color_buttons.values():
            btn.setChecked(False)
        self._emit_style()

    def _reset_to_defaults(self) -> None:
        self._thick_slider.setValue(self.DEFAULT_THICKNESS)
        self._thick_label.setText(f"{self.DEFAULT_THICKNESS:.1f}")
        self._trans_slider.setValue(self.DEFAULT_TRANSPARENCY)
        self._trans_label.setText(f"{self.DEFAULT_TRANSPARENCY}%")
        self._pick_color(self.DEFAULT_COLOR)

    def show_palette(self, pos) -> None:
        self.move(pos)
        self.show()
        self.raise_()

    def hide_palette(self) -> None:
        self.hide()


# -----------------------------------------------------------------------------
# Resizable, editable sticky note widget.

class StickyNoteItem(QGraphicsWidget):
    """A yellow note widget you can drag, resize from its corners, and edit.

    Text editing is done in place via a QGraphicsTextItem child that becomes
    editable on double-click. Corners are resize handles.
    """

    finished = Signal(object)  # emits the StickyNoteItem itself

    MIN_SIZE = 60

    def __init__(self, page_index: int, scene_pos: QPointF, color: str,
                 pdf_view: "PdfView" | None = None) -> None:
        super().__init__()
        self.page_index = page_index
        self.pdf_view = pdf_view          # owner view (for persist + undo)
        self._note_color = color
        self._text_item: QGraphicsTextItem | None = None
        self._resize_handle: QGraphicsRectItem | None = None
        self._edge: str | None = None  # 'nw'|'ne'|'sw'|'se'|None
        self._drag_start = None
        self._deleted = False
        self._last_persisted_text = ""
        self.setMinimumSize(self.MIN_SIZE, self.MIN_SIZE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def init_elements(self) -> None:
        if self._text_item is None:
            self._text_item = QGraphicsTextItem(self)
            self._text_item.setPos(6, 6)
            self._text_item.setTextInteractionFlags(
                Qt.TextEditorInteraction)
            self._text_item.document().setDefaultFont(
                QFont("Segoe UI", 11))
            self._text_item.setDefaultTextColor(QColor("#2A2721"))
        if self._resize_handle is None:
            self._resize_handle = QGraphicsRectItem(
                -4, -4, 8, 8, self)
            self._resize_handle.setRect(
                self.boundingRect().adjusted(-4, -4, 4, 4))
            self._resize_handle.setBrush(QColor("#C7522A"))
            self._resize_handle.setPen(QColor("#FFFFFF"))
            self._resize_handle.setCursor(Qt.SizeFDiagCursor)
            self._resize_handle.setFlag(
                QGraphicsItem.ItemIsMovable, False)
            self._resize_handle.setFlag(
                QGraphicsItem.ItemIsSelectable, False)

    def text(self) -> str:
        if self._text_item is None:
            return ""
        return self._text_item.toPlainText()

    def edit_text(self, color: str | None = None) -> None:
        if color is not None:
            self._note_color = color
        self.init_elements()
        if self._text_item is not None:
            self._text_item.setTextInteractionFlags(
                Qt.TextEditorInteraction)
            self._text_item.setFocus(Qt.OtherFocusReason)
            # Persist to the PDF on every text change, not just on resize.
            self._text_item.document().contentsChanged.connect(
                self._on_text_changed)

    def _on_text_changed(self) -> None:
        # Immediately push the current text to the PDF as a free-text
        # annotation so edits survive save/close without waiting for a
        # corner drag release. Also records undoable text edits (#13).
        if self.page_index is None or self.pdf_view is None:
            return
        text = self.text()
        if text == self._last_persisted_text:
            return
        old = self._last_persisted_text
        self._last_persisted_text = text
        try:
            rect = self.boundingRect()
            self.pdf_view.pdf.add_free_text(
                self.page_index,
                (rect.x(), rect.y(), rect.x() + rect.width(),
                 rect.y() + rect.height()),
                text or "Note",
                self._note_color,
            )
        except Exception:
            logger.exception("sticky note text persist failed")
            self._last_persisted_text = old
            return
        # Record an undoable text edit when the change came from the user
        # (not from undo/redo restoring text programmatically).
        if not getattr(self, "_restoring", False):
            self.pdf_view.undo_stack.push(_StickyNoteCommand(
                self.pdf_view, "text", item=self,
                old_text=old, new_text=text))

    def paint(self, painter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        # Note body.
        painter.setBrush(QColor(self._note_color))
        painter.setPen(QPen(_qcolor("#C7522A", 180), 1))
        painter.drawRoundedRect(
            self.boundingRect().adjusted(0, 0, 0, 0), 4, 4)
        # Shadow.
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(
            self.boundingRect().adjusted(2, 3, -2, -2), 4, 4)
        # Delete button.
        if self._delete_button_rect().isValid():
            self._paint_delete_button(painter)
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.init_elements()
            # Delete button (top-right corner, inside the note)?
            br = self.boundingRect()
            if self._delete_button_rect().contains(event.pos()):
                self._deleted = True
                self.finished.emit(self)
                event.accept()
                return
            # Corner resize?
            margin = 10
            corners = {
                "nw": QPointF(br.x(), br.y()),
                "ne": QPointF(br.x() + br.width(), br.y()),
                "sw": QPointF(br.x(), br.y() + br.height()),
                "se": QPointF(br.x() + br.width(),
                              br.y() + br.height()),
            }
            under = None
            for name, pt in corners.items():
                if (event.pos() - pt).manhattanLength() < margin:
                    under = name
                    break
            if under:
                self._edge = under
                self._drag_start = (event.pos(), self.rect())
                self.setSelected(True)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._edge and self._drag_start:
            pos, rect = self._drag_start
            dx = event.pos().x() - pos.x()
            dy = event.pos().y() - pos.y()
            new_x = rect.x()
            new_y = rect.y()
            new_w = rect.width()
            new_h = rect.height()
            if self._edge in ("nw", "sw"):
                new_x = rect.x() + dx
                new_w = max(self.MIN_SIZE, rect.width() - dx)
            if self._edge in ("ne", "se"):
                new_w = max(self.MIN_SIZE, rect.width() + dx)
            if self._edge in ("nw", "ne"):
                new_y = rect.y() + dy
                new_h = max(self.MIN_SIZE, rect.height() - dy)
            if self._edge in ("sw", "se"):
                new_h = max(self.MIN_SIZE, rect.height() + dy)
            self.resize(new_w, new_h)
            self.move(new_x, new_y)
            self._drag_start = (event.pos(), self.rect())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._edge:
            self._edge = None
            self._drag_start = None
            self.finished.emit(self)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _delete_button_rect(self) -> QRectF:
        # Small X button in the top-right corner of the note.
        pad = 4
        size = 14
        br = self.boundingRect()
        return QRectF(br.x() + br.width() - size - pad,
                      br.y() + pad, size, size)

    def _paint_delete_button(self, painter) -> None:
        r = self._delete_button_rect()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#C7522A"))
        painter.setPen(QPen(_qcolor("#FFFFFF", 160), 1))
        painter.drawRoundedRect(r, 2, 2)
        # X mark.
        painter.setPen(QPen(QColor("#FFFFFF"), 1.4))
        cx = r.center().x()
        cy = r.center().y()
        off = 3.2
        painter.drawLine(cx - off, cy - off, cx + off, cy + off)
        painter.drawLine(cx + off, cy - off, cx - off, cy + off)
        painter.restore()

    def resize(self, w, h) -> None:
        w = max(self.MIN_SIZE, w)
        h = max(self.MIN_SIZE, h)
        super().setGeometry(QRectF(self.x(), self.y(), w, h))

    def setRect(self, rect: QRectF) -> None:
        self.setGeometry(rect)

    def isUnderline(self, point: QPointF) -> bool:
        """Hit test: is a *scene* point inside this note?

        Callers pass scene coordinates, so the point must be mapped into the
        item's local frame — comparing it to boundingRect() directly would
        ignore the note's position.
        """
        try:
            local = self.mapFromScene(point)
        except Exception:
            return False
        return self.boundingRect().contains(local)


class _StickyNoteEditDelegate(QGraphicsTextItem):
    """Placeholder class kept for backward compat; real editing is in
    StickyNoteItem."""
    pass
