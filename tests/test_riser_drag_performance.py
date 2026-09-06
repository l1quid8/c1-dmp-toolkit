"""Dragging must update the existing scene, not rebuild the whole sheet."""

import copy
import sys
import tkinter as tk
import time
from pathlib import Path

import customtkinter as ctk
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from riser_editor import RiserTab
from riser_model import RiserAnnotation
from session import Session
from tk_compat import install_scrollbar_redraw_fix
from test_riser_app_integration import _event_for_world
from test_riser_workflows import seven_rsp_design


@pytest.fixture
def tab():
    install_scrollbar_redraw_fix()
    try:
        root = ctk.CTk()
    except tk.TclError as exc:
        if 'display' in str(exc).lower():
            pytest.skip('Tk display unavailable')
        raise
    root.geometry('1352x800')
    design = seven_rsp_design()
    from riser_scene import layout_riser
    design.riser_document = layout_riser(design)  # legacy saved-scene regression
    tab = RiserTab(root, Session(design=design), lambda: None)
    tab.pack(fill='both', expand=True)
    root.update()
    yield tab
    root.destroy()


def begin_module_move(tab):
    doc = tab.controller.document
    module = doc.elements[doc.elements['device:RSP-3'].location_id]
    start = (module.x + 18, module.y + 18)
    tab._on_press(_event_for_world(tab, start))
    assert tab._gesture[0] == 'move'
    return module, start


def test_drag_preserves_stationary_canvas_items_and_moves_owned_symbols(tab, monkeypatch):
    module, start = begin_module_move(tab)
    doc = tab.controller.document
    fixed = {item: tab.canvas.coords(item) for tag in ('page', 'grid', 'titleblock', 'element|device:RSP-7')
             for item in tab.canvas.find_withtag(tag)}
    port = tab.canvas.find_withtag('port|RSP-3|IN|in')[0]
    port_coords = tab.canvas.coords(port)
    wiring = copy.deepcopy(tab.design.connections)
    def no_full_redraw(*args, **kwargs):
        pytest.fail('A drag event rebuilt the entire sheet')
    monkeypatch.setattr(tab, 'redraw', no_full_redraw)
    for step in range(1, 11):
        tab._on_drag(_event_for_world(tab, (start[0]+step*18, start[1]+step*18)))
    assert all(tab.canvas.coords(item) == coords for item, coords in fixed.items())
    assert tab.canvas.coords(port) == pytest.approx([v + 180*tab.zoom for v in port_coords])
    assert doc.elements['device:RSP-3'].location_id == module.id
    assert tab.design.connections == wiring
    edge = next(e for e in wiring if e.target.device_id == 'RSP-3')
    line = next(item for item in tab.canvas.find_withtag(f'route|{edge.id}')
                if tab.canvas.type(item) == 'line')
    assert tab.canvas.coords(line)[-2:] == pytest.approx(tab._xy(doc.routes[edge.id].points[-1]))
    assert not tab.controller.can_undo  # preview is not ten independent edits


def test_release_uses_final_pointer_and_commits_one_redraw_and_undo(tab, monkeypatch):
    module, start = begin_module_move(tab)
    before = copy.deepcopy(tab.controller.document)
    x, y = module.x, module.y
    calls = []
    original = tab.redraw
    monkeypatch.setattr(tab, 'redraw', lambda **kw: (calls.append(kw), original(**kw)))
    # Mouse-up may arrive at a newer position than the last motion event.
    tab._on_release(_event_for_world(tab, (start[0]+54, start[1]+36)))
    assert (module.x, module.y) == (x+54, y+36)
    assert len(calls) == 1
    assert tab.controller.undo()
    assert tab.controller.document == before
    assert not tab.controller.can_undo


def test_cancel_restores_preview_and_attached_cables(tab):
    module, start = begin_module_move(tab)
    before = copy.deepcopy(tab.controller.document)
    tab._on_drag(_event_for_world(tab, (start[0]+54, start[1]+36)))
    tab.cancel()
    assert tab.controller.document == before
    assert not tab.controller.can_undo
    assert all(tab.canvas.itemcget(item, 'state') != 'hidden'
               for item in tab.canvas.find_all())


def test_group_drop_translates_internal_cables_without_routing_them_twice(tab, monkeypatch):
    module, _ = begin_module_move(tab)
    doc = tab.controller.document
    members = {e.ref for e in doc.elements.values() if e.location_id == module.id}
    internal = {e.id: list(doc.routes[e.id].points) for e in tab.design.connections
                if e.source.device_id in members and e.target.device_id in members}
    assert internal
    original = tab.controller._reroute
    def reroute(edge_id):
        assert edge_id not in internal, 'internal cable was routed before being overwritten by translation'
        return original(edge_id)
    monkeypatch.setattr(tab.controller, '_reroute', reroute)
    tab.controller.move_element(module.id, 18, 36)
    assert all(doc.routes[key].points == [(x+18, y+36) for x, y in points]
               for key, points in internal.items())


def test_pointer_burst_renders_latest_position_once(tab, monkeypatch):
    module, start = begin_module_move(tab)
    before = (module.x, module.y)
    calls = []
    original = tab._on_drag
    def drag(event):
        calls.append((event.x, event.y))
        original(event)
    monkeypatch.setattr(tab, '_on_drag', drag)
    for step in range(1, 41):
        tab._queue_drag(_event_for_world(tab, (start[0]+step*18, start[1]+step*18)))
    assert (module.x, module.y) == before
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        tab.update()
    assert len(calls) == 1
    assert (module.x, module.y) == (before[0]+720, before[1]+720)


@pytest.mark.parametrize('finish', ['release', 'cancel'])
def test_pending_motion_cannot_run_after_gesture_ends(tab, finish):
    module, start = begin_module_move(tab)
    before = copy.deepcopy(tab.controller.document)
    tab._queue_drag(_event_for_world(tab, (start[0]+720, start[1]+720)))
    if finish == 'cancel':
        tab.cancel()
        expected = before
    else:
        tab._on_release(_event_for_world(tab, (start[0]+18, start[1]+18)))
        expected = copy.deepcopy(tab.controller.document)
        assert (module.x, module.y) == (before.elements[module.id].x+18, before.elements[module.id].y+18)
    deadline = time.monotonic() + .04
    while time.monotonic() < deadline:
        tab.update()
    assert tab.controller.document == expected


@pytest.mark.parametrize('gesture', ['move-annotation', 'resize', 'route'])
def test_other_drag_tools_preserve_the_page_and_grid(tab, gesture):
    doc = tab.controller.document
    if gesture == 'move-annotation':
        note = RiserAnnotation('note', 'text', [(150, 150)], text='FIELD NOTE')
        tab.controller.add_annotation(note)
        tab.selected = ('annotation', 'note')
        tab._gesture = (gesture, 'note', (150, 150), list(note.points))
    elif gesture == 'resize':
        e = doc.elements['device:RSP-3']
        tab.selected = ('element', e.id)
        tab._gesture = (gesture, e.id, (150, 150), e.width, e.height)
    else:
        route = next(r for r in doc.routes.values() if len(r.points) > 2)
        tab.selected = ('route', route.connection_id)
        tab._gesture = (gesture, route.connection_id, 1, list(route.points))
    tab.redraw()
    fixed = {item: tab.canvas.coords(item) for tag in ('page', 'grid', 'titleblock')
             for item in tab.canvas.find_withtag(tag)}
    tab._on_drag(_event_for_world(tab, (186, 186)))
    assert all(tab.canvas.coords(item) == coords for item, coords in fixed.items())
