"""Narrow runtime workarounds for the bundled Tk/CustomTkinter combination."""

from functools import wraps

import customtkinter as ctk


def install_scrollbar_redraw_fix() -> None:
    """Let Tk flush scrollbar/dropdown drawings instead of pumping it inline.

    CTkScrollbar._draw() calls its canvas.update_idletasks(). On macOS/Tk 9
    this re-enters canvas scroll callbacks while a previous redraw is still
    running, producing an unbounded redraw loop (including on the home screen).
    Keep the upstream geometry/theme renderer and suppress only that nested
    flush. Normal main-loop rendering and explicit application updates remain
    untouched. Patching the shared class also covers CTkScrollableFrame's
    internally constructed scrollbars, not only our standalone canvas bars.
    CTkOptionMenu has the same nested flush in its dimension-event redraw;
    opening a full project can therefore wedge even after fixing scrollbars.
    Retain the existing installer name for callers and cover both widgets.
    """
    for widget_type in (ctk.CTkScrollbar, ctk.CTkOptionMenu):
        _defer_canvas_idle(widget_type)


def _defer_canvas_idle(widget_type) -> None:
    original_draw = widget_type._draw
    if getattr(original_draw, "_c1_deferred_idle", False):
        return

    @wraps(original_draw)
    def draw_without_nested_events(self, *args, **kwargs):
        canvas = self._canvas
        had_override = "update_idletasks" in canvas.__dict__
        original_update = canvas.update_idletasks
        canvas.update_idletasks = lambda: None
        try:
            return original_draw(self, *args, **kwargs)
        finally:
            if had_override:
                canvas.update_idletasks = original_update
            else:
                del canvas.update_idletasks

    draw_without_nested_events._c1_deferred_idle = True
    widget_type._draw = draw_without_nested_events
