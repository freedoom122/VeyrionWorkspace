"""Grind batch 5 regression tests.

Pins the product fixes behind the sweep-drift batch: annotation lookups
across path separators, settings unknown-key preservation (the roundtrip
bug), smart-collection creation, the summarizer's punctuation-less-text
fallback, printer-name validation, and the command-palette aliases.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ------------------------------------------------------- annotation paths
def test_annotation_paths_normalize_across_separators(db):
    """Windows user paths and fitz POSIX paths must find the same records."""
    from veyrion_workspace.core.annotations.service import AnnotationService
    from veyrion_workspace.storage.repositories import AnnotationRepository

    svc = AnnotationService(AnnotationRepository(db), None)
    win_path = "C:/docs/sweep/sample.pdf".replace("/", "\\")
    posix_path = "C:/docs/sweep/sample.pdf"

    svc.create(win_path, 0, "highlight", rect=(10, 10, 100, 20))
    assert len(svc.for_document(posix_path)) == 1
    assert len(svc.for_document(win_path)) == 1
    assert svc.count_for_document(posix_path) == 1

    svc.create(posix_path, 1, "note", text="x")
    # Same file, both spellings, one record each per page.
    assert svc.count_for_document(win_path) == 2


# --------------------------------------------------------------- settings
def test_settings_unknown_keys_survive_reload(tmp_path):
    """Non-default keys (plugins, future versions) must not vanish on load."""
    from veyrion_workspace.services.settings import Settings

    s = Settings(directory=tmp_path)
    s.set("general", "future_key", "future-value")
    s.set("appearance", "plugin_dict", {"a": 1})

    reloaded = Settings(directory=tmp_path)
    assert reloaded.get("general", "future_key") == "future-value"
    assert reloaded.get("appearance", "plugin_dict") == {"a": 1}


def test_settings_enum_rejection_still_holds(tmp_path):
    from veyrion_workspace.services.settings import Settings

    s = Settings(directory=tmp_path)
    s.set("appearance", "theme", "not-a-real-theme")
    assert s.get("appearance", "theme") == "light"


# -------------------------------------------------------- smart collections
def test_smart_collection_add_is_idempotent(db):
    from veyrion_workspace.storage.repositories import SmartCollectionRepository

    repo = SmartCollectionRepository(db)
    before = {r["name"] for r in repo.all()}
    id1 = repo.add("Sweep recent", '[["last_opened","within_days",30]]')
    id2 = repo.add("Sweep recent", '[["last_opened","within_days",30]]')
    assert id1 == id2
    names = {r["name"] for r in repo.all()}
    assert "Sweep recent" in names and names - before == {"Sweep recent"}


# ------------------------------------------------------------- textstats
def test_summarize_handles_punctuationless_text():
    from veyrion_workspace.core.summarization.textstats import summarize

    text = "\n".join(
        f"line {i} sweep text file content item {i} more words here"
        for i in range(12))
    out = summarize(text, 3)
    assert out and all(isinstance(s, str) and s.strip() for s in out)
    assert len(out) <= 3


def test_summarize_still_works_on_normal_prose():
    from veyrion_workspace.core.summarization.textstats import summarize

    text = (" ".join(
        f"Sentence number {i} talks about testing and quality in software."
        for i in range(10)) + ". ") * 2
    out = summarize(text, 2)
    assert out


# --------------------------------------------------------------- printing
def test_validate_printer_name_accepts_real_names():
    from veyrion_workspace.core.printing.printing import validate_printer_name

    assert validate_printer_name("HP LaserJet 400") == "HP LaserJet 400"
    assert validate_printer_name("  office  ") == "office"


@pytest.mark.parametrize("bad", ["", "   ", "a\nb", "x; rm -rf /", "a`b",
                                 'q"q', "back\\slash", "p|pe", "a\tb"])
def test_validate_printer_name_rejects_hostile_strings(bad):
    from veyrion_workspace.core.printing.printing import (
        PrinterNameError, validate_printer_name)

    with pytest.raises(PrinterNameError):
        validate_printer_name(bad)


def test_build_lp_command_rejects_bad_printer():
    from veyrion_workspace.core.printing.printing import PrinterNameError, build_lp_command

    with pytest.raises(PrinterNameError):
        build_lp_command("x.pdf", "bad\nname")


def test_send_to_printer_returns_false_on_invalid_name(tmp_path, monkeypatch):
    """A hostile printer name must fail gracefully, never raise or inject."""
    from veyrion_workspace.core.printing import printing

    called = []
    monkeypatch.setattr(printing.subprocess, "run",
                        lambda *a, **k: called.append(a))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    assert printing.send_to_printer(pdf, "evil; name") is False
    assert called == []  # nothing executed


# ---------------------------------------------------------------- palette
def test_command_palette_aliases(qapp):
    from PySide6.QtWidgets import QMainWindow

    from veyrion_workspace.ui.command_palette import CommandPalette

    host = QMainWindow()
    pal = CommandPalette(host)
    assert pal.search is pal._input
    assert pal.list is pal._list
    assert callable(pal.open_palette) and callable(pal.close_palette)
    pal.close_palette()
    host.deleteLater()


# ------------------------------------------------------------------ vault
def test_vault_api_methods_callable(tmp_path):
    """Sweep regression: exists/is_locked are methods, not properties."""
    from veyrion_workspace.core.security.vault import Vault

    v = Vault(tmp_path / "vault")
    assert v.exists() is False
    assert v.is_locked() is True
