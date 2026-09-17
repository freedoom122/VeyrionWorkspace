"""User-editable keyboard-shortcut overrides.

Every window-level action is created as an ``act_*`` attribute, which gives
each shortcut a stable identifier: ``act_zoom_in`` -> ``zoom_in``. Users can
paste a JSON map of those ids to new key sequences into
``<settings>/shortcuts.json``; the map is read at startup and again whenever
the user asks for a reload, so the cheat sheet always shows what is live.

Accepted file shapes (the wrapper key is optional)::

    {"bindings": {
        "zoom_in": "Ctrl+Shift+=",          # one sequence
        "goto_page": ["Ctrl+G", "Ctrl+L"],  # first is primary, rest are alternates
        "quit": null,                       # null or "" unbinds the action
        "_comment": "keys starting with _ are ignored"
    }}

    {"shortcuts": {...}}      # or {"keys": {...}} / {"overrides": {...}}
    {"zoom_in": "Ctrl+Shift+="}   # wrapper-less map

Unknown ids are reported instead of being silently dropped, and an invalid key
sequence leaves that action's previous binding untouched. Loading never raises:
a corrupt file yields an error string and an empty map, leaving the app's
built-in bindings in place.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtGui import QAction, QKeySequence

from veyrion_workspace.app import paths

logger = logging.getLogger("veyrion.shortcuts")

SHORTCUTS_FILENAME = "shortcuts.json"
SCHEMA_VERSION = 1

# Wrapper keys that may hold the id map, in priority order.
_WRAPPER_KEYS = ("bindings", "shortcuts", "keys", "overrides", "custom")
# Non-binding metadata that is not an action id.
_META_KEYS = {"version", "schema_version"}
# Prefixes marking a key as documentation rather than a binding.
_COMMENT_PREFIXES = ("_", "$", "//", "#")

ACTION_PREFIX = "act_"


def action_id(name: str) -> str:
    """Normalise an action identifier (``act_zoom_in`` -> ``zoom_in``)."""
    text = str(name).strip()
    return text[len(ACTION_PREFIX):] if text.startswith(ACTION_PREFIX) else text


def text_of(seq: QKeySequence | None) -> str:
    """Human-readable key text ('' for an unbound sequence)."""
    if seq is None or seq.isEmpty():
        return ""
    return seq.toString(QKeySequence.NativeText) or seq.toString()


def overrides_path(directory: Path | None = None) -> Path:
    """Location of the user's shortcut overrides file."""
    base = Path(directory) if directory else paths.settings_dir()
    return base / SHORTCUTS_FILENAME


def collect_actions(window) -> dict[str, QAction]:
    """Map stable id -> QAction for every ``act_*`` attribute of *window*."""
    found: dict[str, QAction] = {}
    for name, value in vars(window).items():
        if name.startswith(ACTION_PREFIX) and isinstance(value, QAction):
            found[action_id(name)] = value
    return dict(sorted(found.items()))


def snapshot(actions: dict[str, QAction]) -> dict[str, list[QKeySequence]]:
    """Record the built-in bindings so a reload can undo removed overrides."""
    return {aid: list(act.shortcuts()) for aid, act in actions.items()}


@dataclass
class OverrideLoad:
    """Result of reading an overrides file from disk."""

    path: Path
    mapping: dict[str, Any] = field(default_factory=dict)
    exists: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _extract_mapping(payload: Any) -> dict[str, Any] | None:
    """Pull the id -> value map out of a parsed JSON document."""
    if not isinstance(payload, dict):
        return None
    for key in _WRAPPER_KEYS:
        wrapped = payload.get(key)
        if isinstance(wrapped, dict):
            return wrapped
    return payload


def read_overrides(path: Path | None = None) -> OverrideLoad:
    """Read the overrides file. Never raises; problems land in the result."""
    target = Path(path) if path else overrides_path()
    load = OverrideLoad(path=target)
    if not target.exists():
        return load
    load.exists = True
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:  # unreadable, missing, or not JSON at all
        load.error = f"{target.name} is not valid JSON ({type(e).__name__})"
        logger.warning("could not read shortcut overrides: %s", e)
        return load

    payload = _extract_mapping(raw)
    if payload is None:
        load.error = f"{target.name} must contain a JSON object of bindings"
        return load

    for key, value in payload.items():
        name = str(key)
        if name in _META_KEYS or name.startswith(_COMMENT_PREFIXES):
            continue
        load.mapping[action_id(name)] = value
    return load


