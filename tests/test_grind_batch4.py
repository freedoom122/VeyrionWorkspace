"""Grind batch 4 regression tests.

Covers: SplitView-hosting tabs no longer crash closeEvent / close_tab /
a second action_split_view (the sweep's real product failures), and the
new settings enum validation rejects bad values on write and on load.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _settle() -> None:
    for _ in range(6):
        QApplication.processEvents()


# ---------------------------------------------------------------- SplitView
def test_close_tab_with_split_tab_does_not_crash(main_window, sample_pdf):
    w, _settings = main_window
    w.show()
    w.open_path(str(sample_pdf))
    _settle()
    w.action_split_view()
    _settle()
    split_idx = w.tabs.currentIndex()
    assert w.tabs.widget(split_idx).__class__.__name__ == "SplitView"
    # The sweep crashed right here: close_tab -> widget(index).is_modified.
    w.close_tab(split_idx)
    _settle()
    assert all(w.tabs.widget(i).__class__.__name__ != "SplitView"
               for i in range(w.tabs.count()))


def test_second_split_view_no_crash(main_window, sample_pdf):
    w, _settings = main_window
    w.show()
    w.open_path(str(sample_pdf))
    _settle()
    w.action_split_view()
    _settle()
    # The sweep crashed here: action_split_view iterated widget(i).is_modified
    # over a tab that hosts a SplitView.
    w.action_split_view()
    _settle()
    splits = [i for i in range(w.tabs.count())
              if w.tabs.widget(i).__class__.__name__ == "SplitView"]
    assert len(splits) >= 1


def test_close_event_with_split_tab_no_crash(main_window, sample_pdf):
    w, _settings = main_window
    w.show()
    w.open_path(str(sample_pdf))
    _settle()
    w.action_split_view()
    _settle()
    # The sweep crashed here: closeEvent -> widget(i).is_modified on SplitView.
    w.close()  # no modified docs -> no dialog
    _settle()


# ----------------------------------------------------------------- settings
def test_settings_rejects_invalid_theme_on_write(tmp_path):
    from veyrion_workspace.services.settings import Settings

    s = Settings(directory=tmp_path)
    default_theme = s.get("appearance", "theme")
    s.set("appearance", "theme", "not-a-real-theme")
    assert s.get("appearance", "theme") == default_theme
    s.set("appearance", "theme", "dark")
    assert s.get("appearance", "theme") == "dark"


def test_settings_drops_invalid_theme_on_load(tmp_path):
    from veyrion_workspace.services.settings import Settings

    s = Settings(directory=tmp_path)
    s.set("appearance", "theme", "dark")
    # Hand-edit the file the way a user would paste a bogus value.
    payload = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    payload["appearance"]["theme"] = "neon-pink"
    (tmp_path / "settings.json").write_text(
        json.dumps(payload), encoding="utf-8")
    s.load()
    assert s.get("appearance", "theme") == "light"  # default restored


def test_settings_unregulated_keys_still_accepted(tmp_path):
    from veyrion_workspace.services.settings import Settings

    s = Settings(directory=tmp_path)
    s.set("general", "future_flag", {"any": "shape"})
    s.set("appearance", "custom_key", 42)
    assert s.get("general", "future_flag") == {"any": "shape"}
    assert s.get("appearance", "custom_key") == 42
