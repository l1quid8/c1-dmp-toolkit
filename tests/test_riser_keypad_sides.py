"""Keypad wires use the same exterior attachment behavior as RSP wires."""
import copy

import pytest

from test_riser_rsp_sides import (
    RiserEditorController, layout_riser, branched_design, port_point,
    _segment_hits_rect, editor, _event_for_world,
)


def setup_keypad():
    design = branched_design()
    controller = RiserEditorController(design, layout_riser(design))
    edge = next(e for e in design.connections if e.target.device_id == 'KEYPAD-2')
    for i, element in enumerate(controller.document.elements.values()):
        element.x, element.y = 3000 + i * 500, 3000
    source = controller.document.elements[f'device:{edge.source.device_id}']
    target = controller.document.elements['device:KEYPAD-2']
    source.x, source.y, source.width, source.height = 0, 0, 160, 100
    target.x, target.y, target.width, target.height = 300, 300, 180, 144
    target.input_side = 'top'
    route = controller.document.routes[edge.id]
    route.points = [(40, 100), (40, 330), (390, 330), (390, 300)]
    route.manual = True
    return design, controller, edge, target


def assert_clear(controller, edge, target):
    points = controller.document.routes[edge.id].points
    assert points[-1] == port_point(target, 'IN', output=False)
    assert not any(_segment_hits_rect(a, b, target, clearance=0)
                   for a, b in zip(points, points[1:]))


def test_keypad_move_repairs_wire_crossing_screen_and_undo_restores():
    design, controller, edge, target = setup_keypad()
    before = copy.deepcopy(controller.document)
    wiring = copy.deepcopy(design.connections)
    controller.move_element(target.id, 0, -200)
    assert_clear(controller, edge, target)
    assert design.connections == wiring
    assert controller.undo()
    assert controller.document == before


@pytest.mark.parametrize('side', ['top', 'right', 'bottom', 'left'])
def test_keypad_side_stays_pinned_when_moved(side):
    _, controller, edge, target = setup_keypad()
    controller.set_input_side(target.id, side)
    controller.move_element(target.id, 0, -200)
    assert target.input_side == side and target.input_side_locked
    assert_clear(controller, edge, target)
    controller.set_input_side(target.id, 'auto')
    assert not target.input_side_locked
    assert_clear(controller, edge, target)


def test_keypad_connect_can_pin_side_with_one_undo():
    _, controller, edge, target = setup_keypad()
    controller.disconnect(edge.id)
    controller.clear_history()
    before = copy.deepcopy(controller.document)
    connected = controller.connect(edge.source, edge.target, input_side='left')
    assert target.input_side == 'left' and target.input_side_locked
    assert_clear(controller, connected, target)
    assert controller.undo()
    assert controller.document == before
    assert not controller.can_undo


def test_keypad_endpoint_drag_pins_side_without_reconnecting(editor):
    frame, _ = editor
    tab = frame.riser_tab
    edge = next(e for e in tab.design.connections if e.target.device_id == 'KEYPAD-2')
    target = tab.controller.document.elements['device:KEYPAD-2']
    before = copy.deepcopy(tab.design.connections)
    tab.selected = ('route', edge.id)
    tab.redraw()
    route = tab.controller.document.routes[edge.id]
    tab._gesture = ('route', edge.id, len(route.points)-1, copy.deepcopy(route.points))
    tab._on_release(_event_for_world(tab, (target.x, target.y + target.height/2)))
    assert target.input_side == 'left' and target.input_side_locked
    assert tab.design.connections == before
    assert_clear(tab.controller, edge, target)


def test_keypad_drag_preview_avoids_body_and_cancel_restores(editor):
    frame, _ = editor
    tab = frame.riser_tab
    design, controller, edge, target = setup_keypad()
    tab.design = design
    tab.session.design = design
    tab.controller = controller
    tab.zoom = 1
    tab.snap_enabled = False
    tab.redraw()
    before = copy.deepcopy(controller.document)
    tab._on_press(_event_for_world(tab, (350, 350)))
    tab._on_drag(_event_for_world(tab, (350, 150)))
    assert target.y == 100
    assert_clear(controller, edge, target)
    tab.clear_selection()
    assert controller.document == before
    assert not controller.can_undo


@pytest.mark.parametrize('gesture', ['move', 'move-group', 'route'])
def test_drag_defers_sheet_wide_routing_until_release(editor, monkeypatch, gesture):
    """A pointer frame must not build routing graphs over the whole drawing."""
    import riser_scene
    frame, _ = editor
    tab = frame.riser_tab
    design, controller, edge, target = setup_keypad()
    tab.design = design
    tab.session.design = design
    tab.controller = controller
    tab.zoom = 1
    tab.snap_enabled = False
    tab.redraw()
    before = copy.deepcopy(controller.document)
    phase = 'preview'
    final_obstacles = []
    original = riser_scene.route_topology_connection

    def checked_route(edge, source, target, obstacles, **kwargs):
        if phase == 'preview':
            assert {e.ref for e in obstacles} <= {source.ref, target.ref}, (
                'Mouse movement invoked sheet-wide routing')
            assert not kwargs.get('reserved_segments'), 'Mouse movement scanned every wire'
        else:
            final_obstacles.extend(e.ref for e in obstacles)
        return original(edge, source, target, obstacles, **kwargs)

    monkeypatch.setattr(riser_scene, 'route_topology_connection', checked_route)
    if gesture == 'move':
        tab._on_press(_event_for_world(tab, (350, 350)))
        end = (350, 150)
    elif gesture == 'move-group':
        tab._gesture = ('move-group', (350, 350), copy.deepcopy(controller.document),
                        [('element', target.id)])
        end = (350, 150)
    else:
        route = controller.document.routes[edge.id]
        tab.selected = ('route', edge.id)
        tab._gesture = ('route', edge.id, len(route.points)-1, list(route.points))
        end = (target.x, target.y+target.height/2)
    tab._on_drag(_event_for_world(tab, end))
    preview_target = copy.copy(controller.document.elements[target.id])
    if gesture == 'route':
        preview_target.input_side = 'left'
        assert not target.input_side_locked
    assert_clear(controller, edge, preview_target)
    assert not controller.can_undo
    phase = 'release'
    tab._on_release(_event_for_world(tab, end))
    assert 'MSP' in final_obstacles  # Final routing still considers the entire sheet.
    assert_clear(controller, edge, controller.document.elements[target.id])
    assert controller.undo()
    assert controller.document == before
