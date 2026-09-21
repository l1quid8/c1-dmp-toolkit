"""Pytest hooks shared by the toolkit's test suite.

Two concerns:
* `--no-gui` opt-in mode: skip GUI tests before they can create Tcl/Tk
  interpreters, so background-only CI runs safely.
* macOS window-clamp workaround: a fresh ``Tk`` root created after an earlier
  root in the same process can map at a stale, smaller size (the OS window
  server refuses the requested geometry). We remember each root's requested
  geometry and re-assert it right after the first pump so widgets lay out at
  the size the test asked for.
"""

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


# --------------------------------------------------------------------------
# macOS window-clamp workaround
# --------------------------------------------------------------------------

_GEOMETRY_REQUESTED: dict[int, str] = {}
_GEOMETRY_PATCHED = False


def _install_geometry_workaround():
    """Patch CTk so each root's requested geometry survives the macOS clamp."""
    global _GEOMETRY_PATCHED
    if _GEOMETRY_PATCHED:
        return
    _GEOMETRY_PATCHED = True

    import customtkinter as ctk

    orig_geometry = ctk.CTk.geometry

    def _patched_geometry(self, geometry_string=None):
        if geometry_string is not None:
            _GEOMETRY_REQUESTED[id(self)] = geometry_string
        return orig_geometry(self, geometry_string)

    def _patched_update(self):
        result = orig_update(self)
        geometry = _GEOMETRY_REQUESTED.get(id(self))
        if geometry is not None:
            # Re-assert after the first pump: macOS can otherwise map this
            # window at a stale smaller size, which then mis-frames the riser
            # canvas and any other size-sensitive layout.
            orig_geometry(self, geometry)
            orig_update_idletasks(self)
            orig_update(self)
        return result

    orig_update = ctk.CTk.update
    orig_update_idletasks = ctk.CTk.update_idletasks
    ctk.CTk.geometry = _patched_geometry
    ctk.CTk.update = _patched_update


def pytest_sessionstart(session):
    """Real-session only: install the macOS window-clamp workaround.

    Deliberately not in pytest_configure: the opt-in --no-gui tests import
    this module directly and drive pytest_configure with a stub config, and
    importing customtkinter there would change their expectations.
    """
    _install_geometry_workaround()
