"""Repositories: typed data access over the SQLite schema."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("veyrion.repo")


@dataclass
class DocumentRecord:
    id: int = 0
    path: str = ""
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    kind: str = "other"
    format: str = ""
    size_bytes: int = 0
    page_count: int = 0
    word_count: int = 0
    rating: int = 0
    favorite: bool = False
    progress: float = 0.0
    folder: str = ""
    cover_path: str = ""
    added_at: float = 0.0
    last_opened: float = 0.0
    last_modified: float = 0.0

    @classmethod
    def from_row(cls, row) -> "DocumentRecord":
        return cls(
            id=row["id"], path=row["path"], title=row["title"], author=row["author"],
            subject=row["subject"], keywords=row["keywords"], kind=row["kind"],
            format=row["format"], size_bytes=row["size_bytes"],
            page_count=row["page_count"], word_count=row["word_count"],
            rating=row["rating"], favorite=bool(row["favorite"]),
            progress=row["progress"], folder=row["folder"],
            cover_path=row["cover_path"], added_at=row["added_at"],
            last_opened=row["last_opened"], last_modified=row["last_modified"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "path": self.path, "title": self.title,
            "author": self.author, "subject": self.subject,
            "keywords": self.keywords, "kind": self.kind, "format": self.format,
            "size_bytes": self.size_bytes, "page_count": self.page_count,
            "word_count": self.word_count, "rating": self.rating,
            "favorite": self.favorite, "progress": self.progress,
            "folder": self.folder, "cover_path": self.cover_path,
            "added_at": self.added_at, "last_opened": self.last_opened,
            "last_modified": self.last_modified,
        }


@dataclass
class AnnotationRecord:
    uuid: str = ""
    doc_path: str = ""
    page: int = 0
    atype: str = "highlight"
    rect_json: str = "[]"
    color: str = "#E5B25D"
    opacity: float = 1.0
    width: float = 2.0
    text: str = ""
    note: str = ""
    author: str = ""
    tags: str = ""
    created_at: float = 0.0
    modified_at: float = 0.0

    @classmethod
    def from_row(cls, row) -> "AnnotationRecord":
        return cls(
            uuid=row["uuid"], doc_path=row["doc_path"], page=row["page"],
            atype=row["atype"], rect_json=row["rect_json"], color=row["color"],
            opacity=row["opacity"], width=row["width"], text=row["text"],
            note=row["note"], author=row["author"], tags=row["tags"],
            created_at=row["created_at"], modified_at=row["modified_at"],
        )


@dataclass
class NoteRecord:
    id: int = 0
    doc_path: str = ""
    title: str = ""
    body: str = ""
    tags: str = ""
    anchors_json: str = "[]"
    created_at: float = 0.0
    modified_at: float = 0.0

    @classmethod
    def from_row(cls, row) -> "NoteRecord":
        return cls(
            id=row["id"], doc_path=row["doc_path"], title=row["title"],
            body=row["body"], tags=row["tags"], anchors_json=row["anchors_json"],
            created_at=row["created_at"], modified_at=row["modified_at"],
        )


class LibraryRepository:
    """CRUD + queries for the document library."""

    def __init__(self, db) -> None:
        self._db = db

    # -- basic CRUD ------------------------------------------------------
    def upsert(self, rec: DocumentRecord) -> int:
        now = time.time()
        existing = self._db.query_one(
            "SELECT id FROM documents WHERE path=?", (rec.path,)
        )
        if existing:
            # metadata_json is intentionally untouched: rescans must not
            # destroy metadata stored by other features.
            self._db.execute(
                """UPDATE documents SET title=?, author=?, subject=?, keywords=?,
                   kind=?, format=?, size_bytes=?, page_count=?, word_count=?,
                   folder=?, last_modified=?
                   WHERE id=?""",
                (rec.title, rec.author, rec.subject, rec.keywords, rec.kind,
                 rec.format, rec.size_bytes, rec.page_count, rec.word_count,
                 rec.folder, now, existing["id"]),
            )
            return existing["id"]
        cur = self._db.execute(
            """INSERT INTO documents (path, title, author, subject, keywords,
               kind, format, size_bytes, page_count, word_count, folder,
               added_at, last_modified)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec.path, rec.title, rec.author, rec.subject, rec.keywords,
             rec.kind, rec.format, rec.size_bytes, rec.page_count,
             rec.word_count, rec.folder, now, now),
        )
        return cur.lastrowid

    def get_by_path(self, path: str) -> Optional[DocumentRecord]:
        row = self._db.query_one("SELECT * FROM documents WHERE path=?", (path,))
        return DocumentRecord.from_row(row) if row else None

    def get_by_id(self, doc_id: int) -> Optional[DocumentRecord]:
        row = self._db.query_one("SELECT * FROM documents WHERE id=?", (doc_id,))
        return DocumentRecord.from_row(row) if row else None

    def delete(self, path: str) -> None:
        row = self._db.query_one("SELECT id FROM documents WHERE path=?", (path,))
        self._db.execute("DELETE FROM documents WHERE path=?", (path,))
        if row:
            self._db.execute("DELETE FROM document_tags WHERE doc_id=?", (row["id"],))
            self._db.execute("DELETE FROM document_collections WHERE doc_id=?", (row["id"],))

    def mark_opened(self, path: str) -> None:
        self._db.execute("UPDATE documents SET last_opened=? WHERE path=?",
                         (time.time(), path))

    def set_progress(self, path: str, progress: float, page: int = 0) -> None:
        self._db.execute("UPDATE documents SET progress=? WHERE path=?",
                         (max(0.0, min(1.0, progress)), path))

    def set_rating(self, path: str, rating: int) -> None:
        self._db.execute("UPDATE documents SET rating=? WHERE path=?",
                         (max(0, min(5, rating)), path))

    def set_favorite(self, path: str, fav: bool) -> None:
        self._db.execute("UPDATE documents SET favorite=? WHERE path=?",
                         (1 if fav else 0, path))

    def set_cover(self, path: str, cover_path: str) -> None:
        self._db.execute("UPDATE documents SET cover_path=? WHERE path=?",
                         (cover_path, path))

    def all(self, limit: int = 0) -> list[DocumentRecord]:
        sql = "SELECT * FROM documents"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [DocumentRecord.from_row(r) for r in self._db.query(sql + " ORDER BY last_opened DESC")]

    # -- tags --------------------------------------------------------------
    def all_tags(self) -> list[tuple[str, str]]:
        return [(r["name"], r["color"]) for r in
                self._db.query("SELECT name, color FROM tags ORDER BY name")]

    def tags_for(self, path: str) -> list[str]:
        return [r["name"] for r in self._db.query(
            """SELECT t.name FROM tags t JOIN document_tags dt ON t.id=dt.tag_id
               JOIN documents d ON d.id=dt.doc_id WHERE d.path=?""", (path,))]

    def add_tag(self, path: str, name: str, color: str = "#8A8F98") -> None:
        self._db.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?,?)",
                         (name, color))
        row = self._db.query_one("SELECT id FROM tags WHERE name=?", (name,))
        drow = self._db.query_one("SELECT id FROM documents WHERE path=?", (path,))
        if row and drow:
            self._db.execute(
                "INSERT OR IGNORE INTO document_tags (doc_id, tag_id) VALUES (?,?)",
                (drow["id"], row["id"]))

    def remove_tag(self, path: str, name: str) -> None:
        self._db.execute(
            """DELETE FROM document_tags WHERE doc_id=(SELECT id FROM documents WHERE path=?)
               AND tag_id=(SELECT id FROM tags WHERE name=?)""", (path, name))

    # -- collections ---------------------------------------------------------
    def collections(self) -> list[tuple[int, str]]:
        return [(r["id"], r["name"]) for r in
                self._db.query("SELECT id, name FROM collections ORDER BY name")]

    def create_collection(self, name: str) -> int:
        return self._db.execute(
            "INSERT INTO collections (name) VALUES (?)", (name,)).lastrowid

    def add_to_collection(self, path: str, collection_id: int) -> None:
        drow = self._db.query_one("SELECT id FROM documents WHERE path=?", (path,))
        if drow:
            self._db.execute(
                "INSERT OR IGNORE INTO document_collections (doc_id, collection_id) VALUES (?,?)",
                (drow["id"], collection_id))

    def remove_from_collection(self, path: str, collection_id: int) -> None:
        drow = self._db.query_one("SELECT id FROM documents WHERE path=?", (path,))
        if drow:
            self._db.execute(
                "DELETE FROM document_collections WHERE doc_id=? AND collection_id=?",
                (drow["id"], collection_id))

    def docs_in_collection(self, collection_id: int) -> list[DocumentRecord]:
        return [DocumentRecord.from_row(r) for r in self._db.query(
            """SELECT d.* FROM documents d
               JOIN document_collections dc ON d.id=dc.doc_id
               WHERE dc.collection_id=? ORDER BY d.title""", (collection_id,))]

    def collection_names_for(self, path: str) -> list[str]:
        return [r["name"] for r in self._db.query(
            """SELECT c.name FROM collections c
               JOIN document_collections dc ON c.id=dc.collection_id
               JOIN documents d ON d.id=dc.doc_id WHERE d.path=?""", (path,))]


