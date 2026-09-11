"""Riser view controls change screen space without editing the drawing."""

import copy
import tkinter as tk

import customtkinter as ctk
import pytest

from test_release_editor_integration import button, descendants
from test_riser_scene import branched_design
from riser_editor import RiserTab
from session import Session


@pytest.fixture
def riser_view():
    try:
        root = ctk.CTk()
    except tk.TclError as exc:
        if 'display' in str(exc).lower():
            pytest.skip('Tk display unavailable')
        raise
    root.geometry('1200x700')
    edits, fullscreen = [], []
    tab = RiserTab(root, Session(design=branched_design()),
                   on_edit=lambda: edits.append('edit'),
                   on_toggle_fullscreen=lambda: fullscreen.append('toggle'))
    tab.pack(fill='both', expand=True)
    root.update()
    edits.clear()  # Opening may recover the existing scene before view actions.
    try:
        yield root, tab, edits, fullscreen
    finally:
        root.destroy()


def test_collapse_reclaims_canvas_and_restores_values_without_editing(riser_view):
    root, tab, edits, _ = riser_view
    tab.selected = ('element', 'device:KEYPAD-2')
    tab._refresh_selection_properties()
    tab._title_vars['drawing_title'].set('Pending title text')
    properties = tab.properties.winfo_children()
    before = copy.deepcopy(tab.design)
    selection = list(tab.selection)
    width = tab.canvas.winfo_width()

    button(tab.toolbar, 'Hide panel').invoke()
    root.update()

    assert not tab.inspector.winfo_viewable()
    assert tab.canvas.winfo_width() >= width + 300
    assert button(tab.toolbar, 'Show panel').winfo_ismapped()

    button(tab.toolbar, 'Show panel').invoke()
    root.update()

    assert tab.inspector.winfo_viewable()
    assert tab.canvas.winfo_width() == width
    assert tab._title_vars['drawing_title'].get() == 'Pending title text'
    assert tab.properties.winfo_children() == properties
    assert tab.selection == selection
    assert tab.design == before
    assert edits == []


def test_fullscreen_button_and_escape_preserve_selection(riser_view):
    _, tab, edits, fullscreen = riser_view
    tab.selected = ('element', 'device:KEYPAD-2')
    button(tab.toolbar, 'Full screen').invoke()
    assert fullscreen == ['toggle']
    tab.set_fullscreen(True)
    assert button(tab.toolbar, 'Exit full screen')
    assert tab._escape_view() == 'break'
    assert fullscreen == ['toggle', 'toggle']
    assert tab.selected == ('element', 'device:KEYPAD-2')
    tab.set_fullscreen(False)
    assert button(tab.toolbar, 'Full screen')
    tab._escape_view()
    assert tab.selected is None
    assert edits == []


@pytest.mark.parametrize('width', [832, 860, 1000, 1352])
def test_toolbar_controls_remain_inside_window_at_supported_widths(riser_view, width):
    root, tab, _, _ = riser_view
    root.geometry(f'{width}x700')
    tab.set_fullscreen(True)  # The longer label must also fit.
    root.update()
    left = tab.toolbar.winfo_rootx()
    right = left + tab.toolbar.winfo_width()
    controls = [child for child in descendants(tab.toolbar)
                if isinstance(child, ctk.CTkButton)]
    for control in controls:
        assert control.winfo_ismapped(), control.cget('text')
        assert control.winfo_width() >= control.cget('width'), control.cget('text')
        assert left <= control.winfo_rootx(), control.cget('text')
        assert control.winfo_rootx() + control.winfo_width() <= right, control.cget('text')
    assert tab.toolbar.winfo_height() == (126 if width < 1100 else 84)
