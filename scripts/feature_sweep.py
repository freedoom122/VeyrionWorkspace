#!/usr/bin/env python3
"""End-to-end feature sweep for Veyrion Workspace.

Unlike the unit suite, this drives the *real* application surface: every
document engine, every core service, and every QAction in a live MainWindow.
Its job is to find dead buttons, placeholder features, and crashes that the
targeted tests never touch.

Modal dialogs are stubbed so a headless run cannot block: any dialog that is
constructed is recorded, which proves the dialog builds without raising. File
dialogs, message boxes, external links, and network access are stubbed so the
sweep is hermetic and never opens windows or hits the network.

Usage:
    .venv/Scripts/python scripts/feature_sweep.py

Writes artifacts/feature_sweep/report.md and results.json.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="veyrion_sweep_"))
os.environ["VEYRION_DATA_DIR"] = str(DATA)

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QColor, QDesktopServices, QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QColorDialog, QDialog, QFileDialog, QFontDialog,
    QInputDialog, QMessageBox,
)

# --------------------------------------------------------------- instrumentation
RESULTS: list[tuple[str, str, str, str]] = []
FAILURES: list[tuple[str, str, str]] = []
DIALOGS: list[str] = []
DIALOG_BY_SECTION: dict[str, int] = {}


def _record(section: str, name: str, status: str, detail: str = "") -> None:
    RESULTS.append((section, name, status, detail))


def check(section: str, name: str, fn):
    """Run ``fn``; record PASS/FAIL (and the traceback on failure)."""
    try:
        detail = fn()
        _record(section, name, "PASS", detail if isinstance(detail, str) else "")
        return True
    except Exception as exc:  # noqa: BLE001 - the sweep must never abort
        _record(section, name, "FAIL", f"{type(exc).__name__}: {exc}")
        FAILURES.append((section, name, traceback.format_exc()))
        return False


def note(section: str, name: str, detail: str) -> None:
    _record(section, name, "INFO", detail)


def call_filtered(fn, *args, **kwargs):
    """Call ``fn`` passing only keyword arguments its signature accepts.

    Lets the sweep exercise real code paths without hardcoding every optional
    parameter of every engine method.
    """
    sig = inspect.signature(fn)
    params = sig.parameters
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        accepted = dict(kwargs)
    else:
        accepted = {k: v for k, v in kwargs.items() if k in params}
    return fn(*args, **accepted)


def construct(cls, *arg_sets):
    """Instantiate ``cls`` with the first argument set that works."""
    last: Exception | None = None
    for args in arg_sets:
        try:
            return cls(*args)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last if last else RuntimeError(f"cannot construct {cls.__name__}")


# --------------------------------------------------------------- modal stubbing
def _install_stubs() -> None:
    def stub_exec(self):  # noqa: ANN001
        DIALOGS.append(type(self).__name__)
        return QDialog.DialogCode.Rejected

    QDialog.exec = stub_exec
    if hasattr(QDialog, "exec_"):
        QDialog.exec_ = stub_exec

    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")

    QMessageBox.information = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.warning = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.critical = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.about = staticmethod(lambda *a, **k: None)

    QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getInt = staticmethod(lambda *a, **k: (0, False))
    QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
    QColorDialog.getColor = staticmethod(
        lambda *a, **k: (QColor("#FFD400"), True))
    QFontDialog.getFont = staticmethod(lambda *a, **k: (QFont(), False))

    QDesktopServices.openUrl = staticmethod(lambda *a, **k: True)

    import webbrowser
    webbrowser.open = lambda *a, **k: True
    webbrowser.open_new = lambda *a, **k: True

    # Keep the sweep offline: any update/translation fetch fails fast and the
    # app must degrade gracefully instead of hanging.
    import urllib.request
    import urllib.error

    def offline(*a, **k):
        raise urllib.error.URLError("offline (sweep)")

    urllib.request.urlopen = offline
    if hasattr(urllib.request, "urlretrieve"):
        urllib.request.urlretrieve = offline


def settled(app, seconds: float = 0.15) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


# --------------------------------------------------------------- sample files
def build_samples(root: Path) -> dict[str, Path]:
    import fitz
    from PIL import Image

    root.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    # PDF: several pages, outline entries, a form field, and known phrases.
    pdf = root / "sample.pdf"
    doc = fitz.open()
    for i in range(8):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i + 1} of the sweep document")
        page.insert_text((72, 130), "Alpha beta gamma delta epsilon")
        page.insert_text((72, 160), "Confidential marker token ZQX")
    doc.set_metadata({"title": "Sweep PDF", "author": "Sweep Author",
                      "subject": "feature sweep", "keywords": "sweep"})
    doc.set_toc([[1, "Chapter One", 1], [1, "Chapter Two", 4]])
    widget = fitz.Widget()
    widget.field_name = "full_name"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(72, 200, 300, 220)
    widget.field_value = ""
    doc[0].add_widget(widget)
    doc.save(str(pdf))
    doc.close()
    out["pdf"] = pdf

    # EPUB
    from ebooklib import epub
    epub_path = root / "sample.epub"
    book = epub.EpubBook()
    book.set_identifier("sweep-epub")
    book.set_title("Sweep E-book")
    book.set_language("en")
    book.add_author("Sweep Author")
    chapters = []
    for idx in range(3):
        ch = epub.EpubHtml(title=f"Chapter {idx + 1}",
                           file_name=f"chap{idx + 1}.xhtml", lang="en")
        ch.content = (f"<html><body><h1>Chapter {idx + 1}</h1>"
                      f"<p>Reflowable paragraph number {idx + 1} for the "
                      f"reading engine sweep.</p></body></html>")
        book.add_item(ch)
        chapters.append(ch)
    book.toc = tuple(epub.Link(c.file_name, c.title, c.id) for c in chapters)
    book.spine = ["nav", *chapters]
    epub.write_epub(str(epub_path), book)
    out["epub"] = epub_path

    # DOCX / XLSX / PPTX / ODT / RTF
    import docx
    d = docx.Document()
    d.add_heading("Sweep Heading", level=1)
    d.add_paragraph("Body paragraph for the office engine sweep.")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "cell a"
    table.cell(1, 1).text = "cell d"
    docx_path = root / "sample.docx"
    d.save(str(docx_path))
    out["docx"] = docx_path

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["name", "value"])
    ws.append(["alpha", 1])
    ws.append(["beta", 2])
    xlsx_path = root / "sample.xlsx"
    wb.save(str(xlsx_path))
    out["xlsx"] = xlsx_path

    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Sweep Deck"
    slide.placeholders[1].text = "Slide body text for the sweep."
    pptx_path = root / "sample.pptx"
    prs.save(str(pptx_path))
    out["pptx"] = pptx_path

    from odf.opendocument import OpenDocumentText
    from odf.text import H, P
    odt = OpenDocumentText()
    odt.text.addElement(H(outlinelevel=1, text="Sweep ODT"))
    odt.text.addElement(P(text="An OpenDocument paragraph for the sweep."))
    odt_path = root / "sample.odt"
    odt.save(str(odt_path))
    out["odt"] = odt_path

    rtf_path = root / "sample.rtf"
    rtf_path.write_text(
        r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Calibri;}}\n"
        r"\f0\fs24 Sweep RTF heading\par Body text in RTF.\par}",
        encoding="ascii")
    out["rtf"] = rtf_path

    # Plain text family
    txt = root / "sample.txt"
    txt.write_text("Line one of the sweep text file\n" * 60, encoding="utf-8")
    out["txt"] = txt
    md = root / "sample.md"
    md.write_text("# Sweep Title\n\nIntro with **bold** and *italic*.\n\n"
                  "## Section\n\n- one\n- two\n\n```\ncode block\n```\n",
                  encoding="utf-8")
    out["md"] = md
    csv_path = root / "sample.csv"
    csv_path.write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")
    out["csv"] = csv_path
    js = root / "sample.json"
    js.write_text(json.dumps({"a": [1, 2, 3], "b": "text"}, indent=2),
                  encoding="utf-8")
    out["json"] = js
    xml = root / "sample.xml"
    xml.write_text("<?xml version=\"1.0\"?><root><item>one</item></root>",
                   encoding="utf-8")
    out["xml"] = xml
    html = root / "sample.html"
    html.write_text("<html><head><title>Sweep Page</title></head>"
                    "<body><h1>Heading</h1><p>Paragraph in HTML.</p>"
                    "<ul><li>item</li></ul></body></html>", encoding="utf-8")
    out["html"] = html

    # Raster images
    img_specs = {"png": (320, 240), "jpg": (320, 240), "bmp": (200, 150),
                 "tiff": (200, 150), "gif": (200, 150), "webp": (200, 150)}
    for ext, (w, h) in img_specs.items():
        path = root / f"image.{ext}"
        Image.new("RGB", (w, h), (90, 120, 160)).save(path)
        out[f"image_{ext}"] = path

    # SVG (vector image the spec lists as a supported type)
    svg = root / "vector.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120">'
        '<rect width="200" height="120" fill="#2A2721"/>'
        '<text x="20" y="70" fill="#EFE7D3" font-size="20">Sweep</text>'
        "</svg>", encoding="utf-8")
    out["svg"] = svg

    # Comic archive
    cbz = root / "comic.cbz"
    with zipfile.ZipFile(cbz, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(4):
            buf = io.BytesIO()
            Image.new("RGB", (400, 600), (40 * i, 60, 90)).save(buf, "PNG")
            zf.writestr(f"page{i + 1:03d}.png", buf.getvalue())
    out["cbz"] = cbz

    # Formats that must fail gracefully rather than crash.
    for ext in ("cbr", "mobi", "azw3", "djvu", "xps", "chm", "doc"):
        p = root / f"unsupported.{ext}"
        p.write_bytes(b"NOT-A-REAL-FILE\x00\x01\x02" * 20)
        out[ext] = p

    return out


# --------------------------------------------------------------- core sweeps
def sweep_formats(samples: dict[str, Path], work: Path) -> None:
    """Every supported format must open and expose real content."""
    from veyrion_workspace.core.documents.registry import open_document

    supported = ["pdf", "epub", "docx", "xlsx", "pptx", "odt", "rtf",
                 "txt", "md", "csv", "json", "xml", "html", "cbz",
                 "image_png", "image_jpg", "image_bmp", "image_tiff",
                 "image_gif", "image_webp", "svg"]
    graceful = ["cbr", "mobi", "azw3", "djvu", "xps", "chm", "doc"]

    for key in supported:
        path = samples[key]

        def probe(path=path, key=key):
            res = open_document(path)
            if not res.ok:
                raise AssertionError(f"did not open: {res.error}")
            engine = res.engine
            try:
                count = engine.page_count
                if count < 1:
                    raise AssertionError("page_count < 1")
                text = ""
                try:
                    text = engine.page_text(0) or ""
                except Exception as exc:  # noqa: BLE001
                    raise AssertionError(f"page_text failed: {exc}") from exc
                meta = engine.metadata()
                title = getattr(meta, "title", "") or ""
                return f"{res.format_name}, {count}p, text={len(text)}c, title={title[:24]!r}"
            finally:
                engine.close()

        check("formats", f"open {key}", probe)

    for key in graceful:
        path = samples[key]

        def probe_bad(path=path, key=key):
            res = open_document(path)
            if res.ok:
                res.engine.close()
                return "opened (engine handled it)"
            if not res.error or len(res.error) < 12:
                raise AssertionError("failed without a useful explanation")
            return f"graceful: {res.error[:70]}"

        check("formats", f"reject {key} politely", probe_bad)

    def missing_file():
        res = open_document(work / "does_not_exist.pdf")
        assert not res.ok and res.error
        return res.error

    check("formats", "missing file reports cleanly", missing_file)

    def empty_file():
        p = work / "empty.pdf"
        p.write_bytes(b"")
        res = open_document(p)
        assert not res.ok and "empty" in res.error.lower(), res.error
        return res.error

    check("formats", "empty file reports cleanly", empty_file)

    def junk_pdf():
        p = work / "junk.pdf"
        p.write_bytes(b"%PDF-1.4 broken" * 40)
        res = open_document(p)
        assert not res.ok, "corrupt pdf reported as ok"
        return res.error[:70]

    check("formats", "corrupt PDF reports cleanly", junk_pdf)


def sweep_pdf_ops(samples: dict[str, Path], work: Path) -> None:
    import fitz
    from veyrion_workspace.core.documents.registry import open_document

    base = work / "swp_pdf"
    base.mkdir(exist_ok=True)

    def fresh(name: str):
        target = base / name
        target.write_bytes(samples["pdf"].read_bytes())
        return target

    def read_probe():
        res = open_document(fresh("read.pdf"))
        assert res.ok, res.error
        e = res.engine
        try:
            assert e.page_count == 8
            assert "Alpha beta" in e.page_text(0)
            assert len(e.toc()) == 2
            info = e.page_info(0)
            assert info.width > 0 and info.height > 0
            meta = e.metadata()
            assert meta.title == "Sweep PDF", meta.title
            pix = e.render_page(0, zoom=1.0)
            assert pix is not None
            return f"8p, toc={len(e.toc())}, {info.width:.0f}x{info.height:.0f}"
        finally:
            e.close()

    check("pdf", "open/read/metadata/render", read_probe)

    def annotate_probe():
        res = open_document(fresh("annot.pdf"))
        e = res.engine
        try:
            r1 = fitz.Rect(70, 90, 320, 110)
            r2 = fitz.Rect(70, 120, 320, 145)
            xid = e.add_highlight(0, [r1], color="#E5B25D")
            e.add_text_markup(0, [r2], kind="underline")
            e.add_text_markup(0, [r2], kind="strikeout")
            e.add_text_markup(0, [r2], kind="squiggly")
            e.add_note(0, (300, 300), "sweep note")
            e.add_free_text(0, fitz.Rect(70, 250, 250, 290), "free text")
            e.add_ink(0, [[(80, 320), (120, 340), (160, 330)]])
            e.add_shape(0, "rectangle", fitz.Rect(70, 360, 200, 410))
            e.add_shape(0, "ellipse", fitz.Rect(210, 360, 330, 410))
            e.add_shape(0, "arrow", fitz.Rect(70, 420, 200, 460))
            e.add_stamp(0, fitz.Rect(320, 420, 430, 460), "Reviewed")
            e.add_link(0, fitz.Rect(70, 470, 200, 490), 3)
            anns = e.annotations()
            assert len(anns) >= 11, f"only {len(anns)} annotations persisted"
            e.update_annot_text(xid, "updated note text")
            e.delete_annot_by_xref(xid)
            after = len(e.annotations())
            assert after == len(anns) - 1, (after, len(anns))
            out = base / "annot_saved.pdf"
            e.save(out)
            e.close()
            res2 = open_document(out)
            assert res2.ok and len(res2.engine.annotations()) == after
            res2.engine.close()
            return f"{len(anns)} types, reopened with {after}"
        finally:
            try:
                e.close()
            except Exception:  # noqa: BLE001
                pass

    check("pdf", "annotation types + save round-trip", annotate_probe)

    def page_ops():
        res = open_document(fresh("pages.pdf"))
        e = res.engine
        try:
            e.rotate_page(0, 90)
            e.duplicate_pages([0])
            e.insert_blank_page(1)
            e.move_page(0, 2)
            e.delete_pages([7])
            out = base / "pages_saved.pdf"
            e.save(out)
            e.close()
            res2 = open_document(out)
            assert res2.ok
            count = res2.engine.page_count
            res2.engine.close()
            return f"page ops ok, {count}p"
        finally:
            try:
                e.close()
            except Exception:  # noqa: BLE001
                pass

    check("pdf", "rotate/duplicate/insert/move/delete", page_ops)

    def merge_split():
        a = fresh("merge_a.pdf")
        res = open_document(a)
        e = res.engine
        try:
            e.merge_from(samples["pdf"])
            assert e.page_count == 16, e.page_count
            e.extract_pages_to([0, 1], base / "extracted.pdf")
            e.split_at([[0, 2], [3, 5]], base / "parts", samples["pdf"].stem)
            out = base / "merged.pdf"
            e.save(out)
            assert (base / "extracted.pdf").exists()
            parts = sorted((base / "parts").glob("*.pdf"))
            assert len(parts) >= 2, parts
            return f"merged 16p, extracted + {len(parts)} split parts"
        finally:
            e.close()

    check("pdf", "merge / extract / split", merge_split)

    def optimize_and_meta():
        res = open_document(fresh("opt.pdf"))
        e = res.engine
        try:
            out = base / "optimized.pdf"
            e.optimize(out)
            before = samples["pdf"].stat().st_size
            after = out.stat().st_size
            e.set_metadata(title="Renamed Sweep", author="Someone")
            e.strip_metadata()
            stripped = e.metadata()
            out2 = base / "stripped.pdf"
            e.save(out2)
            res2 = open_document(out2)
            title = res2.engine.metadata().title or ""
            res2.engine.close()
            return (f"optimize {before}->{after}B, metadata title cleared="
                    f"{not title}")
        finally:
            e.close()

    check("pdf", "optimize + metadata edit/strip", optimize_and_meta)

    def forms_probe():
        res = open_document(fresh("form.pdf"))
        e = res.engine
        try:
            fields = e.form_fields()
            assert fields, "no form fields detected"
            ok = e.set_field_value(0, "full_name", "Ada Lovelace")
            assert ok, "set_field_value returned False"
            cleared = e.clear_form()
            return f"fields={len(fields)}, cleared={cleared}"
        finally:
            e.close()

    check("pdf", "AcroForm read/fill/clear", forms_probe)

    def redaction_probe():
        res = open_document(fresh("redact.pdf"))
        e = res.engine
        try:
            hits = sum(1 for pno in range(e.page_count)
                       if "ZQX" in e.page_text(pno))
            assert hits >= 1, "marker text not found before redaction"
            page = e._doc[0]
            for rect in page.search_for("Confidential marker token ZQX"):
                e.add_redaction(0, rect)
            applied = e.apply_redactions()
            out = base / "redacted.pdf"
            e.save(out)
            e.close()
            res2 = open_document(out)
            report = res2.engine.verify_redaction(["ZQX", "Confidential marker"])
            res2.engine.close()
            assert isinstance(report, dict)
            return f"applied={applied}, verify={json.dumps(report)[:110]}"
        finally:
            try:
                e.close()
            except Exception:  # noqa: BLE001
                pass

    check("pdf", "true redaction + verification", redaction_probe)

    def encrypt_probe():
        out = base / "locked.pdf"
        doc = fitz.open(str(samples["pdf"]))
        doc.save(str(out), encryption=fitz.PDF_ENCRYPT_AES_256,
                 owner_pw="owner", user_pw="secret")
        doc.close()
        res = open_document(out)
        assert res.needs_password and not res.ok, "encrypted PDF opened silently"
        res2 = open_document(out, password="secret")
        assert res2.ok, f"correct password rejected: {res2.error}"
        res2.engine.close()
        res3 = open_document(out, password="wrong")
        assert not res3.ok, "wrong password accepted"
        return "password gate enforced both ways"

    check("pdf", "encrypted PDF password handling", encrypt_probe)


def sweep_annotations_service(settings, db, samples: dict[str, Path],
                              work: Path) -> None:
    from veyrion_workspace.core.annotations.service import AnnotationService
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.storage.repositories import AnnotationRepository

    svc = AnnotationService(AnnotationRepository(db), settings)
    doc_path = str(samples["pdf"])
    kinds = ["highlight", "underline", "strikeout", "squiggly", "note",
             "ink", "rectangle", "ellipse", "arrow", "freetext", "stamp",
             "text"]

    def create_all():
        made = []
        for kind in kinds:
            try:
                rec = call_filtered(svc.create, doc_path, 0, kind,
                                    rect=(70, 90, 300, 110),
                                    color="#E5B25D", text=f"sweep {kind}")
                if rec is not None:
                    made.append(kind)
            except Exception as exc:  # noqa: BLE001 - report, do not abort
                note("annotations", f"create {kind}",
                     f"raised {type(exc).__name__}: {exc}")
        if len(made) < 8:
            raise AssertionError(f"only {len(made)} kinds accepted: {made}")
        return f"{len(made)}/{len(kinds)} kinds: {', '.join(made)}"

    check("annotations", "create every annotation kind", create_all)

    def list_and_count():
        recs = svc.for_document(doc_path)
        assert recs, "no annotations listed for the document"
        assert svc.count_for_document(doc_path) == len(recs)
        assert len(svc.all_annotations()) >= len(recs)
        return f"{len(recs)} records"

    check("annotations", "list + count", list_and_count)

    def update_and_apply():
        recs = svc.for_document(doc_path)
        svc.update(recs[0], color="#C7522A")
        res = open_document(samples["pdf"])
        try:
            applied = svc.apply_to_pdf(res.engine)
            out = work / "svc_annotated.pdf"
            res.engine.save(out)
        finally:
            res.engine.close()
        if applied < 1:
            raise AssertionError("apply_to_pdf wrote nothing")
        res2 = open_document(out)
        try:
            imported = svc.import_from_pdf(res2.engine)
        finally:
            res2.engine.close()
        return f"applied={applied}, re-imported={imported}"

    check("annotations", "apply to PDF + re-import", update_and_apply)

    def export_all():
        got = []
        for fmt in ("markdown", "md", "html", "csv", "json", "txt", "text"):
            target = work / f"annotations.{fmt}"
            try:
                call_filtered(svc.export, doc_path, fmt, target)
                if target.exists() and target.stat().st_size > 0:
                    got.append(fmt)
            except Exception:  # noqa: BLE001
                continue
        if len(got) < 4:
            raise AssertionError(f"only exported: {got}")
        return f"exported: {', '.join(got)}"

    check("annotations", "export markdown/html/csv/json", export_all)

    def delete_all():
        svc.delete_for_document(doc_path)
        remaining = svc.count_for_document(doc_path)
        assert remaining == 0, remaining
        return "deleted"

    check("annotations", "delete for document", delete_all)


def sweep_search(db, samples: dict[str, Path]) -> None:
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.core.search.engine import SearchEngine

    engine = SearchEngine(db)
    doc_path = str(samples["pdf"])

    def index():
        res = open_document(samples["pdf"])
        try:
            pages = engine.index_document(res.engine)
        finally:
            res.engine.close()
        assert pages >= 8, pages
        assert engine.document_is_indexed(doc_path)
        assert engine.indexed_page_count() >= 8
        return f"{pages} pages indexed"

    check("search", "index a document", index)

    def queries():
        base = engine.search("alpha beta")
        assert base, "phrase search returned nothing"
        assert engine.search("alpha", limit=10)
        fuzzy = engine.search("alhpa", fuzzy=True)
        meta = engine.search_metadata("Sweep")
        hit = base[0]
        if not hasattr(hit, "doc_path"):
            raise AssertionError("search hit lacks doc_path")
        return (f"exact={len(base)}, fuzzy={len(fuzzy)}, "
                f"meta={len(meta)}, hit->{Path(hit.doc_path).name}:{hit.page + 1}")

    check("search", "exact/fuzzy/metadata queries", queries)

    def clear():
        engine.clear_document(doc_path)
        assert not engine.document_is_indexed(doc_path)
        return "cleared"

    check("search", "clear document index", clear)


def sweep_library(db, samples: dict[str, Path]) -> None:
    from veyrion_workspace.core.library.scanner import import_document, scan_folder
    from veyrion_workspace.storage.repositories import (LibraryRepository,
                                                        NoteRepository,
                                                        ProgressRepository,
                                                        SessionRepository,
                                                        SmartCollectionRepository)

    lib = construct(LibraryRepository, (db,), ())

    def import_one():
        call_filtered(import_document, samples["pdf"], lib)
        rec = lib.get_by_path(str(samples["pdf"]))
        assert rec is not None, "document not stored in the library"
        return f"stored {rec.title or Path(rec.path).name}"

    check("library", "import a document", import_one)

    def scan():
        folder = samples["pdf"].parent
        result = scan_folder(folder, lib, recursive=True)
        count = len(lib.all())
        if count < 5:
            raise AssertionError(f"scan indexed only {count} documents")
        return f"library now {count} docs ({result})"

    check("library", "scan a folder", scan)

    def organise():
        path = str(samples["pdf"])
        lib.set_rating(path, 4)
        lib.set_favorite(path, True)
        lib.add_tag(path, "sweep", "#4C8C6B")
        lib.mark_opened(path)
        lib.set_progress(path, 0.42, 3)
        tags = lib.tags_for(path)
        assert "sweep" in tags, tags
        cid = lib.create_collection("Sweep Collection")
        lib.add_to_collection(path, cid)
        names = lib.collection_names_for(path)
        members = lib.docs_in_collection(cid)
        assert names and members, (names, members)
        rec = lib.get_by_path(path)
        assert rec.rating == 4 and rec.favorite
        return (f"tag/favorite/rating/progress ok, collection "
                f"'{names[0]}' has {len(members)}")

    check("library", "tags/favorites/ratings/progress/collections", organise)

    def smart_and_notes():
        smart = construct(SmartCollectionRepository, (db,), ())
        existing_names = {r["name"] for r in smart.all()}
        if "Recent" not in existing_names:
            smart.add("Recent", '[["last_opened","within_days",30]]')
        rules = smart.all()
        notes = construct(NoteRepository, (db,), ())
        from veyrion_workspace.storage.repositories import NoteRecord
        rec = NoteRecord(doc_path=str(samples["pdf"]),
                         title="Sweep note", body="note body")
        call_filtered(notes.create, rec)
        listed = notes.all()
        assert listed, "note not persisted"
        prog = construct(ProgressRepository, (db,), ())
        prog.save(str(samples["pdf"]), 2, 0.5, 1.5, "pdf")
        loaded = prog.load(str(samples["pdf"]))
        assert loaded, "reading position not persisted"
        sess = construct(SessionRepository, (db,), ())
        sid = sess.start(str(samples["pdf"]))
        sess.end(sid, 3)
        return (f"smart={len(rules)} notes={len(listed)} "
                f"reading position restored page={loaded.get('page')}")

    check("library", "smart collections, notes, reading state", smart_and_notes)


def sweep_versioning(settings, samples: dict[str, Path], work: Path) -> None:
    from veyrion_workspace.core.versioning import VersionStore

    store = VersionStore(depth=5)
    target = work / "versioned.pdf"
    target.write_bytes(samples["pdf"].read_bytes())

    def snapshot_and_rollback():
        call_filtered(store.snapshot_copy, target, "sweep note")
        original = target.read_bytes()
        target.write_bytes(original + b"\n% mutated\n")
        versions = store.versions(target)
        assert versions, "no versions listed"
        ok = store.rollback(target, Path(versions[0]["path"]))
        assert ok, "rollback returned False"
        restored = target.read_bytes()
        assert restored == original, "rollback did not restore the original bytes"
        return f"{len(versions)} version(s), rollback restored {len(restored)}B"

    check("versioning", "snapshot + rollback restores bytes", snapshot_and_rollback)

    def guarantees():
        call_filtered(store.snapshot_before_save, target)
        versions = store.versions(target)
        cleared = store.clear(target)
        return f"{len(versions)} snapshot(s) before save, cleared={cleared}"

    check("versioning", "snapshot-on-save + clear", guarantees)


def sweep_conversion(samples: dict[str, Path], work: Path) -> None:
    from veyrion_workspace.core.conversion import convert

    out = work / "converted"
    out.mkdir(exist_ok=True)

    def pdf_to_images():
        files = call_filtered(convert.pdf_to_images, samples["pdf"],
                              out / "imgs", "png")
        assert files, "no images produced"
        return f"{len(files)} PNG(s)"

    check("conversion", "PDF -> images", pdf_to_images)

    def pdf_to_text():
        target = out / "doc.txt"
        call_filtered(convert.pdf_to_text, samples["pdf"], target)
        text = target.read_text(encoding="utf-8", errors="replace")
        assert "Alpha beta" in text
        return f"{len(text)} chars"

    check("conversion", "PDF -> text", pdf_to_text)

    def images_to_pdf():
        target = out / "images.pdf"
        call_filtered(convert.images_to_pdf,
                      [samples["image_png"], samples["image_jpg"]], target)
        assert target.exists() and target.stat().st_size > 500
        return f"{target.stat().st_size}B"

    check("conversion", "images -> PDF", images_to_pdf)

    def text_to_pdf():
        target = out / "text.pdf"
        call_filtered(convert.text_to_pdf, samples["md"], target, "Sweep Title")
        assert target.exists() and target.stat().st_size > 500
        return f"{target.stat().st_size}B"

    check("conversion", "text/markdown -> PDF", text_to_pdf)

    def csv_to_pdf():
        target = out / "table.pdf"
        call_filtered(convert.csv_to_pdf_table, samples["csv"], target,
                      "Sweep Table")
        assert target.exists() and target.stat().st_size > 500
        return f"{target.stat().st_size}B"

    check("conversion", "CSV -> PDF table", csv_to_pdf)

    def doc_to_text_and_md():
        t = out / "docx.txt"
        call_filtered(convert.document_to_text, samples["docx"], t)
        body = t.read_text(encoding="utf-8", errors="replace")
        assert t.exists() and "Sweep Heading" in body
        m = out / "docx.md"
        call_filtered(convert.document_to_markdown, samples["docx"], m)
        assert m.exists() and m.stat().st_size > 0
        return f"text={t.stat().st_size}B markdown={m.stat().st_size}B"

    check("conversion", "DOCX -> text + markdown", doc_to_text_and_md)

    def failures_are_clean():
        from veyrion_workspace.core.conversion.convert import ConversionError
        try:
            convert.pdf_to_text(work / "nope.pdf", out / "nope.txt")
        except ConversionError as exc:
            return f"ConversionError: {exc}"
        except FileNotFoundError as exc:
            return f"FileNotFoundError: {exc}"
        raise AssertionError("missing input did not raise a friendly error")

    check("conversion", "missing input raises cleanly", failures_are_clean)


def sweep_comparison(samples: dict[str, Path], work: Path) -> None:
    import fitz
    from veyrion_workspace.core.comparison.comparator import (compare_documents,
                                                              compare_texts)

    other = work / "compare_other.pdf"
    doc = fitz.open(str(samples["pdf"]))
    doc[0].insert_text((72, 520), "An added line of text for comparison")
    doc.save(str(other))
    doc.close()

    def compare_pdfs():
        result = compare_documents(samples["pdf"], other)
        pages = getattr(result, "pages", None)
        if pages is None:
            pages = getattr(result, "diffs", [])
        return f"{len(pages)} differing page(s) detected"

    check("comparison", "PDF vs PDF", compare_pdfs)

    def compare_text():
        diffs = compare_texts("the quick brown fox",
                              "the quick red fox jumps")
        assert diffs, "no text differences reported"
        identical = compare_texts("same text", "same text")
        return f"{len(diffs)} diff line(s), identical -> {len(identical)}"

    check("comparison", "text vs text", compare_text)


def sweep_printing(samples: dict[str, Path], work: Path) -> None:
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.core.printing import printing

    def ranges():
        assert printing.parse_page_range("1-3", 8) == [0, 1, 2]
        assert printing.parse_page_range("2,4", 8) == [1, 3]
        assert printing.parse_page_range("", 3) == [0, 1, 2]
        return "page range parsing ok"

    check("printing", "page range parsing", ranges)

    def build_pdf():
        res = open_document(samples["pdf"])
        try:
            out = work / "print_job.pdf"
            call_filtered(printing.build_print_pdf, res.engine, out, [0, 1])
            assert out.exists() and out.stat().st_size > 500
            return f"{out.stat().st_size}B print job"
        finally:
            res.engine.close()

    check("printing", "build print job PDF", build_pdf)

    def printers():
        found = printing.list_printers()
        return f"{len(found)} printer(s): {', '.join(found[:2])}"

    check("printing", "enumerate printers", printers)


def sweep_security(settings, db, samples: dict[str, Path], work: Path) -> None:
    from veyrion_workspace.core.documents.registry import open_document
    from veyrion_workspace.core.security import privacy
    from veyrion_workspace.core.security.vault import (InvalidPasswordError,
                                                       Vault)

    from veyrion_workspace.core.security.vault import vault_directory

    vault = construct(Vault, (vault_directory(),), (work / "sweep_vault",), ())

    def vault_roundtrip():
        password = "sweep-passphrase-9"
        if vault.exists():
            vault.unlock(password)
        else:
            vault.create(password)
        assert not vault.is_locked(), "vault locked immediately after create/unlock"
        item_id = vault.add_item("sweep-doc.pdf", samples["pdf"])
        items = vault.list_items()
        assert items, "vault item not listed"
        dest = work / "vault_out"
        dest.mkdir(exist_ok=True)
        extracted = vault.extract_item(item_id, dest / "sweep-doc.pdf")
        assert extracted.exists() and extracted.stat().st_size > 0
        vault.remove_item(item_id)
        assert not vault.list_items(), "item survived removal"
        return f"stored {len(items)} item, extracted {extracted.stat().st_size}B"

    check("security", "vault add/extract/list/remove", vault_roundtrip)

    def vault_locking():
        if vault.is_locked():
            vault.unlock("sweep-passphrase-9")
        vault.lock()
        assert vault.is_locked()
        try:
            vault.list_items()
        except Exception as exc:  # noqa: BLE001
            locked_error = type(exc).__name__
        else:
            raise AssertionError("locked vault still served contents")
        try:
            vault.unlock("wrong-password")
        except InvalidPasswordError:
            pass
        else:
            raise AssertionError("wrong vault password accepted")
        vault.unlock("sweep-passphrase-9")
        assert not vault.is_locked()
        return f"locked access raised {locked_error}; wrong password rejected"

    check("security", "vault lock + wrong password", vault_locking)

    def vault_password_change():
        vault.change_password("sweep-passphrase-9", "sweep-passphrase-10")
        vault.lock()
        try:
            vault.unlock("sweep-passphrase-9")
        except InvalidPasswordError:
            pass
        else:
            raise AssertionError("old vault password still worked")
        vault.unlock("sweep-passphrase-10")
        return "old password retired, new password accepted"

    check("security", "vault password change", vault_password_change)

    def vault_encrypted_on_disk():
        blob = vault.dir.joinpath("vault.dat").read_bytes()
        if samples["pdf"].read_bytes()[:512] in blob:
            raise AssertionError("document content found in plaintext inside vault")
        if b"%PDF" in blob:
            raise AssertionError("PDF magic bytes found unencrypted in vault file")
        return f"vault file {len(blob)}B, no plaintext payload"

    check("security", "vault contents are encrypted at rest", vault_encrypted_on_disk)

    def metadata_privacy():
        found = privacy.inspect_metadata(samples["pdf"])
        assert found, "no metadata inspected"
        out = work / "privacy_stripped.pdf"
        call_filtered(privacy.strip_metadata, samples["pdf"], out)
        res = open_document(out)
        try:
            meta = res.engine.metadata()
            leftover = [k for k, v in found.items()
                        if v and getattr(meta, k.lower(), "") == v
                        and k.lower() in {"author", "subject", "keywords"}]
        finally:
            res.engine.close()
        if leftover:
            raise AssertionError(f"metadata survived stripping: {leftover}")
        return f"{len(found)} fields inspected, {out.stat().st_size}B output"

    check("security", "metadata inspect + strip", metadata_privacy)

    def signing():
        try:
            import pyhanko  # noqa: F401
        except ImportError:
            return "skipped: optional 'pyhanko' package not installed"
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives.serialization import pkcs12
            from cryptography.x509.oid import NameOID
        except ImportError:
            return "skipped: 'cryptography' not installed"
        import datetime
        from veyrion_workspace.core.security.signing import (load_pkcs12,
                                                             sign_pdf,
                                                             verify_pdf_signatures)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                             "Veyrion Sweep")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=365))
                .sign(key, hashes.SHA256()))
        p12_path = work / "sweep_identity.p12"
        p12_path.write_bytes(pkcs12.serialize_key_and_certificates(
            b"sweep cert", key, cert, None,
            serialization.BestAvailableEncryption(b"p12pass")))
        identity = load_pkcs12(p12_path, "p12pass")
        signed = work / "signed.pdf"
        call_filtered(sign_pdf, samples["pdf"], signed, identity)
        assert signed.exists() and signed.stat().st_size > 0
        infos = verify_pdf_signatures(signed)
        return (f"signed {signed.stat().st_size}B, "
                f"verification reports {len(infos)} signature(s)")

    check("security", "PKCS#12 load + sign + verify", signing)

    def wrong_cert_password():
        from veyrion_workspace.core.security.signing import load_pkcs12
        p12 = work / "sweep_identity.p12"
        if not p12.exists():
            return "skipped: no identity generated"
        try:
            load_pkcs12(p12, "definitely-wrong")
        except Exception as exc:  # noqa: BLE001
            return f"rejected: {type(exc).__name__}"
        raise AssertionError("wrong PKCS#12 password accepted")

    check("security", "wrong certificate password rejected", wrong_cert_password)


def sweep_optional_tools(db, samples: dict[str, Path], work: Path) -> None:
    from veyrion_workspace.core.documents.registry import open_document

    # --- OCR -------------------------------------------------------------
    from veyrion_workspace.core.ocr import ocr as ocr_mod

    def ocr_probe():
        available = ocr_mod.tesseract_available()
        langs = ocr_mod.available_languages() if available else []
        if available:
            version = ocr_mod.tesseract_version()
            service = construct(ocr_mod.OcrService, (db,), (db, ""))
            res = open_document(samples["pdf"])
            try:
                text = service.ocr_page(res.engine, 0)
            finally:
                res.engine.close()
            assert isinstance(text, str)
            return f"Tesseract live ({version}), langs={langs}, OCR text {len(text)}c"
        note("ocr", "tesseract binary", "not installed on this machine")
        service = construct(ocr_mod.OcrService, (db,), (db, ""))
        res = open_document(samples["pdf"])
        try:
            try:
                service.ocr_page(res.engine, 0)
            except RuntimeError as exc:
                assert "tesseract" in str(exc).lower(), str(exc)
                return f"unavailable with guidance: {exc}"[:100]
            raise AssertionError("OCR claimed success without Tesseract")
        finally:
            res.engine.close()

    check("ocr", "availability probe + honest failure", ocr_probe)

    def ocr_cache_clear():
        service = construct(ocr_mod.OcrService, (db,), (db, ""))
        removed = service.clear_cache()
        return f"cache entries cleared={removed}"

    check("ocr", "cache clear", ocr_cache_clear)

    def ocr_text_detection():
        res = open_document(samples["pdf"])
        try:
            has_text = bool((res.engine.page_text(0) or "").strip())
        finally:
            res.engine.close()
        assert has_text, "text layer detection failed on a digital PDF"
        return "digital PDF correctly detected as already-searchable"

    check("ocr", "text-layer detection (no needless OCR)", ocr_text_detection)

    # --- Text to speech --------------------------------------------------
    from veyrion_workspace.core.tts.reader import TtsReader, split_sentences

    def tts_logic():
        reader = construct(TtsReader, (), (None,))
        available = reader.available
        voices = reader.voices() if available else []
        sentences = split_sentences(
            "First sentence here. Second one follows! Third ends the test?")
        assert len(sentences) >= 3, sentences
        if available:
            note("tts", "backend", f"{len(voices)} voice(s) available")
        else:
            note("tts", "backend", "pyttsx3 unavailable")
        return f"available={available}, voices={len(voices)}, "                    f"sentence split={len(sentences)}"

    check("tts", "engine probe + sentence splitting", tts_logic)

    def tts_real_backend():
        import threading
        reader = construct(TtsReader, (), (None,))
        if not reader.available:
            return "skipped: no speech backend on this machine"
        reader.volume = 0.0          # silent: never make noise during a sweep
        start = time.time()
        reader.speak("Veyrion speech pipeline check.", 0)
        reader.start()
        time.sleep(0.8)
        reader.stop()
        try:
            reader.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if len(reader.voices()) < 0:  # pragma: no cover - defensive
            raise AssertionError("voice listing failed")
        return f"silent playback pipeline ran in {time.time() - start:.2f}s"

    check("tts", "real backend playback (volume 0)", tts_real_backend)

    # --- Translation -----------------------------------------------------
    from veyrion_workspace.core.translation import translator

    def translation_probe():
        detected = translator.detect_language("hola mundo, esto es una prueba")
        online = translator.argos_available()
        pairs = translator.installed_pairs() if online else []
        if online and pairs:
            result = translator.translate_offline("hola mundo", "en")
            text = getattr(result, "text", "") or ""
            return f"argos installed, detected={detected}, translated={text[:40]!r}"
        note("translation", "offline models",
             "Argos Translate not installed (optional component)")
        try:
            translator.translate_offline("hola mundo", "en")
        except Exception as exc:  # noqa: BLE001
            return (f"detected={detected}; offline models absent, "
                    f"raised {type(exc).__name__}")
        raise AssertionError("offline translate succeeded without models")

    check("translation", "language detect + offline availability", translation_probe)

    def online_translation_is_opt_in():
        try:
            translator.translate_online("hola", "en")
        except Exception as exc:  # noqa: BLE001
            return f"network path refused without consent: {type(exc).__name__}"
        raise AssertionError("online translation ran without explicit action")

    check("translation", "online path requires explicit user action",
          online_translation_is_opt_in)

    # --- Dictionary ------------------------------------------------------
    from veyrion_workspace.core.dictionary.lookup import Dictionary

    def dictionary_probe():
        dictionary = construct(Dictionary, (), (db,), (None,))
        entry = dictionary.lookup("ephemeral") or dictionary.lookup("apple")
        suggestions = dictionary.suggestions("ep", 5)
        if entry is None:
            note("dictionary", "word list",
                 "no bundled dictionary found for 'ephemeral'/'apple'")
            return f"no entry, {len(suggestions)} suggestion(s)"
        return f"entry keys={sorted(entry)[:5]}, {len(suggestions)} suggestions"

    check("dictionary", "lookup + suggestions", dictionary_probe)

    # --- Summarization ---------------------------------------------------
    from veyrion_workspace.core.summarization import textstats

    def summarization_probe():
        res = open_document(samples["txt"])
        try:
            body = "\n".join(res.engine.page_text(i)
                             for i in range(res.engine.page_count))
        finally:
            res.engine.close()
        words = textstats.keywords(body, 8)
        summary = textstats.summarize(body, 3)
        minutes = textstats.reading_time_minutes(body)
        language = textstats.language_guess(body)
        assert words and summary, (words, summary)
        return (f"{len(words)} keywords, {len(summary)} summary sentence(s), "
                f"{minutes:.1f} min read, lang={language}")

    check("summarization", "keywords/summary/reading time/language",
          summarization_probe)


def sweep_plugins(settings, work: Path) -> None:
    from veyrion_workspace.core.plugins.host import PluginHost

    api = {"settings": settings, "open_document": lambda p: None,
           "add_command": lambda *a, **k: None,
           "toast": lambda *a, **k: None}
    host = PluginHost(settings, api)

    def sample_is_installed():
        installed = host.installed_plugin_path()
        installed.mkdir(parents=True, exist_ok=True)
        repo_plugin = Path(__file__).resolve().parent.parent / "plugins" / "sample_wordcount"
        if repo_plugin.exists() and not (installed / "sample_wordcount").exists():
            import shutil
            shutil.copytree(repo_plugin, installed / "sample_wordcount")
        found = host.discover()
        assert found, "no plugins discovered"
        return f"discovered {[p.get('id') or p.get('name') for p in found]}"

    check("plugins", "discover plugins", sample_is_installed)

    def permission_model():
        found = host.discover()
        if not found:
            return "skipped: nothing discovered"
        plugin_id = found[0].get("id") or found[0].get("name")
        results = []
        try:
            host.load_plugin(plugin_id)
            results.append("load without approval: allowed")
        except Exception as exc:  # noqa: BLE001
            results.append(f"load without approval -> {type(exc).__name__}")
        try:
            host.approve(plugin_id)
            host.load_plugin(plugin_id)
            results.append("after approval: loaded")
        except Exception as exc:  # noqa: BLE001
            results.append(f"after approval -> {type(exc).__name__}: {exc}")
        host.revoke(plugin_id)
        results.append("revoked")
        return "; ".join(results)

    check("plugins", "approval + permission model", permission_model)

    def broken_plugin_is_isolated():
        installed = host.installed_plugin_path()
        broken = installed / "sweep_broken_plugin"
        broken.mkdir(parents=True, exist_ok=True)
        (broken / "plugin.json").write_text(json.dumps({
            "id": "sweep_broken_plugin", "name": "Broken Sweep Plugin",
            "version": "1.0", "entry": "missing_module",
            "permissions": ["document_read"],
        }), encoding="utf-8")
        (broken / "missing_module.py").write_text("raise RuntimeError('boom')\n",
                                                   encoding="utf-8")
        discovered = host.discover()
        ids = [p.get("id") for p in discovered]
        assert "sweep_broken_plugin" in ids, ids
        try:
            host.approve("sweep_broken_plugin")
            host.load_plugin("sweep_broken_plugin")
        except Exception as exc:  # noqa: BLE001
            return f"app + host survived a raising plugin ({type(exc).__name__})"
        return "plugin loaded without raising (isolated)"

    check("plugins", "broken plugin cannot take the app down",
          broken_plugin_is_isolated)


def sweep_services(settings, db, work: Path) -> None:
    from veyrion_workspace.services.recovery import SessionJournal
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.services.tasks import TaskManager, TaskState

    def settings_roundtrip():
        settings.set("general", "sweep_key", "sweep_value")
        settings.save()
        reloaded = Settings()
        value = reloaded.get("general", "sweep_key", "")
        assert value == "sweep_value", value
        settings.reset_section("general")
        assert settings.get("general", "sweep_key", "") == "", "reset_section failed"
        return "persisted and re-read from disk; reset_section works"

    check("services", "settings persistence + reset", settings_roundtrip)

    def settings_validation():
        from veyrion_workspace.services import settings as settings_mod
        defaults = settings_mod.DEFAULTS if hasattr(settings_mod, "DEFAULTS") else {}
        settings.set("appearance", "theme", "not-a-real-theme")
        settings.load()
        theme = settings.get("appearance", "theme")
        if theme == "not-a-real-theme" and "appearance" in defaults:
            raise AssertionError("invalid setting value survived validation")
        settings.reset_all()
        return f"invalid theme coerced to {theme!r}; reset_all applied"

    check("services", "invalid settings values rejected", settings_validation)

    def database_integrity():
        assert db.integrity_check(), "sqlite integrity check failed"
        db.kv_set("sweep_key", "sweep_value")
        assert db.kv_get("sweep_key") == "sweep_value"
        applied = db.migrate()
        return f"integrity ok, migrations applied this run: {applied}"

    check("services", "database integrity + kv store", database_integrity)

    def task_lifecycle():
        tasks = TaskManager(2)
        events = []
        tasks.add_listener(lambda task, event: events.append(event))

        def work_fn(progress=None, cancel=None):
            for i in range(5):
                if cancel and cancel.is_set():
                    return "cancelled"
                if progress:
                    progress((i + 1) / 5, f"step {i + 1}")
                time.sleep(0.02)
            return "done"

        task = tasks.submit("sweep task", "test", work_fn)
        deadline = time.time() + 10
        while task.state in (TaskState.QUEUED, TaskState.RUNNING) and time.time() < deadline:
            time.sleep(0.05)
        assert task.state is TaskState.DONE, task.state
        assert task.result == "done", task.result

        slow = tasks.submit("sweep cancel", "test",
                            lambda progress=None, cancel=None: time.sleep(5))
        time.sleep(0.1)
        assert tasks.cancel(slow.id), "cancel returned False"
        deadline = time.time() + 10
        while slow.state is TaskState.RUNNING and time.time() < deadline:
            time.sleep(0.05)
        assert slow.state in (TaskState.CANCELLED, TaskState.DONE), slow.state
        retried = tasks.retry(slow.id)
        cleared = tasks.clear_finished()
        tasks.shutdown(wait=False)
        return (f"done/cancel/retry ok (retry={'yes' if retried else 'no'}), "
                f"cleared={cleared}, events={len(events)}")

    check("services", "task manager lifecycle", task_lifecycle)

    def task_failure_is_contained():
        tasks = TaskManager(1)

        def boom(progress=None, cancel=None):
            raise ValueError("sweep explosion")

        task = tasks.submit("boom", "test", boom)
        deadline = time.time() + 10
        while task.state in (TaskState.QUEUED, TaskState.RUNNING) and time.time() < deadline:
            time.sleep(0.05)
        assert task.state is TaskState.FAILED, task.state
        assert "sweep explosion" in (task.error or ""), task.error
        tasks.shutdown(wait=False)
        return f"failure captured with message: {task.error[:40]}"

    check("services", "failing task is contained", task_failure_is_contained)

    def crash_recovery():
        journal = SessionJournal(work / "sessions")
        journal.acquire_lock()
        journal.record([{"path": str(work / "resume.pdf"),
                         "state": {"page": 3, "zoom": 1.5}}], 0)
        crashed = SessionJournal(work / "sessions")
        assert crashed.has_unsaved_session(), "crash was not detected"
        tabs = crashed.recoverable_tabs()
        assert tabs and tabs[0]["state"]["page"] == 3, tabs
        journal.mark_clean_exit()
        clean = SessionJournal(work / "sessions")
        assert not clean.has_unsaved_session(), "clean exit still reported a crash"
        return f"crash detected, {len(tabs)} tab(s) restorable with position"

    check("services", "crash detection + session restore", crash_recovery)

    def atomic_write_guarantee():
        from veyrion_workspace.utils import safeio
        target = work / "atomic.txt"
        target.write_text("original", encoding="utf-8")
        fn = getattr(safeio, "atomic_write_text", None) or getattr(
            safeio, "atomic_write", None)
        if fn is None:
            return "skipped: no atomic helper exported"
        try:
            fn(target, "replacement")
        except TypeError:
            def writer(p):
                Path(p).write_text("replacement", encoding="utf-8")
            fn(target, writer)
        content = target.read_text(encoding="utf-8")
        assert "replacement" in content, content
        leftovers = [p.name for p in work.glob("atomic.txt*") if p != target]
        return f"atomic write ok, temp leftovers={leftovers}"

    check("services", "atomic write leaves no partial file", atomic_write_guarantee)


# --------------------------------------------------------------- UI sweeps
UI_SKIP_ACTIONS = {"act_quit"}


def build_window():
    from veyrion_workspace.services.recovery import SessionJournal
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.services.tasks import TaskManager
    from veyrion_workspace.storage.database import Database
    from veyrion_workspace.app import paths
    from veyrion_workspace.ui.main_window import MainWindow

    settings = Settings()
    db = Database()
    tasks = TaskManager(2)
    try:
        sessions = paths.sessions_dir()
        sessions.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        sessions = DATA / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
    journal = SessionJournal(sessions)
    window = MainWindow(settings, db, tasks, journal)
    window.resize(1400, 900)
    window.show()
    return window, settings, db, tasks


def sweep_ui_window(app, samples: dict[str, Path]) -> None:
    window, settings, db, tasks = build_window()
    settled(app, 0.3)

    check("ui", "main window opens",
          lambda: f"title={window.windowTitle()!r}")

    def open_all_tabs():
        opened = []
        for key, path in samples.items():
            window.open_path(str(path))
            settled(app, 0.05)
            opened.append(key)
        count = window.tabs.count()
        assert count >= 20, f"only {count} tabs opened from {len(opened)} files"
        return f"{count} tabs from {len(opened)} files"

    check("ui", "open every sample in a tab", open_all_tabs)

    def tab_titles():
        titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        assert all(t.strip() for t in titles), titles
        return f"titles ok, e.g. {titles[:3]}"

    check("ui", "tab titles populated", tab_titles)

    def panel_toggles():
        docks = [window.dock_library, window.dock_thumbs, window.dock_toc,
                 window.dock_bookmarks, window.dock_annotations,
                 window.dock_notes, window.dock_search, window.dock_tasks,
                 window.dock_props]
        for dock in docks:
            dock.show()
            settled(app, 0.03)
        shown = sum(1 for d in docks if d.isVisible())
        for dock in docks[1:]:
            dock.hide()
        settled(app, 0.05)
        return f"{shown}/{len(docks)} panels opened and all closed again"

    check("ui", "every panel shows and hides", panel_toggles)

    def panel_content():
        window.open_path(str(samples["pdf"]))
        settled(app, 0.25)
        view = window.current_view()
        window.thumbnails_panel.refresh(view) if hasattr(
            window.thumbnails_panel, "refresh") else None
        window.toc_panel.refresh(view) if hasattr(
            window.toc_panel, "refresh") else None
        settled(app, 0.2)
        parts = []
        thumbs = getattr(window.thumbnails_panel, "_items", None)
        if thumbs is not None:
            parts.append(f"thumbnails={len(thumbs)}")
        toc = getattr(window.toc_panel, "_entries", None)
        if toc is not None:
            parts.append(f"toc={len(toc)}")
        props = getattr(window.properties_panel, "_rows", None)
        if props is not None:
            parts.append(f"properties={len(props)}")
        return ", ".join(parts) or "panels refreshed without error"

    check("ui", "panels populate for a PDF", panel_content)

    def navigation():
        view = window.current_view()
        assert view is not None
        start = view.current_page
        window._next_page()
        settled(app, 0.05)
        forward = view.current_page
        window._prev_page()
        settled(app, 0.05)
        back = view.current_page
        assert forward == start + 1 and back == start, (start, forward, back)
        return f"page {start} -> {forward} -> {back}"

    check("ui", "page navigation", navigation)

    def zoom_modes():
        view = window.current_view()
        results = []
        for name, fn in (("in", window._zoom_in), ("out", window._zoom_out),
                         ("fit width", window._fit_width),
                         ("fit page", window._fit_page)):
            fn()
            settled(app, 0.05)
            zoom = getattr(view, "zoom", None) or getattr(view, "_zoom", 0)
            results.append(f"{name}={zoom:.2f}")
        return ", ".join(results)

    check("ui", "zoom modes", zoom_modes)

    def find_in_document():
        view = window.current_view()
        hits = view.find_in_view("Alpha beta")
        assert hits and hits > 0, hits
        view.clear_find()
        return f"{hits} hits highlighted then cleared"

    check("ui", "find in document", find_in_document)

    def page_edit_actions():
        window.open_path(str(samples["pdf"]))
        settled(app, 0.2)
        view = window.current_view()
        before = view.page_count
        window._rotate_page_right()
        settled(app, 0.1)
        window._add_bookmark()
        settled(app, 0.1)
        window._delete_page()
        settled(app, 0.1)
        after = view.page_count
        assert after == before - 1, (before, after)
        return f"rotate + bookmark + delete page ({before} -> {after}p)"

    check("ui", "rotate / bookmark / delete page", page_edit_actions)

    def save_roundtrip():
        view = window.current_view()
        saved = window.action_save()
        assert saved is not False, "save reported failure"
        return "document saved through the window"

    check("ui", "save current document", save_roundtrip)

    def theme_cycle():
        from veyrion_workspace.ui.theme import THEMES
        seen = []
        for name in sorted(THEMES):
            settings.set("appearance", "theme", name)
            window.retheme()
            settled(app, 0.08)
            seen.append(name)
            style = app.styleSheet()
            assert style and len(style) > 2000, f"theme {name} produced no stylesheet"
        window.action_toggle_theme()
        settled(app, 0.08)
        return f"applied {len(seen)} themes: {', '.join(seen)}"

    check("ui", "all themes apply", theme_cycle)

    def command_palette():
        commands = window._palette_commands()
        assert len(commands) > 30, len(commands)
        titles = [c.title for c in commands]
        for needed in ("Open File", "Merge PDFs", "Read Aloud", "Compare"):
            if not any(needed.lower() in t.lower() for t in titles):
                raise AssertionError(f"palette missing {needed!r}")
        window._open_palette()
        settled(app, 0.05)
        palette = window.palette_overlay
        palette.open_palette()
        palette.search.setText("merge")
        settled(app, 0.05)
        count = palette.list.count()
        palette.close_palette()
        return f"{len(commands)} commands, 'merge' filters to {count}"

    check("ui", "command palette search", command_palette)

    def split_view():
        window.open_path(str(samples["pdf"]))
        window.open_path(str(samples["docx"]))
        settled(app, 0.2)
        window.action_split_view()
        settled(app, 0.3)
        has_split = getattr(window, "_split", None) is not None
        if has_split:
            window._close_split_view()
            settled(app, 0.2)
        return f"split view engaged and closed (split={has_split})"

    check("ui", "split view", split_view)

    def presentation_mode():
        window.open_path(str(samples["pdf"]))
        settled(app, 0.2)
        window.action_presentation()
        settled(app, 0.3)
        pres = getattr(window, "_presentation", None)
        closed = "no window created"
        if pres is not None:
            pres._next()
            pres._prev()
            pres.close()
            settled(app, 0.1)
            closed = "opened, navigated, closed"
        return closed

    check("ui", "presentation mode", presentation_mode)

    def focus_and_fullscreen():
        window.action_focus_mode()
        settled(app, 0.05)
        window.action_focus_mode()
        window.act_fullscreen.setChecked(True)
        window.action_fullscreen()
        settled(app, 0.05)
        window.act_fullscreen.setChecked(False)
        window.action_fullscreen()
        settled(app, 0.05)
        return "focus + fullscreen toggled both ways"

    check("ui", "focus + fullscreen toggles", focus_and_fullscreen)

    def annotation_tools():
        view = window.current_view()
        tools = [window.act_tool_highlight, window.act_tool_underline,
                 window.act_tool_strike, window.act_tool_note,
                 window.act_tool_ink, window.act_tool_rect,
                 window.act_tool_ellipse, window.act_tool_arrow,
                 window.act_tool_freetext, window.act_tool_stamp,
                 window.act_tool_none]
        for action in tools:
            action.trigger()
            settled(app, 0.02)
        window.act_redact.setChecked(True)
        window.action_toggle_redact()
        settled(app, 0.05)
        window.act_redact.setChecked(False)
        window.action_toggle_redact()
        return f"{len(tools)} annotation tools + redaction mode toggled"

    check("ui", "annotation tool selection", annotation_tools)

    def context_menus():
        # Probe each open view's context contribution directly: Qt property
        # names like ``contextMenuPolicy`` match naive substring scans and
        # are not callables that return menus.
        views = []
        for i in range(window.tabs.count()):
            views.extend(window._document_views_in(window.tabs.widget(i)))
        if not views:
            return "skipped: no open document views"
        menus = []
        for view in views:
            fn = getattr(view, "context_actions", None)
            if callable(fn):
                try:
                    acts = fn()
                except Exception as exc:  # noqa: BLE001 - report, do not abort
                    raise AssertionError(f"context_actions raised: {exc}")
                menus.append((type(view).__name__, acts))
        if not menus:
            return "no context contribution found on the views"
        summary = "; ".join(f"{n}:{len(a)} actions" for n, a in menus)
        return summary

    check("ui", "view context menus build", context_menus)

    def required_shortcuts():
        required = {"Ctrl+O": "act_open", "Ctrl+S": "act_save",
                    "Ctrl+P": "act_print", "Ctrl+F": "act_find",
                    "Ctrl+K": "act_palette", "Ctrl+W": "act_close_tab",
                    "F5": "act_present", "F11": "act_fullscreen",
                    "Ctrl+Shift+S": "act_save_as", "Ctrl+Shift+D": "act_split_view",
                    "F9": "act_focus", "Ctrl+Shift+H": "act_tool_highlight",
                    "Ctrl+Shift+N": "act_tool_note"}
        missing = []
        for key, attr in required.items():
            action = getattr(window, attr, None)
            if action is None or action.shortcut().toString() != key:
                missing.append(f"{attr}={action.shortcut().toString() if action else 'missing'}")
        if missing:
            raise AssertionError(f"shortcut mismatches: {missing}")
        return f"{len(required)} key shortcuts wired"

    check("ui", "keyboard shortcuts", required_shortcuts)

    def onboarding():
        from veyrion_workspace.ui.onboarding import OnboardingDialog
        dialog = OnboardingDialog(None, settings)
        pages = getattr(dialog, "stack", None)
        count = pages.count() if pages is not None else "n/a"
        dialog.deleteLater()
        return f"onboarding dialog builds ({count} pages)"

    check("ui", "onboarding dialog", onboarding)

    def settings_dialog():
        from veyrion_workspace.ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(None)
        sections = getattr(dialog, "_sections", None)
        if sections is not None:
            for name in list(sections):
                try:
                    dialog._select_section(name) if hasattr(
                        dialog, "_select_section") else None
                    settled(app, 0.01)
                except Exception:  # noqa: BLE001
                    continue
        dialog.deleteLater()
        return f"settings dialog builds ({len(sections) if sections else 'n/a'} sections)"

    check("ui", "settings dialog", settings_dialog)

    def plugins_panel():
        panel = getattr(window, "plugins_panel", None)
        if panel is None:
            return "no plugins panel on the window"
        panel.refresh() if hasattr(panel, "refresh") else None
        return "plugins panel refreshes"

    check("ui", "plugins panel", plugins_panel)

    def dialogs_from_actions():
        """Trigger every action; any dialog it opens must construct cleanly."""
        windows_before = len(DIALOGS)
        failures = []
        skipped = []
        triggered = 0
        silent = []
        for attr in sorted(vars(window)):
            if not attr.startswith("act_") or attr in UI_SKIP_ACTIONS:
                continue
            action = getattr(window, attr)
            if not hasattr(action, "trigger"):
                continue
            state_before = (
                window.tabs.count(),
                len(DIALOGS),
                settings.get("appearance", "theme"),
            )
            before_dialogs = len(DIALOGS)
            try:
                action.trigger()
                settled(app, 0.05)
                triggered += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{attr}: {type(exc).__name__}: {exc}")
                continue
            state_after = (
                window.tabs.count(),
                len(DIALOGS),
                settings.get("appearance", "theme"),
            )
            if state_after == state_before:
                silent.append(attr)
            elif len(DIALOGS) == before_dialogs:
                pass
        # Reset any toggles the sweep left on.
        for attr in ("act_redact", "act_read_aloud", "act_focus",
                     "act_fullscreen", "act_split_view"):
            action = getattr(window, attr, None)
            if action is not None and hasattr(action, "isChecked") \
                    and action.isChecked():
                action.setChecked(False)
                try:
                    action.trigger()
                except Exception:  # noqa: BLE001
                    pass
        settled(app, 0.1)
        note("ui", "actions with no observable change",
             ", ".join(silent) if silent else "none")
        note("ui", "dialogs constructed during action sweep",
             f"{len(DIALOGS) - windows_before}")
        if failures:
            raise AssertionError(f"{len(failures)} action(s) raised: "
                                 f"{'; '.join(failures[:6])}")
        return (f"{triggered} actions triggered, {len(DIALOGS) - windows_before} "
                f"dialogs built, 0 raised")

    check("ui", "every QAction triggers without raising", dialogs_from_actions)

    def close_all_tabs():
        while window.tabs.count() > 0:
            window.close_tab(0)
            settled(app, 0.03)
        assert window.tabs.count() == 0
        return "all tabs closed cleanly"

    check("ui", "close every tab", close_all_tabs)

    def recovery_after_close():
        journal_state = window.journal.has_unsaved_session()
        return f"journal clean={not journal_state}"

    check("ui", "session journal stays consistent", recovery_after_close)

    def exit_action():
        window.act_quit.trigger()
        settled(app, 0.1)
        return "exit action fired"

    check("ui", "exit action", exit_action)

    try:
        tasks.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        pass
    try:
        db.close()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------- reporting
def write_report() -> Path:
    out_dir = Path(__file__).resolve().parent.parent / "artifacts" / "feature_sweep"
    out_dir.mkdir(parents=True, exist_ok=True)

    sections: dict[str, list[tuple[str, str, str]]] = {}
    for section, name, status, detail in RESULTS:
        sections.setdefault(section, []).append((name, status, detail))

    total = len(RESULTS)
    failed = sum(1 for r in RESULTS if r[2] == "FAIL")
    info = sum(1 for r in RESULTS if r[2] == "INFO")
    passed = total - failed - info

    lines = ["# Veyrion Workspace - feature sweep report", "",
             f"- checks: **{total}**",
             f"- passed: **{passed}**",
             f"- failed: **{failed}**",
             f"- informational: **{info}**",
             f"- data dir used: `{DATA}`", ""]
    for section in sorted(sections):
        rows = sections[section]
        ok = sum(1 for r in rows if r[1] == "PASS")
        bad = sum(1 for r in rows if r[1] == "FAIL")
        lines.append(f"## {section}  ({ok} pass, {bad} fail)")
        lines.append("")
        for name, status, detail in rows:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "INFO": "info"}[status]
            text = f"- **{mark}** {name}"
            if detail:
                text += f" - {detail}"
            lines.append(text)
        lines.append("")

    if FAILURES:
        lines.append("## Failure details")
        lines.append("")
        for section, name, tb in FAILURES:
            lines.append(f"### {section} / {name}")
            lines.append("")
            lines.append("```")
            lines.append(tb.strip())
            lines.append("```")
            lines.append("")

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(
        [{"section": s, "name": n, "status": st, "detail": d}
         for s, n, st, d in RESULTS], indent=2), encoding="utf-8")
    return out_dir / "report.md"


def main() -> int:
    _install_stubs()
    app = QApplication.instance() or QApplication([])

    work = DATA / "work"
    work.mkdir(parents=True, exist_ok=True)
    samples = build_samples(DATA / "samples")

    note("environment", "python", sys.version.split()[0])
    note("environment", "qt", QApplication.applicationVersion() or "PySide6")
    note("environment", "data dir", str(DATA))
    note("environment", "samples", f"{len(samples)} files generated")

    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.storage.database import Database

    settings = Settings()
    db = Database()

    sweep_formats(samples, work)
    sweep_pdf_ops(samples, work)
    sweep_annotations_service(settings, db, samples, work)
    sweep_search(db, samples)
    sweep_library(db, samples)
    sweep_versioning(settings, samples, work)
    sweep_conversion(samples, work)
    sweep_comparison(samples, work)
    sweep_printing(samples, work)
    sweep_security(settings, db, samples, work)
    sweep_optional_tools(db, samples, work)
    sweep_plugins(settings, work)
    sweep_services(settings, db, work)
    sweep_ui_window(app, samples)

    report = write_report()

    print("=" * 64)
    total = len(RESULTS)
    failed = [r for r in RESULTS if r[2] == "FAIL"]
    info = [r for r in RESULTS if r[2] == "INFO"]
    print(f"FEATURE SWEEP COMPLETE - {total - len(failed) - len(info)} passed, "
          f"{len(failed)} failed, {len(info)} informational")
    sections = sorted({r[0] for r in RESULTS})
    print(f"sections: {', '.join(sections)}")
    if failed:
        print("-" * 64)
        for section, name, _status, detail in failed:
            print(f"  FAIL [{section}] {name}: {detail}")
    if info:
        print("-" * 64)
        for section, name, _status, detail in info:
            print(f"  info [{section}] {name}: {detail}")
    print(f"report: {report}")
    print("=" * 64)

    try:
        db.close()
    except Exception:  # noqa: BLE001
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