@dataclass
class ShortcutReport:
    """What happened the last time overrides were applied."""

    source: str = ""
    applied: dict[str, str] = field(default_factory=dict)   # id -> key text
    unbound: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def overridden(self) -> set[str]:
        """Ids whose binding differs from the built-in default right now."""
        return set(self.applied) | set(self.unbound)

    @property
    def count(self) -> int:
        return len(self.overridden)

    @property
    def has_prefix(self) -> bool:
        """True when text ('' for an unbound action) is worth applying."""
        return bool(self.applied) or bool(self.unbound)

    def summary(self) -> str:
        """One-line description suitable for a toast."""
        if self.error:
            return self.error
        if not self.has_prefix:
            return "No shortcut overrides applied"
        bits = [f"{len(self.applied)} shortcut(s) rebound"]
        if self.unbound:
            bits.append(f"{len(self.unbound)} unbound")
        tail = []
        if self.unknown:
            tail.append(f"{len(self.unknown)} unknown id(s)")
        if self.invalid:
            tail.append(f"{len(self.invalid)} invalid")
        if self.conflicts:
            tail.append(f"{len(self.conflicts)} conflict(s)")
        if tail:
            bits.append("— " + ", ".join(tail))
        return " · ".join(bits)


def _prepare(value: Any) -> tuple[list[QKeySequence], str | None]:
    """Validate one override value into sequences, or return an error text."""
    if value is None:
        return [], None
    raw_items = value if isinstance(value, (list, tuple)) else [value]
    if not raw_items:
        return [], None
    sequences: list[QKeySequence] = []
    for item in raw_items:
        if not isinstance(item, str):
            return [], f"{item!r} is not a key sequence string"
        text = item.strip()
        if not text:
            return [], None                      # "" unbinds
        # Qt happily builds a "null key" sequence from unparseable text; such
        # a sequence has no readable form, which is how we spot it here.
        if not text_of(QKeySequence(text)):
            return [], f"{item!r} is not a valid key sequence"
        sequences.append(QKeySequence(text))
    return sequences, None


def _find_conflicts(actions, overridden: set[str]) -> list[str]:
    """Report duplicate bindings that involve an overridden action."""
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for aid, act in actions.items():
        keys = act.shortcuts()
        if not keys:
            continue
        primary = keys[0].toString()
        if not primary:
            continue
        other = seen.get(primary)
        if other is None:
            seen[primary] = aid
            continue
        if aid in overridden or other in overridden:
            clashes.append(f"{text_of(keys[0])} is bound to both "
                           f"{other} and {aid}")
    return clashes


def apply_overrides(window, mapping: dict[str, Any] | None = None, *,
                    baseline: dict[str, list[QKeySequence]] | None = None,
                    source: str = "") -> ShortcutReport:
    """Rebind *window*'s actions from *mapping* and report what changed.

    Built-in bindings are restored first (from a baseline snapshot kept on the
    window), so deleting an entry from the file and reloading really does
    return that action to its default.
    """
    actions = collect_actions(window)
    if baseline is None:
        baseline = getattr(window, "_shortcut_baseline", None)
    if baseline is None:
        baseline = snapshot(actions)
        try:
            window._shortcut_baseline = baseline
        except Exception:            # pragma: no cover - defensive
            pass

    for aid, keys in baseline.items():
        act = actions.get(aid)
        if act is not None and list(act.shortcuts()) != list(keys):
            act.setShortcuts(list(keys))

    report = ShortcutReport(source=source)
    for raw_id, value in (mapping or {}).items():
        aid = action_id(raw_id)
        act = actions.get(aid)
        if act is None:
            report.unknown.append(aid)
            continue
        sequences, problem = _prepare(value)
        if problem:
            report.invalid.append(f"{aid}: {problem}")
            continue
        if not sequences:
            act.setShortcuts([])
            report.unbound.append(aid)
            continue
        act.setShortcuts(sequences)
        report.applied[aid] = text_of(sequences[0])

    report.conflicts = _find_conflicts(actions, report.overridden)
    if report.overridden:
        logger.info("applied %d keyboard shortcut override(s) from %s",
                    report.count, source or "settings")
    return report


def write_template(path: Path | None = None, *,
                   actions: dict[str, QAction] | None = None,
                   overwrite: bool = False) -> Path:
    """Write a documented starter file listing every bindable id.

    The document is inert until the user fills in ``bindings``: the
    reference tables live under ``_``-prefixed keys, which the loader
    deliberately ignores.
    """
    target = Path(path) if path else overrides_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        return target

    current = {aid: text_of(act.shortcut()) for aid, act in (actions or {}).items()}
    payload = {
        "_readme": (
            "Paste your key overrides into \"bindings\" and save, then use "
            "Settings > Reload Shortcut Overrides (or restart the app). "
            "A value may be a key sequence (\"Ctrl+Shift+=\"), a list of "
            "alternatives (first is primary), or null/\"\" to unbind. Keys "
            "starting with _ are documentation and are ignored."
        ),
        "version": SCHEMA_VERSION,
        "bindings": {},
        "_current": current,
        "_available_ids": sorted(current),
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target
