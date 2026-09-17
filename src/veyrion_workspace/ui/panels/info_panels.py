"""Information panels: search results, annotations, notes, thumbnails,
table of contents, bookmarks, background tasks, and document properties."""
from __future__ import annotations
import time
from pathlib import Path
from typing import TYPE_CHECKING
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QTextBrowser, QToolBar,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    QHeaderView, QSizePolicy,
)

from veyrion_workspace.core.search.engine import SearchEngine
from veyrion_workspace.core.annotations.service import AnnotationService
from veyrion_workspace.storage.repositories import NoteRecord, NoteRepository
from veyrion_workspace.ui.icons import icon
from veyrion_workspace.ui.theme import Palette
from veyrion_workspace.ui.reference_export import (
    ANNOTATION_COLUMNS,
    copy_table_markdown,
    export_dialog,
    print_table,
)
from veyrion_workspace.ui.widgets import EmptyState, SectionHeader, show_toast
from veyrion_workspace.utils.pathutils import format_size

if TYPE_CHECKING:  # runtime dep injected by main_window; avoids an import cycle
    from veyrion_workspace.services.settings import Settings


class SearchPanel(QWidget):
    """Global search across indexed documents and annotations."""

    hit_selected = Signal(str, int)   # doc path, page

    def __init__(self, search_engine: SearchEngine, parent=None) -> None:
        super().__init__(parent)
        self._search = search_engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self._query = QLineEdit()
        self._query.setPlaceholderText("Search all documents…")
        self._query.setClearButtonEnabled(True)
        self._query.returnPressed.connect(self.run_search)
        lay.addWidget(self._query)

        opts = QHBoxLayout()
        self._scope = QComboBox()
        self._scope.addItems(["Documents", "Annotations only", "Metadata"])
        opts.addWidget(self._scope, 1)
        self._go = QPushButton("Search")
        self._go.setObjectName("accentButton")
        self._go.clicked.connect(self.run_search)
        opts.addWidget(self._go)
        lay.addLayout(opts)

        self._status = QLabel("")
        self._status.setObjectName("dimLabel")
        lay.addWidget(self._status)

        self._results = QListWidget()
        self._results.itemClicked.connect(self._on_hit)
        self._results.setWordWrap(True)
        lay.addWidget(self._results, 1)

    def run_search(self) -> None:
        query = self._query.text().strip()
        self._results.clear()
        if not query:
            return
        scope = self._scope.currentText()
        if scope == "Annotations only":
            hits = self._search.search(query, annotation_only=True)
        elif scope == "Metadata":
            hits = self._search.search_metadata(query)
        else:
            hits = self._search.search(query)
        for h in hits[:300]:
            label = (f"<b>{h.doc_title}</b>  ·  page {h.page + 1}<br>"
                     f"<span style='color:#8a8578'>{h.context}</span>")
            item = QListWidgetItem()
            item.setData(Qt.UserRole, {"path": h.doc_path, "page": h.page})
            from PySide6.QtWidgets import QLabel as _Q
            widget = QLabel(label)
            widget.setContentsMargins(6, 4, 6, 4)
            item.setSizeHint(widget.sizeHint() + QSize(0, 8))
            self._results.addItem(item)
            self._results.setItemWidget(item, widget)
        self._status.setText(f"{len(hits)} result(s)" if hits else "No matches")

    def _on_hit(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if data:
            self.hit_selected.emit(data["path"], data["page"])


# PDF-native type names (fitz) -> UI atype names used by the DB/service.
PDF_TYPE_MAP = {
    "Highlight": "highlight", "Underline": "underline",
    "StrikeOut": "strikeout", "Squiggly": "squiggly",
    "Square": "rectangle", "Circle": "ellipse", "Line": "line",
    "FreeText": "freetext", "Text": "note", "Ink": "ink",
    "Stamp": "stamp", "Caret": "note", "FileAttachment": "attachment",
}

# Legend chips under the panel header: (label, color, atypes, dropdown target).
CHIP_GROUPS = [
    ("Highlights", "#E5B25D",
     {"highlight", "underline", "strikeout", "squiggly"}, 1),
    ("Notes", "#D9A0A0",
     {"note", "note-widget", "freetext"}, 2),
    ("Ink", "#A33B2E",
     {"ink"}, 3),
    ("Shapes", "#7A6A8A",
     {"rectangle", "ellipse", "line", "arrow", "stamp"}, 4),
]


class AnnotationsPanel(QWidget):
    """Annotation workspace: filter, search, jump, edit, delete, export.

    Renders a live merge of two sources (roadmap #15):
      * the current PDF view's live annotations (PdfView.live_annotations()),
        which include markups added this session and on-screen sticky notes;
      * database records (AnnotationService) — persistent, searchable,
        exportable, and the only source for the "All documents" scope.
    Refreshes live (debounced) when the view emits content_changed.
    """

    annotation_selected = Signal(str, int)   # doc_path, page
    annotations_changed = Signal()
    flash_requested = Signal(dict)           # live annotation data to flash on-page

    def __init__(self, ann_service: AnnotationService, settings: "SettingsService" = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._anns = ann_service
        self._settings = settings
        self._restoring = False  # suppress persistence while applying saved state
        self._doc_filter = ""
        self._view = None  # live PdfView source
        self._chips: set[str] = set()      # active legend-chip group names
        self._chip_counts: dict[str, int] = {}  # group name -> row count
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # Debounced live refresh (sticky-note typing fires content_changed
        # per keystroke; don't rebuild the list on every one).
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self.reload)

        # Header: title + live count badge (roadmap #15 polish).
        header = QHBoxLayout()
        title = QLabel("Annotations")
        title.setObjectName("panelHeader")
        header.addWidget(title)
        header.addStretch(1)
        self._count_badge = QLabel("0")
        self._count_badge.setAlignment(Qt.AlignCenter)
        self._count_badge.setFixedWidth(36)
        self._count_badge.setStyleSheet(
            "QLabel { background: #C7522A; color: #FFFFFF;"
            " border-radius: 10px; padding: 1px 4px; font-weight: 600; }")
        header.addWidget(self._count_badge)
        lay.addLayout(header)

        # Legend chips: per-type color legend that doubles as quick filters.
        # Clicking a chip toggles that type group; chips compose with the
        # type dropdown (intersection) and the search box.
        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self._chip_buttons: dict[str, QToolButton] = {}
        for label, color, _atypes, _dropdown in CHIP_GROUPS:
            btn = QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"Show only {label.lower()} — click again to clear")
            btn.setFixedHeight(20)
            btn.clicked.connect(lambda checked=False, name=label: self._toggle_chip(name))
            chip_row.addWidget(btn)
            self._chip_buttons[label] = btn
        chip_row.addStretch(1)
        lay.addLayout(chip_row)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter annotations…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self.reload)
        lay.addWidget(self._filter)

        # Scope + annotation-type filter side by side.
        combo_row = QHBoxLayout()
        self._scope_combo = QComboBox()
        self._scope_combo.addItems(["Current document", "All documents"])
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        combo_row.addWidget(self._scope_combo, 1)

        # Type filter groups (label -> allowed atypes; None = all).
        self.TYPE_FILTERS = [
            ("All types", None),
            ("Highlights & markup",
             {"highlight", "underline", "strikeout", "squiggly"}),
            ("Notes & text", {"note", "note-widget", "freetext"}),
            ("Ink", {"ink"}),
            ("Shapes", {"rectangle", "ellipse", "line", "arrow", "stamp"}),
        ]
        self._type_combo = QComboBox()
        for label, _ in self.TYPE_FILTERS:
            self._type_combo.addItem(label)
        self._type_combo.currentIndexChanged.connect(self._on_type_filter_changed)
        combo_row.addWidget(self._type_combo, 1)
        lay.addLayout(combo_row)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_selected)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self._list, 1)

        # Export has two distinct jobs, so the menu separates them: a
        # reference card of the rows actually shown (filters and scope
        # included), or the full annotation dataset from the library.
        export_row = QHBoxLayout()
        self._export_btn = QToolButton()
        self._export_btn.setText("Export…")
        self._export_btn.setToolTip("Export these annotations")
        export_menu = QMenu(self._export_btn)
        export_menu.addSection("Reference card of the rows shown")
        for fmt, label in (("pdf", "PDF card…"), ("csv", "CSV table…"),
                           ("markdown", "Markdown table…")):
            act = export_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, f=fmt: self.export_card(f))
        copy_act = export_menu.addAction("Copy as Markdown")
        copy_act.triggered.connect(self.copy_card_markdown)
        export_menu.addSection("Annotation data (from the library)")
        for fmt, label in (("markdown", "Markdown…"), ("html", "HTML…"),
                           ("csv", "CSV…"), ("json", "JSON…"),
                           ("pdf", "PDF…")):
            act = export_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, f=fmt: self._export(f))
        self._export_btn.setMenu(export_menu)
        self._export_btn.setPopupMode(QToolButton.InstantPopup)
        export_row.addWidget(self._export_btn)
        self._print_btn = QToolButton()
        self._print_btn.setText("Print…")
        self._print_btn.setToolTip("Print a card of the annotations shown")
        self._print_btn.clicked.connect(self.print_card)
        export_row.addWidget(self._print_btn)
        export_row.addStretch(1)
        lay.addLayout(export_row)

        # Restore last session's choices (roadmap #15 polish). Runs last so
        # the setCurrentIndex signals fire reload() on a fully built panel.
        self._restore_filter_state()

    def set_document_filter(self, doc_path: str, view=None) -> None:
        self._doc_filter = doc_path
        self._view = view

    def set_view(self, view) -> None:
        """Attach the live view so the panel reflects this-session markups."""
        self._view = view

    def refresh_live(self) -> None:
        """Debounced reload for content_changed storms."""
        self._refresh_timer.start()

    # -- persisted panel state (scope + type filter) --------------------------
    def _restore_filter_state(self) -> None:
        """Apply the scope/type choices saved by the previous session."""
        if self._settings is None:
            return
        self._restoring = True
        try:
            scope = self._settings.get("annotations", "panel_scope", 0)
            self._scope_combo.setCurrentIndex(
                scope if isinstance(scope, int) and 0 <= scope < self._scope_combo.count()
                else 0)
            tfilter = self._settings.get("annotations", "panel_type_filter", 0)
            self._type_combo.setCurrentIndex(
                tfilter if isinstance(tfilter, int) and 0 <= tfilter < self._type_combo.count()
                else 0)
            chips = self._settings.get("annotations", "panel_chips", [])
            valid = {label for label, _c, _a, _d in CHIP_GROUPS}
            self._chips = {c for c in chips if isinstance(c, str) and c in valid} \
                if isinstance(chips, list) else set()
        finally:
            self._restoring = False
        self._sync_chip_buttons()

    def _on_scope_changed(self, index: int) -> None:
        if self._settings is not None and not self._restoring:
            self._settings.set("annotations", "panel_scope", int(index))
        self.reload()

    def _on_type_filter_changed(self, index: int) -> None:
        if self._settings is not None and not self._restoring:
            self._settings.set("annotations", "panel_type_filter", int(index))
        self.reload()

    # -- legend chips (quick type filters) ------------------------------------
    def _toggle_chip(self, name: str) -> None:
        """Flip a legend chip; active chips intersect the type dropdown."""
        if name in self._chips:
            self._chips.discard(name)
        else:
            self._chips.add(name)
        if self._settings is not None and not self._restoring:
            self._settings.set("annotations", "panel_chips", sorted(self._chips))
        self.reload()

    def _sync_chip_buttons(self) -> None:
        """Restyle each chip: count, active fill vs. legend outline."""
        for label, color, _atypes, _dropdown in CHIP_GROUPS:
            btn = self._chip_buttons.get(label)
            if btn is None:
                continue
            active = label in self._chips
            btn.setChecked(active)
            count = self._chip_counts.get(label, 0)
            btn.setText(f"{label} · {count}")
            if active:
                btn.setStyleSheet(
                    f"QToolButton {{ background: {color}; color: #FFFFFF;"
                    f" border: 1px solid {color}; border-radius: 9px;"
                    f" padding: 0 8px; font-weight: 600; }}")
            else:
                dim = "rgba(" + ", ".join(
                    str(int(color[i:i + 2], 16))
                    for i in (1, 3, 5)) + ", 0.45)"
                btn.setStyleSheet(
                    f"QToolButton {{ background: transparent; color: {color};"
                    f" border: 1px solid {dim}; border-radius: 9px;"
                    f" padding: 0 8px; }}")
            verb = "hide" if active else "show only"
            btn.setToolTip(
                f"{count} {label.lower()} — click to {verb} this type")

    def _collect_list_types(self) -> list[str]:
        """Atypes of the rows currently shown in the list (test/introspection)."""
        out = []
        for i in range(self._list.count()):
            d = self._list.item(i).data(Qt.UserRole)
            if d:
                out.append(d.get("atype", ""))
        return out

    # -- sources --------------------------------------------------------------
    def _collect(self) -> list[dict]:
        """Merge live view annotations (if attached) with DB records."""
        text = (self._filter.text() or "").lower()
        out: list[dict] = []

        # 1) Live view source — PDF-native + on-screen sticky notes.
        if self._view is not None and not text:
            pass  # filter still applies below; live source supports it
        if self._view is not None:
            try:
                for a in self._view.live_annotations():
                    raw_type = str(a.get("type", ""))
                    atype = PDF_TYPE_MAP.get(raw_type, raw_type.lower())
                    out.append({
                        "path": str(self._view.path),
                        "page": int(a.get("page", 0)),
                        "atype": atype,
                        "color": a.get("color", "#E5B25D"),
                        "body": str(a.get("text") or ""),
                        "note": "",
                        "author": a.get("author", ""),
                        "date": a.get("date", ""),
                        "xref": a.get("pdf_xref"),
                        "rect": list(a.get("rect") or [0, 0, 0, 0]),
                        "sticky": bool(a.get("sticky")),
                        "live": True,
                    })
            except Exception:
                pass

        # 2) Database records. In "current document" scope, skip DB rows that
        # duplicate a live PDF annotation (same page + type) so the list does
        # not double-count markups that exist in both stores.
        all_docs = self._scope_combo.currentText() == "All documents"
        if all_docs:
            records = self._anns.all_annotations()
        else:
            records = self._anns.for_document(self._doc_filter)
        live_keys = {(a["page"], a["atype"]) for a in out if a["atype"]}
        for rec in records:
            if not all_docs and (rec.page, rec.atype) in live_keys \
                    and rec.atype not in ("note", "freetext"):
                continue
            out.append({
                "path": rec.doc_path,
                "page": rec.page,
                "atype": rec.atype,
                "color": rec.color,
                "body": rec.text or "",
                "note": rec.note or "",
                "author": rec.author or "",
                "date": "",
                "xref": None,
                "uuid": rec.uuid,
                "sticky": False,
                "live": False,
            })
        # Filter after merge so both sources honor the search box and the
        # type dropdown.
        if text:
            out = [a for a in out
                   if text in (a["body"] + a["note"] + a["atype"]).lower()]
        allowed = self.TYPE_FILTERS[self._type_combo.currentIndex()][1]
        if allowed is not None:
            out = [a for a in out if a["atype"] in allowed]
        if self._chips:
            chip_allowed: set[str] = set()
            for label, _color, atypes, _dropdown in CHIP_GROUPS:
                if label in self._chips:
                    chip_allowed |= atypes
            out = [a for a in out if a["atype"] in chip_allowed]
        out.sort(key=lambda a: (a["path"], a["page"]))
        return out

    def reload(self) -> None:
        rows = self._collect()
        self._list.clear()
        for a in rows:
            kind_icon = {
                "highlight": "highlighter", "underline": "underline",
                "strikeout": "strikeout", "squiggly": "underline",
                "note": "note", "note-widget": "note", "freetext": "text",
                "ink": "ink", "rectangle": "shapes", "ellipse": "shapes",
                "arrow": "shapes", "line": "shapes", "stamp": "stamp",
            }.get(a["atype"], "note")
            body = a["body"] or a["note"] or f"[{a['atype']}]"
            prefix = "✎ " if a["sticky"] else ""
            item = QListWidgetItem(icon(kind_icon, a["color"]),
                                   f"p.{a['page'] + 1}  ·  {prefix}{body[:100]}")
            item.setData(Qt.UserRole, a)
            tip = f"{a['atype']} by {a['author'] or 'Me'}"
            if a["live"]:
                tip += " · live (unsaved session state)"
            item.setToolTip(tip)
            self._list.addItem(item)
        # Per-group counts feed the legend chips (post-filter list size).
        for label, _color, atypes, _dropdown in CHIP_GROUPS:
            self._chip_counts[label] = sum(
                1 for a in rows if a["atype"] in atypes)
        self._sync_chip_buttons()
        # Count badge shows the post-filter list size (#15 polish); the
        # "No annotations yet" placeholder row is not counted.
        first = self._list.item(0)
        empty = self._list.count() == 1 and (
            first is None or first.text() == "No annotations yet")
        self._count_badge.setText("0" if empty else str(self._list.count()))

    def _on_selected(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if data:
            self.annotation_selected.emit(data["path"], data["page"])

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        """Double-click: jump + flash for live items, edit note for DB rows."""
        data = item.data(Qt.UserRole)
        if not data:
            return
        if data.get("live"):
            self.flash_requested.emit(data)
        elif data.get("uuid"):
            self._edit_note(data)

    def _menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if not item or not item.data(Qt.UserRole):
            return
        data = item.data(Qt.UserRole)
        menu = QMenu(self)
        jump = menu.addAction("Go to annotation")
        flash = menu.addAction("Show on page (flash)") if data.get("live") else None
        edit = menu.addAction("Edit note…")
        delete = menu.addAction(icon("trash", "#A33B2E"), "Delete")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is jump:
            self.annotation_selected.emit(data["path"], data["page"])
        elif flash is not None and chosen is flash:
            self.flash_requested.emit(data)
        elif chosen is edit:
            self._edit_note(data)
        elif chosen is delete:
            self._delete_annotation(data)

    def _delete_annotation(self, data: dict) -> None:
        """Delete from the live view (PDF or sticky widget) and/or the DB."""
        deleted = False
        view = self._view
        if data.get("live") and view is not None:
            xref = data.get("xref")
            if data.get("sticky"):
                for note in list(getattr(view, "_sticky_notes", [])):
                    if note.page_index == data["page"] \
                            and (note.text() or "") == data["body"]:
                        view.remove_sticky_note(note)
                        deleted = True
                        break
            elif xref:
                try:
                    view.delete_annotation_xref(int(xref))
                    deleted = True
                except Exception:
                    pass
        else:
            # DB-backed record: also remove the matching PDF markup when the
            # current view is the same document.
            if view is not None and str(view.path) == data["path"] \
                    and data.get("xref"):
                try:
                    view.delete_annotation_xref(int(data["xref"]))
                except Exception:
                    pass
            try:
                self._anns.delete(data["uuid"], data["path"])
                deleted = True
            except Exception:
                pass
        if deleted:
            self.reload()
            self.annotations_changed.emit()

    def _edit_note(self, data: dict) -> None:
        from PySide6.QtWidgets import QInputDialog
        uuid = data.get("uuid", "")
        rec = next((r for r in self._anns.all_annotations()
                    if r.uuid == uuid), None) if uuid else None
        if not rec:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Edit annotation note", "Note text:", rec.note)
        if ok:
            self._anns.update(rec, note=text)
            self.reload()
            self.annotations_changed.emit()

    # -- export / print (reference surfaces) -------------------------------
    def visible_rows(self) -> list[tuple[str, str, str, str]]:
        """The rows currently listed, as card cells.

        Honors the search box, the type dropdown, the legend chips and the
        scope, so an export matches what the user is looking at (including
        markups that only exist in this session).
        """
        all_docs = self._scope_combo.currentText() == "All documents"
        rows: list[tuple[str, str, str, str]] = []
        for a in self._collect():
            body = (a.get("body") or a.get("note") or "").strip()
            body = " ".join(body.split())          # collapse newlines for the card
            if a.get("sticky"):
                body = f"sticky note: {body}" if body else "sticky note"
            where = f"p.{a['page'] + 1}"
            if all_docs:
                where = f"{Path(str(a.get('path') or '')).name} {where}"
            rows.append((where, str(a.get("atype") or ""),
                         str(a.get("author") or "Me"), body or "[no text]"))
        return rows

    def _card_title(self) -> str:
        scope = Path(self._doc_filter).name if self._doc_filter else ""
        return f"Annotations — {scope}" if scope else "Annotations"

    def _card_subtitle(self, count: int) -> str:
        bits = [self._scope_combo.currentText().lower(),
                self._type_combo.currentText().lower()]
        if self._chips:
            bits.append("chips: " + ", ".join(sorted(self._chips)))
        text = (self._filter.text() or "").strip()
        if text:
            bits.append(f"filter: {text}")
        return f"{count} annotations · " + " · ".join(bits)

    def export_card(self, fmt: str = "pdf"):
        """Export the listed annotations as a PDF card, CSV or Markdown."""
        rows = self.visible_rows()
        stem = Path(self._doc_filter).stem if self._doc_filter else "annotations"
        return export_dialog(
            self.window(), rows, title=self._card_title(),
            default_name=f"{stem}.annotations.pdf",
            columns=ANNOTATION_COLUMNS, subtitle=self._card_subtitle(len(rows)),
            fmt=fmt,
            empty_warning="Nothing to export — no annotations match")

    def print_card(self) -> bool:
        """Print a card of the listed annotations."""
        rows = self.visible_rows()
        return print_table(self.window(), rows, title=self._card_title(),
                           subtitle=self._card_subtitle(len(rows)),
                           columns=ANNOTATION_COLUMNS,
                           empty_warning="Nothing to print — no annotations "
                                         "match")

    def copy_card_markdown(self) -> bool:
        """Copy the listed annotations to the clipboard as Markdown."""
        rows = self.visible_rows()
        return copy_table_markdown(
            self.window(), rows, title=self._card_title(),
            subtitle=self._card_subtitle(len(rows)),
            columns=ANNOTATION_COLUMNS,
            empty_warning="Nothing to copy — no annotations match")

    def _export(self, fmt: str) -> None:
        from PySide6.QtWidgets import QFileDialog
        doc_path = self._doc_filter or ""
        if not doc_path:
            self._scope_combo.setCurrentIndex(0)
            return
        default = Path(doc_path).with_suffix(f".annotations.{fmt}")
        target, _ = QFileDialog.getSaveFileName(
            self, "Export annotations", str(default))
        if not target:
            return
        out = self._anns.export(doc_path, fmt, Path(target),
                                title=Path(doc_path).name)
        show_toast(self.window(), f"Annotations exported to {out.name}", "success")


class NotesPanel(QWidget):
    """Document-linked notes / notebook entries."""

    note_selected = Signal(str, int)  # doc_path, page (anchor)

    def __init__(self, note_repo: NoteRepository, parent=None) -> None:
        super().__init__(parent)
        self._repo = note_repo
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        self._new_btn = QPushButton("New note")
        self._new_btn.clicked.connect(self._new_note)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._delete_note)
        row.addWidget(self._new_btn)
        row.addWidget(self._delete_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selected)
        lay.addWidget(self._list, 1)

        self._editor_title = QLineEdit()
        self._editor_title.setPlaceholderText("Title")
        self._editor_title.textEdited.connect(self._save_current)
        self._editor = QTextBrowser()
        lay.addWidget(self._editor_title)
        lay.addWidget(self._editor, 2)
        self._current: NoteRecord | None = None
        self.reload()

    def reload(self) -> None:
        self._list.clear()
        for rec in self._repo.all():
            title = rec.title or "(untitled note)"
            when = time.strftime("%Y-%m-%d", time.localtime(rec.modified_at))
            item = QListWidgetItem(icon("note", "#8A5A2B"), f"{title}\n{when}")
            item.setData(Qt.UserRole, rec.id)
            self._list.addItem(item)

    def _new_note(self) -> None:
        rec = NoteRecord(title="New note", body="")
        self._repo.create(rec)
        self.reload()

    def _delete_note(self) -> None:
        if self._current:
            self._repo.delete(self._current.id)
            self._current = None
            self.reload()

    def _on_selected(self, current, previous) -> None:
        if current is None:
            return
        note_id = current.data(Qt.UserRole)
        for rec in self._repo.all():
            if rec.id == note_id:
                self._current = rec
                self._editor_title.setText(rec.title)
                self._editor.setHtml(markdown_to_html(rec.body or ""))
                break

    def _save_current(self) -> None:
        if self._current:
            self._current.title = self._editor_title.text()
            self._repo.update(self._current)
            self.reload()

    def set_body_editable(self) -> None:
        pass


