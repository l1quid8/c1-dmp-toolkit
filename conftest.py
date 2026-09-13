"""Opt-in background-only pytest mode; default GUI-capable CI is unchanged."""

import _tkinter

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--no-gui", action="store_true", default=False,
        help="Skip GUI tests before they can create Tcl/Tk interpreters or windows.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "gui: launches a GUI outside the in-process Tk factory guard")
    if not config.getoption("--no-gui"):
        return
    config._no_gui_original_tk_create = _tkinter.create

    def blocked_create(*args, **kwargs):
        pytest.skip("Tcl/Tk creation disabled by --no-gui background-only mode")

    _tkinter.create = blocked_create


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--no-gui"):
        return
    skip_gui = pytest.mark.skip(reason="Explicit GUI test disabled by --no-gui background-only mode")
    for item in items:
        if item.get_closest_marker("gui") is not None:
            item.add_marker(skip_gui)


def pytest_unconfigure(config):
    original = config.__dict__.pop("_no_gui_original_tk_create", None)
    if original is not None:
        _tkinter.create = original
