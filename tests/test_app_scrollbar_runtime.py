"""A scrollbar redraw must not recursively run unrelated Tk callbacks."""

import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import app as app_module


@pytest.fixture
def application(monkeypatch, tmp_path):
    # Isolate the real application shell from user preferences and update I/O.
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})
    monkeypatch.setattr(app_module, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(app_module, "list_recent_sessions", lambda **_kw: [])
    monkeypatch.setattr(app_module.App, "_check_for_updates", lambda *a, **kw: None)
    try:
        application = app_module.App()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    try:
        application.root.update_idletasks()
        yield application
    finally:
        application.root.destroy()


@pytest.mark.parametrize("kind", ["embedded", "standalone"])
def test_app_scrollbar_draw_does_not_reenter_the_event_loop(application, kind):
    # Removing the runtime workaround must fail this: upstream _draw() drains
    # idle events synchronously, allowing canvas scroll callbacks to recurse.
    bar = (application._flow_host._scrollbar if kind == "embedded"
           else ctk.CTkScrollbar(application.root))
    callbacks = []
    application.root.after_idle(lambda: callbacks.append("idle"))

    bar.set("0.2", "0.7")

    assert callbacks == [], "scrollbar drawing re-entered the Tk event loop"
    assert bar.get() == (0.2, 0.7)
    application.root.update_idletasks()
    assert callbacks == ["idle"]


def test_deferred_scrollbar_still_tracks_the_scrolled_canvas(application):
    ctk.CTkFrame(application.flow, height=1600, width=120).grid(row=99, column=0)
    application.root.update_idletasks()
    canvas = application._flow_host._parent_canvas
    bar = application._flow_host._scrollbar

    canvas.yview_moveto(0.5)
    application.root.update_idletasks()

    start, end = bar.get()
    assert 0.45 < start < 0.55
    assert start < end < 1
    assert bar.get() == canvas.yview()
    assert bar._canvas.find_withtag("scrollbar_parts")

    canvas.yview_moveto(0)
    application.root.update_idletasks()
    assert bar.get()[0] == 0
