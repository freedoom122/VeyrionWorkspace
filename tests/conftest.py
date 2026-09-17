"""Shared fixtures: isolated app-data dir and sample documents.

Every test gets a fresh VEYRION_DATA_DIR so no test touches the real
user database. Sample documents are generated with the same libraries the
app ships, so the suite runs offline and hermetic.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path

import pytest

# Ensure the package is importable from the repo layout.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "appdata"
    monkeypatch.setenv("VEYRION_DATA_DIR", str(d))
    return d


@pytest.fixture()
def sample_dir(tmp_path) -> Path:
    d = tmp_path / "samples"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture()
def sample_pdf(sample_dir: Path) -> Path:
    import fitz
    path = sample_dir / "sample.pdf"
    doc = fitz.open()
    for i in range(6):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i + 1} of the sample document")
        page.insert_text((72, 130), f"Alpha beta gamma delta {i}")
        if i == 0:
            page.insert_text((72, 160), "Confidential report summary")
    doc.set_metadata({"title": "Sample PDF", "author": "Veyrion Tests"})
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def sample_epub(sample_dir: Path) -> Path:
    from ebooklib import epub
    path = sample_dir / "sample.epub"
    book = epub.EpubBook()
    book.set_identifier("veyrion-test-epub")
    book.set_title("Test E-book")
    book.set_language("en")
    book.add_author("Test Author")
    c1 = epub.EpubHtml(title="Chapter One", file_name="chap1.xhtml", lang="en")
    c1.content = ("<html><body><h1>Chapter One</h1>"
                  "<p>The quick brown fox jumps over the lazy dog.</p>"
                  "</body></html>")
    c2 = epub.EpubHtml(title="Chapter Two", file_name="chap2.xhtml", lang="en")
    c2.content = ("<html><body><h1>Chapter Two</h1>"
                  "<p>Another chapter with distinct content.</p>"
                  "</body></html>")
    book.add_item(c1)
    book.add_item(c2)
    book.toc = (epub.Link("chap1.xhtml", "Chapter One", "c1"),
                epub.Link("chap2.xhtml", "Chapter Two", "c2"))
    book.spine = ["nav", c1, c2]
    epub.write_epub(str(path), book)
    return path


@pytest.fixture()
def sample_docx(sample_dir: Path) -> Path:
    import docx
    path = sample_dir / "sample.docx"
    d = docx.Document()
    d.add_heading("Test Heading", level=1)
    d.add_paragraph("A paragraph with regular words for testing.")
    d.add_heading("Second Heading", level=2)
    d.add_paragraph("Another paragraph of content.")
    d.save(str(path))
    return path


@pytest.fixture()
def sample_txt(sample_dir: Path) -> Path:
    path = sample_dir / "sample.txt"
    path.write_text(
        "Line one of the text file\n# A heading\nBody content here.\n" * 40,
        encoding="utf-8")
    return path


@pytest.fixture()
def sample_md(sample_dir: Path) -> Path:
    path = sample_dir / "sample.md"
    path.write_text(
        "# Title\n\nIntro paragraph with **bold** text.\n\n"
        "## Section\n\n- item one\n- item two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
        encoding="utf-8")
    return path


@pytest.fixture()
def sample_cbz(sample_dir: Path) -> Path:
    from PIL import Image
    path = sample_dir / "comic.cbz"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(3):
            buf = io.BytesIO()
            img = Image.new("RGB", (200, 300), (200, 30 + i * 40, 60))
            img.save(buf, format="PNG")
            zf.writestr(f"page{i + 1:03d}.png", buf.getvalue())
    return path


@pytest.fixture()
def sample_image(sample_dir: Path) -> Path:
    from PIL import Image
    path = sample_dir / "photo.png"
    Image.new("RGB", (320, 240), (90, 120, 160)).save(path)
    return path


@pytest.fixture()
def sample_csv(sample_dir: Path) -> Path:
    path = sample_dir / "data.csv"
    path.write_text("name,value\nalpha,1\nbeta,2\ngamma,3\n", encoding="utf-8")
    return path


@pytest.fixture()
def db(data_dir: Path):
    from veyrion_workspace.storage.database import Database
    database = Database()
    yield database
    database.close()


@pytest.fixture()
def settings(data_dir: Path):
    from veyrion_workspace.services.settings import Settings
    return Settings()


@pytest.fixture(scope="session")
def qapp():
    """One offscreen QApplication shared by all UI tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def main_window(qapp, data_dir):
    """A real MainWindow over isolated services (closed on teardown)."""
    from veyrion_workspace.services.recovery import SessionJournal
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.services.tasks import TaskManager
    from veyrion_workspace.storage.database import Database
    from veyrion_workspace.ui.main_window import MainWindow
    settings = Settings()
    db = Database()
    tasks = TaskManager(2)
    journal = SessionJournal(data_dir / "sessions")
    w = MainWindow(settings, db, tasks, journal)
    w.resize(1200, 800)
    yield w, settings
    w.close()
    w.deleteLater()
    db.close()


@pytest.fixture()
def app_launcher(qapp, data_dir):
    """Factory that boots a full MainWindow over the shared data dir.

    Each call simulates a fresh app launch: brand-new Settings (re-read from
    ``settings.json`` on disk), database, task manager, and session journal.
    Returns ``(window, settings)``; every window opened this way is closed on
    teardown.
    """
    from veyrion_workspace.services.recovery import SessionJournal
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.services.tasks import TaskManager
    from veyrion_workspace.storage.database import Database
    from veyrion_workspace.ui.main_window import MainWindow

    opened: list[tuple] = []

    def launch():
        settings = Settings()
        db = Database()
        tasks = TaskManager(2)
        journal = SessionJournal(data_dir / "sessions")
        w = MainWindow(settings, db, tasks, journal)
        w.resize(1200, 800)
        opened.append((w, db))
        return w, settings

    yield launch
    for w, db in opened:
        w.close()
        w.deleteLater()
        db.close()