def markdown_to_html(text: str) -> str:
    from veyrion_workspace.ui.views.text_view import markdown_to_html as _m
    return _m(text)


class ThumbnailsPanel(QWidget):
    """Page thumbnail strip with click navigation."""

    page_selected = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._view = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(6)
        self._area.setWidget(self._container)
        lay.addWidget(self._area)
        self._buttons: list[QToolButton] = []

    def set_view(self, view) -> None:
        self._view = view
        self.rebuild()

    def rebuild(self) -> None:
        for b in self._buttons:
            b.deleteLater()
        self._buttons = []
        if self._view is None:
            return
        count = self._view.page_count
        cols = 2
        for i in range(count):
            btn = QToolButton()
            btn.setText(str(i + 1))
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(88, 110))
            btn.clicked.connect(lambda _=False, idx=i: self.page_selected.emit(idx))
            self._buttons.append(btn)
            self._grid.addWidget(btn, i // cols, i % cols)
        self._render_thumbs()

    def _render_thumbs(self) -> None:
        """Render thumbnails in the background via the view's engine."""
        if self._view is None:
            return
        import io
        for i, btn in enumerate(self._buttons):
            try:
                if i < self._view.page_count:
                    img = self._view.engine.render_thumbnail(i, 110)
                    if img is not None:
                        if hasattr(img, "save") and not hasattr(img, "scaled"):
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            from PySide6.QtGui import QImage
                            qimg = QImage.fromData(buf.getvalue(), "PNG")
                            btn.setIcon(icon_or_pixmap(qimg))
                        else:
                            btn.setIcon(icon_or_pixmap(img))
            except Exception:
                pass
        self.highlight(self._view.current_page)

    def highlight(self, page: int) -> None:
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == page)


