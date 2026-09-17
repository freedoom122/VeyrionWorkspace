"""Shared export/print engine for the app's "reference surfaces".

Several places in the app present a table-shaped summary that a user wants to
keep: the keyboard-shortcut sheet, the annotations list, the reading summary,
and a selection of library documents. They all want the same three outputs — a
printable A4 PDF card, a CSV table, and a Markdown table — plus a Print button.

This module owns that behaviour so each surface only has to supply its rows and
a column spec:

    from veyrion_workspace.ui.reference_export import (
        export_dialog, print_table, write_table_pdf,
    )

    rows = [("Page 3", "Highlight", "Me", "the exact words I marked")]
    export_dialog(self, rows, title="Annotations",
                  default_name="report.annotations.pdf",
                  columns=ANNOTATION_COLUMNS)

Design notes:
  * Rows are plain ``tuple[str, ...]`` — one entry per column, nothing else.
    Callers stay free of any export-specific types.
  * Layout is computed, never rasterized: text is wrapped to its column and
    pages flow automatically with the header row repeated, so a long list
    stays readable instead of being squeezed onto one sheet.
  * Nothing here raises at the call site: writers report I/O problems by
    raising only from the write itself (callers guard), and the dialog and the
    print helper surface failures through the app's toasts/message boxes.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import QDialog

logger = logging.getLogger("veyrion.reference_export")

GENERATED_BY = "Veyrion Workspace"

# A column spec: (header, share of the printable width). Shares should sum to
# roughly 1.0; the middle column normally takes the slack for long text.
ColumnSpec = Sequence[tuple[str, float]]

DEFAULT_COLUMNS: ColumnSpec = (("Where", 0.26), ("Action", 0.48),
                               ("Shortcut", 0.26))
SUMMARY_COLUMNS: ColumnSpec = (("Metric", 0.38), ("Value", 0.62))
ANNOTATION_COLUMNS: ColumnSpec = (("Page", 0.11), ("Type", 0.16),
                                  ("Author", 0.16), ("Annotation", 0.57))
LIBRARY_COLUMNS: ColumnSpec = (("Title", 0.30), ("Author", 0.16),
                               ("Type", 0.09), ("Pages", 0.08),
                               ("Size", 0.09), ("Progress", 0.10),
                               ("Rating", 0.08), ("Tags", 0.10))

# format key -> (canonical suffix, label offered in the save dialog)
FORMATS: dict[str, tuple[str, str]] = {
    "pdf": (".pdf", "PDF card (*.pdf)"),
    "csv": (".csv", "CSV table (*.csv)"),
    "markdown": (".md", "Markdown table (*.md)"),
}
FILTER_ORDER = ("pdf", "csv", "markdown")


def filter_string() -> str:
    """The name filters for a save dialog, in :data:`FILTER_ORDER`."""
    return ";;".join(FORMATS[fmt][1] for fmt in FILTER_ORDER)


def headers_for(columns: ColumnSpec | None) -> list[str]:
    spec = columns or DEFAULT_COLUMNS
    return [str(header) for header, _share in spec]


def format_for(path: str, selected_filter: str = "") -> str:
    """Decide the export format from the chosen filter, then the file name."""
    chosen = (selected_filter or "").lower()
    for fmt in FILTER_ORDER:
        suffix, label = FORMATS[fmt]
        if fmt in chosen or label.split()[0].lower() in chosen or suffix in chosen:
            return fmt
    suffix = Path(path).suffix.lower()
    for fmt in FILTER_ORDER:
        canonical, _label = FORMATS[fmt]
        if suffix == canonical:
            return fmt
    if suffix in (".markdown", ".mkd"):
        return "markdown"
    if suffix in (".txt", ".text"):
        return "csv"
    return "pdf"


def canonical_path(path: str, fmt: str) -> Path:
    """Correct the file name to match the format the user picked."""
    target = Path(path)
    suffix = FORMATS[fmt][0]
    if target.suffix.lower() != suffix:
        return target.with_suffix(suffix)
    return target


# ------------------------------------------------------------------ PDF card
_PAGE_W, _PAGE_H = 595.0, 842.0        # A4 portrait, in points
_MARGIN = 42.0
_COLUMN_GAP = 10.0
_RULE = (0.76, 0.77, 0.79)
_GREY = (0.45, 0.47, 0.50)
_FS_TITLE, _FS_SUB, _FS_HEAD, _FS_ROW = 17.0, 8.5, 9.0, 9.0
_LEADING, _ROW_PAD = 11.5, 4.5


def _wrap_to_width(text: str, width: float, fontsize: float,
                   fontname: str = "helv") -> list[str]:
    """Greedy word-wrap of *text* into lines that fit *width* points."""
    import fitz

    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if fitz.get_text_length(trial, fontname=fontname,
                                fontsize=fontsize) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _column_widths(columns: ColumnSpec) -> list[float]:
    """Turn column shares into absolute widths that exactly fill the page."""
    gaps = _COLUMN_GAP * max(len(columns) - 1, 0)
    usable = _PAGE_W - 2 * _MARGIN - gaps
    shares = [float(share) for _header, share in columns] or [1.0]
    total = sum(shares) or 1.0
    return [usable * share / total for share in shares]


def write_table_pdf(rows, out_path, *, title: str = "Reference",
                    subtitle: str = "", columns: ColumnSpec = DEFAULT_COLUMNS,
                    generated_by: str = GENERATED_BY,
                    empty_message: str = "Nothing to report.") -> Path:
    """Render ``rows`` as a printable A4 card with a header per column.

    Long cells wrap inside their column, content flows onto extra pages with
    the column headers repeated, and every page carries a footer. Returns the
    written path.
    """
    import fitz

    columns = tuple(columns)
    widths = _column_widths(columns)
    xs: list[float] = []
    x = _MARGIN
    for width in widths:
        xs.append(x)
        x += width + _COLUMN_GAP
    x_end = _PAGE_W - _MARGIN
    bottom = _PAGE_H - _MARGIN - 16.0

    out = fitz.open()

    def _open_page(first: bool):
        page = out.new_page(width=_PAGE_W, height=_PAGE_H)
        y = _MARGIN + _FS_TITLE * 0.8
        page.insert_text((_MARGIN, y), title, fontname="hebo",
                         fontsize=_FS_TITLE if first else _FS_HEAD + 1)
        y += (_FS_TITLE + 6) if first else (_FS_HEAD + 12)
        if first and subtitle:
            page.insert_text((_MARGIN, y), subtitle, fontname="helv",
                             fontsize=_FS_SUB, color=_GREY)
            y += _FS_SUB + 12
        for (header, _share), x_pos in zip(columns, xs):
            page.insert_text((x_pos, y), str(header), fontname="hebo",
                             fontsize=_FS_HEAD)
        y += 4
        page.draw_line(fitz.Point(_MARGIN, y), fitz.Point(x_end, y),
                       color=_RULE, width=0.7)
        return page, y + 11.0

    page, y = _open_page(first=True)
    if not rows:
        page.insert_text((_MARGIN, y), empty_message, fontname="helv",
                         fontsize=_FS_ROW, color=_GREY)
    for row in rows:
        cells = [str(cell) for cell in row]
        wrapped = [_wrap_to_width(cells[i] if i < len(cells) else "",
                                  widths[i], _FS_ROW)
                   for i in range(len(columns))]
        tall = max((len(lines) for lines in wrapped), default=1)
        row_h = tall * _LEADING + _ROW_PAD
        if y + row_h > bottom:
            page, y = _open_page(first=False)
        for x_pos, lines in zip(xs, wrapped):
            for i, line in enumerate(lines):
                page.insert_text((x_pos, y + i * _LEADING), line,
                                 fontname="helv", fontsize=_FS_ROW)
        y += row_h

    total = out.page_count
    for i, p in enumerate(out, start=1):
        p.insert_text((_MARGIN, _PAGE_H - 24), generated_by,
                      fontname="helv", fontsize=7.5, color=_GREY)
        p.insert_text((x_end - 70, _PAGE_H - 24), f"Page {i} of {total}",
                      fontname="helv", fontsize=7.5, color=_GREY)

    out.save(str(out_path), garbage=4, deflate=True)
    out.close()
    return Path(out_path)


# ------------------------------------------------------------------ CSV table
def write_table_csv(rows, out_path, *, columns: ColumnSpec | None = None) -> Path:
    """Write ``rows`` as a three-or-more column CSV.

    No title or footer line: every line is a row, so the file can be parsed by
    a spreadsheet or a script. UTF-8 with a BOM, which is what Excel expects
    from a double-clicked ``.csv``.
    """
    import csv

    target = Path(out_path)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers_for(columns))
        for row in rows:
            writer.writerow([str(cell) for cell in row])
    return target


# ------------------------------------------------------------- Clipboard
_CLIPBOARD_MARKDOWN_FLAVOR = "text/markdown"


def copy_table_markdown(parent, rows, *, title: str = "Reference",
                        subtitle: str = "",
                        columns: ColumnSpec = DEFAULT_COLUMNS,
                        generated_by: str = GENERATED_BY,
                        empty_warning: str = "Nothing to copy") -> bool:
    """Put the rows on the clipboard as the same Markdown table we write to disk.

    Offers both ``text/plain`` and a ``text/markdown`` flavor, so rich-text
    consumers can tell a table from pasted prose while plain-text targets
    (editors, chat, issue trackers) still get the exact same characters.
    Reports the outcome as a toast and returns whether anything was copied.
    """
    from PySide6.QtCore import QMimeData
    from PySide6.QtWidgets import QApplication

    rows = list(rows)
    if not rows:
        _notify(parent, empty_warning, "warning")
        return False
    text = build_markdown_table(rows, title=title, subtitle=subtitle,
                                columns=columns, generated_by=generated_by)
    mime = QMimeData()
    payload = text.encode("utf-8")
    mime.setData("text/plain", payload)
    mime.setData(_CLIPBOARD_MARKDOWN_FLAVOR, payload)
    QApplication.clipboard().setMimeData(mime)
    _notify(parent, f"Copied {len(rows)} row"
                    f"{'s' if len(rows) != 1 else ''} as Markdown", "success")
    return True


# ------------------------------------------------------------- Markdown table
def _md_cell(text: str) -> str:
    """Escape a table cell so pipes and line breaks cannot break the table."""
    return (str(text).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " ").strip())


def build_markdown_table(rows, *, title: str = "Reference",
                         subtitle: str = "",
                         columns: ColumnSpec = DEFAULT_COLUMNS,
                         generated_by: str = GENERATED_BY,
                         empty_message: str = "Nothing to report.") -> str:
    """The exact Markdown document :func:`write_table_markdown` writes.

    Shared with the clipboard helper so Copy as Markdown puts the same
    characters on the clipboard that Export as Markdown puts in the file.
    """
    columns = tuple(columns)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# {title}", ""]
    count = len(rows)
    detail = subtitle or (f"{count} row{'s' if count != 1 else ''}")
    lines.append(f"_{detail} · exported {stamp} from {generated_by}._")
    lines.append("")
    header = " | ".join(_md_cell(str(h)) for h, _s in columns)
    lines.append(f"| {header} |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    if rows:
        for row in rows:
            cells = [str(cell) for cell in row]
            cells += [""] * max(len(columns) - len(cells), 0)
            lines.append("| " + " | ".join(_md_cell(c) for c in cells[:len(columns)]) + " |")
    else:
        filler = [empty_message] + [""] * (len(columns) - 1)
        lines.append("| " + " | ".join(_md_cell(c) for c in filler) + " |")
    return "\n".join(lines) + "\n"


def write_table_markdown(rows, out_path, *, title: str = "Reference",
                         subtitle: str = "",
                         columns: ColumnSpec = DEFAULT_COLUMNS,
                         generated_by: str = GENERATED_BY,
                         empty_message: str = "Nothing to report.") -> Path:
    """Write ``rows`` as a Markdown table ready to paste into documentation."""
    text = build_markdown_table(rows, title=title, subtitle=subtitle,
                                columns=columns, generated_by=generated_by,
                                empty_message=empty_message)
    target = Path(out_path)
    target.write_text(text, encoding="utf-8")
    return target


# ------------------------------------------------------------------ dispatch
def export_table(rows, out_path, fmt: str = "pdf", *,
                 title: str = "Reference", subtitle: str = "",
                 columns: ColumnSpec = DEFAULT_COLUMNS,
                 generated_by: str = GENERATED_BY,
                 empty_message: str = "Nothing to report.") -> Path:
    """Write ``rows`` in the requested format (``pdf``/``csv``/``markdown``)."""
    if fmt == "csv":
        return write_table_csv(rows, out_path, columns=columns)
    if fmt == "markdown":
        return write_table_markdown(rows, out_path, title=title,
                                    subtitle=subtitle, columns=columns,
                                    generated_by=generated_by,
                                    empty_message=empty_message)
    return write_table_pdf(rows, out_path, title=title, subtitle=subtitle,
                           columns=columns, generated_by=generated_by,
                           empty_message=empty_message)


def _notify(parent, message: str, kind: str = "info") -> None:
    """Toast through the app helper, tolerating a parentless surface."""
    if parent is None:
        logger.info("%s", message)
        return
    from veyrion_workspace.ui.widgets import show_toast

    show_toast(parent, message, kind)


def export_dialog(parent, rows, *, title: str = "Export", default_name: str,
                  columns: ColumnSpec = DEFAULT_COLUMNS, subtitle: str = "",
                  fmt: str | None = None,
                  empty_warning: str = "Nothing to export",
                  empty_message: str = "Nothing to report.") -> Path | None:
    """Ask where to save, write ``rows``, and report the result.

    ``fmt`` forces a single format; otherwise the user's chosen filter decides
    (with the file name corrected to match). Returns the written path, or
    ``None`` when the user cancelled or there was nothing to write.
    """
    from PySide6.QtWidgets import QFileDialog

    rows = list(rows)
    if not rows:
        _notify(parent, empty_warning, "warning")
        return None
    filters = filter_string()
    if fmt:
        filters = FORMATS[fmt][1]
        default_name = str(Path(default_name).with_suffix(FORMATS[fmt][0]))
    target, selected = QFileDialog.getSaveFileName(parent, title, default_name,
                                                   filters)
    if not target:
        return None
    chosen = fmt or format_for(target, selected)
    path = canonical_path(target, chosen)
    try:
        out = export_table(rows, path, chosen, title=title, subtitle=subtitle,
                           columns=columns, empty_message=empty_message)
    except Exception as e:
        logger.exception("reference export failed")
        _notify(parent, f"Could not export {chosen}: {e}", "warning")
        return None
    _notify(parent, f"Exported {Path(out).name}", "success")
    return Path(out)


def print_table(parent, rows, *, title: str = "Reference", subtitle: str = "",
                columns: ColumnSpec = DEFAULT_COLUMNS,
                generated_by: str = GENERATED_BY,
                empty_warning: str = "Nothing to print") -> bool:
    """Preview the card, then hand it to the OS print queue on approval.

    The preview shows the exact PDF file that will be printed (the shared
    preview mechanism the Print dialog uses — a rendered page image on a
    white page — extended with a pager for multi-page cards). Cancelling
    from the preview sends nothing. Reports both outcomes (toast on
    success, a warning box when no printer could be reached) and returns
    whether a print job was started.
    """
    from PySide6.QtWidgets import QMessageBox

    from veyrion_workspace.core.printing.printing import send_to_printer
    from veyrion_workspace.utils.safeio import make_temp_file

    rows = list(rows)
    if not rows:
        _notify(parent, empty_warning, "warning")
        return False
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    detail = subtitle or f"{len(rows)} rows · {stamp}"
    try:
        tmp = Path(make_temp_file(suffix="_reference.pdf"))
        write_table_pdf(rows, tmp, title=title, subtitle=detail,
                        columns=columns, generated_by=generated_by)
    except Exception as e:
        logger.exception("could not build the print card")
        _notify(parent, f"Could not prepare the print job: {e}", "warning")
        return False
    preview = ReferencePrintPreview(parent, tmp, title=title)
    if preview.exec() != QDialog.DialogCode.Accepted:
        return False
    if send_to_printer(tmp):
        _notify(parent, f"Print job sent: {title}", "success")
        return True
    QMessageBox.warning(parent, "Print failed",
                        "No printer could be reached. Check that a printer "
                        "is configured in your OS.")
    return False


class ReferencePrintPreview(QDialog):
    """WYSIWYG preview of a reference card before it goes to the printer.

    Reuses the Print dialog's preview mechanism — a bordered white QLabel
    holding a rendered, smooth-scaled page image — with a small pager,
    because reference cards flow onto multiple pages. What is on screen is
    a rendering of the very PDF file that approval hands to the printer.
    """

    _PREVIEW_WIDTH = 380   # px target for the scaled page image

    def __init__(self, parent, card_path, *, title: str = "Reference") -> None:
        import fitz

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
            QVBoxLayout,
        )
        from veyrion_workspace.ui.widgets import HelpLabel

        super().__init__(parent)
        self.setWindowTitle(f"Print preview — {title}")
        self.setMinimumSize(430, 540)
        self._card_path = Path(card_path)
        self._doc = fitz.open(self._card_path)
        self._page = 0

        lay = QVBoxLayout(self)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setMinimumHeight(300)
        self._preview.setStyleSheet(
            "border: 1px solid #B9B2A3; background: white;")
        lay.addWidget(self._preview, 1)

        pager = QHBoxLayout()
        self._prev_btn = QPushButton("< Previous")
        self._prev_btn.clicked.connect(self._go_prev)
        pager.addWidget(self._prev_btn)
        self._page_label = QLabel("")
        self._page_label.setAlignment(Qt.AlignCenter)
        pager.addWidget(self._page_label, 1)
        self._next_btn = QPushButton("Next >")
        self._next_btn.clicked.connect(self._go_next)
        pager.addWidget(self._next_btn)
        lay.addLayout(pager)

        lay.addWidget(HelpLabel(
            "This is exactly what will be printed. Cancel sends nothing."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Print…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._render()

    # -- pager --------------------------------------------------------------
    def _go_prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._render()

    def _go_next(self) -> None:
        if self._page < self._doc.page_count - 1:
            self._page += 1
            self._render()

    def _render(self) -> None:
        """Paint the current page of the card into the preview label."""
        import fitz

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPixmap

        page = self._doc[self._page]
        pix = page.get_pixmap(matrix=fitz.Matrix(96 / 72, 96 / 72),
                              alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888)
        self._preview.setPixmap(QPixmap.fromImage(img).scaledToWidth(
            self._PREVIEW_WIDTH, Qt.SmoothTransformation))
        self._page_label.setText(
            f"Page {self._page + 1} of {self._doc.page_count}")
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(
            self._page < self._doc.page_count - 1)
