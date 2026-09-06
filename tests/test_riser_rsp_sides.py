"""RSP attachment sides affect drawing geometry, never the electrical input."""
import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from riser_model import RiserElement, DevicePortRef, TopologyConnection
from riser_scene import port_point, route_topology_connection, _segment_hits_rect, layout_riser
from riser_editor import RiserEditorController
from session import Session, save_session, load_session
from test_riser_scene import branched_design
from test_release_editor_integration import editor
from test_riser_app_integration import _event_for_world


@pytest.mark.parametrize('side, expected', [('top', (400, 200)), ('right', (500, 250)),
                                           ('bottom', (400, 300)), ('left', (300, 250))])
def test_rsp_midpoint_and_route_approach(side, expected):
    source = RiserElement('device:710-LX500-1', 'device', '710-LX500-1', 0, 0, 160, 100)
    target = RiserElement('device:RSP-1', 'device', 'RSP-1', 300, 200, 200, 100, input_side=side)
    edge = TopologyConnection('wire', DevicePortRef(source.ref, 'OUT1'), DevicePortRef(target.ref, 'IN'))
    assert port_point(target, 'IN', output=False) == expected
    points = route_topology_connection(edge, source, target, [source, target])
    assert points[-1] == expected
    assert not any(_segment_hits_rect(a, b, target, clearance=0) for a, b in zip(points, points[1:]))


def setup_scene():
    design = branched_design()
    c = RiserEditorController(design, layout_riser(design))
    edge = next(e for e in design.connections if e.target.device_id == 'RSP-2')
    for i, e in enumerate(c.document.elements.values()):
        e.x, e.y = 3000 + i * 500, 3000
    source = c.document.elements[f'device:{edge.source.device_id}']
    target = c.document.elements['device:RSP-2']
    source.x, source.y, source.width, source.height = 0, 0, 160, 100
    target.x, target.y, target.width, target.height = 300, 300, 200, 100
    target.input_side = 'top'
    c.document.routes[edge.id].points = [(40, 100), (40, 200), (400, 200), (400, 300)]
    c.document.routes[edge.id].manual = True
    return design, c, edge


def assert_clear(c, edge):
    target = c.document.elements['device:RSP-2']
    points = c.document.routes[edge.id].points
    assert points[-1] == port_point(target, 'IN', output=False)
    assert not any(_segment_hits_rect(a, b, target, clearance=0) for a, b in zip(points, points[1:]))


def test_moving_rsp_chooses_left_and_avoids_body_with_one_undo():
    design, c, edge = setup_scene()
    before = copy.deepcopy(c.document)
    wiring = copy.deepcopy(design.connections)
    c.move_element('device:RSP-2', 0, -200)
    assert c.document.elements['device:RSP-2'].input_side == 'left'
    assert_clear(c, edge)
    assert design.connections == wiring
    assert c.undo()
    assert c.document == before
    assert not c.can_undo


def test_pinned_side_survives_move_save_load_and_auto_reset(tmp_path):
    design, c, edge = setup_scene()
    c.set_input_side('device:RSP-2', 'bottom')
    c.move_element('device:RSP-2', 0, -200)
    target = c.document.elements['device:RSP-2']
    assert target.input_side == 'bottom'
    assert target.input_side_locked
    assert_clear(c, edge)
    path = tmp_path / 'sides.dmps'
    save_session(Session(design=design), path)
    loaded = load_session(path).design.riser_document.elements['device:RSP-2']
    assert loaded.input_side == 'bottom' and loaded.input_side_locked
    c.set_input_side('device:RSP-2', 'auto')
    assert not target.input_side_locked
    assert target.input_side == 'left'


def test_endpoint_drag_snaps_to_same_rsp_side_without_reconnecting(editor):
    frame, _ = editor
    tab = frame.riser_tab
    edge = next(e for e in tab.design.connections if e.target.device_id == 'RSP-2')
    target = tab.controller.document.elements['device:RSP-2']
    before = copy.deepcopy(tab.design.connections)
    tab.selected = ('route', edge.id)
    tab.redraw()
    route = tab.controller.document.routes[edge.id]
    tab._gesture = ('route', edge.id, len(route.points)-1, copy.deepcopy(route.points))
    end = (target.x, target.y + target.height/2)
    tab._on_release(_event_for_world(tab, end))
    assert target.input_side == 'left'
    assert target.input_side_locked
    assert tab.design.connections == before
    assert_clear(tab.controller, edge)