def icon_or_pixmap(qimg) -> object:
    from PySide6.QtGui import QPixmap, QImage, QIcon
    if isinstance(qimg, QImage):
        return QIcon(QPixmap.fromImage(qimg))
    if isinstance(qimg, QPixmap):
        return QIcon(qimg)
    return qimg


class TocPanel(QWidget):
    """Table of contents / outline with clickable navigation."""

    entry_selected = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemClicked.connect(self._on_click)
        lay.addWidget(self._tree)
        self._empty = EmptyState("toc", "No outline",
                                 "This document has no table of contents.")
        lay.addWidget(self._empty)
        self._empty.hide()

    def set_toc(self, entries) -> None:
        self._tree.clear()
        if not entries:
            self._tree.hide()
            self._empty.show()
            return
        self._tree.show()
        self._empty.hide()
        stack = [(entry, None) for entry in reversed(entries)]
        parents: dict[int, QTreeWidgetItem] = {}
        # Build by level with a simple stack pass.
        level_items: dict[int, QTreeWidgetItem] = {}
        roots: list[QTreeWidgetItem] = []
        for entry in entries:
            item = QTreeWidgetItem([entry.title or "(untitled)"])
            item.setData(0, Qt.UserRole, entry.page)
            if entry.level <= 1 or not level_items:
                roots.append(item)
                level_items.clear()
                level_items[entry.level] = item
            else:
                parent = None
                for lvl in range(entry.level - 1, 0, -1):
                    if lvl in level_items:
                        parent = level_items[lvl]
                        break
                if parent is None:
                    roots.append(item)
                else:
                    parent.addChild(item)
                level_items[entry.level] = item
        self._tree.insertTopLevelItems(0, roots)
        self._tree.expandAll()

    def _on_click(self, item, col) -> None:
        page = item.data(0, Qt.UserRole)
        if page is not None:
            self.entry_selected.emit(int(page))


