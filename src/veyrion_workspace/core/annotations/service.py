"""Annotation service: the single source of truth for annotation state.

Annotations are stored in two places by design:
  * the local SQLite database (always, for the annotation manager, search,
    and export), and
  * the PDF itself as standard PDF annotations (for PDFs, so annotations
    travel with the document).

Both stores are reconciled on open: PDF-native annotations are imported
into the database when missing; database records re-apply to the PDF when
the document was edited externally.
"""
from __future__ import annotations

import json
import logging
import time
import uuid as uuid_mod
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from veyrion_workspace.core.documents.base import Capability, DocumentEngine
from veyrion_workspace.storage.repositories import AnnotationRecord, AnnotationRepository

logger = logging.getLogger("veyrion.annotations")

TYPE_MAP_PDF = {  # UI type -> PDF engine method
    "highlight": "add_highlight",
    "underline": "underline",
    "strikeout": "strikeout",
    "squiggly": "squiggly",
    "note": "note",
    "freetext": "freetext",
    "ink": "ink",
    "rectangle": "rectangle",
    "ellipse": "ellipse",
    "line": "line",
    "arrow": "arrow",
    "stamp": "stamp",
}


class AnnotationService:
    def __init__(self, ann_repo: AnnotationRepository, settings) -> None:
        self._repo = ann_repo
        self._settings = settings
        self._listeners: list = []

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def _notify(self, doc_path: str) -> None:
        for cb in self._listeners:
            try:
                cb(doc_path)
            except Exception:
                logger.exception("annotation listener failed")

    # -- queries ------------------------------------------------------------
    @staticmethod
    def _norm_path(doc_path: str) -> str:
        """Canonical DB key form: forward slashes.

        Records are created from user paths (backslashes on Windows) but
        looked up from engine paths (fitz gives POSIX form). Without
        normalization one of the two lookups silently finds nothing.
        """
        return str(doc_path).replace("\\", "/")

    def for_document(self, doc_path: str) -> list[AnnotationRecord]:
        return self._repo.for_document(self._norm_path(doc_path))

    def all_annotations(self) -> list[AnnotationRecord]:
        return self._repo.all()

    def count_for_document(self, doc_path: str) -> int:
        return self._repo.count_for_document(self._norm_path(doc_path))

    # -- create / update / delete -------------------------------------------
    def create(self, doc_path: str, page: int, atype: str, *, rect: Any = None,
               color: str = "#E5B25D", opacity: float = 1.0, width: float = 2.0,
               text: str = "", note: str = "", quads: Any = None,
               points: Any = None) -> AnnotationRecord:
        payload = {"rect": rect, "quads": quads, "points": points}
        payload = {k: v for k, v in payload.items() if v is not None}
        rec = AnnotationRecord(
            uuid=uuid_mod.uuid4().hex,
            doc_path=self._norm_path(doc_path),
            page=page,
            atype=atype,
            rect_json=json.dumps(payload),
            color=color,
            opacity=opacity,
            width=width,
            text=text,
            note=note,
            author=self._settings.get("annotations", "author", "Me") if self._settings else "Me",
            created_at=time.time(),
            modified_at=time.time(),
        )
        self._repo.upsert(rec)
        self._notify(doc_path)
        return rec

    def update(self, rec: AnnotationRecord, **changes) -> AnnotationRecord:
        for key, value in changes.items():
            if hasattr(rec, key):
                setattr(rec, key, value)
        rec.modified_at = time.time()
        self._repo.upsert(rec)
        self._notify(rec.doc_path)
        return rec

    def delete(self, uuid: str, doc_path: str = "") -> None:
        self._repo.delete(uuid)
        if doc_path:
            self._notify(doc_path)

    def delete_for_document(self, doc_path: str) -> None:
        self._repo.delete_for_document(self._norm_path(doc_path))
        self._notify(doc_path)

    # -- PDF sync ---------------------------------------------------------
    def apply_to_pdf(self, engine: DocumentEngine) -> int:
        """Write DB annotations that are not yet in the PDF. Returns count."""
        if not hasattr(engine, "add_highlight"):
            return 0
        pdf_annots = engine.annotations()
        existing_keys = set()
        for a in pdf_annots:
            # Key on (page, type, rounded rect) — PDFs have no stable ids we control.
            r = a.get("rect") or [0, 0, 0, 0]
            existing_keys.add((a.get("page"), a.get("type", "").lower(),
                               round(r[0], 1), round(r[1], 1), round(r[2], 1), round(r[3], 1)))
        applied = 0
        for rec in self.for_document(self._norm_path(str(engine.path))):
            if rec.atype not in TYPE_MAP_PDF:
                continue
            try:
                payload = json.loads(rec.rect_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            rect = payload.get("rect")
            quads = payload.get("quads")
            points = payload.get("points")
            key_type = {"arrow": "line", "freetext": "FreeText", "note": "Text"}.get(
                rec.atype, rec.atype.capitalize())
            r = rect or [0, 0, 0, 0]
            key = (rec.page, key_type.lower(), round(r[0], 1), round(r[1], 1),
                   round(r[2], 1), round(r[3], 1))
            if key in existing_keys:
                continue
            self._apply_record_to_pdf(engine, rec, payload)
            applied += 1
        return applied

    def _apply_record_to_pdf(self, engine, rec: AnnotationRecord, payload: dict) -> None:
        import fitz
        rect = payload.get("rect")
        quads = payload.get("quads")
        points = payload.get("points")
        try:
            if rec.atype == "highlight":
                engine.add_highlight(rec.page, [fitz.Rect(*q) for q in (quads or [rect])],
                                     rec.color, rec.opacity)
            elif rec.atype in ("underline", "strikeout", "squiggly"):
                engine.add_text_markup(rec.page, [fitz.Rect(*q) for q in (quads or [rect])],
                                       rec.atype, rec.color, rec.opacity)
            elif rec.atype == "note":
                engine.add_note(rec.page, rect[:2] if rect else (72, 72), rec.note or rec.text,
                                rec.color, rec.author)
            elif rec.atype == "freetext":
                engine.add_free_text(rec.page, rect, rec.text, rec.color,
                                     float(rec.width) or 11)
            elif rec.atype == "ink":
                engine.add_ink(rec.page, points or [], rec.color, rec.width)
            elif rec.atype in ("rectangle", "ellipse", "line", "arrow"):
                engine.add_shape(rec.page, rec.atype, rect, rec.color, rec.width)
            elif rec.atype == "stamp":
                engine.add_stamp(rec.page, rect)
        except Exception:
            logger.exception("apply annotation %s failed", rec.uuid)

    def import_from_pdf(self, engine: DocumentEngine) -> int:
        """Pull PDF-native annotations into the DB when missing."""
        doc_path = self._norm_path(str(engine.path))
        known = {(r.page, r.atype, r.rect_json) for r in self.for_document(doc_path)}
        imported = 0
        for a in engine.annotations():
            atype = a.get("type", "").lower()
            ui_type = {"highlight": "highlight", "underline": "underline",
                       "strikeout": "strikeout", "squiggly": "squiggly",
                       "text": "note", "freetext": "freetext", "ink": "ink",
                       "square": "rectangle", "circle": "ellipse",
                       "line": "line", "stamp": "stamp"}.get(atype)
            if not ui_type:
                continue
            rect = a.get("rect") or [0, 0, 0, 0]
            rect_json = json.dumps({"rect": [round(c, 2) for c in rect]})
            key = (a["page"], ui_type, rect_json)
            if key in known:
                continue
            rec = AnnotationRecord(
                uuid=uuid_mod.uuid4().hex,
                doc_path=doc_path,
                page=a["page"],
                atype=ui_type,
                rect_json=rect_json,
                color=a.get("color") or "#E5B25D",
                opacity=a.get("opacity") or 1.0,
                width=2.0,
                text=a.get("text") or "",
                note=a.get("text") or "",
                author=a.get("author") or "",
                created_at=time.time(),
                modified_at=time.time(),
            )
            self._repo.upsert(rec)
            imported += 1
        return imported

    # -- export -------------------------------------------------------------
    def export(self, doc_path: str, fmt: str, out_path: Path,
               title: str = "") -> Path:
        """Export annotations to markdown/html/csv/json/pdf."""
        recs = self.for_document(doc_path)
        out_path = Path(out_path)
        if fmt == "json":
            data = [self._rec_payload(r) for r in recs]
            out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                encoding="utf-8")
        elif fmt == "csv":
            import csv
            with out_path.open("w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["page", "type", "text", "note", "author", "color", "created"])
                for r in recs:
                    w.writerow([r.page + 1, r.atype, r.text, r.note, r.author,
                                r.color, time.strftime("%Y-%m-%d %H:%M",
                                                       time.localtime(r.created_at))])
        elif fmt == "html":
            rows = "".join(
                f"<tr><td>{r.page + 1}</td><td>{r.atype}</td>"
                f"<td>{_html_escape(r.text)}</td><td>{_html_escape(r.note)}</td></tr>"
                for r in recs)
            html = (f"<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>{_html_escape(title)}</title>"
                    f"<style>body{{font-family:Georgia,serif;max-width:800px;"
                    f"margin:2rem auto;color:#222}}table{{border-collapse:collapse;"
                    f"width:100%}}td,th{{border:1px solid #ccc;padding:6px 10px;"
                    f"text-align:left;font-size:14px}}th{{background:#f4efe8}}"
                    f"</style></head><body><h1>{_html_escape(title)}</h1>"
                    f"<table><tr><th>Page</th><th>Type</th><th>Text</th>"
                    f"<th>Note</th></tr>{rows}</table></body></html>")
            out_path.write_text(html, encoding="utf-8")
        elif fmt == "pdf":
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            y = 72
            page.insert_text((72, y), f"Annotations — {title}",
                             fontsize=16, fontname="hebo")
            y += 28
            for r in recs:
                if y > 780:
                    page = doc.new_page()
                    y = 72
                line = f"p.{r.page + 1} [{r.atype}] {r.text or r.note}"
                page.insert_text((72, y), line[:100], fontsize=10, fontname="helv")
                y += 16
            doc.save(str(out_path))
            doc.close()
        else:  # markdown
            lines = [f"# Annotations — {title}", ""]
            for r in recs:
                lines.append(f"- **p.{r.page + 1}** `{r.atype}` — "
                             f"{r.text or r.note}".rstrip())
                if r.note and r.text:
                    lines.append(f"  - note: {r.note}")
            out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    @staticmethod
    def _rec_payload(r: AnnotationRecord) -> dict:
        return {
            "uuid": r.uuid, "page": r.page, "type": r.atype, "text": r.text,
            "note": r.note, "author": r.author, "color": r.color,
            "opacity": r.opacity, "created": r.created_at,
            "modified": r.modified_at,
        }


def _html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
