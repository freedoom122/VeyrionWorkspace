"""Reading-summary reference surface.

The status bar has carried "42% read · page 7 of 16 · 12m 03s" for a while,
but that is a glance, not something you can keep. This module turns the same
facts — plus what the library knows about the document (bookmarks, saved
position, recorded sessions) — into a small exportable report: on screen in a
dialog, and on disk as the same PDF card / CSV / Markdown table the other
reference surfaces produce (see :mod:`veyrion_workspace.ui.reference_export`).

It is also historic: ``reading_sessions`` is aggregated into time-per-day and
pages-per-week trends, so the card answers "how much did I actually read this
month?" and not just "where am I right now?".

Everything is gathered defensively: the summary runs against whichever view
type is open (PDF, EPUB, text, comic, image), so a missing attribute degrades
to an omitted row rather than an error.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from veyrion_workspace.ui.reference_export import (
    SUMMARY_COLUMNS,
    copy_table_markdown,
    export_dialog,
    print_table,
)

logger = logging.getLogger("veyrion.reading_summary")

TITLE = "Reading Summary"


# ----------------------------------------------------------------- formatting
def format_duration(seconds: int) -> str:
    """'—', '45s', '12m 03s' or '1h 04m 09s'."""
    try:
        total = max(int(seconds), 0)
    except (TypeError, ValueError):
        return "—"
    if not total:
        return "—"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_stamp(epoch) -> str:
    """Local timestamp for a float epoch ('—' when unset/invalid)."""
    try:
        value = float(epoch)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


def _query_one(db, sql: str, params=()):
    """Run a read-only query, returning ``None`` instead of raising."""
    if db is None:
        return None
    try:
        return db.query_one(sql, params)
    except Exception:
        logger.debug("summary query failed: %s", sql, exc_info=True)
        return None


def _cell(row, key, default=None):
    if row is None:
        return default
    try:
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


# ------------------------------------------------------------------ gathering
def gather_summary(view, db=None) -> list[tuple[str, str]]:
    """The summary as ``(metric, value)`` rows, front-loaded with the basics."""
    rows: list[tuple[str, str]] = []
    if view is None:
        return [("Document", "No document open")]

    path = str(getattr(view, "path", "") or "")
    name = Path(path).name if path else str(
        getattr(view, "display_name", "") or "Untitled")
    total = int(getattr(view, "page_count", 0) or 0)
    page = int(getattr(view, "current_page", 0) or 0)
    read_pages = min(page + 1, total) if total else 0
    percent = round(read_pages / total * 100) if total else 0

    rows.append(("Document", name))
    if path:
        rows.append(("Location", path))
    rows.append(("Pages", str(total) if total else "—"))
    rows.append(("Current page", f"{page + 1} of {total}" if total else "—"))
    rows.append(("Progress", f"{percent}% read" if total else "—"))
    if total:
        rows.append(("Pages remaining", str(max(total - read_pages, 0))))

    try:
        zoom = float(getattr(view, "zoom", 1.0) or 1.0)
    except (TypeError, ValueError):
        zoom = 1.0
    if zoom and zoom != 1.0:
        rows.append(("Zoom", f"{int(round(zoom * 100))}%"))

    try:
        seconds = int(view.reading_seconds())
    except Exception:
        seconds = 0
    rows.append(("Time read this session", format_duration(seconds)))
    if total and seconds:
        rows.append(("Average per page", format_duration(seconds / total)))

    try:
        annots = list(view.live_annotations())
    except Exception:
        annots = []
    if annots or hasattr(view, "live_annotation_count"):
        rows.append(("Markups", str(len(annots))))
        sticky = sum(1 for a in annots if a.get("sticky"))
        if sticky:
            rows.append(("Sticky notes", str(sticky)))
    if bool(getattr(view, "is_modified", False)):
        rows.append(("Unsaved changes", "Yes"))

    if path:
        rows.extend(_library_facts(db, path))
    trends = reading_trends(db)
    if trends:
        rows.append(("Reading history", ""))
        rows.extend(trends)
    rows.append(("Report generated", format_stamp(time.time())))
    return rows


def _library_facts(db, path: str) -> list[tuple[str, str]]:
    """What the library recorded about this document."""
    rows: list[tuple[str, str]] = []

    bookmarks = _query_one(
        db, "SELECT COUNT(*) AS n FROM bookmarks WHERE doc_path=?", (path,))
    if bookmarks is not None:
        rows.append(("Bookmarks", str(int(_cell(bookmarks, "n", 0) or 0))))

    progress = _query_one(
        db, "SELECT page, updated_at FROM reading_progress WHERE doc_path=?",
        (path,))
    if progress is not None:
        saved_page = int(_cell(progress, "page", 0) or 0)
        rows.append(("Saved position", f"page {saved_page + 1}"))
        rows.append(("Position last saved",
                     format_stamp(_cell(progress, "updated_at", 0))))

    sessions = _query_one(
        db, "SELECT COUNT(*) AS n, COALESCE(SUM(pages_read), 0) AS p "
            "FROM reading_sessions WHERE doc_path=?", (path,))
    if sessions is not None and int(_cell(sessions, "n", 0) or 0):
        rows.append(("Reading sessions recorded",
                     str(int(_cell(sessions, "n", 0) or 0))))
        rows.append(("Pages read (recorded)",
                     str(int(_cell(sessions, "p", 0) or 0))))

    doc = _query_one(
        db, "SELECT title, author, kind, added_at, last_opened "
            "FROM documents WHERE path=?", (path,))
    if doc is not None:
        for label, key in (("Library title", "title"), ("Author", "author"),
                           ("Kind", "kind")):
            value = str(_cell(doc, key, "") or "")
            if value:
                rows.append((label, value))
        for label, key in (("Added to library", "added_at"),
                           ("Last opened", "last_opened")):
            stamp = format_stamp(_cell(doc, key, 0))
            if stamp != "—":
                rows.append((label, stamp))
    return rows


# ----------------------------------------------------------------- history
def _as_epoch(value) -> float:
    """Row timestamp as a positive float epoch (0.0 for junk)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _as_pages(value) -> int:
    """Row pages_read as a non-negative int (0 for junk)."""
    try:
        pages = int(value)
    except (TypeError, ValueError):
        return 0
    return max(pages, 0)


