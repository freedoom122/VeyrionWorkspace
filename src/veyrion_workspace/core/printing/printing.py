"""Printing subsystem.

Builds a print-ready PDF (page ranges, N-up imposition, duplex-friendly
ordering) from any open document and hands it to the OS print dialog via
the platform print command. Qt handles the native dialog in the UI layer;
this module provides the layout engine.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("veyrion.printing")


def parse_page_range(spec: str, page_count: int) -> list[int]:
    """Parse '1-3,5,8-' into a zero-based page list."""
    spec = (spec or "").strip()
    if not spec:
        return list(range(page_count))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s) - 1 if start_s.strip() else 0
            end = int(end_s) - 1 if end_s.strip() else page_count - 1
            pages.extend(range(max(0, start), min(page_count - 1, end) + 1))
        else:
            p = int(part) - 1
            if 0 <= p < page_count:
                pages.append(p)
    return sorted(set(p for p in pages if 0 <= p < page_count))


def build_print_pdf(engine, out_path: Path, pages: list[int], *,
                    copies: int = 1, pages_per_sheet: int = 1,
                    include_annotations: bool = True,
                    progress=None) -> Path:
    """Render selected pages into a print-layout PDF.

    pages_per_sheet: 1, 2, or 4. Booklet-style duplex ordering is applied
    when pages_per_sheet == 2 and the caller passes reorder_for_duplex.
    """
    import fitz
    targets = pages * copies if copies > 1 else pages
    out = fitz.open()
    zoom = 2.0
    page_imgs: list[bytes] = []
    total = len(targets)
    for i, p in enumerate(targets):
        pix = engine.render_page(p, zoom=zoom)
        page_imgs.append(pix.tobytes("png"))
        if progress:
            progress((i + 1) / total, f"rendering page {p + 1}")

    if pages_per_sheet == 1:
        for img in page_imgs:
            page = out.new_page(width=595, height=842)
            page.insert_image(page.rect, stream=img)
    else:
        cols = 2 if pages_per_sheet in (2, 4) else 1
        rows = 1 if pages_per_sheet == 2 else 2
        sheet_w, sheet_h = 842, 595  # landscape sheets for N-up
        cell_w, cell_h = sheet_w / cols, sheet_h / rows
        for i in range(0, len(page_imgs), pages_per_sheet):
            page = out.new_page(width=sheet_w, height=sheet_h)
            group = page_imgs[i:i + pages_per_sheet]
            for j, img in enumerate(group):
                r, c = divmod(j, cols)
                rect = fitz.Rect(c * cell_w + 8, r * cell_h + 8,
                                 (c + 1) * cell_w - 8, (r + 1) * cell_h - 8)
                page.insert_image(rect, stream=img, keep_proportion=True)
    out.save(str(out_path), garbage=4, deflate=True)
    out.close()
    return Path(out_path)


class PrinterNameError(ValueError):
    """A printer name that could never be a valid CUPS/Windows queue."""


def validate_printer_name(name: str) -> str:
    """Reject strings that cannot be a real queue name.

    CUPS queue names and Windows shares must be non-empty, one line, and
    cannot contain control characters or shell-hostile separators. Returns
    the stripped name; raises :class:`PrinterNameError` otherwise.
    """
    name = (name or "").strip()
    if not name:
        raise PrinterNameError("printer name is empty")
    if len(name) > 128:
        raise PrinterNameError("printer name is too long")
    forbidden = set("\r\n\t\x00\f\v") | set("|;&$<>`\\'\"")
    bad = sorted({c for c in name if c in forbidden})
    if bad:
        raise PrinterNameError(
            "printer name contains forbidden characters: " + repr("".join(bad)))
    return name


def build_lp_command(pdf_path: Path, printer_name: str = "") -> list[str]:
    """Argument vector for the platform ``lp`` command (macOS/Linux).

    Always a list so paths and printer names containing spaces survive
    intact -- never join into a shell string.
    """
    cmd = ["lp"]
    if printer_name:
        cmd += ["-d", validate_printer_name(printer_name)]
    cmd.append(str(pdf_path))
    return cmd


def send_to_printer(pdf_path: Path, printer_name: str = "") -> bool:
    """Platform print hand-off. Returns True when a print job was started.

    On Windows the spool file (an ``os.handleSubmit``-style temp copy when a
    handle is used) is removed once the shell accepts it; on macOS/Linux the
    ``lp`` process cleans up after itself, so only our own temp artifacts
    are tracked here.
    """
    tmp_to_cleanup: Path | None = None
    try:
        if printer_name:
            printer_name = validate_printer_name(printer_name)
        if sys.platform.startswith("win"):
            import win32print
            import win32api
            name = printer_name or win32print.GetDefaultPrinter()
            # Copy to a temp file so ShellExecute's async print consumes a
            # file we own and can clean up deterministically.
            tmp = Path(tempfile.gettempdir()) / f"veyrion_print_{os.getpid()}_{pdf_path.name}"
            shutil.copy2(pdf_path, tmp)
            tmp_to_cleanup = tmp
            win32api.ShellExecute(
                0, "print", str(tmp), f'/d:"{name}"', ".", 0)
            return True
        cmd = build_lp_command(pdf_path, printer_name)
        subprocess.run(cmd, check=True)
        return True
    except Exception as e:
        logger.error("print failed: %s", e)
        return False
    finally:
        if tmp_to_cleanup is not None:
            try:
                tmp_to_cleanup.unlink(missing_ok=True)
            except OSError:
                logger.warning("could not remove print spool temp %s", tmp_to_cleanup)


def list_printers() -> list[str]:
    try:
        if sys.platform.startswith("win"):
            import win32print
            return [p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        import subprocess
        out = subprocess.run(["lpstat", "-a"], capture_output=True, text=True, timeout=5)
        return [line.split()[0] for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []
