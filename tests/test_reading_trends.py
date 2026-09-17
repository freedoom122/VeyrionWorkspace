"""Historic reading summary: reading_sessions aggregation + session recording.

The reading card is no longer just a snapshot — it aggregates the
``reading_sessions`` table into time-per-day and pages-per-week trends. These
tests pin the bucketing, the junk filtering, how the section renders on the
dialog, and — because nothing had ever written to ``reading_sessions`` — the
open/close lifecycle in the main window that feeds it.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from veyrion_workspace.ui.reading_summary import (  # noqa: E402
    gather_summary,
    reading_trends,
)


def _stamp(today: date, day_offset: int, hour: int = 20, minute: int = 0):
    """Local epoch for ``day_offset`` days before today at ``hour:minute``.

    20:00 local is used because it survives DST transitions in every zone
    that shifts in the small hours.
    """
    d = datetime.combine(today - timedelta(days=day_offset),
                         datetime.min.time()) + timedelta(hours=hour,
                                                          minutes=minute)
    return d.timestamp()


def _insert(db, started, ended, pages, doc="C:/books/x.pdf"):
    db.execute(
        "INSERT INTO reading_sessions (doc_path, started_at, ended_at, "
        "pages_read) VALUES (?,?,?,?)", (doc, started, ended, pages))


TODAY = date(2026, 9, 16)  # a Wednesday; fixed so bucket labels are stable


# ------------------------------------------------------------------ bucketing
def test_no_history_no_crash(db):
    assert reading_trends(db) == []
    assert reading_trends(None) == []


def test_time_per_day_buckets(db):
    _insert(db, _stamp(TODAY, 1, 9), _stamp(TODAY, 1, 10), 12)   # 60 min
    _insert(db, _stamp(TODAY, 3, 8), _stamp(TODAY, 3, 8.5 + 0), 5)  # 30 min
    rows = reading_trends(db, today=TODAY)

    assert rows[0] == ("Reading time (7 days)", "1h 30m 00s")
    day_rows = [(k, v) for k, v in rows if k.startswith("  ")
                and not v.endswith("pages")]
    yesterday_label = "  " + (TODAY - timedelta(days=1)).strftime("%a %d %b")
    three_days_label = "  " + (TODAY - timedelta(days=3)).strftime("%a %d %b")
    assert (yesterday_label, "1h 00m") in day_rows
    assert (three_days_label, "30m") in day_rows
    # Multiple sessions on one day are summed, not overwritten.
    _insert(db, _stamp(TODAY, 3, 22), _stamp(TODAY, 3, 22.5), 1)
    rows2 = dict(reading_trends(db, today=TODAY))
    assert rows2["Reading time (7 days)"] == "2h 00m 00s"


def test_pages_per_week_buckets(db):
    _insert(db, _stamp(TODAY, 1), _stamp(TODAY, 1, 21), 12)   # this-ish week
    _insert(db, _stamp(TODAY, 9), _stamp(TODAY, 9, 21), 40)   # previous week
    rows = reading_trends(db, today=TODAY)
    weeks = {k.strip(): v for k, v in rows if v.endswith("pages")}
    assert len(weeks) == 2
    assert sorted(int(v.split()[0]) for v in weeks.values()) == [12, 40]
    # Week labels carry an ISO week number and a year disambiguator.
    assert all(k.startswith("W") and "'" in k for k in weeks)


def test_session_crossing_midnight_buckets_by_end_day(db):
    # Starts 23:30 yesterday, ends 00:15 today -> lands in today's bucket.
    start = datetime.combine(TODAY - timedelta(days=1),
                             datetime.min.time()) + timedelta(hours=23,
                                                              minutes=30)
    end = start + timedelta(minutes=45)
    _insert(db, start.timestamp(), end.timestamp(), 3)
    rows = dict(reading_trends(db, today=TODAY))
    assert rows["Reading time (7 days)"] == "45m 00s"
    today_label = "  " + TODAY.strftime("%a %d %b")
    assert rows[today_label] == "45m"


def test_8_week_window(db):
    # Oldest in-window day: 51 days back = Monday of the 8th week (W31).
    _insert(db, _stamp(TODAY, 51), _stamp(TODAY, 51, 21), 8)
    # 52 days back = Sunday of W30, one printed week too early -> out.
    _insert(db, _stamp(TODAY, 52), _stamp(TODAY, 52, 21), 9)
    # 62 days back = W29 -> out.
    _insert(db, _stamp(TODAY, 62), _stamp(TODAY, 62, 21), 4)
    rows = reading_trends(db, today=TODAY)
    weeks = [v for _, v in rows if v.endswith("pages")]
    assert weeks == ["8 pages"]


# ----------------------------------------------------------------- junk safety
def test_junk_rows_are_skipped(db):
    _insert(db, _stamp(TODAY, 1, 9), _stamp(TODAY, 1, 10), 12)   # 60 min
    _insert(db, _stamp(TODAY, 3, 8), _stamp(TODAY, 3, 8.5), 5)   # 30 min
    _insert(db, "junk", "junk", "junk")                          # garbage
    _insert(db, _stamp(TODAY, -1, 8), _stamp(TODAY, -1, 9), 3)   # future-dated
    _insert(db, _stamp(TODAY, 2, 8), 0, 7)                       # abandoned
    _insert(db, _stamp(TODAY, 1, 11), _stamp(TODAY, 1, 10.5), 4)  # negative

    rows = dict(reading_trends(db, today=TODAY))
    # Only the two healthy sessions count; the clamped one adds 0 minutes.
    assert rows["Reading time (7 days)"] == "1h 30m 00s"
    weeks = sorted(int(v.split()[0])
                   for v in rows.values() if v.endswith("pages"))
    assert weeks == [5, 16]   # W-now: 12 + 4, W-3d: 5; no 3, no 7


def test_broken_db_degrades_to_empty(db):
    class Boom:
        def query(self, *a, **k):
            raise RuntimeError("boom")

    assert reading_trends(Boom()) == []


# ------------------------------------------------------- summary-card surface
def test_gather_summary_embeds_history(db, qapp, data_dir, sample_pdf):
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.ui.views.factory import create_view

    view = create_view(open_document(sample_pdf), Settings())
    try:
        _insert(db, time.time() - 1800, time.time(), 2, doc=str(view.path))
        rows = gather_summary(view, db)
        labels = [k for k, _ in rows]
        idx = labels.index("Reading history")
        # Order: total first, then the day rows.
        assert labels[idx + 1] == "Reading time (7 days)"
        assert labels[idx + 2].strip()[:3] in (
            "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        # A 30-minute session renders as "30m" on the day row.
        assert rows[idx + 2][1] == "30m"

        fresh = gather_summary(view, None)
        assert not any(k == "Reading history" for k, _ in fresh)
    finally:
        view.close_view()


def test_dialog_renders_history_section(qapp):
    from veyrion_workspace.ui.reading_summary import ReadingSummaryDialog

    rows = [
        ("Pages", "6"),
        ("Reading history", ""),          # section header -> spans both cols
        ("  Mon 14 Sep", "1h 00m"),
        ("  W37 '26", "12 pages"),
    ]
    dlg = ReadingSummaryDialog(rows=rows, view=None, db=None)
    try:
        # The header row is a bold, spanning section header.
        items = dlg.table.findItems("Reading history",
                                    __import__("PySide6.QtCore",
                                               fromlist=["Qt"]).Qt.MatchExactly)
        assert len(items) == 1
        header = items[0]
        assert header.font().bold()
        assert dlg.table.columnSpan(header.row(), 0) == 2
        # The value cell of a header row is empty, not a stray "".
        assert dlg.table.item(header.row(), 1) is None
    finally:
        dlg.deleteLater()


# --------------------------------------------------- lifecycle that feeds it
def _session_rows(db, like):
    from veyrion_workspace.storage.database import Database  # noqa: F401
    return [dict(r) for r in db.query(
        "SELECT doc_path, started_at, ended_at, pages_read "
        "FROM reading_sessions WHERE doc_path LIKE ?", (like,))]


def test_opening_a_document_starts_a_session(main_window, sample_pdf, db):
    w, _ = main_window
    w.open_path(str(sample_pdf))
    rows = _session_rows(db, "%sample.pdf")
    assert len(rows) == 1
    assert rows[0]["ended_at"] == 0          # open session


def test_closing_the_tab_ends_the_session(main_window, sample_pdf, db):
    w, _ = main_window
    w.open_path(str(sample_pdf))
    w.close_tab(w.tabs.currentIndex())
    rows = _session_rows(db, "%sample.pdf")
    assert len(rows) == 1
    assert rows[0]["ended_at"] > 0
    assert rows[0]["pages_read"] >= 1


def test_opening_another_document_swaps_the_session(
        main_window, sample_pdf, sample_txt, db):
    w, _ = main_window
    w.open_path(str(sample_pdf))
    w.open_path(str(sample_txt))
    pdf_rows = _session_rows(db, "%sample.pdf")
    txt_rows = _session_rows(db, "%sample.txt")
    assert len(pdf_rows) == 1 and pdf_rows[0]["ended_at"] > 0
    assert len(txt_rows) == 1 and txt_rows[0]["ended_at"] == 0


def test_quit_ends_the_open_session(main_window, sample_pdf, db):
    w, _ = main_window
    w.open_path(str(sample_pdf))
    w.close()                                 # triggers closeEvent
    rows = _session_rows(db, "%sample.pdf")
    assert len(rows) == 1 and rows[0]["ended_at"] > 0


def test_session_recording_never_blocks_closing(
        main_window, sample_pdf, db, monkeypatch):
    w, _ = main_window
    w.open_path(str(sample_pdf))

    class Broken:
        def end(self, *a, **k):
            raise RuntimeError("db gone")

    monkeypatch.setattr(w, "_sessions", Broken())
    w.close_tab(w.tabs.currentIndex())        # must not raise
    assert w.tabs.count() == 0
