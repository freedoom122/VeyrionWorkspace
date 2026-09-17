"""PDF engine built on PyMuPDF (fitz).

Implements rendering with rotation/crop, per-page text, outline, AcroForm
reading and filling, standard PDF annotations, page operations, redaction
with content removal, and digital signature inspection.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from veyrion_workspace.core.documents.base import (
    Capability,
    CorruptDocumentError,
    DocumentEngine,
    DocumentError,
    DocumentMetadata,
    EncryptedDocumentError,
    PageInfo,
    TocEntry,
)
from veyrion_workspace.utils.safeio import make_temp_file

logger = logging.getLogger("veyrion.pdf")

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:  # pragma: no cover
    fitz = None
    HAS_FITZ = False


class PdfEngine(DocumentEngine):
    def __init__(self, path: Path, password: str = "") -> None:
        if not HAS_FITZ:
            raise DocumentError("PyMuPDF is not installed; PDF support is unavailable")
        super().__init__(path)
        self._doc = None
        self._password = password
        self._annot_page_cache: dict[int, tuple] = {}  # xref -> (page, annot)
        self._open(password)

    def _open(self, password: str) -> None:
        try:
            self._doc = fitz.open(str(self.path))
        except Exception:
            raise CorruptDocumentError(
                "This PDF's internal structure appears damaged.")
        if self._doc.is_encrypted:
            if password:
                if not self._doc.authenticate(password):
                    self._doc.close()
                    raise EncryptedDocumentError("Incorrect password")
            else:
                self._doc.close()
                raise EncryptedDocumentError("Password required")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            finally:
                self._doc = None

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc is not None else 0

    @property
    def capabilities(self) -> set:
        caps = {Capability.RENDER, Capability.TEXT, Capability.SEARCH_IN_DOC,
                Capability.TOC, Capability.SAVE, Capability.THUMBNAIL,
                Capability.ANNOTATE_PDF, Capability.EDIT_PAGES, Capability.FORMS}
        return caps

    def needs_password(self) -> bool:
        return bool(self._doc and self._doc.needs_pass)

    # -- info ---------------------------------------------------------------
    def page_info(self, index: int) -> PageInfo:
        page = self._doc[index]
        return PageInfo(
            index=index,
            width=page.rect.width,
            height=page.rect.height,
            rotation=page.rotation,
            label=self._page_label(index),
        )

    def _page_label(self, index: int) -> str:
        try:
            labels = self._doc.get_page_labels()
            if not labels:
                return ""
            import fitz
            best = None
            for item in labels:
                if index >= item.get("startpage", 0):
                    if best is None or item["startpage"] >= best["startpage"]:
                        best = item
            if not best:
                return ""
            style = best.get("style", "D")
            prefix = best.get("prefix", "") or ""
            n = index - best.get("startpage", 0) + 1
            if style == "D":
                body = str(n)
            elif style == "r":
                body = _to_roman(n).lower()
            elif style == "R":
                body = _to_roman(n)
            elif style in ("a", "A"):
                body = _to_alpha(n, style)
            else:
                body = str(n)
            return f"{prefix}{body}"
        except Exception:
            return ""

    def metadata(self) -> DocumentMetadata:
        md = self._doc.metadata or {}
        meta = DocumentMetadata(page_count=self.page_count)
        meta.title = md.get("title", "")
        meta.author = md.get("author", "")
        meta.subject = md.get("subject", "")
        meta.keywords = md.get("keywords", "")
        meta.creator = md.get("creator", "")
        meta.producer = md.get("producer", "")
        meta.created = md.get("creationDate", "")
        meta.modified = md.get("modDate", "")
        meta.encrypted = bool(self._doc.is_encrypted)
        meta.has_acroform = bool(self._doc.is_form_pdf)
        meta.signed = self._has_signatures()
        word_total = 0
        scanned_pages = 0
        sample = min(self.page_count, 25)
        for i in range(sample):
            text = self._doc[i].get_text().strip()
            word_total += len(text.split())
            if len(text) < 10:
                scanned_pages += 1
        meta.scanned = sample > 0 and scanned_pages / sample > 0.8
        meta.word_count = word_total if sample >= self.page_count else int(
            word_total * self.page_count / sample)
        try:
            fonts = set()
            for i in range(sample):
                for f in self._doc[i].get_fonts():
                    fonts.add(f[3])
            meta.extra["fonts"] = sorted(fonts)[:40]
        except Exception:
            meta.extra["fonts"] = []
        meta.extra["images"] = self._count_images(sample)
        return meta

    def _count_images(self, sample: int) -> int:
        try:
            total = 0
            for i in range(sample):
                total += len(self._doc[i].get_images(full=True))
            if sample < self.page_count:
                total = int(total * self.page_count / sample)
            return total
        except Exception:
            return 0

    def toc(self) -> list[TocEntry]:
        entries = []
        try:
            for level, title, page in self._doc.get_toc():
                entries.append(TocEntry(
                    title=title, page=page - 1 if page > 0 else 0, level=level))
        except Exception:
            logger.debug("toc failed", exc_info=True)
        return entries

    # -- text ---------------------------------------------------------
    def page_text(self, index: int) -> str:
        if self._doc is None:
            return ""
        try:
            return self._doc[index].get_text()
        except Exception:
            return ""

    # -- rendering ------------------------------------------------------
    def render_page(self, index: int, zoom: float = 1.0, rotation: int = 0):
        page = self._doc[index]
        eff_rotation = (page.rotation + rotation) % 360
        mat = fitz.Matrix(zoom, zoom).prerotate(eff_rotation)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix

    def extract_page_image(self, index: int) -> bytes:
        pix = self.render_page(index, zoom=2.0)
        return pix.tobytes("png")

    # -- annotations (standard PDF structures) ----------------------------
    def annotations(self) -> list[dict]:
        out = []
        for pno in range(self.page_count):
            for annot in self._doc[pno].annots() or []:
                out.append(self._annot_to_dict(annot, pno))
        return out

    def _annot_to_dict(self, annot, pno: int) -> dict:
        colors = annot.colors or {}
        col = colors.get("stroke") or colors.get("fill") or [0.9, 0.7, 0.35]
        return {
            "pdf_xref": annot.xref,
            "page": pno,
            "type": annot.type[1],
            "type_code": annot.type[0],
            "rect": list(annot.rect),
            "color": _rgb_to_hex(col),
            "opacity": float(getattr(annot, "opacity", 1.0) or 1.0),
            "text": (annot.info.get("content") or "").strip(),
            "author": annot.info.get("title") or "",
            "date": annot.info.get("modDate") or annot.info.get("date") or "",
        }

    def add_highlight(self, page: int, quads: list, color: str = "#E5B25D",
                      opacity: float = 0.4) -> int:
        page_obj = self._doc[page]
        annot = page_obj.add_highlight_annot(quads)
        _set_annot_color(annot, color)
        annot.set_opacity(opacity)
        annot.update()
        self.modified = True
        return annot.xref

    def add_text_markup(self, page: int, quads: list, kind: str = "underline",
                        color: str = "#C7522A", opacity: float = 1.0) -> int:
        page_obj = self._doc[page]
        if kind == "underline":
            a = page_obj.add_underline_annot(quads)
        elif kind == "strikeout":
            a = page_obj.add_strikeout_annot(quads)
        else:
            a = page_obj.add_squiggly_annot(quads)
        _set_annot_color(a, color)
        a.set_opacity(opacity)
        a.update()
        self.modified = True
        return a.xref

    def add_note(self, page: int, point, text: str, color: str = "#E5B25D",
                 author: str = "Me") -> int:
        page_obj = self._doc[page]
        annot = page_obj.add_text_annot(fitz.Point(*point), text, icon="Comment")
        _set_annot_color(annot, color)
        annot.info["title"] = author
        annot.update()
        self.modified = True
        return annot.xref

    def add_free_text(self, page: int, rect, text: str, color: str = "#C7522A",
                      font_size: float = 11) -> int:
        page_obj = self._doc[page]
        annot = page_obj.add_freetext_annot(
            fitz.Rect(*rect), text, fontsize=font_size,
            text_color=_hex_to_rgb(color), fill_color=(1, 1, 1))
        annot.set_border(width=0.5)
        annot.update()
        self.modified = True
        return annot.xref

    def add_ink(self, page: int, strokes: list, color: str = "#222222",
                width: float = 2.0) -> int:
        page_obj = self._doc[page]
        points = [[(float(x), float(y)) for (x, y) in stroke] for stroke in strokes]
        annot = page_obj.add_ink_annot(points)
        _set_annot_color(annot, color)
        annot.set_border(width=width)
        annot.update()
        self.modified = True
        return annot.xref

    def add_shape(self, page: int, kind: str, rect, color: str = "#C7522A",
                  width: float = 1.5, fill: str = "") -> int:
        page_obj = self._doc[page]
        r = fitz.Rect(*rect)
        if kind == "rectangle":
            annot = page_obj.add_rect_annot(r)
        elif kind == "ellipse":
            annot = page_obj.add_circle_annot(r)
        elif kind in ("line", "arrow"):
            p1 = fitz.Point(r.x0, r.y0)
            p2 = fitz.Point(r.x1, r.y1)
            annot = page_obj.add_line_annot(p1, p2)
            if kind == "arrow":
                annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
            else:
                annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_NONE)
        else:
            raise DocumentError(f"Unknown shape kind: {kind}")
        _set_annot_color(annot, color)
        annot.set_border(width=width)
        if fill and kind in ("rectangle", "ellipse"):
            annot.set_fill(_hex_to_rgb(fill))
        annot.update()
        self.modified = True
        return annot.xref

    def add_stamp(self, page: int, rect, text: str = "Reviewed") -> int:
        page_obj = self._doc[page]
        annot = page_obj.add_stamp_annot(fitz.Rect(*rect), stamp=0)
        annot.info["content"] = text
        annot.update()
        self.modified = True
        return annot.xref

    def add_link(self, page: int, rect, target_page: int) -> int:
        lk = {"kind": fitz.LINK_GOTO, "from": fitz.Rect(*rect), "page": target_page}
        page_obj = self._doc[page]
        page_obj.insert_link(lk)
        self.modified = True
        return 0

    def delete_annot_by_xref(self, xref: int) -> None:
        for pno in range(self.page_count):
            page_obj = self._doc[pno]
            for annot in page_obj.annots() or []:
                if annot.xref == xref:
                    page_obj.delete_annot(annot)
                    self.modified = True
                    return

    def update_annot_text(self, xref: int, text: str) -> None:
        for pno in range(self.page_count):
            page_obj = self._doc[pno]
            for annot in page_obj.annots() or []:
                if annot.xref == xref:
                    annot.info["content"] = text
                    annot.update()
                    self.modified = True
                    return

    # -- annotation mutation (Phase 2: post-creation editing) --------------

    def annot_at(self, pno: int, px: float, py: float):
        """(xref, subtype) of the topmost PDF annotation at (px, py), or None.

        Returns plain data rather than an Annot handle — Annot objects are
        weakly bound to their page and must not outlive it.

        This PyMuPDF build has no ``Page.annot_from_point``, so the page's
        annotations are walked in paint order and the last hit wins.
        """
        try:
            page = self._doc[pno]
            point = fitz.Point(px, py)
            hit = None
            for annot in page.annots() or []:
                if _annot_contains(annot, point):
                    hit = annot
            if hit is None:
                return None
            return int(hit.xref), hit.type[1]
        except Exception:
            logger.exception("annot_at failed on page %s", pno)
            return None

    def _annot_with_page(self, xref: int):
        """(page, annot) for an xref; either may be None.

        PyMuPDF annots are weakly bound to the Page instance they came from,
        so the (page, annot) pair is cached together and a stale pair is
        probed and re-resolved. Holding the page alive also keeps the annot
        mutable.
        """
        cached = self._annot_page_cache.get(xref)
        if cached is not None:
            page, annot = cached
            try:
                annot.type  # probe: raises if the page was GC'd
                return page, annot
            except Exception:
                self._annot_page_cache.pop(xref, None)
        page, annot = self._find_annot(xref)
        if annot is not None:
            self._annot_page_cache[xref] = (page, annot)
        return page, annot

    def get_annot(self, xref: int):
        """Live Annot handle for an xref (must .update() after mutation)."""
        return self._annot_with_page(xref)[1]

    def _find_annot(self, xref: int):
        """(page, annot) for an xref; either may be None."""
        for pno in range(self.page_count):
            page = self._doc[pno]
            for annot in page.annots() or []:
                if annot.xref == xref:
                    return page, annot
        return None, None

    # -- subtype-specific geometry (page-space in, page-space out) ---------

    def _markup_points(self, page, annot) -> list[float] | None:
        """Flat page-space QuadPoints for a text-markup annotation.

        PDF stores markup geometry in /QuadPoints (bottom-origin PDF space);
        PyMuPDF's ``vertices`` is only a read-only view of the same data, so
        writes must target /QuadPoints.
        """
        t, val = self._doc.xref_get_key(annot.xref, "QuadPoints")
        if t == "array" and val:
            try:
                nums = [float(v) for v in val.strip("[]").split()]
            except ValueError:
                nums = []
            if nums:
                return _pdf_flat_to_page(page, nums)
        flat = _flatten_vertices(annot.vertices)
        return flat or None

    def _set_markup_points(self, page, annot, page_flat) -> None:
        pdf = _page_flat_to_pdf(page, page_flat)
        self._doc.xref_set_key(
            annot.xref, "QuadPoints",
            "[" + " ".join(f"{v:.3f}" for v in pdf) + "]")

    def _ink_strokes(self, page, annot) -> list[list[float]] | None:
        """Ink strokes as flat page-space coordinate lists."""
        t, val = self._doc.xref_get_key(annot.xref, "InkList")
        if t != "array" or not val:
            return None
        groups = _parse_number_groups(val)
        strokes = [_pdf_flat_to_page(page, g) for g in groups if g]
        return strokes or None

    def _set_ink_strokes(self, page, annot, strokes) -> None:
        parts = []
        for s in strokes:
            pdf = _page_flat_to_pdf(page, s)
            parts.append("[" + " ".join(f"{v:.3f}" for v in pdf) + "]")
        self._doc.xref_set_key(annot.xref, "InkList",
                               "[" + " ".join(parts) + "]")

    def _line_points(self, page, annot) -> list[float] | None:
        """A line annotation's two endpoints in page space."""
        t, val = self._doc.xref_get_key(annot.xref, "L")
        if t != "array" or not val:
            return None
        try:
            nums = [float(v) for v in val.strip("[]").split()]
        except ValueError:
            return None
        if len(nums) < 4:
            return None
        return _pdf_flat_to_page(page, nums[:4])

    def _set_line_points(self, page, annot, page_flat) -> None:
        pdf = _page_flat_to_pdf(page, page_flat)
        self._doc.xref_set_key(
            annot.xref, "L",
            f"[{pdf[0]:.3f} {pdf[1]:.3f} {pdf[2]:.3f} {pdf[3]:.3f}]")

    def move_annot(self, xref: int, dx: float, dy: float) -> bool:
        """Translate an annotation by (dx, dy) in page coordinates.

        Rect-like annots move via set_rect; text markups rewrite their
        /QuadPoints; ink rewrites /InkList; lines rewrite /L. Raw-key edits
        are expressed in PDF (bottom-origin) space through the page's
        transformation matrix, so rotated pages translate correctly, and
        ``update()`` regenerates /Rect and the appearance stream.
        """
        page, annot = self._annot_with_page(xref)
        if annot is None or (abs(dx) < 1e-9 and abs(dy) < 1e-9):
            return False
        subtype = annot.type[1]
        try:
            if subtype in _MARKUP_SUBTYPES:
                pts = self._markup_points(page, annot)
                if not pts:
                    return False
                self._set_markup_points(page, annot, _shift_flat(pts, dx, dy))
            elif subtype == "Ink":
                strokes = self._ink_strokes(page, annot)
                if not strokes:
                    return False
                self._set_ink_strokes(page, annot,
                                      [_shift_flat(s, dx, dy)
                                       for s in strokes])
            elif subtype == "Line":
                pts = self._line_points(page, annot)
                if not pts:
                    return False
                self._set_line_points(page, annot, _shift_flat(pts, dx, dy))
            else:
                # Square, Circle, FreeText, Text (pop-up note), Stamp, …
                # NOTE: MuPDF re-derives a pop-up note's /Rect from its icon
                # anchor, so raw /Rect writes are ignored for Text annots;
                # they move through set_rect (accurate on unrotated pages).
                vr = _annot_visual_rect(self._doc, annot)
                annot.set_rect(fitz.Rect(vr.x0 + dx, vr.y0 + dy,
                                         vr.x1 + dx, vr.y1 + dy))
            annot.update()
            self.modified = True
            return True
        except Exception:
            logger.exception("move_annot failed for xref %s", xref)
            return False

    def resize_annot_rect(self, xref: int, rect) -> bool:
        """Resize/reshape an annotation to a new page-space Rect.

        Rect-like annots resize natively; text markups rescale /QuadPoints,
        ink rescales /InkList and lines rescale /L — each from its current
        drawn bounds into the requested rect.
        """
        page, annot = self._annot_with_page(xref)
        if annot is None:
            return False
        try:
            nr = fitz.Rect(float(rect[0]), float(rect[1]),
                           float(rect[2]), float(rect[3]))
            if nr.is_empty or nr.is_infinite:
                return False
            nr.normalize()
            subtype = annot.type[1]
            if subtype == "Text":
                # Pop-up note icons keep a fixed size; MuPDF ignores resize.
                return False
            src = _annot_visual_rect(self._doc, annot)
            if src.is_empty:
                src = fitz.Rect(annot.rect)
            if subtype in _MARKUP_SUBTYPES:
                pts = self._markup_points(page, annot)
                if not pts:
                    return False
                self._set_markup_points(page, annot,
                                        _remap_flat(pts, src, nr))
            elif subtype == "Ink":
                strokes = self._ink_strokes(page, annot)
                if not strokes:
                    return False
                self._set_ink_strokes(
                    page, annot, [_remap_flat(s, src, nr) for s in strokes])
            elif subtype == "Line":
                pts = self._line_points(page, annot)
                if not pts:
                    return False
                bb = fitz.Rect(min(pts[0], pts[2]), min(pts[1], pts[3]),
                               max(pts[0], pts[2]), max(pts[1], pts[3]))
                if bb.is_empty:
                    return False
                # The visual rect is the endpoint bbox padded by the stroke
                # width, so shrink the target bbox by that padding to make the
                # drawn line fill `nr` exactly.
                dl, dt = src.x0 - bb.x0, src.y0 - bb.y0
                dr, db = src.x1 - bb.x1, src.y1 - bb.y1
                dst = fitz.Rect(nr.x0 - dl, nr.y0 - dt,
                                nr.x1 - dr, nr.y1 - db)
                self._set_line_points(page, annot, _remap_flat(pts, bb, dst))
            else:
                annot.set_rect(nr)
            annot.update()
            self.modified = True
            return True
        except Exception:
            logger.exception("resize_annot_rect failed for xref %s", xref)
            return False

    def annot_visual_rect(self, xref: int):
        """The annotation's drawn bounds as a fitz.Rect (RD-adjusted).

        annot.rect reports the raw /Rect inflated by the border /RD
        differences; overlays and hit tests want the drawn frame instead.
        Falls back to annot.rect when RD is absent.
        """
        annot = self.get_annot(xref)
        if annot is None:
            return None
        return _annot_visual_rect(self._doc, annot)

    def set_annot_opacity(self, xref: int, opacity: float) -> bool:
        annot = self.get_annot(xref)
        if annot is None:
            return False
        try:
            annot.set_opacity(max(0.05, min(1.0, float(opacity))))
            annot.update()
            self.modified = True
            return True
        except Exception:
            logger.exception("set_annot_opacity failed for xref %s", xref)
            return False

    def set_annot_color(self, xref: int, hex_color: str) -> bool:
        annot = self.get_annot(xref)
        if annot is None:
            return False
        if annot.type[1] == "FreeText":
            # A text box's colour lives in its appearance stream, not in /C,
            # so set_colors() has no visible effect. Report honestly rather
            # than claiming the restyle happened.
            return False
        try:
            colors = annot.colors or {}
            if colors.get("fill") and not colors.get("stroke"):
                # Fill-only annots (e.g. a shaded Square) recolor the fill.
                annot.set_colors(fill=_hex_to_rgb(hex_color))
            else:
                _set_annot_color(annot, hex_color)
            annot.update()
            self.modified = True
            return True
        except Exception:
            logger.exception("set_annot_color failed for xref %s", xref)
            return False

    def annot_stroke_width(self, xref: int) -> float:
        """Border/stroke width of an annotation (0.0 when it has none)."""
        annot = self.get_annot(xref)
        if annot is None:
            return 0.0
        try:
            return float((annot.border or {}).get("width", 0.0) or 0.0)
        except Exception:
            return 0.0

    def annot_geometry_snapshot(self, xref: int) -> dict:
        """Subtype-specific geometry in *page* coordinates, for undo.

        Deleting an annotation loses more than its rect: ink strokes, line
        endpoints and text-markup quads are captured here so a restored
        annotation matches the original rather than approximating it.
        """
        page, annot = self._annot_with_page(xref)
        if annot is None:
            return {}
        subtype = annot.type[1]
        snap: dict = {
            "subtype": subtype,
            "rect": list(_annot_visual_rect(self._doc, annot)),
        }
        try:
            if subtype in _MARKUP_SUBTYPES:
                snap["quads"] = self._markup_points(page, annot) or []
            elif subtype == "Ink":
                snap["strokes"] = self._ink_strokes(page, annot) or []
            elif subtype == "Line":
                snap["line"] = self._line_points(page, annot) or []
        except Exception:
            logger.exception("geometry snapshot failed for xref %s", xref)
        return snap

    def add_markup_quads(self, page: int, kind: str, flat_page_pts,
                         color: str, opacity: float = 1.0) -> int:
        """Re-create a text markup from flat page-space QuadPoints.

        Used by undo to restore a deleted highlight/underline/etc. with its
        original multi-quad geometry.
        """
        page_obj = self._doc[page]
        quads = []
        for i in range(0, len(flat_page_pts) - 7, 8):
            pts = [fitz.Point(flat_page_pts[i + j], flat_page_pts[i + j + 1])
                   for j in range(0, 8, 2)]
            try:
                quads.append(fitz.Quad(pts[0], pts[1], pts[2], pts[3]))
            except Exception:
                quads.append(fitz.Rect(flat_page_pts[i], flat_page_pts[i + 1],
                                       flat_page_pts[i + 2],
                                       flat_page_pts[i + 3]))
        if not quads:
            return 0
        if kind == "Highlight":
            a = page_obj.add_highlight_annot(quads)
        elif kind == "Underline":
            a = page_obj.add_underline_annot(quads)
        elif kind == "StrikeOut":
            a = page_obj.add_strikeout_annot(quads)
        else:
            a = page_obj.add_squiggly_annot(quads)
        _set_annot_color(a, color)
        a.set_opacity(opacity)
        a.update()
        self.modified = True
        return a.xref

    def add_line_between(self, page: int, p1, p2, color: str = "#C7522A",
                         width: float = 1.5) -> int:
        """Re-create a line annotation from two page-space endpoints."""
        page_obj = self._doc[page]
        annot = page_obj.add_line_annot(fitz.Point(*p1), fitz.Point(*p2))
        _set_annot_color(annot, color)
        annot.set_border(width=width)
        annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_NONE)
        annot.update()
        self.modified = True
        return annot.xref

    def set_annot_border_width(self, xref: int, width: float) -> bool:
        """Border / stroke width for shapes, ink and line annotations."""
        annot = self.get_annot(xref)
        if annot is None:
            return False
        try:
            annot.set_border(width=max(0.1, min(24.0, float(width))))
            annot.update()
            self.modified = True
            return True
        except Exception:
            logger.exception("set_annot_border_width failed for xref %s",
                             xref)
            return False

    # -- page operations --------------------------------------------------
    def rotate_page(self, index: int, degrees: int = 90) -> None:
        page = self._doc[index]
        page.set_rotation((page.rotation + degrees) % 360)
        self.modified = True

    def delete_pages(self, indices: list[int]) -> None:
        self._doc.delete_pages(indices)
        self.modified = True

    def duplicate_pages(self, indices: list[int]) -> None:
        for i in sorted(indices, reverse=True):
            self._doc.fullcopy_page(i, 1)
        self.modified = True

    def move_page(self, from_index: int, to_index: int) -> None:
        self._doc.move_page(from_index, to_index)
        self.modified = True

    def insert_blank_page(self, after: int, width: float = 595,
                          height: float = 842) -> None:
        self._doc.new_page(pno=after + 1, width=width, height=height)
        self.modified = True

    def insert_pages_from(self, after: int, other_pdf: Path,
                          other_range=None) -> None:
        src = fitz.open(str(other_pdf))
        if other_range:
            src.select(list(other_range))
        self._doc.insert_pdf(src, start_at=after + 1)
        src.close()
        self.modified = True

    def merge_from(self, other_pdf: Path) -> None:
        src = fitz.open(str(other_pdf))
        self._doc.insert_pdf(src)
        src.close()
        self.modified = True

    def extract_pages_to(self, indices: list[int], target: Path) -> None:
        new = fitz.open()
        for i in indices:
            new.insert_pdf(self._doc, from_page=i, to_page=i)
        new.save(str(target), garbage=4, deflate=True)
        new.close()

    def split_at(self, ranges: list, out_dir: Path,
                 base_name: str) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for idx, (a, b) in enumerate(ranges, 1):
            part = fitz.open()
            part.insert_pdf(self._doc, from_page=a, to_page=b)
            out = out_dir / f"{base_name}_part{idx}.pdf"
            part.save(str(out), garbage=4, deflate=True)
            part.close()
            outputs.append(out)
        return outputs

    # -- metadata ------------------------------------------------------
    def set_metadata(self, title: Optional[str] = None,
                     author: Optional[str] = None, subject: Optional[str] = None,
                     keywords: Optional[str] = None) -> None:
        md = self._doc.metadata or {}
        if title is not None:
            md["title"] = title
        if author is not None:
            md["author"] = author
        if subject is not None:
            md["subject"] = subject
        if keywords is not None:
            md["keywords"] = keywords
        self._doc.set_metadata(md)
        self.modified = True

    def strip_metadata(self) -> None:
        self._doc.set_metadata({})
        try:
            self._doc.del_xml_metadata()
        except Exception:
            pass
        self.modified = True

    # -- forms ---------------------------------------------------------
    def form_fields(self) -> list[dict]:
        fields = []
        for pno in range(self.page_count):
            for w in self._doc[pno].widgets() or []:
                fields.append({
                    "page": pno, "name": w.field_name,
                    "type": w.field_type_string,
                    "value": w.field_value,
                    "choices": list(w.choice_values) if w.choice_values else [],
                    "rect": list(w.rect),
                })
        return fields

    def set_field_value(self, page: int, name: str, value) -> bool:
        for w in self._doc[page].widgets() or []:
            if w.field_name == name:
                w.field_value = value
                w.update()
                self.modified = True
                return True
        return False

    def clear_form(self) -> int:
        cleared = 0
        for pno in range(self.page_count):
            for w in self._doc[pno].widgets() or []:
                try:
                    w.field_value = False if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX else ""
                    w.update()
                    cleared += 1
                except Exception:
                    pass
        self.modified = True
        return cleared

    # -- redaction ------------------------------------------------------
    def add_redaction(self, page: int, rect, text: str = "") -> None:
        self._doc[page].add_redact_annot(fitz.Rect(*rect), text=(text or None))
        self.modified = True

    def apply_redactions(self) -> int:
        """Remove redacted content from all marked pages. Returns page count."""
        count = 0
        redact_type = getattr(fitz, "PDF_ANNOT_REDACT", None)
        for pno in range(self.page_count):
            page = self._doc[pno]
            try:
                redact_annots = list(page.annots(
                    types=[redact_type]) if redact_type else page.annots())
                if redact_annots:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
                    count += 1
            except Exception:
                logger.exception("redaction failed on page %d", pno)
        self.modified = True
        return count

    def verify_redaction(self, phrases: list[str]) -> dict:
        """Reopen the saved file from disk and search for the removed phrases."""
        return verify_redaction_in_file(self.path, phrases)

    # -- signatures ------------------------------------------------------
    def _has_signatures(self) -> bool:
        try:
            return bool(self._doc.get_sig_flags())
        except Exception:
            return False

    def signature_info(self) -> list[dict]:
        """Best-effort signature listing via pypdf (no crypto verification)."""
        infos = []
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(self.path))
            fields = reader.get_fields() or {}
            for name, f in fields.items():
                if f.get("/FT") != "/Sig":
                    continue
                v = f.get("/V")
                entry = {"name": name, "signed": v is not None,
                         "signer": "", "issuer": "", "valid_from": "",
                         "valid_to": "", "intact": None}
                if v is not None:
                    try:
                        cert = v.get("/Cert")
                        if cert is not None:
                            from cryptography import x509
                            der = bytes(cert)
                            c = x509.load_der_x509_certificate(der)
                            entry["signer"] = c.subject.rfc4514_string()
                            entry["issuer"] = c.issuer.rfc4514_string()
                            entry["valid_from"] = c.not_valid_before_utc.isoformat()
                            entry["valid_to"] = c.not_valid_after_utc.isoformat()
                    except Exception:
                        logger.debug("cert parse failed", exc_info=True)
                infos.append(entry)
        except Exception:
            logger.debug("signature inspection failed", exc_info=True)
        return infos

    # -- saving ------------------------------------------------------
    def save(self, target: Optional[Path] = None) -> Path:
        target = Path(target) if target else self.path
        tmp = make_temp_file(suffix=".pdf")
        tmp.unlink()
        self._doc.save(str(tmp), garbage=3, deflate=True)
        if Path(target).resolve() == self.path.resolve():
            # Windows keeps the source open while fitz has it loaded; swap
            # the file out safely and reload the saved state.
            self._doc.close()
            try:
                os.replace(str(tmp), str(target))
            finally:
                self._open(self._password)
        else:
            os.replace(str(tmp), str(target))
        self.modified = False
        return target

    def save_incremental(self) -> Path:
        """Preserve signature validity: append-only save."""
        self._doc.save(str(self.path), incremental=True,
                       encryption=fitz.PDF_ENCRYPT_NONE)
        self.modified = False
        return self.path

    def optimize(self, out: Path, *, garbage: int = 4, linear: bool = False,
                 strip_metadata: bool = False) -> tuple:
        before = self.path.stat().st_size
        tmp = make_temp_file(suffix=".pdf")
        tmp.unlink()
        if strip_metadata:
            self.strip_metadata()
        self._doc.save(str(tmp), garbage=garbage, deflate=True,
                       clean=True, linear=linear)
        after = tmp.stat().st_size
        os.replace(str(tmp), str(out))
        return Path(out), before, after

    def to_searchable(self, ocr_result: dict) -> None:
        """Add an invisible text layer from OCR results (per page)."""
        for pno, text in ocr_result.items():
            page = self._doc[pno]
            lines = [l for l in text.splitlines() if l.strip()]
            if not lines:
                continue
            n = len(lines)
            rect = page.rect
            line_h = rect.height / n
            y = 6
            for line in lines:
                page.insert_text(
                    fitz.Point(2, y), line[:200],
                    fontsize=max(4, min(8, line_h * 0.7)),
                    fontname="helv", render_mode=3,  # invisible text
                )
                y += line_h
        self.modified = True

    def page_pixels(self, index: int, dpi: int = 300):
        """High-resolution pixmap for OCR."""
        return self.render_page(index, zoom=dpi / 72.0)