class BookmarksPanel(QWidget):
    bookmark_selected = Signal(int, float)

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self._db = db
        self._doc_path = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._add_btn = QPushButton("Bookmark current page")
        self._add_btn.clicked.connect(self._add)
        lay.addWidget(self._add_btn)
        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_click)
        lay.addWidget(self._list, 1)

    def set_document(self, doc_path: str) -> None:
        self._doc_path = doc_path
        self.reload()

    def reload(self) -> None:
        self._list.clear()
        if not self._doc_path:
            return
        rows = self._db.query(
            "SELECT * FROM bookmarks WHERE doc_path=? ORDER BY page", (self._doc_path,))
        for row in rows:
            item = QListWidgetItem(icon("bookmark", "#C7522A"),
                                   f"p.{row['page'] + 1} — {row['title'] or 'Bookmark'}")
            item.setData(Qt.UserRole, {"page": row["page"], "scroll": row["scroll"]})
            self._list.addItem(item)

    def _add(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        if not self._doc_path:
            return
        page = 0
        view = self.window().current_view() if hasattr(self.window(), "current_view") else None
        if view is not None:
            page = view.current_page
        title, ok = QInputDialog.getText(self, "Bookmark", "Label (optional):")
        self._db.execute(
            "INSERT INTO bookmarks (doc_path, page, title, created_at) VALUES (?,?,?,?)",
            (self._doc_path, page, title if ok else "", time.time()))
        self.reload()

    def _on_click(self, item) -> None:
        data = item.data(Qt.UserRole)
        self.bookmark_selected.emit(data["page"], data["scroll"])


class TasksPanel(QWidget):
    """Background task center: live progress, cancel, retry, errors."""

    def __init__(self, task_manager, parent=None) -> None:
        super().__init__(parent)
        self._tm = task_manager
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._rows: dict[str, dict] = {}
        self._grid = QVBoxLayout()
        self._grid.setSpacing(6)
        lay.addLayout(self._grid)
        lay.addStretch(1)
        self._clear_btn = QPushButton("Clear finished")
        self._clear_btn.clicked.connect(self._clear)
        lay.addWidget(self._clear_btn)
        self._tm.add_listener(self._on_event)
        self.reload()

    def reload(self) -> None:
        for task in self._tm.all_tasks():
            self._update_row(task)

    def _on_event(self, task, event) -> None:
        try:
            # Task events arrive from worker threads; schedule on the UI thread.
            from PySide6.QtCore import QMetaObject, Q_ARG, QueuedConnection
            QMetaObject.invokeMethod(self, "_ui_reload",
                                     QueuedConnection)
        except Exception:
            pass

    def _ui_reload(self) -> None:
        self.reload()

    def _update_row(self, task) -> None:
        from PySide6.QtCore import QMetaObject, QueuedConnection
        QMetaObject.invokeMethod(self, "_ui_update", QueuedConnection)

    def _clear(self) -> None:
        self._tm.clear_finished()
        self.reload()


class PropertiesPanel(QWidget):
    """Read-only document properties, computed from the live engine."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignRight)
        lay.addLayout(self._form)
        lay.addStretch(1)
        self._empty = EmptyState("properties", "No document",
                                 "Open a document to see its properties.")
        lay.addWidget(self._empty)
        self.set_document(None)

    def set_document(self, view) -> None:
        # Clear form
        while self._form.rowCount():
            self._form.removeRow(0)
        if view is None:
            self._empty.show()
            return
        self._empty.hide()
        try:
            md = view.engine.metadata()
            fields = [
                ("Title", md.title or "—"),
                ("Author", md.author or "—"),
                ("Subject", md.subject or "—"),
                ("Pages", str(md.page_count)),
                ("Words", f"{md.word_count:,}" if md.word_count else "—"),
                ("Encrypted", "Yes" if md.encrypted else "No"),
                ("Scanned", "Likely" if md.scanned else "No"),
                ("Signed", "Yes" if md.signed else "No"),
                ("Producer", md.producer or "—"),
            ]
            for key, value in fields:
                label = QLabel(str(value))
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self._form.addRow(f"<b>{key}</b>", label)
        except Exception:
            self._empty.show()
