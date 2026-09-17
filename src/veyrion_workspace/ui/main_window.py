"""Veyrion Workspace main window.

Composition:
  * Custom toolbar with document tools, nav rail, and tabbed workspace.
  * Dockable panels: library, thumbnails, TOC, bookmarks, annotations,
    notes, search, tasks, properties — all state persisted.
  * Status bar: page, zoom, progress, background tasks, save state.
  * Command palette (Ctrl+K), full menu bar, keyboard shortcuts.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, QTimer, Qt, QSettings, QPoint
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QSplitter, QStatusBar,
    QToolBar, QToolButton, QVBoxLayout, QWidget, QInputDialog, QTabWidget,
    QStackedWidget,
)

from veyrion_workspace import APP_NAME, APP_TAGLINE, __version__
from veyrion_workspace.app import paths
from veyrion_workspace.core.annotations.service import AnnotationService
from veyrion_workspace.core.documents.registry import open_document
from veyrion_workspace.core.documents.base import DocumentError, EncryptedDocumentError
from veyrion_workspace.core.search.engine import SearchEngine
from veyrion_workspace.core.versioning import VersionStore
from veyrion_workspace.services.recovery import SessionJournal
from veyrion_workspace.services.settings import Settings
from veyrion_workspace.services.shortcuts import (
    ShortcutReport,
    apply_overrides,
    collect_actions,
    overrides_path,
    read_overrides,
    write_template,
)
from veyrion_workspace.services.tasks import TaskManager
from veyrion_workspace.storage.database import Database
from veyrion_workspace.storage.repositories import (
    AnnotationRepository, LibraryRepository, NoteRepository, ProgressRepository,
    SessionRepository,
)
from veyrion_workspace.ui.command_palette import Command, CommandPalette
from veyrion_workspace.ui.shortcuts_dialog import ShortcutsDialog
from veyrion_workspace.ui.dialogs import (
    AboutDialog, get_password, show_error, show_toast_msg,
)
from veyrion_workspace.ui.icons import icon, make_window_icon
from veyrion_workspace.ui.panels.info_panels import (
    AnnotationsPanel, BookmarksPanel, NotesPanel, PropertiesPanel,
    SearchPanel, TocPanel, TasksPanel, ThumbnailsPanel,
)
from veyrion_workspace.ui.panels.library_panel import LibraryPanel
from veyrion_workspace.ui.document_view import DocumentView
from veyrion_workspace.ui.tabwidget import DocumentTabWidget
from veyrion_workspace.ui.theme import build_qss, get_palette
from veyrion_workspace.ui.widgets import (
    ClickableLabel,
    NavRail,
    show_actionable_toast,
    show_toast,
)
from veyrion_workspace.ui.views.factory import create_view
from veyrion_workspace.utils.pathutils import format_size


import logging

logger = logging.getLogger("veyrion.mainwin")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, db: Database, tasks: TaskManager,
                 journal: SessionJournal) -> None:
        super().__init__()
        self.settings = settings
        self.db = db
        self.tasks = tasks
        self.journal = journal
        self._versioning = VersionStore(
            depth=int(settings.get("general", "version_history_depth", 5)))
        self._library = LibraryRepository(db)
        self._notes = NoteRepository(db)
        self._progress = ProgressRepository(db)
        self._annotations = AnnotationService(AnnotationRepository(db), settings)
        self._search_engine = SearchEngine(db)
        self._sessions = SessionRepository(db)
        self._fullscreen = False
        self._focus_mode = False
        self._session_start = time.time()
        self._current_session_id = None

        self.setWindowTitle(f"{APP_NAME} — {APP_TAGLINE}")
        self.setWindowIcon(make_window_icon())
        self.resize(1280, 820)
        self.setDockNestingEnabled(True)
        self.setAcceptDrops(True)

        self._apply_theme()
        self._build_tabs()
        self._build_panels()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._build_palette()
        self._connect_signals()
        self._restore_window_state()

        # Apply any user-pasted shortcut map before the window is shown so
        # the menus, the cheat sheet and the palette all agree from the start.
        self.shortcut_report: ShortcutReport = ShortcutReport()
        self._apply_shortcut_overrides(notify=False)

        self._journal_timer = QTimer(self)
        self._journal_timer.setInterval(15000)
        self._journal_timer.timeout.connect(self._record_session)
        self._journal_timer.start()

        # Distraction-free mode (#79) persists across restarts.
        if bool(self.settings.get("reading", "distraction_free", False)):
            self._apply_distraction_free(True)
            self.act_distraction.setChecked(True)

        # Point users at the shortcut cheat sheet once, after onboarding
        # has completed. Modeless so it never blocks startup or tests.
        if bool(self.settings.get("general", "first_run_complete", False)) \
                and not bool(self.settings.get("shortcuts", "hint_shown", False)):
            self.settings.set("shortcuts", "hint_shown", True)
            QTimer.singleShot(1200, self._show_shortcuts_once)

    # ================================================================ theme
    def _apply_theme(self) -> None:
        palette = get_palette(
            self.settings.get("appearance", "theme", "light"),
            self.settings.get("appearance", "accent", ""))
        qss = build_qss(
            palette,
            large_text=bool(self.settings.get("appearance", "large_text", False)),
            dyslexia_font=bool(self.settings.get("appearance", "dyslexia_font", False)),
            reduced_motion=bool(self.settings.get("appearance", "reduced_motion", False)))
        app = QApplication.instance()
        app.setStyleSheet(qss)
        self._palette = palette

    def retheme(self) -> None:
        self._apply_theme()
        show_toast(self, "Theme updated", "info")

    # ================================================================ workspace
    def _build_tabs(self) -> None:
        self.tabs = DocumentTabWidget()
        self.tabs.tab_close_requested.connect(self.close_tab)
        self.tabs.tab_detach_requested.connect(self.detach_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Periodic crash-journal heartbeat, parented to the window so it is
        # collected with it (main.py no longer needs its own timer).
        self._journal_timer = QTimer(self)
        self._journal_timer.setInterval(30_000)
        self._journal_timer.timeout.connect(self._record_session)
        self._journal_timer.start()
        placeholder = self._make_placeholder()
        self._placeholder = placeholder
        self.setCentralWidget(placeholder)
        self.tabs.hide()

    def _make_placeholder(self) -> QWidget:
        from veyrion_workspace.ui.widgets import EmptyState
        page = QWidget()
        lay = QVBoxLayout(page)
        empty = EmptyState(
            "app", "Welcome to Veyrion Workspace",
            "Open a document (Ctrl+O), drop files anywhere in this window,\n"
            "or browse your library.",
            "Open document…")
        if empty.action_button:
            empty.action_button.clicked.connect(self.action_open)
        lay.addWidget(empty)
        return page

    def current_view(self) -> DocumentView | None:
        if not self.tabs.isVisible() or self.tabs.currentWidget() is None:
            return None
        view = self.tabs.currentWidget()
        return view if isinstance(view, DocumentView) else None

    def current_view_any(self) -> DocumentView | None:
        return self.current_view()

    # -- SplitView-aware document collection -------------------------------
    def _document_views_in(self, widget) -> list[DocumentView]:
        """Every DocumentView hosted by *widget* (a tab may be a SplitView)."""
        if isinstance(widget, DocumentView):
            return [widget]
        views = getattr(widget, "views", None)
        if callable(views):
            return [v for v in views() if isinstance(v, DocumentView)]
        return []

    def _all_document_views(self) -> list[DocumentView]:
        out: list[DocumentView] = []
        for i in range(self.tabs.count()):
            out.extend(self._document_views_in(self.tabs.widget(i)))
        return out

    # ================================================================ panels
    def _build_panels(self) -> None:
        self.library_panel = LibraryPanel(self._library, self.settings,
                                          self.tasks)
        self.library_panel.open_requested.connect(self.open_path)
        self.library_panel.open_in_tab_requested.connect(self.open_path)

        self.thumbnails_panel = ThumbnailsPanel()
        self.thumbnails_panel.page_selected.connect(self._goto_page)
        self.toc_panel = TocPanel()
        self.toc_panel.entry_selected.connect(self._goto_page)
        self.bookmarks_panel = BookmarksPanel(self.db)
        self.bookmarks_panel.bookmark_selected.connect(self._goto_page_scroll)
        self.annotations_panel = AnnotationsPanel(self._annotations, self.settings)
        self.annotations_panel.annotation_selected.connect(
            lambda path, page: self._goto_page(page))
        self.annotations_panel.flash_requested.connect(
            self._flash_annotation_on_page)
        self.annotations_panel.set_document_filter("")
        self.notes_panel = NotesPanel(self._notes)
        self.search_panel = SearchPanel(self._search_engine)
        self.search_panel.hit_selected.connect(
            lambda path, page: self._goto_page(page))
        self.tasks_panel = TasksPanel(self.tasks)
        self.properties_panel = PropertiesPanel()

        self.dock_library = self._dock("Library", self.library_panel,
                                       Qt.LeftDockWidgetArea)
        self.dock_thumbs = self._dock("Pages", self.thumbnails_panel,
                                      Qt.LeftDockWidgetArea)
        self.dock_toc = self._dock("Contents", self.toc_panel,
                                   Qt.LeftDockWidgetArea)
        self.dock_bookmarks = self._dock("Bookmarks", self.bookmarks_panel,
                                         Qt.LeftDockWidgetArea)
        self.dock_annotations = self._dock("Annotations",
                                           self.annotations_panel,
                                           Qt.RightDockWidgetArea)
        self.dock_notes = self._dock("Notes", self.notes_panel,
                                     Qt.RightDockWidgetArea)
        self.dock_search = self._dock("Search", self.search_panel,
                                      Qt.RightDockWidgetArea)
        self.dock_tasks = self._dock("Tasks", self.tasks_panel,
                                     Qt.BottomDockWidgetArea)
        self.dock_props = self._dock("Properties", self.properties_panel,
                                     Qt.RightDockWidgetArea)
        self.tabifyDockWidget(self.dock_annotations, self.dock_notes)
        self.tabifyDockWidget(self.dock_annotations, self.dock_props)
        self.tabifyDockWidget(self.dock_toc, self.dock_bookmarks)
        self.dock_annotations.raise_()

        # Document-first defaults: only the Library is open initially.
        # Every other panel is one click away in View -> Panels and opens
        # at a working size instead of hogging the whole window.
        for dock in (self.dock_thumbs, self.dock_toc, self.dock_bookmarks,
                     self.dock_annotations, self.dock_notes, self.dock_search,
                     self.dock_tasks, self.dock_props):
            dock.hide()
        for dock in (self.dock_library, self.dock_thumbs, self.dock_toc,
                     self.dock_bookmarks, self.dock_annotations,
                     self.dock_notes, self.dock_search, self.dock_props):
            dock.setMinimumWidth(200)

    def showEvent(self, event) -> None:
        """On first show, size the docks to their working widths.

        QDockWidgets otherwise open at their content's sizeHint, which for
        the Library's toolbar is far wider than a side panel should be.
        """
        super().showEvent(event)
        if getattr(self, "_docks_initial_sized", False):
            return
        self._docks_initial_sized = True
        self.resizeDocks([self.dock_library], [300], Qt.Horizontal)
        self.resizeDocks([self.dock_thumbs], [220], Qt.Horizontal)
        self.resizeDocks([self.dock_toc, self.dock_bookmarks], [260], Qt.Horizontal)
        self.resizeDocks([self.dock_annotations, self.dock_notes,
                          self.dock_search, self.dock_props], [320], Qt.Horizontal)

    def _dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        dock.setObjectName(f"dock_{title.lower().replace(' ', '_')}")
        dock.setFeatures(QDockWidget.DockWidgetMovable |
                         QDockWidget.DockWidgetClosable |
                         QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, dock)
        return dock

    # ================================================================ actions
    def _build_actions(self) -> None:
        A = QAction
        self.act_open = A(icon("open", "#2A2721"), "&Open…", self)
        self.act_open.setShortcut(QKeySequence("Ctrl+O"))
        self.act_open.triggered.connect(self.action_open)

        self.act_open_folder = A(icon("folder", "#2A2721"), "Open &Folder…", self)
        self.act_open_folder.triggered.connect(self.action_open_folder)

        self.act_save = A(icon("save", "#2A2721"), "&Save", self)
        self.act_save.setShortcut(QKeySequence("Ctrl+S"))
        self.act_save.triggered.connect(self.action_save)

        self.act_save_as = A("Save &As…", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.triggered.connect(self.action_save_as)

        self.act_versions = A(icon("history", "#2A2721"), "&Version History…", self)
        self.act_versions.triggered.connect(self.action_version_history)

        self.act_print = A(icon("print", "#2A2721"), "&Print…", self)
        self.act_print.setShortcut(QKeySequence("Ctrl+P"))
        self.act_print.triggered.connect(self.action_print)

        self.act_export = A(icon("convert", "#2A2721"), "&Export…", self)
        self.act_export.triggered.connect(self.action_export)

        self.act_close_tab = A("&Close Tab", self)
        self.act_close_tab.setShortcut(QKeySequence("Ctrl+W"))
        self.act_close_tab.triggered.connect(
            lambda: self.close_tab(self.tabs.currentIndex()))

        self.act_quit = A("E&xit", self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.triggered.connect(self.close)

        self.act_undo = A("&Undo", self)
        self.act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.act_undo.triggered.connect(self.action_undo)

        self.act_redo = A("&Redo", self)
        self.act_redo.setShortcut(QKeySequence("Ctrl+Y"))
        self.act_redo.triggered.connect(self.action_redo)

        self.act_find = A("&Find in Document…", self)
        self.act_find.setShortcut(QKeySequence("Ctrl+F"))
        self.act_find.triggered.connect(self.action_find)

        self.act_palette = A("Command &Palette", self)
        self.act_palette.setShortcut(QKeySequence("Ctrl+K"))
        self.act_palette.triggered.connect(self._open_palette)

        self.act_zoom_in = A("Zoom &In", self)
        self.act_zoom_in.setShortcut(QKeySequence("Ctrl++"))
        self.act_zoom_in.triggered.connect(self._zoom_in)

        self.act_zoom_out = A("Zoom &Out", self)
        self.act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        self.act_zoom_out.triggered.connect(self._zoom_out)

        self.act_fit_width = A("Fit &Width", self)
        self.act_fit_width.setShortcut(QKeySequence("Ctrl+1"))
        self.act_fit_width.triggered.connect(self._fit_width)

        self.act_fit_page = A("Fit &Page", self)
        self.act_fit_page.setShortcut(QKeySequence("Ctrl+0"))
        self.act_fit_page.triggered.connect(self._fit_page)

        self.act_next_page = A("&Next Page", self)
        self.act_next_page.setShortcut(QKeySequence("Right"))
        self.act_next_page.triggered.connect(self._next_page)

        self.act_prev_page = A("&Previous Page", self)
        self.act_prev_page.setShortcut(QKeySequence("Left"))
        self.act_prev_page.triggered.connect(self._prev_page)

        self.act_goto_page = A("&Go to Page…", self)
        self.act_goto_page.setShortcut(QKeySequence("Ctrl+G"))
        self.act_goto_page.triggered.connect(self._goto_page_dialog)

        self.act_nav_back = A("&Back", self)
        self.act_nav_back.setShortcut(QKeySequence("Alt+Left"))
        self.act_nav_back.triggered.connect(self._nav_back)

        self.act_nav_forward = A("&Forward", self)
        self.act_nav_forward.setShortcut(QKeySequence("Alt+Right"))
        self.act_nav_forward.triggered.connect(self._nav_forward)

        self.act_ocr = A(icon("ocr", "#2A2721"), "Run &OCR…", self)
        self.act_ocr.triggered.connect(self.action_ocr)

        self.act_read_aloud = A(icon("speaker", "#2A2721"), "&Read Aloud", self)
        self.act_read_aloud.setCheckable(True)
        self.act_read_aloud.triggered.connect(self.action_read_aloud)

        self.act_translate = A(icon("globe", "#2A2721"), "&Translate Selection…", self)
        self.act_translate.triggered.connect(self.action_translate)

        self.act_dictionary = A("&Dictionary Lookup…", self)
        self.act_dictionary.triggered.connect(self.action_dictionary)

        self.act_summarize = A("Su&mmarize Document…", self)
        self.act_summarize.triggered.connect(self.action_summarize)

        self.act_compare = A(icon("compare", "#2A2721"), "Compare &Documents…", self)
        self.act_compare.triggered.connect(self.action_compare)

        self.act_split_view = A(icon("split", "#2A2721"), "&Split View…", self)
        self.act_split_view.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.act_split_view.triggered.connect(self.action_split_view)

        self.act_redact = A(icon("redact", "#A33B2E"), "Redaction Mode", self)
        self.act_redact.setCheckable(True)
        self.act_redact.triggered.connect(self.action_toggle_redact)

        self.act_verify_redaction = A("Verify Redaction…", self)
        self.act_verify_redaction.triggered.connect(self.action_verify_redaction)

        self.act_sign = A(icon("signature", "#2A2721"), "&Sign Document…", self)
        self.act_sign.triggered.connect(self.action_sign)

        self.act_signatures = A("View &Signatures", self)
        self.act_signatures.triggered.connect(self.action_show_signatures)

        self.act_forms = A(icon("properties", "#2A2721"), "Edit &Form Fields…", self)
        self.act_forms.triggered.connect(self.action_forms)

        self.act_meta_edit = A("Edit &Metadata…", self)
        self.act_meta_edit.triggered.connect(self.action_edit_metadata)

        self.act_meta_strip = A("Strip &Metadata…", self)
        self.act_meta_strip.triggered.connect(self.action_strip_metadata)

        self.act_optimize = A("Optimi&ze PDF…", self)
        self.act_optimize.triggered.connect(self.action_optimize)

        self.act_merge = A(icon("merge", "#2A2721"), "Merge &PDF…", self)
        self.act_merge.triggered.connect(self.action_merge)

        self.act_split = A("&Split PDF…", self)
        self.act_split.triggered.connect(self.action_split)

        self.act_focus = A(icon("focus", "#2A2721"), "&Focus Mode", self)
        self.act_focus.setCheckable(True)
        self.act_focus.setShortcut(QKeySequence("F9"))
        self.act_focus.triggered.connect(self.action_focus_mode)

        # Distraction-free mode (#79): fades out ALL chrome (F8).
        self.act_distraction = A(icon("focus", "#2A2721"),
                                 "&Distraction-Free Mode", self)
        self.act_distraction.setCheckable(True)
        self.act_distraction.setShortcut(QKeySequence("F8"))
        self.act_distraction.triggered.connect(self.action_distraction_free)

        # Per-view page theme radios (#77) + spread tuning (#10).
        self.act_themes = []
        for key, label in (("", "&Follow App Theme"), ("light", "&Light"),
                           ("sepia", "&Sepia"), ("dark", "&Dark"),
                           ("oled", "O&LED Black")):
            a = A(label, self)
            a.setCheckable(True)
            a.setData(key)
            a.triggered.connect(lambda _=False, k=key: self._set_view_theme(k))
            self.act_themes.append(a)
        self.act_gap_tuning = A("Spread &Gap…", self)
        self.act_gap_tuning.triggered.connect(self._gap_tuning_dialog)
        self.act_themes[0].setChecked(True)   # follow app theme by default

        # Customizable toolbar (#78).
        self.act_customize_toolbar = A("&Customize Toolbar…", self)
        self.act_customize_toolbar.triggered.connect(
            self.action_customize_toolbar)

        self.act_present = A(icon("present", "#2A2721"), "&Presentation Mode", self)
        self.act_present.setShortcut(QKeySequence("F5"))
        self.act_present.triggered.connect(self.action_presentation)

        self.act_fullscreen = A(icon("fullscreen", "#2A2721"), "&Fullscreen", self)
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.setShortcut(QKeySequence("F11"))
        self.act_fullscreen.triggered.connect(self.action_fullscreen)

        self.act_settings = A(icon("settings", "#2A2721"), "&Settings…", self)
        self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.act_settings.triggered.connect(self.action_settings)

        self.act_vault = A(icon("lock", "#2A2721"), "Secure &Vault…", self)
        self.act_vault.triggered.connect(self.action_vault)

        self.act_privacy = A(icon("shield", "#2A2721"), "&Privacy Dashboard", self)
        self.act_privacy.triggered.connect(self.action_privacy)

        self.act_onboarding = A("Run &Onboarding Again", self)
        self.act_onboarding.triggered.connect(self.action_onboarding)

        self.act_plugins = A(icon("plugin", "#2A2721"), "&Plugins…", self)
        self.act_plugins.triggered.connect(self.action_plugins)
        self.act_updates = A("Check for &Updates…", self)
        self.act_updates.triggered.connect(self.action_updates)

        self.act_logs = A("Open &Logs Folder", self)
        self.act_logs.triggered.connect(self.action_open_logs)

        self.act_diagnostics = A("Export &Diagnostic Report…", self)
        self.act_diagnostics.triggered.connect(self.action_diagnostics)

        self.act_about = A("&About", self)
        self.act_about.triggered.connect(
            lambda: AboutDialog(self).exec())

        self.act_theme_group: QActionGroup | None = None

        # Annotation tool actions
        self.act_tool_none = A("&Select/Pan", self)
        self.act_tool_none.setShortcut(QKeySequence("Esc"))
        self.act_tool_none.triggered.connect(lambda: self._set_tool(""))

        self.act_tool_highlight = A("&Highlight", self)
        self.act_tool_highlight.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self.act_tool_highlight.triggered.connect(
            lambda: self._set_tool("highlight"))

        self.act_tool_underline = A("&Underline", self)
        self.act_tool_underline.triggered.connect(
            lambda: self._set_tool("underline"))

        self.act_tool_strike = A("&Strikeout", self)
        self.act_tool_strike.triggered.connect(
            lambda: self._set_tool("strikeout"))

        self.act_tool_note = A("Sticky &Note", self)
        self.act_tool_note.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self.act_tool_note.triggered.connect(lambda: self._set_tool("note"))

        self.act_tool_ink = A("&Draw (Ink)", self)
        self.act_tool_ink.triggered.connect(lambda: self._set_tool("ink"))

        self.act_tool_rect = A("Rect&angle", self)
        self.act_tool_rect.triggered.connect(lambda: self._set_tool("rectangle"))

        self.act_tool_ellipse = A("&Ellipse", self)
        self.act_tool_ellipse.triggered.connect(lambda: self._set_tool("ellipse"))

        self.act_tool_arrow = A("Arro&w", self)
        self.act_tool_arrow.triggered.connect(lambda: self._set_tool("arrow"))

        self.act_tool_freetext = A("&Text Box", self)
        self.act_tool_freetext.triggered.connect(lambda: self._set_tool("freetext"))

        self.act_tool_stamp = A("Sta&mp", self)
        self.act_tool_stamp.triggered.connect(lambda: self._set_tool("stamp"))

        self.act_tool_eraser = A("E&raser", self)
        self.act_tool_eraser.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_tool_eraser.triggered.connect(
            lambda: self._set_tool("eraser"))

        # Phase 2 annotation editing: select existing markups on the page.
        self.act_select_annots = A("Select &All Annotations", self)
        self.act_select_annots.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.act_select_annots.triggered.connect(self._select_all_annotations)

        self.act_rotate_page = A(icon("rotate", "#2A2721"), "Rotate Page &Right", self)
        self.act_rotate_page.setShortcut(QKeySequence("Ctrl+R"))
        self.act_rotate_page.triggered.connect(self._rotate_page_right)

        self.act_delete_page = A("&Delete Page", self)
        self.act_delete_page.triggered.connect(self._delete_page)

        self.act_bookmark = A(icon("bookmark", "#C7522A"), "Add Book&mark", self)
        self.act_bookmark.setShortcut(QKeySequence("Ctrl+B"))
        self.act_bookmark.triggered.connect(self._add_bookmark)

        # Reading-experience actions (roadmap #7, #8, #59).
        self.act_autoscroll = A("&Autoscroll", self)
        self.act_autoscroll.setCheckable(True)
        self.act_autoscroll.setShortcut(QKeySequence("Space"))
        self.act_autoscroll.triggered.connect(self._toggle_autoscroll)

        self.act_slideshow = A("Slideshow &Auto-Advance…", self)
        self.act_slideshow.setCheckable(True)
        self.act_slideshow.triggered.connect(self._toggle_slideshow)

        self.act_rtl = A("&Right-to-Left (Manga) Mode", self)
        self.act_rtl.setCheckable(True)
        self.act_rtl.triggered.connect(self._toggle_rtl)

        # Reference surface: the reading report behind the status bar.
        self.act_reading_summary = A("Reading Su&mmary…", self)
        self.act_reading_summary.setToolTip(
            "Pages, progress, time read and history — exportable or printable")
        self.act_reading_summary.triggered.connect(self.action_reading_summary)

        # Keyboard shortcuts cheat sheet (discoverability).
        self.act_shortcuts = A("&Keyboard Shortcuts…", self)
        self.act_shortcuts.setShortcut(QKeySequence("F1"))
        self.act_shortcuts.setToolTip("Every action and its key (F1)")
        self.act_shortcuts.triggered.connect(self._show_shortcuts)

        # User-pasted shortcut map (shortcuts.json).
        self.act_shortcut_file = A("Edit Shortcut &Overrides File…", self)
        self.act_shortcut_file.setToolTip(
            "Open shortcuts.json and paste your own key map")
        self.act_shortcut_file.triggered.connect(self.action_edit_shortcuts_file)

        self.act_shortcuts_reload = A("&Reload Shortcut Overrides", self)
        self.act_shortcuts_reload.setToolTip(
            "Re-read shortcuts.json and rebind the app now")
        self.act_shortcuts_reload.triggered.connect(
            self.action_reload_shortcut_overrides)

        self.act_shortcuts_reset = A("Reset Shortcut Overrides", self)
        self.act_shortcuts_reset.setToolTip(
            "Return every action to its built-in binding for this session")
        self.act_shortcuts_reset.triggered.connect(
            self.action_reset_shortcut_overrides)

    # ================================================================ menus
    def _build_menus(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_open_folder)
        self._recent_menu = m_file.addMenu("Recent &Files")
        m_file.addSeparator()
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_save_as)
        m_file.addAction(self.act_versions)
        m_file.addSeparator()
        m_file.addAction(self.act_export)
        m_file.addAction(self.act_print)
        m_file.addSeparator()
        m_file.addAction(self.act_close_tab)
        m_file.addAction(self.act_quit)

        m_edit = mb.addMenu("&Edit")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)
        m_edit.addSeparator()
        m_edit.addAction(self.act_find)
        m_edit.addAction(self.act_palette)
        m_edit.addSeparator()
        m_meta = m_edit.addMenu("Metadata")
        m_meta.addAction(self.act_meta_edit)
        m_meta.addAction(self.act_meta_strip)

        m_view = mb.addMenu("&View")
        m_view.addAction(self.act_zoom_in)
        m_view.addAction(self.act_zoom_out)
        m_view.addSeparator()
        m_view.addAction(self.act_fit_width)
        m_view.addAction(self.act_fit_page)
        m_view.addSeparator()
        m_view.addAction(self.act_next_page)
        m_view.addAction(self.act_prev_page)
        m_view.addAction(self.act_goto_page)
        m_view.addSeparator()
        m_view.addAction(self.act_nav_back)
        m_view.addAction(self.act_nav_forward)
        m_view.addSeparator()
        m_view.addAction(self.act_autoscroll)
        m_view.addAction(self.act_slideshow)
        m_view.addAction(self.act_rtl)
        m_view.addAction(self.act_reading_summary)
        m_view.addSeparator()
        m_gap_menu = m_view.addMenu("Page &Theme")
        for a in self.act_themes:
            m_gap_menu.addAction(a)
        m_view.addAction(self.act_gap_tuning)
        m_view.addSeparator()
        m_view.addAction(self.act_distraction)
        m_view.addSeparator()
        m_view.addAction(self.act_focus)
        m_view.addAction(self.act_customize_toolbar)
        m_view.addAction(self.act_present)
        m_view.addAction(self.act_fullscreen)
        m_view.addSeparator()
        for dock in (self.dock_library, self.dock_thumbs, self.dock_toc,
                     self.dock_bookmarks, self.dock_annotations,
                     self.dock_notes, self.dock_search, self.dock_tasks,
                     self.dock_props):
            m_view.addAction(dock.toggleViewAction())

        m_annot = mb.addMenu("&Annotate")
        m_annot.addAction(self.act_tool_none)
        m_annot.addAction(self.act_tool_highlight)
        m_annot.addAction(self.act_tool_underline)
        m_annot.addAction(self.act_tool_strike)
        m_annot.addAction(self.act_tool_note)
        m_annot.addAction(self.act_tool_ink)
        m_annot.addAction(self.act_tool_rect)
        m_annot.addAction(self.act_tool_ellipse)
        m_annot.addAction(self.act_tool_arrow)
        m_annot.addAction(self.act_tool_freetext)
        m_annot.addAction(self.act_tool_stamp)
        m_annot.addSeparator()
        m_annot.addAction(self.act_select_annots)
        m_annot.addSeparator()
        m_annot.addAction(self.act_bookmark)
        m_annot.addSeparator()
        m_annot.addAction(self.act_redact)
        m_annot.addAction(self.act_verify_redaction)

        m_view.addSeparator()
        m_view.addAction(self.act_split_view)

        m_tools = mb.addMenu("&Tools")
        m_pdf = m_tools.addMenu("&PDF")
        m_pdf.addAction(self.act_rotate_page)
        m_pdf.addAction(self.act_delete_page)
        m_pdf.addSeparator()
        m_pdf.addAction(self.act_merge)
        m_pdf.addAction(self.act_split)
        m_pdf.addAction(self.act_optimize)
        m_pdf.addSeparator()
        m_pdf.addAction(self.act_forms)
        m_pdf.addSeparator()
        m_sec = m_pdf.addMenu("Security")
        m_sec.addAction(self.act_sign)
        m_sec.addAction(self.act_signatures)
        m_tools.addAction(self.act_ocr)
        m_tools.addAction(self.act_read_aloud)
        m_tools.addSeparator()
        m_tools.addAction(self.act_translate)
        m_tools.addAction(self.act_dictionary)
        m_tools.addAction(self.act_summarize)
        m_tools.addSeparator()
        m_tools.addAction(self.act_compare)
        m_tools.addAction(self.act_vault)
        m_tools.addSeparator()
        m_tools.addAction(self.act_plugins)

        m_settings = mb.addMenu("&Settings")
        m_settings.addAction(self.act_settings)
        m_settings.addAction(self.act_onboarding)
        m_settings.addSeparator()
        m_settings.addAction(self.act_privacy)
        m_settings.addSeparator()
        m_settings.addAction(self.act_updates)
        m_settings.addSeparator()
        m_settings.addAction(self.act_logs)
        m_settings.addAction(self.act_diagnostics)
        m_settings.addSeparator()
        m_settings.addAction(self.act_shortcuts)
        m_settings.addAction(self.act_shortcut_file)
        m_settings.addAction(self.act_shortcuts_reload)
        m_settings.addAction(self.act_shortcuts_reset)
        m_settings.addSeparator()
        m_settings.addAction(self.act_about)

    # ================================================================ toolbar
    # Customizable toolbar registry (#78): (key, label) in default order.
    # Settings persist the ordered list; a "-" prefix hides the item.
    TOOLBAR_ITEMS: list[tuple[str, str]] = [
        ("open", "Open"), ("save", "Save"), ("sep1", "separator"),
        ("zoom_out", "Zoom out"), ("zoom_in", "Zoom in"),
        ("fit_width", "Fit width"), ("sep2", "separator"),
        ("prev_page", "Previous page"), ("next_page", "Next page"),
        ("nav_back", "Reading history back"),
        ("nav_forward", "Reading history forward"), ("sep3", "separator"),
        ("goto_box", "Go-to-page box"), ("sep4", "separator"),
        ("tool_highlight", "Highlight tool"), ("tool_note", "Sticky note tool"),
        ("tool_ink", "Draw tool"), ("tool_freetext", "Text box tool"),
        ("tool_eraser", "Eraser tool"), ("sep5", "separator"),
        ("rotate_page", "Rotate page"), ("print", "Print"),
        ("ocr", "Run OCR"), ("read_aloud", "Read aloud"),
        ("sep6", "separator"), ("palette_btn", "Command palette"),
        ("search_box", "Search box"),
    ]
    TOOLBAR_DEFAULTS: list[str] = [k for k, _ in TOOLBAR_ITEMS]

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        # QMainWindow has no toolbar() getter; keep a reference (also fixes
        # the latent AttributeError in focus mode).
        self.main_toolbar = tb
        self._render_toolbar_items()

    def _render_toolbar_items(self) -> None:
        """Rebuild the toolbar from the persisted item order (#78)."""
        tb = self.main_toolbar
        tb.clear()
        self._tool_buttons: dict[str, QToolButton] = {}
        saved = self.settings.get("reading", "toolbar_items", [])
        if not isinstance(saved, list) or not saved:
            saved = list(self.TOOLBAR_DEFAULTS)
        valid = {k for k, _ in self.TOOLBAR_ITEMS}
        keys = [str(k).lstrip("-") for k in saved if str(k).lstrip("-") in valid]
        seen: set[str] = set()
        keys = [k for k in keys if not (k in seen or seen.add(k))]
        keys += [k for k in self.TOOLBAR_DEFAULTS if k not in keys]
        hidden = {str(k)[1:] for k in saved
                  if isinstance(k, str) and k.startswith("-")}
        for key in keys:
            if key in hidden:
                continue
            self._add_toolbar_item(tb, key)

    def _add_toolbar_item(self, tb: QToolBar, key: str) -> None:
        """Append one registered item (action, widget, or separator)."""
        if key.startswith("sep"):
            tb.addSeparator()
            return
        if key in ("open", "save", "zoom_out", "zoom_in", "fit_width",
                   "prev_page", "next_page", "nav_back", "nav_forward",
                   "rotate_page", "print", "ocr", "read_aloud"):
            tb.addAction(getattr(self, f"act_{key}"))
            return
        if key == "goto_box":
            from PySide6.QtWidgets import QLineEdit
            from PySide6.QtGui import QIntValidator
            self._goto_box = QLineEdit()
            self._goto_box.setPlaceholderText("Page")
            self._goto_box.setFixedWidth(64)
            self._goto_box.setValidator(QIntValidator(1, 999999, self._goto_box))
            self._goto_box.setClearButtonEnabled(True)
            self._goto_box.setToolTip("Go to page (Enter)")
            self._goto_box.returnPressed.connect(self._goto_box_commit)
            tb.addWidget(self._goto_box)
            return
        if key.startswith("tool_"):
            tool = key[len("tool_"):]
            meta = {"highlight": ("Highlight", "highlighter"),
                    "note": ("Sticky note", "note"),
                    "ink": ("Draw", "ink"),
                    "freetext": ("Text box", "text"),
                    "eraser": ("Eraser", "eraser")}.get(tool)
            if meta is None:
                return
            tip, icon_name = meta
            btn = QToolButton()
            btn.setIcon(icon(icon_name, "#2A2721"))
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, t=tool: self._set_tool(t))
            self._tool_buttons[tool] = btn
            tb.addWidget(btn)
            return
        if key == "palette_btn":
            palette_btn = QToolButton()
            palette_btn.setIcon(icon("command", "#2A2721"))
            palette_btn.setToolTip("Command palette (Ctrl+K)")
            palette_btn.clicked.connect(self._open_palette)
            tb.addWidget(palette_btn)
            return
        if key == "search_box":
            search_widget = self.search_panel._query
            search_widget.setParent(None)
            search_widget.setPlaceholderText(
                "Search library & documents… (Ctrl+K for commands)")
            search_widget.setMaximumWidth(280)
            tb.addWidget(search_widget)

    def action_customize_toolbar(self) -> None:
        """Reorder (drag) and hide/show toolbar items; persisted (#78)."""
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                                       QListWidget, QListWidgetItem,
                                       QPushButton, QVBoxLayout)
        saved = self.settings.get("reading", "toolbar_items", [])
        if not isinstance(saved, list) or not saved:
            saved = list(self.TOOLBAR_DEFAULTS)
        labels = dict(self.TOOLBAR_ITEMS)
        keys = [str(k).lstrip("-") for k in saved if str(k).lstrip("-") in labels]
        seen: set[str] = set()
        keys = [k for k in keys if not (k in seen or seen.add(k))]
        keys += [k for k in self.TOOLBAR_DEFAULTS if k not in keys]
        hidden = {str(k)[1:] for k in saved
                  if isinstance(k, str) and k.startswith("-")}

        dlg = QDialog(self)
        dlg.setWindowTitle("Customize toolbar")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Drag to reorder. Untick an item to hide it."))
        lst = QListWidget()
        lst.setDragDropMode(QListWidget.InternalMove)
        for key in keys:
            item = QListWidgetItem(labels.get(key, key))
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if key in hidden else Qt.Checked)
            lst.addItem(item)
        lay.addWidget(lst, 1)
        row = QHBoxLayout()
        reset = QPushButton("Reset to default")
        reset.clicked.connect(lambda: self._reset_toolbar_list(lst))
        row.addWidget(reset)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        order: list[str] = []
        for i in range(lst.count()):
            it = lst.item(i)
            key = it.data(Qt.UserRole)
            order.append(key if it.checkState() == Qt.Checked else f"-{key}")
        self.settings.set("reading", "toolbar_items", order)
        self._render_toolbar_items()

    def _reset_toolbar_list(self, lst) -> None:
        """Restore the dialog list to the default order, all visible."""
        from PySide6.QtWidgets import QListWidgetItem
        lst.clear()
        for key, label in self.TOOLBAR_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            lst.addItem(item)

    # ================================================================ statusbar
    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        sb.setSizeGripEnabled(True)
        self.setStatusBar(sb)
        self._status_page = QLabel("No document")
        self._status_zoom = QLabel("")
        self._status_tasks = QLabel("")
        self._status_doc_state = QLabel("")
        # Reading progress (#9): “42% read · page 7 of 16”. Clickable: the
        # segment opens the full reading summary, which can be exported or
        # printed like the other reference surfaces.
        self._status_progress = ClickableLabel("")
        self._status_progress.setToolTip(
            "Click for the reading summary (export or print it)")
        self._status_progress.clicked.connect(self.action_reading_summary)
        # Enhanced status segments (#81): active tool, markup count,
        # reading time.
        self._status_tool = QLabel("")
        self._status_annots = QLabel("")
        self._status_reading = QLabel("")
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(160)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.hide()
        sb.addWidget(self._status_doc_state)
        sb.addPermanentWidget(self._progress_bar)
        sb.addPermanentWidget(self._status_tasks)
        sb.addPermanentWidget(self._status_tool)
        sb.addPermanentWidget(self._status_annots)
        sb.addPermanentWidget(self._status_reading)
        sb.addPermanentWidget(self._status_progress)
        sb.addPermanentWidget(self._status_zoom)
        sb.addPermanentWidget(self._status_page)

    # ================================================================ palette
    def _build_palette(self) -> None:
        self.palette_overlay = CommandPalette(self)
        self.palette_overlay.command_run.connect(self._run_command)

    def _open_palette(self) -> None:
        self.palette_overlay.set_commands(self._palette_commands())
        self.palette_overlay.open_palette()

    def _palette_commands(self) -> list[Command]:
        cmds: list[Command] = []

        def add(title, cb, subtitle="", icon_name="command", keywords=""):
            cmds.append(Command(title=title, subtitle=subtitle,
                                icon_name=icon_name, payload=cb,
                                keywords=keywords))

        add("Open File", self.action_open, "Ctrl+O", "open", "open document file")
        add("Open Folder", self.action_open_folder, "", "folder", "folder import")
        add("Save", self.action_save, "Ctrl+S", "save")
        add("Save As", self.action_save_as, "Ctrl+Shift+S", "save")
        add("Export…", self.action_export, "", "convert", "export convert")
        add("Select All Annotations", self.act_select_annots, "Ctrl+Shift+A",
            "command", "select annotations markups edit move resize")
        add("Print…", self.action_print, "Ctrl+P", "print")
        add("Find in Document", self.action_find, "Ctrl+F", "search", "find search")
        add("Undo", self.action_undo, "Ctrl+Z", "undo", "undo revert annotation")
        add("Redo", self.action_redo, "Ctrl+Y", "redo", "redo reapply")
        add("Reading History: Back", self._nav_back, "Alt+Left", "page",
            "back previous location history")
        add("Reading History: Forward", self._nav_forward, "Alt+Right", "page",
            "forward next location history")
        add("Go to Page…", self._goto_page_dialog, "Ctrl+G", "page",
            "goto jump page number")
        add("Reading Summary…", self.action_reading_summary, "", "info",
            "reading progress stats summary report time pages export print")
        add("Autoscroll", self._toggle_autoscroll, "Space", "page",
            "autoscroll scroll automatic hands-free")
        add("Slideshow Auto-Advance", self._toggle_slideshow, "", "present",
            "slideshow auto advance timer presentation")
        add("Toggle Right-to-Left (Manga) Mode", self._toggle_rtl, "", "page",
            "rtl right-to-left manga reading order")
        add("Toggle Dark Mode", self.action_toggle_theme, "", "moon", "theme dark light")
        add("Enter Focus Mode", self.action_focus_mode, "F9", "focus")
        add("Toggle Distraction-Free Mode", self.action_distraction_free, "F8",
            "focus", "distraction free hide chrome zen minimal")
        add("Spread Gap Tuning…", self._gap_tuning_dialog, "", "page",
            "gap spread two-page spacing cover offset")
        add("Customize Toolbar…", self.action_customize_toolbar, "",
            "settings", "toolbar customize reorder hide items")
        add("Page Theme: Sepia", lambda: self._set_view_theme("sepia"), "",
            "moon", "sepia theme page warm")
        add("Page Theme: Dark", lambda: self._set_view_theme("dark"), "",
            "moon", "dark theme page night")
        add("Presentation Mode", self.action_presentation, "F5", "present")
        add("Run OCR", self.action_ocr, "", "ocr", "text recognition")
        add("Read Aloud", self.action_read_aloud, "", "speaker", "tts speech")
        add("Merge PDFs", self.action_merge, "", "merge")
        add("Split PDF", self.action_split, "", "pages")
        add("Compare Documents", self.action_compare, "", "compare")
        add("Secure Vault", self.action_vault, "", "lock", "vault encryption")
        add("Privacy Dashboard", self.action_privacy, "", "shield", "privacy")
        add("Settings", self.action_settings, "Ctrl+,", "settings")
        add("Keyboard Shortcuts", self._show_shortcuts, "F1", "info",
            "shortcuts keys hotkeys cheat sheet help discover f1 f8 f9")
        add("Reload Shortcut Overrides", self.action_reload_shortcut_overrides,
            "", "settings",
            "shortcuts keymap overrides file reload rebind paste json")
        add("Edit Shortcut Overrides File…", self.action_edit_shortcuts_file,
            "", "settings", "shortcuts keymap overrides file edit open json")
        add("Reset Shortcut Overrides", self.action_reset_shortcut_overrides,
            "", "settings", "shortcuts keymap overrides reset default restore")
        add("Library: Import Folder", self.library_panel._add_folder, "",
            "library", "import folder library")
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if view is not None:
                add(f"Go to: {view.display_name}",
                    lambda v=view: self.tabs.setCurrentWidget(v),
                    "open document", "page")
        for row in self.db.query(
                "SELECT path, last_opened FROM documents "
                "WHERE last_opened > 0 ORDER BY last_opened DESC LIMIT 8"):
            add(f"Recent: {Path(row['path']).name}",
                lambda p=row['path']: self.open_path(p), "recent file", "history")
        return cmds

    def _run_command(self, command: Command) -> None:
        if callable(command.payload):
            try:
                command.payload()
            except Exception:
                import traceback
                show_error(self, "Command failed",
                           "That command could not be completed.",
                           traceback.format_exc())

    # ================================================================ signals
    def _connect_signals(self) -> None:
        self.tabs.currentChanged.connect(self._sync_ui_to_view)
        self.tasks.add_listener(self._on_task_event)
        self._annotations.add_listener(self._on_annotations_changed)

    # ================================================================ nav history + goto
    def _show_shortcuts(self) -> None:
        """Open the keyboard-shortcuts cheat sheet (modal)."""
        ShortcutsDialog(self).exec()

    # -- user-pasted shortcut map -------------------------------------------
    def _apply_shortcut_overrides(self, notify: bool = True) -> ShortcutReport:
        """Rebind actions from the settings map and the overrides file.

        The file wins over the stored setting so a pasted map never fights a
        stale value, and built-in bindings are restored first, which makes
        "delete the entry and reload" a real undo.
        """
        stored = self.settings.get("keyboard", "custom", {}) or {}
        mapping = dict(stored) if isinstance(stored, dict) else {}
        load = read_overrides()
        mapping.update(load.mapping)

        report = apply_overrides(self, mapping, source=str(load.path))
        report.error = report.error or load.error
        self.shortcut_report = report
        if notify:
            show_toast(self, report.summary(), self._override_toast_level(report))
        return report

    @staticmethod
    def _override_toast_level(report: ShortcutReport) -> str:
        if report.error or report.unknown or report.invalid or report.conflicts:
            return "warning"
        return "success"

    def action_reload_shortcut_overrides(self) -> ShortcutReport:
        """Re-read the paste target from disk and apply it immediately."""
        return self._apply_shortcut_overrides(notify=True)

    def action_reset_shortcut_overrides(self) -> None:
        """Drop every override in memory (the file itself is left alone)."""
        report = apply_overrides(self, {})
        self.shortcut_report = report
        show_toast(self, "Shortcuts reset to their defaults for this session",
                   "info")

    def action_edit_shortcuts_file(self) -> None:
        """Create the paste target if needed and hand it to the desktop."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        target = overrides_path()
        try:
            write_template(target, actions=collect_actions(self))
        except Exception as e:
            show_toast(self, f"Could not create {target.name}: {e}", "warning")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        if not opened:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))
        show_toast(self,
                   f"Edit {target.name}, save it, then Reload Shortcut Overrides",
                   "info")

    def _show_shortcuts_once(self) -> None:
        """Auto-hint variant: same sheet, modeless so it never blocks."""
        ShortcutsDialog(self).show()

    def _nav_back(self) -> None:
        view = self.current_view()
        if view is not None and view.nav_back():
            self._sync_ui_to_view()

    def _nav_forward(self) -> None:
        view = self.current_view()
        if view is not None and view.nav_forward():
            self._sync_ui_to_view()

    # -- reading-experience handlers (#7, #8, #59) -----------------------------
    def _toggle_autoscroll(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "toggle_autoscroll"):
            self.act_autoscroll.setChecked(False)
            return
        view.toggle_autoscroll()
        self.act_autoscroll.setChecked(view._autoscroll_timer.isActive())
        state = "started" if self.act_autoscroll.isChecked() else "stopped"
        show_toast(self, f"Autoscroll {state}", "info")

    def _toggle_slideshow(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "start_slideshow"):
            self.act_slideshow.setChecked(False)
            return
        if view.slideshow_active():
            view.stop_slideshow()
            self.act_slideshow.setChecked(False)
            show_toast(self, "Slideshow stopped", "info")
            return
        interval, ok = QInputDialog.getDouble(
            self, "Slideshow auto-advance",
            "Seconds per page:", view._slideshow_interval, 0.5, 120.0, 1)
        if not ok:
            self.act_slideshow.setChecked(False)
            return
        view.start_slideshow(interval)
        self.act_slideshow.setChecked(True)
        show_toast(self, f"Slideshow: one page every {interval:g}s", "info")

    def _toggle_rtl(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "set_rtl"):
            self.act_rtl.setChecked(False)
            return
        view.set_rtl(not view.rtl())
        self.act_rtl.setChecked(view.rtl())
        show_toast(self, "Right-to-left mode "
                   + ("on" if view.rtl() else "off"), "info")

    def _update_reading_actions(self) -> None:
        """Keep reading-mode checkboxes honest when the tab changes."""
        view = self.current_view()
        if view is not None and hasattr(view, "_autoscroll_timer"):
            self.act_autoscroll.setChecked(view._autoscroll_timer.isActive())
            self.act_slideshow.setChecked(view.slideshow_active())
            self.act_rtl.setChecked(view.rtl())
        else:
            self.act_autoscroll.setChecked(False)
            self.act_slideshow.setChecked(False)
            self.act_rtl.setChecked(False)
    # ================================================================ document open/close
    def open_path(self, path: str, password: str = "") -> None:
        result = open_document(Path(path), password)
        if result.needs_password:
            pwd = get_password(self, "Password required",
                               f"The document is encrypted:\n{Path(path).name}")
            if pwd:
                self.open_path(path, pwd)
            return
        if not result.ok:
            show_error(self, "Could not open document",
                       result.error or "The document could not be opened.",
                       technical=f"File: {path}")
            return
        view = create_view(result, self.settings)
        if view is None:
            show_error(self, "Could not open document",
                       "The file was recognized but no viewer could be created.",
                       technical=f"Format: {result.format_name}\nFile: {path}")
            return

        # Restore reading state + wire signals.
        try:
            rec = self._progress.load(str(path))
            if rec:
                view.restore_state(rec)
        except Exception:
            logger.exception("open_path failed")
        view.state_changed.connect(self._sync_ui_to_view)
        view.nav_state_changed.connect(self._update_nav_actions)
        view.content_changed.connect(self._on_content_changed)
        view.request_toast.connect(
            lambda msg, kind: show_toast(self, msg, kind))
        self._wire_view_signals(view)

        existing = self.tabs.find_tab_by_path(str(path))
        if existing >= 0:
            old = self.tabs.widget(existing)
            self.tabs.removeTab(existing)
            if old is not None:
                old.close_view()
            self.tabs.insertTab(existing, view, self._tab_title(view))
        else:
            idx = self.tabs.addTab(view, self._tab_title(view))
        if not self.tabs.isVisible():
            self.setCentralWidget(self.tabs)
            self.tabs.show()
            self._placeholder.hide()
        self.tabs.setCurrentWidget(view)

        # Library bookkeeping.
        try:
            self._library.upsert(
                __import__("veyrion_workspace.storage.repositories",
                           fromlist=["DocumentRecord"]).DocumentRecord(
                    path=str(path), title=view.display_name,
                    kind=_kind_of(path), format=result.format_name,
                    size_bytes=Path(path).stat().st_size,
                    folder=str(Path(path).parent),
                    last_modified=Path(path).stat().st_mtime))
            self._library.mark_opened(str(path))
        except Exception:
            logger.exception("open_path failed")

        # PDF-specific sync: import native annotations; index text.
        if hasattr(view, "pdf"):
            try:
                imported = self._annotations.import_from_pdf(view.pdf)
                if imported:
                    self.annotations_panel.reload()
            except Exception:
                logger.exception("open_path failed")
        self._maybe_index(view)
        self._sync_ui_to_view()
        self._record_session()
        self._begin_reading_session(view)

    def _tab_title(self, view: DocumentView) -> str:
        name = view.display_name
        if view.is_modified:
            name = "● " + name
        return name

    def _maybe_index(self, view: DocumentView) -> None:
        """Index document text in the background for global search."""
        doc_path = str(view.path)
        if self._search_engine.document_is_indexed(doc_path):
            return
        engine = view.engine
        from veyrion_workspace.core.documents.base import DocumentEngine

        def work(progress=None, cancel=None) -> int:
            return self._search_engine.index_document(engine, progress, cancel)

        self.tasks.submit(f"Indexing {Path(doc_path).name}", "index", work)

    def close_tab(self, index: int) -> None:
        if index < 0:
            return
        view = self.tabs.widget(index)
        if view is None:
            return
        doc_views = self._document_views_in(view)
        if any(v.is_modified for v in doc_views):
            name = (view.display_name if isinstance(view, DocumentView)
                    else self.tabs.tabText(index))
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved changes")
            box.setText(f"'{name}' has unsaved changes.")
            save_btn = box.addButton("Save", QMessageBox.AcceptRole)
            discard_btn = box.addButton("Discard", QMessageBox.DestructiveRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn:
                return
            if clicked is save_btn:
                if not self.action_save():
                    return
        self._end_reading_session()
        for v in doc_views:
            try:
                v.close_view()
            except Exception:
                logger.exception("close_tab failed")
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.tabs.hide()
            self.setCentralWidget(self._placeholder)
            self._placeholder.show()
        self._sync_ui_to_view()
        self._record_session()

    # -- reading-session lifecycle (feeds the reading summary's trends) ----
    def _begin_reading_session(self, view) -> None:
        """End any open session, then start one for the newly opened doc."""
        self._end_reading_session()
        try:
            self._current_session_id = self._sessions.start(str(view.path))
        except Exception:
            self._current_session_id = None

    def _end_reading_session(self) -> None:
        """Close the open session with elapsed wall time and pages read.

        Defensive throughout: a recording failure must never block closing a
        tab or the app. Duplicate ends are harmless (id becomes None).
        """
        sid, self._current_session_id = self._current_session_id, None
        if sid is None:
            return
        view = self.tabs.currentWidget()
        pages = max(int(getattr(view, "current_page", 0) or 0) + 1, 1)
        try:
            self._sessions.end(sid, pages)
        except Exception:
            logger.exception("_end_reading_session failed")

    def reopen_path(self, path: str) -> None:
        if path:
            self.open_path(path)

    def detach_tab(self, index: int) -> None:
        """Detach a tab into its own window (or duplicate when index == -1)."""
        if index == -1:
            view = self.tabs.currentWidget()
            if view is not None:
                self.open_path(str(getattr(view, "path", "")))
            return
        view = self.tabs.widget(index)
        if view is None:
            return
        if not isinstance(view, DocumentView):
            show_toast(self, "Only a document tab can be detached", "warning")
            return
        path = str(getattr(view, "path", ""))
        title = self.tabs.tabText(index)
        state = view.save_state()
        self.tabs.removeTab(index)
        sub = MainWindow(self.settings, self.db, self.tasks, self.journal)
        sub.setAttribute(Qt.WA_DeleteOnClose)
        sub.resize(1000, 720)
        sub.show()
        sub.open_path(path)
        sub._apply_view_state(state)
        if self.tabs.count() == 0:
            self.tabs.hide()
            self.setCentralWidget(self._placeholder)
            self._placeholder.show()

    def _apply_view_state(self, state: dict) -> None:
        view = self.current_view()
        if view is not None and state:
            view.restore_state(state)

    # ================================================================ state sync
    def _on_tab_changed(self, index: int) -> None:
        view = self.current_view()
        if view is not None:
            path = str(view.path)
            self.annotations_panel.set_document_filter(path, view)
            self.bookmarks_panel.set_document(path)
            self.thumbnails_panel.set_view(view)
            self._update_reading_actions()
            try:
                view.tool_changed.connect(self._on_tool_changed)
                view.reading_tick.connect(self._on_reading_tick)
            except (AttributeError, TypeError, RuntimeError):
                pass
            self._on_tool_changed(getattr(view, "annotation_tool", lambda: "")())
            try:
                self._progress.save(path, view.current_page,
                                    view.save_state().get("scroll", 0.0),
                                    view.zoom, getattr(view, "view_id", ""))
            except Exception:
                logger.exception("_on_tab_changed failed")
        self._sync_ui_to_view()

    def _update_window_title(self, view) -> None:
        """Track the active document (and unsaved marker) in the title bar."""
        doc = str(getattr(view, "display_name", "")) if view is not None else ""
        dirty = " *" if getattr(view, "is_modified", False) else ""
        if doc:
            self.setWindowTitle(f"{doc}{dirty} — {APP_NAME}")
        else:
            self.setWindowTitle(f"{APP_NAME} — {APP_TAGLINE}")

    def _sync_ui_to_view(self, *args) -> None:
        view = self.current_view()
        self._update_window_title(view)
        if view is None:
            self._status_page.setText("No document")
            self._status_zoom.setText("")
            self._status_doc_state.setText("")
            self._status_tool.setText("")
            self._status_annots.setText("")
            self._status_reading.setText("")
            self._status_progress.setText("")
            return
        self._apply_view_tuning_defaults(view)
        page = view.current_page
        total = view.page_count
        label = f"Page {page + 1} of {total}"
        try:
            info = view.engine.page_info(page)
            if info and info.label:
                label = f"Page {info.label} ({page + 1}/{total})"
        except Exception:
            logger.exception("_sync_ui_to_view failed")
        self._status_page.setText(label)
        self._status_zoom.setText(f"{int(view.zoom * 100)}%")
        # Reading progress (#9).
        if total > 0:
            pct = int(round((page + 1) / total * 100))
            self._status_progress.setText(f"{pct}% read")
        else:
            self._status_progress.setText("")
        # Enhanced status segments (#81): annotation count + reading time.
        try:
            n = view.live_annotation_count()
            self._status_annots.setText(f"{n} markup" + ("s" if n != 1 else ""))
        except Exception:
            self._status_annots.setText("")
        secs = getattr(view, "reading_seconds", lambda: 0)()
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        self._status_reading.setText(
            f"{h}:{m:02d}:{s:02d}" if h else f"{m}m {s:02d}s")
        # Keep the go-to-page box and Back/Forward in sync.
        self._update_nav_actions()
        if hasattr(self, "_goto_box"):
            self._goto_box.setPlaceholderText(
                f"1–{total}" if total > 0 else "Page")
        self._status_doc_state.setText(
            "● Unsaved changes" if view.is_modified else "")
        # Update progress in library.
        if total > 0:
            try:
                self._library.set_progress(str(view.path), (page + 1) / total)
            except Exception:
                logger.exception("_sync_ui_to_view failed")
        self.thumbnails_panel.highlight(page)
        self._update_tab_title()

    def _update_nav_actions(self) -> None:
        """Enable/disable Back/Forward based on the current view's history."""
        view = self.current_view()
        has_back = bool(view and view.nav_can_back())
        has_fwd = bool(view and view.nav_can_forward())
        self.act_nav_back.setEnabled(has_back)
        self.act_nav_forward.setEnabled(has_fwd)

    def _on_tool_changed(self, tool: str) -> None:
        """Reflect the active annotation tool in the status bar (#81)."""
        labels = {"": "", "highlight": "Highlight", "underline": "Underline",
                  "strikeout": "Strikeout", "squiggly": "Squiggly",
                  "note": "Sticky note", "ink": "Ink", "rectangle": "Rectangle",
                  "ellipse": "Ellipse", "arrow": "Arrow", "freetext": "Text box",
                  "stamp": "Stamp", "eraser": "Eraser", "redact": "Redaction"}
        self._status_tool.setText(labels.get(tool, tool))

    def _on_reading_tick(self, seconds: int) -> None:
        """Update the reading-time status segment once per second (#81)."""
        view = self.current_view()
        if view is None or not hasattr(view, "reading_seconds"):
            return
        if self.sender() is not view._reading_timer:
            return  # a background tab ticked; only show the active tab's time
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        self._status_reading.setText(
            f"{h}:{m:02d}:{s:02d}" if h else f"{m}m {s:02d}s")

    def _update_tab_title(self) -> None:
        idx = self.tabs.currentIndex()
        view = self.current_view()
        if idx >= 0 and view is not None:
            self.tabs.setTabText(idx, self._tab_title(view))

    def _on_content_changed(self) -> None:
        self._update_tab_title()
        self._sync_ui_to_view()
        # Live-refresh the annotations panel (roadmap #15) — debounced inside
        # the panel, so sticky-note keystrokes don't rebuild the list.
        self.annotations_panel.refresh_live()

    def _wire_view_signals(self, view) -> None:
        """Attach action_toast (#82) to freshly created or restored views."""
        if view is None or not hasattr(view, "action_toast"):
            return
        wired = getattr(self, "_toast_wired", None)
        if wired is None:
            wired = self._toast_wired = set()
        if id(view) in wired:
            return
        wired.add(id(view))
        view.action_toast.connect(self._on_action_toast)

    def _on_annotations_changed(self, doc_path: str) -> None:
        self.annotations_panel.reload()

    def _goto_page(self, page: int) -> None:
        view = self.current_view()
        if view is not None:
            view.go_to_page(page)
            self._sync_ui_to_view()

    def _flash_annotation_on_page(self, data: dict) -> None:
        """Panel asked to highlight a live annotation on its page (#15 polish)."""
        view = self.current_view()
        if view is None or not hasattr(view, "flash_annotation"):
            return
        if str(view.path) != data.get("path"):
            # Wrong tab focused; just jump to the page instead.
            self._goto_page(int(data.get("page", 0)))
            return
        view.flash_annotation(int(data.get("page", 0)),
                              data.get("rect") or [0, 0, 0, 0],
                              sticky=bool(data.get("sticky")))

    def _goto_box_commit(self) -> None:
        """Jump to the page typed into the go-to-page toolbar box."""
        text = self._goto_box.text().strip()
        if not text:
            return
        view = self.current_view()
        if view is None or view.page_count <= 0:
            self._goto_box.clear()
            return
        try:
            page = int(text) - 1  # toolbar box is 1-based like the status bar
        except ValueError:
            self._goto_box.clear()
            return
        view.go_to_page(max(0, min(page, view.page_count - 1)))
        self._goto_box.clear()
        self._sync_ui_to_view()

    def _goto_page_dialog(self) -> None:
        """Ctrl+G 'Go to page…' prompt."""
        view = self.current_view()
        if view is None or view.page_count <= 0:
            return
        page, ok = QInputDialog.getInt(
            self, "Go to page",
            f"Page number (1-{view.page_count}):",
            view.current_page + 1, 1, view.page_count)
        if ok:
            view.go_to_page(page - 1)
            self._sync_ui_to_view()

    def _goto_page_scroll(self, page: int, scroll: float) -> None:
        self._goto_page(page)

    # ================================================================ task events
    def _on_task_event(self, task, event: str) -> None:
        from PySide6.QtCore import QMetaObject, Qt as Qt2, Q_ARG
        if event == "started":
            QMetaObject.invokeMethod(self, "_ui_task_started",
                                     Qt2.QueuedConnection)
        elif event == "progress":
            QMetaObject.invokeMethod(self, "_ui_task_progress",
                                     Qt2.QueuedConnection)
        elif event in ("done", "failed", "cancelled"):
            QMetaObject.invokeMethod(self, "_ui_task_finished",
                                     Qt2.QueuedConnection,
                                     Q_ARG(str, task.name),
                                     Q_ARG(str, event),
                                     Q_ARG(str, task.error or ""))

    def _ui_task_started(self) -> None:
        self._progress_bar.show()
        self._status_tasks.setText(f"{self.tasks.active_count()} task(s) running")

    def _ui_task_progress(self) -> None:
        tasks = self.tasks.all_tasks()
        running = [t for t in tasks if t.state.value == "running"]
        if running:
            t = running[0]
            self._progress_bar.setValue(int(t.progress * 100))
            self._status_tasks.setText(f"{t.name} — {int(t.progress * 100)}%")

    def _ui_task_finished(self, name: str, event: str, error: str) -> None:
        remaining = self.tasks.active_count()
        if remaining:
            self._status_tasks.setText(f"{remaining} task(s) running")
        else:
            self._progress_bar.hide()
            self._status_tasks.setText("")
        if event == "done":
            show_toast(self, f"Completed: {name}", "success")
        elif event == "failed":
            show_toast(self, f"Failed: {name} — {error[:80]}", "error")

    # ================================================================ actions
    def action_open(self) -> None:
        folder = self.settings.get("general", "default_open_folder", "")
        start = folder if folder and Path(folder).exists() else str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(
            self, "Open documents", start,
            "All supported (*.pdf *.epub *.mobi *.azw *.azw3 *.cbz *.cbr "
            "*.docx *.odt *.rtf *.txt *.md *.markdown *.html *.htm *.csv "
            "*.json *.xml *.xlsx *.pptx *.png *.jpg *.jpeg *.webp *.bmp "
            "*.tif *.tiff *.gif);;PDF (*.pdf);;E-books (*.epub *.mobi *.azw "
            "*.azw3);;Comics (*.cbz *.cbr);;Office (*.docx *.odt *.rtf "
            "*.xlsx *.pptx);;Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif "
            "*.tiff *.gif);;All files (*.*)")
        for f in files:
            self.open_path(f)
        if files:
            self.settings.set("general", "default_open_folder",
                              str(Path(files[0]).parent))

    def action_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open folder as library")
        if folder:
            self.library_panel.import_folder(Path(folder))
            self.dock_library.show()
            self.library_panel.reload()

    def action_save(self) -> bool:
        view = self.current_view()
        if view is None:
            return False
        try:
            self._versioning.snapshot_before_save(view.path)
            saved = view.save()
            if hasattr(view, "pdf"):
                self._annotations.apply_to_pdf(view.pdf)
            self._update_tab_title()
            self._sync_ui_to_view()
            show_toast(self, f"Saved {Path(saved).name}", "success")
            return True
        except Exception as e:
            import traceback
            show_error(self, "Save failed",
                       "The document could not be saved. Your original file "
                       "is untouched.", traceback.format_exc())
            return False

    def action_save_as(self) -> bool:
        view = self.current_view()
        if view is None:
            return False
        target, _ = QFileDialog.getSaveFileName(
            self, "Save as", str(view.path))
        if not target:
            return False
        try:
            saved = view.save_as(Path(target))
            show_toast(self, f"Saved a copy as {Path(saved).name}", "success")
            return True
        except Exception as e:
            import traceback
            show_error(self, "Save As failed",
                       "The copy could not be written.", traceback.format_exc())
            return False

    def action_version_history(self) -> None:
        view = self.current_view()
        if view is None:
            return
        versions = self._versioning.versions(view.path)
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Version history")
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        for v in versions:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(v["time"]))
            item = QListWidgetItem(
                f"{stamp}   ({format_size(v['size'])})")
            item.setData(Qt.UserRole, str(v["path"]))
            lst.addItem(item)
        lay.addWidget(QLabel("Most recent 5 states are kept per document."))
        lay.addWidget(lst)
        buttons = QDialogButtonBox(QDialogButtonBox.RestoreDefaults |
                                   QDialogButtonBox.Close)
        restore = buttons.button(QDialogButtonBox.RestoreDefaults)
        restore.setText("Restore selected")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() == QDialog.Accepted and lst.currentItem():
            target = lst.currentItem().data(Qt.UserRole)
            if self._versioning.rollback(view.path, Path(target)):
                show_toast(self, "Version restored", "success")
                self.open_path(str(view.path))
            else:
                show_error(self, "Rollback failed",
                           "The selected version could not be restored.")

    def action_print(self) -> None:
        view = self.current_view()
        if view is None:
            return
        from veyrion_workspace.ui.dialogs import PrintDialog
        dlg = PrintDialog(self, view)
        dlg.exec()

    def action_export(self) -> None:
        view = self.current_view()
        if view is None:
            return
        from veyrion_workspace.ui.dialogs import ExportDialog
        dlg = ExportDialog(self, view, self.tasks)
        dlg.exec()

    def action_find(self) -> None:
        view = self.current_view()
        if view is None:
            return
        from veyrion_workspace.ui.dialogs import FindDialog
        dlg = FindDialog(self, view)
        dlg.show()

    def action_undo(self) -> None:
        view = self.current_view()
        if view is None:
            return
        # Annotation/note undo on PDF views; falls back to focused-widget undo
        # (e.g. text editing inside a form field or note) first.
        focused = QApplication.focusObject()
        if focused is not None and hasattr(focused, "undo") and \
                hasattr(focused, "hasAcceptableInput"):
            try:
                focused.undo()
                return
            except Exception:
                logger.exception("action_undo failed")
        if hasattr(view, "can_undo") and view.can_undo():
            view.undo()
            return
        if focused is not None and hasattr(focused, "undo"):
            try:
                focused.undo()
            except Exception:
                logger.exception("action_undo failed")

    def action_redo(self) -> None:
        view = self.current_view()
        if view is None:
            return
        focused = QApplication.focusObject()
        if focused is not None and hasattr(focused, "redo") and \
                hasattr(focused, "hasAcceptableInput"):
            try:
                focused.redo()
                return
            except Exception:
                logger.exception("action_redo failed")
        if hasattr(view, "can_redo") and view.can_redo():
            view.redo()
            return
        if focused is not None and hasattr(focused, "redo"):
            try:
                focused.redo()
            except Exception:
                logger.exception("action_redo failed")

    def action_redo(self) -> None:
        focused = QApplication.focusObject()
        if hasattr(focused, "redo"):
            focused.redo()

    def action_toggle_theme(self) -> None:
        current = self.settings.get("appearance", "theme", "light")
        new_theme = "dark" if current in ("light", "sepia") else "light"
        self.settings.set("appearance", "theme", new_theme)
        self.retheme()

    def _zoom_in(self) -> None:
        view = self.current_view()
        if view:
            view.zoom_in()

    def _zoom_out(self) -> None:
        view = self.current_view()
        if view:
            view.zoom_out()

    def _fit_width(self) -> None:
        view = self.current_view()
        if view and hasattr(view, "fit_width"):
            view.fit_width()

    def _fit_page(self) -> None:
        view = self.current_view()
        if view and hasattr(view, "fit_page"):
            view.fit_page()

    def _next_page(self) -> None:
        view = self.current_view()
        if view:
            view.next_page()

    def _prev_page(self) -> None:
        view = self.current_view()
        if view:
            view.previous_page()

    def _set_tool(self, tool: str) -> None:
        view = self.current_view()
        if view is not None and hasattr(view, "set_annotation_tool"):
            view.set_annotation_tool(tool)
            # Show the annotation style palette whenever an annotation tool is
            # active; create-once, reuse thereafter.
            if not hasattr(view, "_annot_palette") or view._annot_palette is None:
                from veyrion_workspace.ui.views.pdf_view import (
                    AnnotationStylePalette)
                view._annot_palette = AnnotationStylePalette(self)
                view._annot_palette.style_changed.connect(
                    lambda c, o, w: view.set_annotation_style(
                        color=c, opacity=o, width=w))
            palette = view._annot_palette
            if tool and tool != "eraser":
                # Position palette at top-right of the view's viewport.
                vp = view._view.viewport()
                parent_global = vp.mapToGlobal(QPoint(0, 0))
                palette.move(parent_global + QPoint(
                    max(0, vp.width() - palette.width() - 6),
                    max(0, vp.height() - palette.height() - 6)))
                palette.show()
                palette.raise_()
                palette.activateWindow()
            else:
                palette.hide()
        else:
            # Deselect tool: hide any attached palette.
            if view is not None and getattr(view, "_annot_palette", None):
                view._annot_palette.hide()
        for t, btn in self._tool_buttons.items():
            btn.setChecked(t == tool)
        if tool:
            show_toast(self, f"Tool: {tool} — drag on the page", "info")

    def _select_all_annotations(self) -> None:
        """Select every annotation on the current page (Phase 2 editing)."""
        view = self.current_view()
        if view is None or not hasattr(view, "select_all_on_page"):
            return
        view.set_annotation_tool("")
        count = view.select_all_on_page(view.current_page)
        if count:
            show_toast(self, f"{count} annotation(s) selected — drag to move, "
                             "right-click to restyle", "info")
        else:
            show_toast(self, "No annotations on this page", "warning")

    def _rotate_page_right(self) -> None:
        view = self.current_view()
        if view is not None and hasattr(view, "rotate_current_page"):
            view.rotate_current_page(90)

    def _delete_page(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "delete_current_page"):
            return
        confirm = QMessageBox.question(
            self, "Delete page",
            f"Delete page {view.current_page + 1}? This can be undone by "
            f"reverting to a previous version (File > Version History).")
        if confirm == QMessageBox.Yes:
            view.delete_current_page()

    def _add_bookmark(self) -> None:
        view = self.current_view()
        if view is None:
            return
        title, ok = QInputDialog.getText(self, "Add bookmark",
                                         "Label (optional):")
        self.db.execute(
            "INSERT INTO bookmarks (doc_path, page, title, created_at) VALUES (?,?,?,?)",
            (str(view.path), view.current_page, title if ok else "", time.time()))
        self.bookmarks_panel.reload()
        show_toast(self, "Bookmark added", "success")

    # -- tools ----------------------------------------------------------------
    def action_ocr(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "OCR applies to PDF documents", "warning")
            return
        from veyrion_workspace.core.ocr.ocr import tesseract_available
        if not tesseract_available():
            show_error(self, "OCR unavailable",
                       "Tesseract is not installed. Install it from "
                       "https://github.com/UB-Mannheim/tesseract/wiki "
                       "(Windows installer) and restart Veyrion to "
                       "enable text recognition.")
            return
        from veyrion_workspace.core.ocr.ocr import OcrService
        service = OcrService(self.db)
        page = view.current_page
        task = self.tasks.submit(f"OCR page {page + 1}", "ocr",
                                 service.ocr_page, view.pdf, page)

        def done(t, event) -> None:
            if t.id == task.id and event == "done":
                text = t.result or ""
                self._show_text_result(f"OCR — page {page + 1}", text)
        self.tasks.add_listener(done)

    def _show_text_result(self, title: str, text: str) -> None:
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        edit.setMinimumSize(560, 420)
        lay.addWidget(edit)
        dlg.exec()

    def action_read_aloud(self) -> None:
        view = self.current_view()
        if view is None:
            return
        from veyrion_workspace.core.tts import TtsReader
        if not hasattr(self, "_tts"):
            self._tts = TtsReader()
            self._tts.rate = float(self.settings.get("speech", "rate", 1.0))
        if self.act_read_aloud.isChecked():
            text = view.selected_text() or view.page_text()
            if not text.strip():
                show_toast(self, "Nothing to read on this page", "warning")
                self.act_read_aloud.setChecked(False)
                return
            self._tts.speak(text)
            show_toast(self, "Reading aloud — uncheck to stop", "info")
        else:
            self._tts.stop()

    def action_translate(self) -> None:
        view = self.current_view()
        if view is None:
            return
        text = view.selected_text()
        if not text:
            show_toast(self, "Select text to translate", "warning")
            return
        from veyrion_workspace.ui.dialogs import TranslateDialog
        TranslateDialog(self, text, self.settings).exec()

    def action_dictionary(self) -> None:
        from veyrion_workspace.ui.dialogs import DictionaryDialog
        view = self.current_view()
        initial = view.selected_text() if view else ""
        DictionaryDialog(self, initial).exec()

    def action_summarize(self) -> None:
        view = self.current_view()
        if view is None:
            return
        from veyrion_workspace.core.summarization import summarize, keywords
        text = view.page_text()
        if not text.strip():
            show_toast(self, "No text to summarize", "warning")
            return
        sentences = summarize(text, 5)
        kws = keywords(text, 8)
        body = "Summary:\n\n" + "\n\n".join(f"• {s}" for s in sentences)
        body += "\n\nKeywords: " + ", ".join(w for w, _ in kws)
        self._show_text_result("Document summary", body)

    def action_compare(self) -> None:
        from veyrion_workspace.ui.dialogs import CompareDialog
        CompareDialog(self, self.tasks).exec()

    def action_split_view(self) -> None:
        """Open the current document (or two chosen ones) in a split view."""
        from veyrion_workspace.ui.split_view import SplitView
        current = self.current_view()
        if current is None:
            show_toast(self, "Open a document first, then use Split View",
                       "warning")
            return
        views = []
        for v in self._all_document_views():
            if not v.is_modified:
                views.append(v)
        if not views:
            views = [current]
        left = current
        right = views[1] if len(views) > 1 else views[0]
        split = SplitView(left, right)
        split.populate_combos(views)
        split.close_requested.connect(self._close_split_view)
        idx = self.tabs.addTab(split, "Split view")
        self.tabs.setCurrentIndex(idx)
        self._split_view = split
        show_toast(self, "Split view — use Sync scroll to compare pages",
                   "info")

    def _close_split_view(self) -> None:
        idx = self.tabs.currentIndex()
        widget = self.tabs.widget(idx)
        if widget is not None and widget.__class__.__name__ == "SplitView":
            self.tabs.removeTab(idx)

    def keyPressEvent(self, event) -> None:
        """Vim-style navigation when enabled and no text input is focused."""
        if self.settings.get("keyboard", "vim_mode", False):
            from PySide6.QtGui import QKeyEvent
            from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit,
                                  QComboBox)):
                super().keyPressEvent(event)
                return
            key = event.key()
            view = self.current_view()
            if view is not None:
                if key in (Qt.Key_J, Qt.Key_Down):
                    view.next_page()
                    event.accept()
                    return
                if key in (Qt.Key_K, Qt.Key_Up):
                    view.previous_page()
                    event.accept()
                    return
                if key == Qt.Key_G:
                    view.go_to_page(0)
                    event.accept()
                    return
        super().keyPressEvent(event)

    def action_toggle_redact(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "Redaction applies to PDF documents", "warning")
            self.act_redact.setChecked(False)
            return
        if self.act_redact.isChecked():
            warning = QMessageBox(self)
            warning.setWindowTitle("Redaction mode")
            warning.setText(
                "Drag rectangles over content to redact, then apply "
                "redactions to permanently remove the underlying content.\n\n"
                "Redaction removes text and rasterizes covered image areas "
                "when applied and saved.")
            warning.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            if warning.exec() != QMessageBox.Ok:
                self.act_redact.setChecked(False)
                return
            self._set_tool("redact")
        else:
            self._set_tool("")
            self._apply_redactions()

    def _apply_redactions(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            return
        confirm = QMessageBox.question(
            self, "Apply redactions",
            "Permanently remove all redacted content from this document?\n\n"
            "A version backup is created first; use File > Version History "
            "to recover if needed.")
        if confirm != QMessageBox.Yes:
            return
        self._versioning.snapshot_before_save(view.path)
        count = view.pdf.apply_redactions()
        view.pdf.save()
        show_toast(self, f"Redactions applied on {count} page(s)", "success")
        self.open_path(str(view.path))

    def action_verify_redaction(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            return
        from veyrion_workspace.ui.dialogs import VerifyRedactionDialog
        VerifyRedactionDialog(self, view.pdf).exec()

    def action_sign(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "Signing applies to PDF documents", "warning")
            return
        from veyrion_workspace.ui.dialogs import SignDialog
        SignDialog(self, view).exec()

    def action_show_signatures(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            return
        infos = view.pdf.signature_info()
        if not infos:
            show_toast(self, "This document has no signature fields", "info")
            return
        lines = []
        for info in infos:
            lines.append(
                f"Field: {info['name']}\nSigned: {'yes' if info['signed'] else 'no'}\n"
                f"Signer: {info.get('signer') or '—'}\nIssuer: {info.get('issuer') or '—'}\n"
                f"Valid: {info.get('valid_from') or '—'} → {info.get('valid_to') or '—'}")
        self._show_text_result("Signatures", "\n\n".join(lines))

    def action_forms(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "Forms apply to PDF documents", "warning")
            return
        from veyrion_workspace.ui.dialogs import FormsDialog
        FormsDialog(self, view).exec()

    def action_edit_metadata(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "Metadata editing applies to PDF documents", "warning")
            return
        from veyrion_workspace.ui.dialogs import MetadataDialog
        MetadataDialog(self, view).exec()

    def action_strip_metadata(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "Metadata stripping applies to PDF documents", "warning")
            return
        from veyrion_workspace.core.security.privacy import inspect_metadata
        md = inspect_metadata(view.path)
        if not md:
            show_toast(self, "No metadata found to strip", "info")
            return
        listing = "\n".join(f"  {k}: {v[:60]}" for k, v in md.items())
        confirm = QMessageBox.question(
            self, "Strip metadata",
            f"The document contains metadata:\n\n{listing}\n\n"
            f"Remove it? A cleaned copy will be saved.")
        if confirm != QMessageBox.Yes:
            return
        from veyrion_workspace.core.security.privacy import strip_metadata
        target = view.path.with_name(view.path.stem + "_clean.pdf")
        strip_metadata(view.path, target)
        show_toast(self, f"Cleaned copy saved: {target.name}", "success")

    def action_optimize(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "Optimization applies to PDF documents", "warning")
            return
        from veyrion_workspace.ui.dialogs import OptimizeDialog
        OptimizeDialog(self, view, self.tasks).exec()

    def action_merge(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Merge PDFs (in order selected)", "",
            "PDF files (*.pdf)")
        if len(files) < 2:
            show_toast(self, "Select at least two PDFs", "warning")
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Save merged PDF", "merged.pdf", "PDF (*.pdf)")
        if not target:
            return

        def work(progress=None, cancel=None):
            import fitz
            out = fitz.open()
            for i, f in enumerate(files):
                src = fitz.open(f)
                out.insert_pdf(src)
                src.close()
                progress((i + 1) / len(files), Path(f).name)
            out.save(target, garbage=4, deflate=True)
            out.close()
            return target
        self.tasks.submit("Merging PDFs", "merge", work)

    def action_split(self) -> None:
        view = self.current_view()
        if view is None or not hasattr(view, "pdf"):
            show_toast(self, "Splitting applies to PDF documents", "warning")
            return
        spec, ok = QInputDialog.getText(
            self, "Split PDF",
            "Page ranges, comma separated (e.g. 1-5,6-12,13-):")
        if not ok or not spec.strip():
            return
        from veyrion_workspace.core.printing.printing import parse_page_range
        ranges = []
        for part in spec.split(","):
            pages = parse_page_range(part.strip(), view.page_count)
            if pages:
                ranges.append((pages[0], pages[-1]))
        if not ranges:
            show_toast(self, "No valid ranges", "warning")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Output folder")
        if not out_dir:
            return
        task = self.tasks.submit(
            "Splitting PDF", "split", view.pdf.split_at, ranges,
            Path(out_dir), view.path.stem)

    def action_reading_summary(self) -> None:
        """Open the reading report for the active document."""
        from veyrion_workspace.ui.reading_summary import ReadingSummaryDialog

        view = self.current_view()
        ReadingSummaryDialog(self, view=view, db=self.db).exec()

    def action_focus_mode(self) -> None:
        self._focus_mode = not self._focus_mode
        for dock in (self.dock_library, self.dock_thumbs, self.dock_toc,
                     self.dock_bookmarks, self.dock_annotations,
                     self.dock_notes, self.dock_search, self.dock_tasks,
                     self.dock_props):
            if self._focus_mode:
                dock._or_was_visible = dock.isVisible()
                dock.hide()
            else:
                if getattr(dock, "_or_was_visible", False):
                    dock.show()
        self.main_toolbar.setVisible(not self._focus_mode)
        self.menuBar().setVisible(not self._focus_mode)
        self.statusBar().setVisible(not self._focus_mode)
        self.act_focus.setChecked(self._focus_mode)
        if self._focus_mode:
            show_toast(self, "Focus mode — press F9 to exit", "info")

    # -- distraction-free mode (#79) ---------------------------------------
    def action_distraction_free(self) -> None:
        """Fade out ALL chrome — toolbar, menus, status bar, docks, nav rail.

        Unlike focus mode, this is remembered per session and the nav rail
        hides too. Press F8 again to restore.
        """
        on = not bool(self.settings.get("reading", "distraction_free", False))
        self.settings.set("reading", "distraction_free", on)
        self._apply_distraction_free(on)
        self.act_distraction.setChecked(on)
        if on:
            show_toast(self, "Distraction-free — press F8 to restore", "info")

    def _apply_distraction_free(self, on: bool) -> None:
        rail = getattr(self, "nav_rail", None)
        if on:
            self._chrome_was_visible = {
                "toolbar": self.main_toolbar.isVisible(),
                "menubar": self.menuBar().isVisible(),
                "statusbar": self.statusBar().isVisible(),
                "nav_rail": bool(rail is not None and rail.isVisible()),
                "docks": [d.isVisible() for d in self._all_docks()],
            }
            self.main_toolbar.hide()
            self.menuBar().hide()
            self.statusBar().hide()
            if rail is not None:
                rail.hide()
            for d in self._all_docks():
                d.hide()
        else:
            was = getattr(self, "_chrome_was_visible", None)
            if was:
                if was.get("toolbar"):
                    self.main_toolbar.show()
                if was.get("menubar"):
                    self.menuBar().show()
                if was.get("statusbar"):
                    self.statusBar().show()
                if was.get("nav_rail") and rail is not None:
                    rail.show()
                for d, vis in zip(self._all_docks(), was.get("docks", [])):
                    if vis:
                        d.show()
            else:
                self.main_toolbar.show()
                self.menuBar().show()
                self.statusBar().show()
                if rail is not None:
                    rail.show()

    def _all_docks(self) -> list:
        return [self.dock_library, self.dock_thumbs, self.dock_toc,
                self.dock_bookmarks, self.dock_annotations, self.dock_notes,
                self.dock_search, self.dock_tasks, self.dock_props]

    # -- per-view theme (#77) + spread gap tuning (#10) ---------------------
    def _set_view_theme(self, key: str) -> None:
        from veyrion_workspace.ui.views.pdf_view import PdfView
        view = self.current_view()
        if not isinstance(view, PdfView):
            if view is not None:
                show_toast(self, "Page themes apply to PDF documents", "warning")
            return
        view.set_theme(key)
        self.settings.set("reading", "pdf_theme", key)
        self._sync_theme_actions(view)

    def _sync_theme_actions(self, view: PdfView) -> None:
        for a in self.act_themes:
            a.setChecked(a.data() == view.theme())

    def _gap_tuning_dialog(self) -> None:
        from veyrion_workspace.ui.views.pdf_view import PdfView
        view = self.current_view()
        if not isinstance(view, PdfView):
            show_toast(self, "Spread tuning applies to PDF documents", "warning")
            return
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
                                       QFormLayout, QCheckBox, QVBoxLayout)
        dlg = QDialog(self)
        dlg.setWindowTitle("Spread tuning")
        form = QFormLayout()
        gap = QDoubleSpinBox()
        gap.setRange(0.0, 120.0)
        gap.setSingleStep(2.0)
        gap.setDecimals(1)
        gap.setSuffix(" pt")
        gap.setValue(view.gap())
        form.addRow("Gap between pages", gap)
        cover = QCheckBox("Offset first page (book cover layout)")
        cover.setChecked(view.cover_offset())
        form.addRow("", cover)
        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() == QDialog.Accepted:
            view.set_gap(float(gap.value()))
            if bool(cover.isChecked()) != view.cover_offset():
                view.set_cover_offset(bool(cover.isChecked()))
            self.settings.set("reading", "pdf_gap", float(view.gap()))
            self.settings.set("reading", "pdf_cover_offset", view.cover_offset())

    def _on_action_toast(self, message: str, kind: str, action) -> None:
        """Route view deletion confirmations to an Undo-button toast (#82)."""
        show_actionable_toast(self, message, kind, on_action=action,
                              action_text="Undo")

    def _apply_view_tuning_defaults(self, view) -> None:
        """Apply persisted spread/theme defaults to a PDF view once (#10/#77).

        Views that restored an explicit gap/theme from reading progress keep
        it; untouched views pick up the global defaults.
        """
        from veyrion_workspace.ui.views.pdf_view import PdfView
        if not isinstance(view, PdfView) or getattr(view, "_tuning_applied", False):
            return
        view._tuning_applied = True
        try:
            gap = float(self.settings.get("reading", "pdf_gap", 14) or 14)
        except (TypeError, ValueError):
            gap = 14.0
        if abs(view.gap() - 14.0) <= 0.01 and abs(gap - 14.0) > 0.01:
            view.set_gap(gap)
        theme = str(self.settings.get("reading", "pdf_theme", "") or "")
        if view.theme() == "" and theme:
            view.set_theme(theme)
        self._sync_theme_actions(view)

    def action_presentation(self) -> None:
        view = self.current_view()
        if view is None:
            return
        from veyrion_workspace.ui.presentation import PresentationWindow
        self._present = PresentationWindow(self, view)
        self._present.showFullScreen()

    def action_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.act_fullscreen.setChecked(False)
        else:
            self.showFullScreen()
            self.act_fullscreen.setChecked(True)

    def action_settings(self) -> None:
        from veyrion_workspace.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._apply_theme()

    def action_vault(self) -> None:
        from veyrion_workspace.ui.dialogs import VaultDialog
        VaultDialog(self, self.settings).exec()

    def action_privacy(self) -> None:
        from veyrion_workspace.ui.dialogs import PrivacyDialog
        PrivacyDialog(self, self.db, self.settings).exec()

    def action_onboarding(self) -> None:
        from veyrion_workspace.ui.onboarding import OnboardingDialog
        OnboardingDialog(self, self.settings).exec()

    def action_plugins(self) -> None:
        if not hasattr(self, "_plugin_host"):
            from veyrion_workspace.core.plugins.host import PluginHost

            def add_plugin_command(plugin_id, title, callback):
                self.palette_overlay.set_commands(
                    self._palette_commands() +
                    [Command(title=title, subtitle=f"plugin: {plugin_id}",
                             payload=callback)])

            api = {
                "settings": self.settings,
                "open_document": lambda p: self.current_view().engine
                if p == "__current__" and self.current_view() else None,
                "add_command": add_plugin_command,
                "toast": lambda msg, kind: show_toast(self, msg, kind),
            }
            self._plugin_host = PluginHost(self.settings, api)
            self._plugin_host.load_all_approved()
        from veyrion_workspace.ui.plugins_panel import PluginsDialog
        PluginsDialog(self, self._plugin_host).exec()

    def action_updates(self) -> None:
        if self.settings.get("privacy", "offline_mode", True):
            confirm = QMessageBox.question(
                self, "Update check",
                "Offline mode is enabled. Allow one manual network check "
                "for updates?")
            if confirm != QMessageBox.Yes:
                return
        if not self.settings.get("privacy", "allow_update_check", False):
            self.settings.set("privacy", "allow_update_check", True)
        from veyrion_workspace.core.updates import run_update_check_dialog
        run_update_check_dialog(self)

    def action_open_logs(self) -> None:
        from veyrion_workspace.app.logging_setup import open_logs_folder
        open_logs_folder()

    def action_diagnostics(self) -> None:
        from veyrion_workspace.app.logging_setup import export_diagnostic_report
        target, _ = QFileDialog.getSaveFileName(
            self, "Export diagnostic report", "veyrion-diagnostics.txt.gz",
            "Diagnostic bundle (*.gz)")
        if target:
            out = export_diagnostic_report(Path(target))
            show_toast(self, f"Report saved: {Path(out).name}", "success")

    # ================================================================ drag & drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                if Path(path).is_dir():
                    self.library_panel.import_folder(Path(path))
                else:
                    self.open_path(path)

    # ================================================================ session
    def _record_session(self) -> None:
        tabs = []
        for i in range(self.tabs.count()):
            for view in self._document_views_in(self.tabs.widget(i)):
                try:
                    tabs.append({"path": str(view.path), "state": view.save_state()})
                except Exception:
                    tabs.append({"path": str(view.path), "state": {}})
        self.journal.record(tabs, self.tabs.currentIndex())

    def restore_session(self) -> bool:
        """Restore the last session if it ended uncleanly. Returns True when
        tabs were reopened."""
        if not self.settings.get("general", "restore_session", True):
            return False
        if not self.journal.has_unsaved_session():
            return False
        tabs = self.journal.recoverable_tabs()
        if not tabs:
            return False
        box = QMessageBox(self)
        box.setWindowTitle("Restore session")
        box.setText(f"Veyrion was closed unexpectedly.\n\nRestore "
                    f"{len(tabs)} document(s) from the previous session?")
        restore = box.addButton("Restore", QMessageBox.AcceptRole)
        box.addButton("Start fresh", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not restore:
            self.journal.clear()
            return False
        opened = 0
        for entry in tabs:
            path = entry.get("path", "")
            state = entry.get("state", {})
            if path and Path(path).exists():
                self.open_path(path)
                opened += 1
                view = self.current_view()
                if view is not None and state:
                    view.restore_state(state)
        return opened > 0

    # ================================================================ window state
    def _restore_window_state(self) -> None:
        state = self.settings.get("session", "window", None)
        if state and isinstance(state, dict):
            geo = state.get("geometry", "")
            if geo:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
            docks = state.get("docks", "")
            if docks:
                self.restoreState(QByteArray.fromBase64(docks.encode()))
            if state.get("maximized"):
                self.showMaximized()

    def _save_window_state(self) -> None:
        self.settings.set("session", "window", {
            "geometry": bytes(self.saveGeometry().toBase64()).decode(),
            "docks": bytes(self.saveState().toBase64()).decode(),
            "maximized": bool(self.windowState() & Qt.WindowMaximized),
        })

    # ================================================================ shutdown
    def closeEvent(self, event) -> None:
        # Autosave unsaved docs is intentionally NOT silent; ask per doc.
        modified = []
        for i in range(self.tabs.count()):
            for view in self._document_views_in(self.tabs.widget(i)):
                if view.is_modified:
                    modified.append((i, view))
        if modified:
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved changes")
            box.setText(f"{len(modified)} document(s) have unsaved changes.")
            save_all = box.addButton("Save all", QMessageBox.AcceptRole)
            discard = box.addButton("Discard", QMessageBox.DestructiveRole)
            cancel = box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel:
                event.ignore()
                return
            if clicked is save_all:
                for i, view in modified:
                    self.tabs.setCurrentIndex(i)
                    if not self.action_save():
                        event.ignore()
                        return
        self._end_reading_session()
        self._record_session()
        self.journal.mark_clean_exit()
        self._save_window_state()
        # Close engines.
        for view in self._all_document_views():
            try:
                view.close_view()
            except Exception:
                logger.exception("closeEvent failed")
        self.tasks.shutdown(wait=False)
        event.accept()


def _kind_of(path: str) -> str:
    from veyrion_workspace.utils.pathutils import detect_kind
    return detect_kind(Path(path))