class AnnotationRepository:
    def __init__(self, db) -> None:
        self._db = db

    def upsert(self, rec: AnnotationRecord) -> None:
        now = time.time()
        self._db.execute(
            """INSERT INTO annotations (uuid, doc_path, page, atype, rect_json,
               color, opacity, width, text, note, author, tags, created_at, modified_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(uuid) DO UPDATE SET
                 page=excluded.page, atype=excluded.atype,
                 rect_json=excluded.rect_json, color=excluded.color,
                 opacity=excluded.opacity, width=excluded.width,
                 text=excluded.text, note=excluded.note, tags=excluded.tags,
                 modified_at=excluded.modified_at""",
            (rec.uuid, rec.doc_path, rec.page, rec.atype, rec.rect_json,
             rec.color, rec.opacity, rec.width, rec.text, rec.note,
             rec.author, rec.tags, now, now),
        )

    def for_document(self, doc_path: str) -> list[AnnotationRecord]:
        return [AnnotationRecord.from_row(r) for r in self._db.query(
            "SELECT * FROM annotations WHERE doc_path=? ORDER BY page, created_at",
            (doc_path,))]

    def all(self) -> list[AnnotationRecord]:
        return [AnnotationRecord.from_row(r) for r in self._db.query(
            "SELECT * FROM annotations ORDER BY modified_at DESC")]

    def delete(self, uuid: str) -> None:
        self._db.execute("DELETE FROM annotations WHERE uuid=?", (uuid,))

    def delete_for_document(self, doc_path: str) -> None:
        self._db.execute("DELETE FROM annotations WHERE doc_path=?", (doc_path,))

    def count_for_document(self, doc_path: str) -> int:
        row = self._db.query_one(
            "SELECT COUNT(*) AS c FROM annotations WHERE doc_path=?", (doc_path,))
        return row["c"] if row else 0