def reading_trends(db, today: date | datetime | None = None) -> list[tuple[str, str]]:
    """Aggregate ``reading_sessions`` into (metric, value) trend rows.

    Two lenses on the same history:

    * **Time per day** — the last 7 calendar days, bucketed by the day a
      session *ended*, expressed as ``h m`` (e.g. ``Mon 1h 25m``).
    * **Pages per week** — the last 8 ISO weeks (plus a year disambiguator),
      summing ``pages_read`` (e.g. ``W37 '26 · 84 pages``).

    Junk rows — a session with no ``ended_at``, non-numeric stamps, negative
    durations or negative pages — are skipped, never allowed to poison a
    bucket. Abandoned sessions (never ended) contribute nothing: with no end
    stamp there is neither a trustworthy duration nor a day to bucket them
    into. Future-dated rows are ignored too, so clock skew can't inflate the
    totals. A database that fails or is empty returns ``[]`` and the card
    simply omits the history section.
    """
    if db is None:
        return []
    try:
        rows = db.query(
            "SELECT started_at, ended_at, pages_read FROM reading_sessions")
    except Exception:
        logger.debug("reading_sessions unavailable", exc_info=True)
        return []

    if today is None:
        today = datetime.now().date()
    elif isinstance(today, datetime):
        today = today.date()
    # The 8 ISO weeks the card prints, oldest first: this week's Monday and
    # the seven before it. Bucket membership is checked against these keys,
    # so a session can never land in a week the card doesn't enumerate.
    this_monday = today - timedelta(days=today.weekday())
    week_keys = [
        (this_monday - timedelta(days=7 * i)).isocalendar()[:2]
        for i in range(8)
    ]
    week_set = set(week_keys)
    per_day: dict = {}                      # date -> minutes
    per_week: dict = {}                     # ISO (year, week) -> pages
    for r in rows:
        try:
            started = _as_epoch(r["started_at"])
            ended = _as_epoch(r["ended_at"])
            pages = _as_pages(r["pages_read"])
        except (TypeError, KeyError, IndexError):
            continue
        if ended <= 0:
            continue                        # abandoned session: no duration
        minutes = max((ended - started) / 60.0, 0.0)
        end_day = datetime.fromtimestamp(ended).date()
        delta = (today - end_day).days
        if delta < 0:
            continue                        # future-dated row (clock skew)
        if delta <= 6:
            per_day[end_day] = per_day.get(end_day, 0.0) + minutes
        year, week, _ = end_day.isocalendar()
        if (year, week) in week_set:
            per_week[(year, week)] = per_week.get((year, week), 0) + pages

    out: list[tuple[str, str]] = []
    if per_day:
        total = sum(per_day.values())
        out.append(("Reading time (7 days)", format_duration(int(total * 60))))
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            mins = per_day.get(day)
            if mins is None:
                continue                    # skip empty days on the card
            if mins < 60:
                value = f"{int(mins)}m"
            else:
                h, m = divmod(int(mins), 60)
                value = f"{h}h {m:02d}m"
            out.append((f"  {day.strftime('%a %d %b')}", value))
    if per_week:
        for year, week in reversed(week_keys):
            pages = per_week.get((year, week))
            if pages is None:
                continue
            label = f"W{week:02d} '{year % 100:02d}"
            out.append((f"  {label}", f"{pages} pages"))
    return out


