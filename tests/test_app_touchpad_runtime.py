"""Real Tk trackpad events must reach the pane under the pointer."""
import tkinter as tk

import customtkinter as ctk
import pytest

from test_app_scrollbar_runtime import application
from test_riser_workflows import seven_rsp_design
from session import Session, save_session


def gesture(widget, dx=0, dy=-24):
    # Tk packs signed X/Y pixel deltas into the high/low 16 bits.
    delta = (dx << 16) | (dy & 0xffff)
    widget.event_generate('<TouchpadScroll>', delta=delta)


@pytest.fixture
def project(application, tmp_path):
    if not application.root.tk.call('info', 'commands', '::tk::PreciseScrollDeltas'):
        pytest.skip('Requires Tk with precise trackpad events')
    path = save_session(Session(seven_rsp_design(), path=tmp_path / 'scroll.dmps'))
    application._open_session_path(path)
    application.root.update()
    return application


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@pytest.mark.parametrize('title', ['SITE', 'SPLITTERS', 'KEYPADS', 'RSP/POWER', 'REMOTELINK', 'RISER'])
def test_trackpad_scrolls_each_form_pane(project, title):
    editor = project.editor
    editor.tabs.set(title)
    project.root.update()
    panes = [w for w in descendants(editor.tabs.tab(title)) if isinstance(w, ctk.CTkScrollableFrame)]
    assert panes, f'{title} has no scroll container for overflowing content'
    for pane in panes:
        # Guarantee overflow even for short/empty projects, and send the event
        # over a nested native entry, not directly to the scrolling canvas.
        filler = ctk.CTkFrame(pane, height=1800)
        filler.grid(row=999, column=0)
        entry = ctk.CTkEntry(filler)
        entry.place(x=0, y=0)
        project.root.update()
        canvas = pane._parent_canvas
        canvas.yview_moveto(0)
        before = canvas.yview()
        gesture(entry._entry)
        assert canvas.yview()[0] > before[0], title
        gesture(entry._entry, dy=24)
        assert canvas.yview()[0] == pytest.approx(before[0], abs=.001), title
        filler.destroy()


def test_riser_trackpad_pans_both_axes_without_changing_zoom(project):
    project.editor.tabs.set('RISER')
    project.root.update()
    riser = project.editor.riser_tab
    canvas = riser.canvas
    canvas.configure(scrollregion=(0, 0, 4000, 4000))
    canvas.xview_moveto(.25)
    canvas.yview_moveto(.25)
    zoom = riser.zoom
    gesture(canvas, dx=-20, dy=-40)
    assert canvas.xview()[0] == pytest.approx(.255, abs=.0003)
    assert canvas.yview()[0] == pytest.approx(.260, abs=.0003)
    gesture(canvas, dx=20, dy=40)
    assert canvas.xview()[0] == pytest.approx(.25, abs=.0003)
    assert canvas.yview()[0] == pytest.approx(.25, abs=.0003)
    assert riser.zoom == zoom


def test_native_text_scroll_does_not_also_move_outer_pane(project):
    project.editor.tabs.set('REMOTELINK')
    pane = project.editor.remotelink_tab.left
    textbox = tk.Text(pane, height=4)
    textbox.grid(row=999, column=0)
    textbox.insert('1.0', '\n'.join(str(i) for i in range(100)))
    project.root.update()
    canvas = pane._parent_canvas
    canvas.yview_moveto(.3)
    before = canvas.yview()
    gesture(textbox)
    assert textbox.yview()[0] > 0
    assert canvas.yview() == before


def test_zones_retains_native_trackpad_scrolling(project):
    project.editor.tabs.set('ZONES')
    tree = project.editor.zones.tree
    for i in range(100):
        tree.insert('', 'end', values=(i, 'Scroll test'))
    project.root.update()
    tree.yview_moveto(0)
    # Tk's table binding intentionally samples every fifth precise event.
    tree.event_generate('<TouchpadScroll>', delta=65535, serial=5)
    assert tree.yview()[0] > 0


def test_home_trackpad_and_mouse_wheel_both_scroll(application):
    root = application.root
    if not root.tk.call('info', 'commands', '::tk::PreciseScrollDeltas'):
        pytest.skip('Requires Tk with precise trackpad events')
    from tk_compat import install_touchpad_scroll
    install_touchpad_scroll(root)  # Reinstalling must not double the distance.
    pane = application._flow_host
    filler = ctk.CTkFrame(application.flow, height=2000, width=120)
    filler.grid(row=999, column=0)
    root.update()
    canvas = pane._parent_canvas
    canvas.yview_moveto(0)
    before = canvas.canvasy(0)
    gesture(filler._canvas, dy=-20)
    assert canvas.canvasy(0) - before == pytest.approx(20, abs=8)
    before = canvas.yview()[0]
    filler._canvas.event_generate('<MouseWheel>', delta=-120)
    assert canvas.yview()[0] > before


def test_site_lower_fields_reachable_at_minimum_window_size(project):
    project.root.geometry('860x560')
    project.editor.tabs.set('SITE')
    project.root.update()
    page = project.editor.tabs.tab('SITE')
    pane = next(w for w in descendants(page) if isinstance(w, ctk.CTkScrollableFrame))
    canvas = pane._parent_canvas
    assert canvas.yview()[1] < 1, 'fixture must exercise a clipped form'
    for _ in range(30):
        gesture(canvas, dy=-24)
    assert canvas.yview()[1] == 1
    project.root.update_idletasks()
    entries = [w for w in descendants(pane) if isinstance(w, ctk.CTkEntry)]
    bottom = max(w.winfo_rooty() + w.winfo_height() for w in entries)
    assert bottom <= canvas.winfo_rooty() + canvas.winfo_height()


def test_gentle_trackpad_deltas_accumulate(application):
    root = application.root
    if not root.tk.call('info', 'commands', '::tk::PreciseScrollDeltas'):
        pytest.skip('Requires Tk with precise trackpad events')
    pane = application._flow_host
    ctk.CTkFrame(application.flow, height=2000).grid(row=999, column=0)
    root.update()
    canvas = pane._parent_canvas
    canvas.yview_moveto(0)
    before = canvas.canvasy(0)
    for _ in range(24):
        gesture(canvas, dy=-1)
    assert canvas.canvasy(0) - before == pytest.approx(24, abs=1)