def test_rsp_drag_preview_is_clear_and_escape_restores_side(editor):
    frame, _ = editor
    tab = frame.riser_tab
    design, c, edge = setup_scene()
    tab.design = design
    tab.session.design = design
    tab.controller = c
    tab.zoom = 1
    tab.snap_enabled = False
    tab.redraw()
    before = copy.deepcopy(c.document)
    tab._on_press(_event_for_world(tab, (350, 350)))
    tab._on_drag(_event_for_world(tab, (350, 150)))
    assert c.document.elements['device:RSP-2'].input_side == 'left'
    assert_clear(c, edge)
    tab.clear_selection()
    assert c.document == before
    assert not c.can_undo


def test_connect_can_pin_an_rsp_side_as_one_edit():
    design, c, edge = setup_scene()
    c.disconnect(edge.id)
    c.clear_history()
    before = copy.deepcopy(c.document)
    connected = c.connect(edge.source, edge.target, input_side='left')
    assert c.document.elements['device:RSP-2'].input_side_locked
    assert c.document.elements['device:RSP-2'].input_side == 'left'
    assert_clear(c, connected)
    assert c.undo()
    assert c.document == before
    assert not c.can_undo


def test_endpoint_hover_previews_snapped_side_without_saving_it(editor):
    frame, _ = editor
    tab = frame.riser_tab
    edge = next(e for e in tab.design.connections if e.target.device_id == 'RSP-2')
    target = tab.controller.document.elements['device:RSP-2']
    tab.zoom = 1
    tab.selected = ('route', edge.id)
    tab.redraw()
    before = copy.deepcopy(tab.controller.document)
    route = tab.controller.document.routes[edge.id]
    tab._gesture = ('route', edge.id, len(route.points)-1, copy.deepcopy(route.points))
    tab._on_drag(_event_for_world(tab, (target.x+3, target.y+target.height/2+2)))
    assert route.points[-1] == (target.x, target.y+target.height/2)
    assert not any(_segment_hits_rect(a, b, target, clearance=0)
                   for a, b in zip(route.points, route.points[1:]))
    assert not target.input_side_locked
    tab.clear_selection()
    assert tab.controller.document == before


def test_legacy_saves_default_to_automatic_input_sides(tmp_path):
    import json
    design, c, edge = setup_scene()
    path = tmp_path / 'legacy.dmps'
    save_session(Session(design=design), path)
    raw = json.loads(path.read_text())
    raw['schema_version'] = 7
    for element in raw['design']['riser_document']['elements'].values():
        element.pop('input_side_locked')
    path.write_text(json.dumps(raw))
    loaded = load_session(path).design.riser_document.elements['device:RSP-2']
    assert loaded.input_side == 'top'
    assert not loaded.input_side_locked


def test_endpoint_reconnect_uses_full_side_snap_radius(editor):
    frame, _ = editor
    tab = frame.riser_tab
    edge = next(e for e in tab.design.connections if e.target.device_id == 'RSP-2')
    occupied = next(e for e in tab.design.connections if e.target.device_id == 'RSP-1')
    tab.controller.disconnect(occupied.id)
    target = tab.controller.document.elements['device:RSP-1']
    tab.zoom = 1
    tab.selected = ('route', edge.id)
    tab.redraw()
    before = copy.deepcopy(tab.controller.document)
    tab.controller.clear_history()
    route = tab.controller.document.routes[edge.id]
    tab._gesture = ('route', edge.id, len(route.points)-1, copy.deepcopy(route.points))
    tab._on_release(_event_for_world(tab, (target.x-9, target.y+target.height/2)))
    updated = next(e for e in tab.design.connections if e.id == edge.id)
    assert updated.target.device_id == 'RSP-1'
    assert target.input_side == 'left' and target.input_side_locked
    assert tab.controller.undo()
    assert tab.controller.document == before
    assert not tab.controller.can_undo
