"""Narrow runtime workarounds for the bundled Tk/CustomTkinter combination."""

from functools import wraps
import tkinter as tk

import customtkinter as ctk


def install_touchpad_scroll(root) -> None:
    """Route Tk 8.7/9 precise gestures to the nearest scrolling canvas.

    CustomTkinter 5.2 only binds MouseWheel. Tk on newer macOS sends
    TouchpadScroll instead, with two signed pixel deltas packed into %D.
    Bind once per interpreter so newly built forms and dialogs work too.
    Native text/table widgets retain their own Tk class bindings.
    Older Tk versions keep their existing MouseWheel handling.
    """
    if getattr(root, "_c1_touchpad_scroll", False):
        return
    if not root.tk.call("info", "commands", "::tk::PreciseScrollDeltas"):
        return

    def scroll(event):
        widget = event.widget
        while isinstance(widget, tk.Misc):
            if widget.winfo_class() in ("Text", "Treeview", "Listbox"):
                return
            if isinstance(widget, tk.Canvas) and widget.cget("scrollregion"):
                dx, dy = root.tk.call("::tk::PreciseScrollDeltas", event.delta)
                region = [float(n) for n in root.tk.splitlist(widget.cget("scrollregion"))]
                # Canvas 'units' default to a tenth of the viewport, not a
                # pixel. Move by a fraction of the scrollregion instead, so
                # gestures don't jump by a viewport-dependent distance.
                pending = getattr(widget, "_c1_touchpad_pending", {})
                for axis, delta, view, move, extent in (
                    ("x", dx, widget.xview, widget.xview_moveto, region[2] - region[0]),
                    ("y", dy, widget.yview, widget.yview_moveto, region[3] - region[1]),
                ):
                    if delta and extent > 0 and view() != (0.0, 1.0):
                        current = view()
                        previous, old_extent, remainder = pending.get(axis, (None, None, 0))
                        if previous != current or old_extent != extent:
                            remainder = 0
                        # CTk uses eight-pixel increments on macOS. Retain
                        # rounding loss so slow one-pixel gestures accumulate.
                        target = current[0] * extent + remainder - int(delta)
                        target = max(0, min(target, (1 - current[1] + current[0]) * extent))
                        move(target / extent)
                        pending[axis] = (view(), extent, target - view()[0] * extent)
                widget._c1_touchpad_pending = pending
                return "break"
            widget = widget.master

    root.bind_all("<TouchpadScroll>", scroll, add="+")
    root._c1_touchpad_scroll = True


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