class NoteRepository:
    def __init__(self, db) -> None:
        self._db = db

    def create(self, rec: NoteRecord) -> int:
        now = time.time()
        rec.created_at = now
        rec.modified_at = now
        return self._db.execute(
            """INSERT INTO notes (doc_path, title, body, tags, anchors_json,
               created_at, modified_at) VALUES (?,?,?,?,?,?,?)""",
            (rec.doc_path, rec.title, rec.body, rec.tags, rec.anchors_json,
             now, now),
        ).lastrowid

    def update(self, rec: NoteRecord) -> None:
        rec.modified_at = time.time()
        self._db.execute(
            """UPDATE notes SET doc_path=?, title=?, body=?, tags=?,
               anchors_json=?, modified_at=? WHERE id=?""",
            (rec.doc_path, rec.title, rec.body, rec.tags, rec.anchors_json,
             rec.modified_at, rec.id),
        )

    def delete(self, note_id: int) -> None:
        self._db.execute("DELETE FROM notes WHERE id=?", (note_id,))

    def all(self) -> list[NoteRecord]:
        return [NoteRecord.from_row(r) for r in self._db.query(
            "SELECT * FROM notes ORDER BY modified_at DESC")]

    def for_document(self, doc_path: str) -> list[NoteRecord]:
        return [NoteRecord.from_row(r) for r in self._db.query(
            "SELECT * FROM notes WHERE doc_path=? ORDER BY modified_at DESC",
            (doc_path,))]