def verify_redaction_in_file(pdf_path: Path, phrases: list) -> dict:
    """Reopen a saved PDF and check whether ``phrases`` can still be found."""
    doc = fitz.open(str(pdf_path))
    report = {"pages": doc.page_count, "found": {}, "object_leaks": []}
    for phrase in phrases:
        hits = []
        for pno in range(doc.page_count):
            text = doc[pno].get_text()
            if phrase.lower() in text.lower():
                hits.append(pno)
        report["found"][phrase] = hits
    doc.close()
    raw = Path(pdf_path).read_bytes()
    for phrase in phrases:
        for encoding in (phrase.encode("utf-16-le"), phrase.encode("latin-1", "ignore")):
            if encoding and encoding in raw:
                report["object_leaks"].append(phrase)
                break
    return report


def _to_roman(num: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
            (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
            (5, "V"), (4, "IV"), (1, "I")]
    out = []
    for v, sym in vals:
        while num >= v:
            out.append(sym)
            num -= v
    return "".join(out)


def _to_alpha(num: int, style: str) -> str:
    letters = "abcdefghijklmnopqrstuvwxyz" if style == "a" else "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = []
    while num > 0:
        num, rem = divmod(num - 1, 26)
        out.append(letters[rem])
    return "".join(reversed(out)) or letters[0]


_MARKUP_SUBTYPES = ("Highlight", "Underline", "StrikeOut", "Squiggly")


def _page_flat_to_pdf(page, flat: list[float]) -> list[float]:
    """Map flat page-space (Y-down) coords into PDF (Y-up) space.

    Uses the page transformation matrix so rotated pages convert correctly.
    """
    inv = ~page.transformation_matrix
    out: list[float] = []
    for i in range(0, len(flat) - 1, 2):
        p = fitz.Point(flat[i], flat[i + 1]) * inv
        out.extend((p.x, p.y))
    return out


def _pdf_flat_to_page(page, flat: list[float]) -> list[float]:
    """Inverse of _page_flat_to_pdf."""
    tm = page.transformation_matrix
    out: list[float] = []
    for i in range(0, len(flat) - 1, 2):
        p = fitz.Point(flat[i], flat[i + 1]) * tm
        out.extend((p.x, p.y))
    return out


def _shift_flat(flat: list[float], dx: float, dy: float) -> list[float]:
    """Translate flat [x, y, x, y, …] coords by (dx, dy) in page space."""
    return [v + (dx if i % 2 == 0 else dy) for i, v in enumerate(flat)]


def _remap_flat(flat: list[float], src, dst) -> list[float]:
    """Map flat [x, y, …] coords from the src rect into the dst rect."""
    sx = dst.width / max(1e-6, src.width)
    sy = dst.height / max(1e-6, src.height)
    out: list[float] = []
    for i in range(0, len(flat) - 1, 2):
        out.append(dst.x0 + (flat[i] - src.x0) * sx)
        out.append(dst.y0 + (flat[i + 1] - src.y0) * sy)
    return out


def _extract_array(obj: str, key: str) -> str | None:
    """Return the raw ``/key [ ... ]`` array body from a PDF object string.

    Tracks bracket depth so nested arrays (ink strokes, quads) survive
    intact; returns the substring including the outer brackets.
    """
    i = obj.find("/" + key)
    if i < 0:
        return None
    j = obj.find("[", i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(obj)):
        if obj[k] == "[":
            depth += 1
        elif obj[k] == "]":
            depth -= 1
            if depth == 0:
                return obj[j:k + 1]
    return None


def _parse_number_groups(body: str) -> list[list[float]]:
    """Parse ``[[x y x y] [x y]]``-style bodies into lists of floats.

    A single flat array yields one group; nested arrays yield one group per
    inner bracket pair. Used for /InkList, /Vertices and /L.
    """
    groups: list[list[float]] = []
    depth = 0
    cur: list[float] = []
    num = ""
    for ch in body:
        if ch == "[":
            depth += 1
            if depth > 1:
                cur = []
            num = ""
        elif ch == "]":
            if num.strip():
                try:
                    cur.append(float(num))
                except ValueError:
                    pass
                num = ""
            if depth > 1:
                groups.append(cur)
                cur = []
            depth -= 1
        elif ch in " \n\r\t":
            if num.strip():
                try:
                    cur.append(float(num))
                except ValueError:
                    pass
                num = ""
        else:
            num += ch
    if cur:
        groups.append(cur)
    return groups


def _flatten_vertices(verts) -> list[float]:
    """Flatten annot.vertices into [x0, y0, x1, y1, ...] page coords.

    PyMuPDF's Vertices come back as a flat list of point tuples (4 per
    quad), but be liberal: accept numbers, points, quad tuples, or nesting.
    """
    flat: list[float] = []

    def walk(v) -> None:
        if isinstance(v, (int, float)):
            flat.append(float(v))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    for item in verts or []:
        walk(item)
    return flat


def _annot_contains(annot, point, tolerance: float = 2.0) -> bool:
    """Hit test: is ``point`` inside the annotation's drawn geometry?

    Text markups are tested per quad so clicking inside the bounding box but
    off the marked line does not hit; everything else uses its drawn rect.
    """
    try:
        subtype = annot.type[1]
        if subtype in _MARKUP_SUBTYPES:
            flat = _flatten_vertices(annot.vertices)
            for i in range(0, len(flat) - 7, 8):
                xs = flat[i:i + 8:2]
                ys = flat[i + 1:i + 8:2]
                if (min(xs) - tolerance <= point.x <= max(xs) + tolerance
                        and min(ys) - tolerance <= point.y
                        <= max(ys) + tolerance):
                    return True
            return False
        r = fitz.Rect(annot.rect)
        if r.is_empty:
            return False
        padded = fitz.Rect(r.x0 - tolerance, r.y0 - tolerance,
                           r.x1 + tolerance, r.y1 + tolerance)
        return bool(padded.contains(point))
    except Exception:
        return False


def _annot_visual_rect(doc, annot) -> fitz.Rect:
    """Annot's visual bounds: raw /Rect deflated by /RD (if present).

    MuPDF pads /Rect by the RD differences; annot.rect reports the padded
    box, while Vertices/InkList coordinates live in the visual frame.
    """
    r = annot.rect
    try:
        t, val = doc.xref_get_key(annot.xref, "RD")
        if t == "array" and val:
            nums = [float(x) for x in val.strip("[]").split()][:4]
            if len(nums) == 4:
                left, bottom, right, top = nums
                return fitz.Rect(r.x0 + left, r.y0 + top,
                                 r.x1 - right, r.y1 - bottom)
    except Exception:
        pass
    return fitz.Rect(r)


def _rgb_to_hex(rgb) -> str:
    if not rgb:
        return "#E5B25D"
    try:
        r, g, b = (int(round(c * 255)) for c in rgb[:3])
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#E5B25D"


def _hex_to_rgb(h: str):
    h = (h or "#888888").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.53, 0.32, 0.16)


def _set_annot_color(annot, color: str) -> None:
    try:
        annot.set_colors(stroke=_hex_to_rgb(color))
    except Exception:
        pass
