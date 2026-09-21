"""Verify background test safety without creating a Tcl/Tk interpreter."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import _tkinter

import pytest


def guard_plugin():
    path = Path(__file__).resolve().parents[1] / "conftest.py"
    assert path.exists(), "Missing opt-in --no-gui pytest safety plugin"
    spec = importlib.util.spec_from_file_location("background_guard_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config(enabled):
    def getoption(name):
        assert name == "--no-gui"
        return enabled
    return SimpleNamespace(getoption=getoption, addinivalue_line=lambda *args: None)


def test_enabled_guard_skips_before_calling_any_tk_factory(monkeypatch):
    plugin = guard_plugin()
    calls = []

    def sentinel(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Background mode called the Tcl/Tk factory")

    monkeypatch.setattr(_tkinter, "create", sentinel)
    settings = config(True)
    plugin.pytest_configure(settings)
    try:
        with pytest.raises(pytest.skip.Exception, match="--no-gui"):
            _tkinter.create("screen", wantTk=True)
        assert calls == []
    finally:
        plugin.pytest_unconfigure(settings)


def test_disabled_guard_leaves_existing_factory_unchanged(monkeypatch):
    plugin = guard_plugin()
    sentinel = lambda *args, **kwargs: pytest.fail("Factory must not be called")
    monkeypatch.setattr(_tkinter, "create", sentinel)
    settings = config(False)

    plugin.pytest_configure(settings)

    assert _tkinter.create is sentinel
    plugin.pytest_unconfigure(settings)
    assert _tkinter.create is sentinel


def test_teardown_restores_exact_factory_and_can_repeat_without_changes(monkeypatch):
    plugin = guard_plugin()
    sentinel = lambda *args, **kwargs: pytest.fail("Factory must not be called")
    monkeypatch.setattr(_tkinter, "create", sentinel)
    settings = config(True)
    plugin.pytest_configure(settings)
    assert _tkinter.create is not sentinel

    plugin.pytest_unconfigure(settings)

    assert _tkinter.create is sentinel
    plugin.pytest_unconfigure(settings)
    assert _tkinter.create is sentinel


@pytest.mark.parametrize("enabled", [False, True])
def test_collection_skip_only_targets_explicit_subprocess_gui_test(enabled):
    from test_full_app_project_open import (
        test_full_app_opens_seven_rsp_project_and_switches_to_riser as subprocess_gui_test,
    )
    plugin = guard_plugin()
    gui_marks = getattr(subprocess_gui_test, "pytestmark", [])
    gui_item_marks, pure_item_marks = [], []
    gui_item = SimpleNamespace(
        get_closest_marker=lambda name: next((mark for mark in gui_marks if mark.name == name), None),
        add_marker=gui_item_marks.append,
    )
    pure_item = SimpleNamespace(
        get_closest_marker=lambda name: None,
        add_marker=pure_item_marks.append,
    )
    items = [gui_item, pure_item]

    plugin.pytest_collection_modifyitems(config(enabled), items)

    assert items == [gui_item, pure_item]
    assert pure_item_marks == []
    if enabled:
        assert len(gui_item_marks) == 1
        assert gui_item_marks[0].name == "skip"
        assert "--no-gui" in gui_item_marks[0].kwargs["reason"]
    else:
        assert gui_item_marks == []
