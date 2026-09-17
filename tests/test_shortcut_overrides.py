"""User-pasted keyboard shortcut maps (``services.shortcuts``).

Covers the file format, validation and reporting, rebinding real actions,
restoring built-ins when an entry disappears, how the stored settings map
interacts with the file, the documented template, and the main-window wiring
(actions, menu, palette, startup application, reset).
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from veyrion_workspace.services.shortcuts import (  # noqa: E402
    SHORTCUTS_FILENAME,
    action_id,
    collect_actions,
    overrides_path,
    read_overrides,
    text_of,
    write_template,
)


def _key(action) -> str:
    return action.shortcut().toString()


def _write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------- action ids

def test_action_ids_cover_the_window(main_window):
    w, _settings = main_window
    actions = collect_actions(w)
    assert len(actions) > 50, f"only {len(actions)} bindable actions found"
    for expected in ("open", "save", "print", "undo", "zoom_in", "zoom_out",
                     "quit", "goto_page", "autoscroll", "shortcuts"):
        assert expected in actions, f"{expected!r} is not bindable"
    # Every id maps back to the ``act_*`` attribute it came from.
    assert actions["zoom_in"] is w.act_zoom_in
    assert actions["goto_page"] is w.act_goto_page
    assert action_id("act_zoom_in") == "zoom_in"
    assert action_id("zoom_in") == "zoom_in"
    assert text_of(w.act_zoom_in.shortcut()).startswith("Ctrl")


# ------------------------------------------------------------ reading files

def test_overrides_path_lives_in_the_settings_dir(data_dir):
    path = overrides_path()
    assert path.name == SHORTCUTS_FILENAME
    assert str(path).endswith(os.path.join("settings", SHORTCUTS_FILENAME))
    assert path.parent.parent == data_dir


def test_missing_file_is_not_an_error(data_dir):
    load = read_overrides()
    assert load.exists is False
    assert load.ok is True
    assert load.mapping == {}
    assert load.error is None


@pytest.mark.parametrize("payload", [
    {"zoom_in": "Ctrl+9"},                                # wrapper-less
    {"bindings": {"zoom_in": "Ctrl+9"}},
    {"shortcuts": {"zoom_in": "Ctrl+9"}},
    {"keys": {"zoom_in": "Ctrl+9"}},
    {"overrides": {"zoom_in": "Ctrl+9"}},
    {"version": 1, "bindings": {"zoom_in": "Ctrl+9"}},
])
def test_accepted_file_shapes(tmp_path, payload):
    path = tmp_path / SHORTCUTS_FILENAME
    _write(path, payload)
    load = read_overrides(path)
    assert load.ok, load.error
    assert load.exists
    assert load.mapping == {"zoom_in": "Ctrl+9"}, load.mapping


def test_comments_and_metadata_are_ignored(tmp_path):
    path = tmp_path / SHORTCUTS_FILENAME
    _write(path, {
        "_readme": "instructions",
        "$schema": "whatever",
        "#note": "hash comment",
        "//": "slash comment",
        "schema_version": 1,
        "bindings": {"act_save": "Ctrl+Alt+S"},
    })
    load = read_overrides(path)
    assert load.ok, load.error
    assert load.mapping == {"save": "Ctrl+Alt+S"}, load.mapping


def test_non_object_documents_report_an_error(tmp_path):
    path = tmp_path / SHORTCUTS_FILENAME
    _write(path, "[1, 2, 3]")
    load = read_overrides(path)
    assert not load.ok and load.mapping == {}
    assert "object" in load.error

    _write(path, "{ not json at all")
    load = read_overrides(path)
    assert not load.ok and load.error
    assert "JSON" in load.error


# ---------------------------------------------------------------- applying

def test_apply_rebinds_unbinds_and_reports(main_window):
    from veyrion_workspace.services.shortcuts import apply_overrides

    w, _settings = main_window
    mapping = {
        "zoom_in": "Ctrl+Shift+=",
        "act_zoom_out": "Ctrl+Shift+-",
        "goto_page": ["Ctrl+G", "Ctrl+L"],
        "quit": None,
        "autoscroll": "",
        "does_not_exist": "Ctrl+Alt+Q",
        "print": "not-a-real-key",
        "save": 42,
    }
    report = apply_overrides(w, mapping, source="test.json")
    assert report.source.endswith("test.json")

    assert _key(w.act_zoom_in) == "Ctrl+Shift+="
    assert _key(w.act_zoom_out) == "Ctrl+Shift+-"
    assert [_key(w.act_goto_page), w.act_goto_page.shortcuts()[1].toString()] \
        == ["Ctrl+G", "Ctrl+L"]
    assert w.act_quit.shortcuts() == []
    assert w.act_autoscroll.shortcuts() == []
    # Invalid entries leave the built-in binding alone.
    assert _key(w.act_print) == "Ctrl+P"
    assert _key(w.act_save) == "Ctrl+S"

    assert set(report.applied) == {"zoom_in", "zoom_out", "goto_page"}
    assert sorted(report.unbound) == ["autoscroll", "quit"]
    assert report.unknown == ["does_not_exist"]
    assert len(report.invalid) == 2
    assert any("print" in msg for msg in report.invalid)
    assert any("save" in msg for msg in report.invalid)
    assert report.count == 5
    assert "rebound" in report.summary()
    assert "unknown" in report.summary()


def test_removing_an_entry_restores_the_built_in_binding(main_window, data_dir):
    w, _settings = main_window
    path = overrides_path()
    builtin = _key(w.act_zoom_in)

    _write(path, {"bindings": {"zoom_in": "Ctrl+Alt+9"}})
    w.action_reload_shortcut_overrides()
    assert _key(w.act_zoom_in) == "Ctrl+Alt+9"

    # Deleting the entry (and reloading) really does undo the override.
    _write(path, {"bindings": {}})
    report = w.action_reload_shortcut_overrides()
    assert _key(w.act_zoom_in) == builtin
    assert report.count == 0
    assert report.summary() == "No shortcut overrides applied"


def test_corrupt_file_keeps_built_ins_and_reports(main_window, data_dir):
    w, _settings = main_window
    _write(overrides_path(), "{ oops")
    report = w.action_reload_shortcut_overrides()
    assert report.error and "JSON" in report.error
    assert _key(w.act_zoom_in) == "Ctrl++"
    assert report.summary() == report.error


def test_conflicting_binding_is_reported(main_window):
    from veyrion_workspace.services.shortcuts import apply_overrides
    w, _settings = main_window
    report = apply_overrides(w, {"next_page": "Ctrl+O"})
    assert report.conflicts, "duplicate binding was not reported"
    assert "Ctrl+O" in report.conflicts[0]
    assert "open" in report.conflicts[0] and "next_page" in report.conflicts[0]
    # Only conflicts involving an overridden action are noise-worthy.
    clean = apply_overrides(w, {"next_page": "Ctrl+Alt+Right"})
    assert clean.conflicts == []


def test_settings_map_is_applied_and_the_file_wins(main_window, data_dir):
    w, _settings = main_window
    # The window's own Settings instance is the source of truth it reloads
    # from; a dialog writing through it must be picked up immediately.
    w.settings.set("keyboard", "custom", {"zoom_in": "Ctrl+9"})
    w.action_reload_shortcut_overrides()
    assert _key(w.act_zoom_in) == "Ctrl+9"

    # A pasted file beats a stale stored value for the same id.
    _write(overrides_path(), {"bindings": {"zoom_in": "Ctrl+8"}})
    w.action_reload_shortcut_overrides()
    assert _key(w.act_zoom_in) == "Ctrl+8"

    # Removing the file falls back to the stored map, not to the default.
    overrides_path().unlink()
    w.action_reload_shortcut_overrides()
    assert _key(w.act_zoom_in) == "Ctrl+9"


def test_reset_returns_every_action_to_its_default(main_window, data_dir):
    w, _settings = main_window
    _write(overrides_path(), {"bindings": {"zoom_in": "Ctrl+9",
                                           "quit": None}})
    w.action_reload_shortcut_overrides()
    assert _key(w.act_zoom_in) == "Ctrl+9"
    assert w.act_quit.shortcuts() == []

    w.action_reset_shortcut_overrides()
    assert _key(w.act_zoom_in) == "Ctrl++"
    assert _key(w.act_quit) == "Ctrl+Q"
    # The file is left untouched: reset is in-memory only.
    assert overrides_path().exists()
    assert w.action_reload_shortcut_overrides().count == 2


# ----------------------------------------------------------------- template

def test_template_lists_every_id_and_is_inert(main_window, tmp_path):
    w, _settings = main_window
    target = tmp_path / SHORTCUTS_FILENAME
    write_template(target, actions=collect_actions(w))

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["bindings"] == {}
    assert payload["_readme"] and "bindings" in payload["_readme"]
    assert payload["_current"]["zoom_in"] == "Ctrl++"
    assert payload["_current"]["quit"] == "Ctrl+Q"
    assert payload["_available_ids"] == sorted(payload["_current"])
    assert set(payload["_available_ids"]) == set(collect_actions(w))

    # Loading the pristine template changes nothing.
    load = read_overrides(target)
    assert load.ok and load.mapping == {}
    report = w.action_reload_shortcut_overrides()
    assert report.count == 0


def test_template_does_not_clobber_user_content(main_window, tmp_path):
    w, _settings = main_window
    target = tmp_path / SHORTCUTS_FILENAME
    _write(target, {"bindings": {"zoom_in": "Ctrl+9"}})
    write_template(target, actions=collect_actions(w))
    assert json.loads(target.read_text(encoding="utf-8")) == \
        {"bindings": {"zoom_in": "Ctrl+9"}}
    # Explicit overwrite is still available for a "start over" workflow.
    write_template(target, actions=collect_actions(w), overwrite=True)
    assert json.loads(target.read_text(encoding="utf-8"))["bindings"] == {}


# ------------------------------------------------------- startup / wiring

def test_overrides_apply_on_a_fresh_launch(app_launcher, data_dir):
    _write(overrides_path(), {
        "bindings": {"zoom_in": "Ctrl+Alt+=", "quit": None},
    })
    w, _settings = app_launcher()
    assert _key(w.act_zoom_in) == "Ctrl+Alt+="
    assert w.act_quit.shortcuts() == []
    assert w.shortcut_report.count == 2


def test_window_wiring_and_menu_palette(main_window):
    w, _settings = main_window
    from veyrion_workspace.ui.shortcuts_dialog import collect_menu_shortcuts

    for name in ("act_shortcut_file", "act_shortcuts_reload",
                 "act_shortcuts_reset"):
        assert getattr(w, name, None) is not None, f"{name} missing"
    labels = [r[1] for r in collect_menu_shortcuts(w.menuBar())]
    for label in ("Edit Shortcut Overrides File…",
                  "Reload Shortcut Overrides", "Reset Shortcut Overrides"):
        assert label in labels, f"{label!r} is not reachable from a menu"

    titles = [c.title for c in w._palette_commands()]
    assert "Reload Shortcut Overrides" in titles
    assert "Edit Shortcut Overrides File…" in titles
    assert "Reset Shortcut Overrides" in titles

    # The file-management actions stay unbound; only the cheat sheet keeps a
    # key (F1), so nothing here can shadow a user's own map.
    for name in ("shortcut_file", "shortcuts_reload", "shortcuts_reset"):
        assert getattr(w, f"act_{name}").shortcuts() == []
    assert _key(w.act_shortcuts) == "F1"


def test_reload_action_rebinds_live(main_window, data_dir):
    w, _settings = main_window
    _write(overrides_path(), {"bindings": {"find": "Ctrl+Alt+F"}})
    report = w.action_reload_shortcut_overrides()
    assert report.applied == {"find": "Ctrl+Alt+F"}
    assert _key(w.act_find) == "Ctrl+Alt+F"