class ProgressRepository:
    def __init__(self, db) -> None:
        self._db = db

    def save(self, doc_path: str, page: int, scroll: float, zoom: float,
             mode: str = "") -> None:
        self._db.execute(
            """INSERT INTO reading_progress (doc_path, page, scroll, zoom, mode, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(doc_path) DO UPDATE SET page=excluded.page,
                 scroll=excluded.scroll, zoom=excluded.zoom,
                 mode=excluded.mode, updated_at=excluded.updated_at""",
            (doc_path, page, scroll, zoom, mode, time.time()))

    def load(self, doc_path: str) -> Optional[dict[str, Any]]:
        row = self._db.query_one(
            "SELECT * FROM reading_progress WHERE doc_path=?", (doc_path,))
        if not row:
            return None
        return {"page": row["page"], "scroll": row["scroll"], "zoom": row["zoom"],
                "mode": row["mode"], "updated_at": row["updated_at"]}

    def forget(self, doc_path: str) -> None:
        self._db.execute("DELETE FROM reading_progress WHERE doc_path=?", (doc_path,))


class SessionRepository:
    def __init__(self, db) -> None:
        self._db = db

    def start(self, doc_path: str) -> int:
        return self._db.execute(
            "INSERT INTO reading_sessions (doc_path, started_at) VALUES (?,?)",
            (doc_path, time.time())).lastrowid

    def end(self, session_id: int, pages_read: int) -> None:
        self._db.execute(
            "UPDATE reading_sessions SET ended_at=?, pages_read=? WHERE id=?",
            (time.time(), pages_read, session_id))

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._db.query(
            """SELECT doc_path, started_at, ended_at, pages_read
               FROM reading_sessions WHERE ended_at > 0
               ORDER BY started_at DESC LIMIT ?""", (limit,))
        return [dict(r) for r in rows]

    def total_minutes(self) -> float:
        row = self._db.query_one(
            "SELECT SUM(ended_at - started_at) AS total FROM reading_sessions")
        return (row["total"] or 0) / 60.0 if row else 0.0


class SmartCollectionRepository:
    def __init__(self, db) -> None:
        self._db = db

    def add(self, name: str, rules_json: str, built_in: bool = False) -> int:
        """Insert a smart collection; returns its row id (existing id if the
        name already exists, matching the built-in INSERT OR IGNORE)."""
        row = self._db.query_one(
            "SELECT id FROM smart_collections WHERE name=?", (name,))
        if row is not None:
            return int(row["id"])
        self._db.execute(
            "INSERT INTO smart_collections (name, rules_json, built_in) "
            "VALUES (?, ?, ?)", (name, rules_json, int(built_in)))
        row = self._db.query_one(
            "SELECT id FROM smart_collections WHERE name=?", (name,))
        return int(row["id"])

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM smart_collections ORDER BY name")]

    def evaluate(self, rules_json: str, doc: DocumentRecord,
                 extra: dict[str, Any]) -> bool:
        try:
            rules = json.loads(rules_json)
        except (json.JSONDecodeError, TypeError):
            return False
        fields = doc.to_dict()
        fields.update(extra)
        for field_name, op, value in rules:
            actual = fields.get(field_name)
            try:
                if op == "eq" and not (actual == value):
                    return False
                elif op == "ne" and not (actual != value):
                    return False
                elif op == "lt" and not (actual is not None and actual < value):
                    return False
                elif op == "gt" and not (actual is not None and actual > value):
                    return False
                elif op == "contains" and not (value.lower() in str(actual or "").lower()):
                    return False
                elif op == "within_days":
                    ref = actual or 0
                    if not (ref > 0 and (time.time() - ref) <= value * 86400):
                        return False
            except (TypeError, ValueError):
                return False
        return True