def summary_subtitle(view) -> str:
    """One-line description used on the exported card."""
    if view is None:
        return "No document open"
    name = Path(str(getattr(view, "path", "") or "")).name or "Untitled"
    total = int(getattr(view, "page_count", 0) or 0)
    page = int(getattr(view, "current_page", 0) or 0)
    if not total:
        return name
    percent = round(min(page + 1, total) / total * 100)
    return f"{name} · {percent}% read · page {page + 1} of {total}"


def default_export_name(view) -> str:
    """File name offered in the save dialog."""
    stem = Path(str(getattr(view, "path", "") or "")).stem or "reading"
    return f"{stem}.reading-summary.pdf"


# ----------------------------------------------------------------------- UI
class ReadingSummaryDialog(QDialog):
    """The reading report, exportable as a card or sendable to a printer."""

    def __init__(self, parent=None, *, rows=None, view=None, db=None) -> None:
        super().__init__(parent)
        self._view = view
        self._db = db
        self._rows = list(rows) if rows is not None else gather_summary(view, db)

        name = Path(str(getattr(view, "path", "") or "")).name
        self.setWindowTitle(f"{TITLE} — {name}" if name else TITLE)
        self.resize(560, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self.table = QTableWidget(len(self._rows), 2)
        self.table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.resizeSection(0, 190)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        section_font = self.table.font()
        section_font.setBold(True)
        for r, (metric, value) in enumerate(self._rows):
            if str(value):
                self.table.setItem(r, 0, QTableWidgetItem(str(metric)))
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(r, 1, item)
            else:
                # Section header (e.g. "Reading history"): span both columns.
                self.table.setSpan(r, 0, 1, 2)
                item = QTableWidgetItem(str(metric))
                item.setFont(section_font)
                self.table.setItem(r, 0, item)
        lay.addWidget(self.table, 1)

        hint = QLabel("Export or print the report, or copy values from the "
                      "table.")
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        buttons = QHBoxLayout()
        self._copy_btn = QPushButton("Copy as Markdown")
        self._copy_btn.setToolTip(
            "Copy this summary to the clipboard as a Markdown table, ready "
            "to paste into notes or documentation")
        self._copy_btn.clicked.connect(self.copy_markdown)
        buttons.addWidget(self._copy_btn)
        self._export_btn = QPushButton("Export…")
        self._export_btn.setToolTip(
            "Save this summary as a PDF card, CSV or Markdown table")
        self._export_btn.clicked.connect(self.export)
        buttons.addWidget(self._export_btn)
        self._print_btn = QPushButton("Print…")
        self._print_btn.setToolTip("Send this summary to your printer")
        self._print_btn.clicked.connect(self.print_report)
        buttons.addWidget(self._print_btn)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.clicked.connect(lambda *_: self.accept())
        buttons.addWidget(box)
        lay.addLayout(buttons)

    # -- actions ---------------------------------------------------------
    def rows_for_export(self) -> list[tuple[str, str]]:
        """Re-gather live values so the export reflects the current page."""
        if self._view is not None:
            self._rows = gather_summary(self._view, self._db)
        return list(self._rows)

    def export(self, fmt: str | None = None) -> Path | None:
        rows = self.rows_for_export()
        return export_dialog(
            self, rows, title=TITLE,
            default_name=default_export_name(self._view),
            columns=SUMMARY_COLUMNS, subtitle=summary_subtitle(self._view),
            fmt=fmt, empty_warning="Nothing to export",
            empty_message="No reading data to report.")

    def copy_markdown(self) -> bool:
        """Copy the summary table to the clipboard as Markdown."""
        rows = self.rows_for_export()
        return copy_table_markdown(
            self, rows, title=TITLE, subtitle=summary_subtitle(self._view),
            columns=SUMMARY_COLUMNS, empty_warning="Nothing to copy")

    def print_report(self) -> bool:
        rows = self.rows_for_export()
        return print_table(self, rows, title=TITLE,
                           subtitle=summary_subtitle(self._view),
                           columns=SUMMARY_COLUMNS,
                           empty_warning="Nothing to print")
