"""Versioned JSON settings storage with validation and corruption recovery.

Settings are validated on load; if the file is corrupt, a timestamped backup
is preserved and defaults are restored (the caller can inform the user via
the returned ``restored_defaults`` flag).
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from veyrion_workspace.app import paths

logger = logging.getLogger("veyrion.settings")

SCHEMA_VERSION = 1

# Enum-valued settings: only these values are accepted on write and load.
# Anything else is rejected (previous value kept) and logged.
_ENUM_SETTINGS: dict[tuple[str, str], frozenset] = {
    ("appearance", "theme"): frozenset(
        {"light", "dark", "oled", "sepia", "high-contrast"}),
}


def _is_allowed_value(section: str, key: str, value: Any) -> bool:
    """True when *value* is legal (unregulated keys always allow)."""
    choices = _ENUM_SETTINGS.get((section, key))
    return choices is None or value in choices


DEFAULTS: dict[str, Any] = {
    "general": {
        "first_run_complete": False,
        "default_open_folder": "",
        "watch_folders": [],
        "onboarding_choices": {},
        "restore_session": True,
        "autosave_interval_sec": 120,
        "version_history_depth": 5,
        "confirm_destructive": True,
    },
    "appearance": {
        "theme": "light",              # light|dark|oled|sepia|high-contrast
        "accent": "#C7522A",           # burnt sienna — Veyrion identity color
        "ui_scale": 1.0,
        "large_text": False,
        "reduced_motion": False,
        "dyslexia_font": False,
        "font_family": "",
    },
    "reading": {
        "pdf_mode": "continuous",      # single|continuous|two-page|two-cover
        "fit": "width",                # width|page|height|none
        "zoom": 1.0,
        "smooth_scroll": True,
        "epub_font_family": "serif",
        "epub_font_size": 17,
        "epub_line_height": 1.6,
        "epub_width": 720,
        "epub_theme": "light",
        "comic_rtl": False,
        "comic_double": False,
        "pdf_gap": 14,                 # two-page spread gap (points)
        "pdf_cover_offset": False,     # offset first page in two-cover mode
        "pdf_theme": "",               # per-view PDF theme; "" = app theme
        "toolbar_items": [],           # #78: visible toolbar item keys, in order
    },
    "annotations": {
        "default_color": "#E5B25D",
        "highlight_opacity": 0.4,
        "ink_width": 2.2,
        "font_size": 11,
        "author": "Me",
        "panel_scope": 0,              # AnnotationsPanel scope combo index
        "panel_type_filter": 0,        # AnnotationsPanel type-filter combo index
        "panel_chips": [],             # AnnotationsPanel active legend-chip groups
    },
    "ocr": {
        "enabled": True,
        "language": "eng",
        "engine_path": "",
        "dpi": 300,
    },
    "speech": {
        "rate": 1.0,
        "volume": 1.0,
        "voice_id": "",
    },
    "library": {
        "view": "grid",                # grid|list|compact|table|cover
        "sort": "last_opened",
        "sort_desc": True,
        "thumb_size": 132,
        "cover_flow_size": 200,
    },
    "search": {
        "case_sensitive": False,
        "whole_word": False,
        "regex": False,
        "fuzzy": False,
    },
    "performance": {
        "cache_page_mb": 160,
        "thumbnail_workers": 3,
        "prefetch": 3,
    },
    "privacy": {
        "offline_mode": True,
        "allow_update_check": False,
        "remember_doc_passwords": False,
    },
    "security": {
        "vault_autolock_min": 10,
        "external_links": "ask",       # ask|open|block
        "external_links_remember": False,
    },
    "keyboard": {
        "vim_mode": False,
        "custom": {},
    },
    "session": {
        "tabs": [],
        "active_tab": -1,
        "window": None,
        "panel_state": None,
    },
}


class Settings:
    """Thread-safe JSON-backed settings with schema validation."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = Path(directory) if directory else paths.settings_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "settings.json"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._listeners: list = []
        self.restored_defaults = False
        self.load()

    # -- persistence ---------------------------------------------------
    def load(self) -> None:
        with self._lock:
            self._data = _deep_copy(DEFAULTS)
            self.restored_defaults = False
            if not self._file.exists():
                return
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("settings root is not an object")
                version = raw.get("schema_version", 0)
                if version != SCHEMA_VERSION:
                    logger.info("Settings schema %s -> migrating to %s", version, SCHEMA_VERSION)
                _merge_validated(self._data, raw)
            except Exception as e:
                backup = self._file.with_suffix(f".corrupt-{int(time.time())}.json")
                try:
                    shutil.copy2(self._file, backup)
                except OSError:
                    pass
                logger.error("Settings corrupt (%s); backed up to %s and restored defaults",
                             type(e).__name__, backup.name)
                self._data = _deep_copy(DEFAULTS)
                self.restored_defaults = True

    def save(self) -> None:
        with self._lock:
            payload = dict(self._data)
            payload["schema_version"] = SCHEMA_VERSION
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            tmp.replace(self._file)

    # -- access ---------------------------------------------------------
    def get(self, section: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            if not _is_allowed_value(section, key, value):
                logger.warning(
                    "rejected invalid setting %s.%s = %r", section, key, value)
                return
            self._data.setdefault(section, {})[key] = value
            if save:
                self.save()
        for cb in list(self._listeners):
            try:
                cb(section, key, value)
            except Exception:
                logger.exception("settings listener failed")

    def section(self, name: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._data.get(name, {}))

    def reset_section(self, name: str) -> None:
        with self._lock:
            if name in DEFAULTS:
                self._data[name] = _deep_copy(DEFAULTS[name])
                self.save()
            else:
                self._data.pop(name, None)
                self.save()

    def reset_all(self) -> None:
        with self._lock:
            self._data = _deep_copy(DEFAULTS)
            self.save()

    def all(self) -> dict[str, Any]:  # noqa: A003
        with self._lock:
            return _deep_copy(self._data)

    # -- change notification ---------------------------------------------
    def add_listener(self, callback) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)


def _deep_copy(d: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(d))


def _merge_validated(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Merge loaded settings over defaults, dropping wrong-typed values."""
    for section, values in incoming.items():
        if section == "schema_version" or not isinstance(values, dict):
            continue
        if not isinstance(target.get(section), dict):
            continue
        for key, value in values.items():
            if key in target[section]:
                tv = target[section][key]
                # Type check only for scalar defaults; lists/dicts pass through.
                if isinstance(tv, bool) or isinstance(tv, int) or isinstance(tv, float) or isinstance(tv, str):
                    if type(tv) is not type(value):
                        logger.warning("Dropping invalid setting %s.%s (type mismatch)", section, key)
                        continue
                    if isinstance(tv, float) and isinstance(value, int):
                        value = float(value)
                if not _is_allowed_value(section, key, value):
                    logger.warning("Dropping invalid setting %s.%s (not an allowed choice)",
                                   section, key)
                    continue
                target[section][key] = value
            else:
                # Unknown keys (plugins, future versions, user hand-edits)
                # must survive a reload: keep them verbatim. Type mismatches
                # alone never justify destroying data we do not model.
                target[section].setdefault(key, value)
                target[section][key] = value
