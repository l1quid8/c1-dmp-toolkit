"""Dropping equipment must clear wires it obstructs, including unrelated feeds."""
import copy

import pytest

from test_riser_scene import branched_design, RiserElement, port_point, _segment_hits_rect
from test_release_editor_integration import editor
from test_riser_app_integration import _event_for_world
from riser_editor import RiserEditorController
from riser_scene import layout_riser


def crossing_scene():
    design = branched_design()
    controller = RiserEditorController(design, layout_riser(design))
    doc = controller.document
    doc.show_location_frames = False
    for i, element in enumerate(doc.elements.values()):
        element.x, element.y = 3000+i*500, 3000
    blocker = doc.elements['device:RSP-1']
    blocker.x, blocker.y, blocker.width, blocker.height = 800, 280, 200, 100
    target = doc.elements['device:RSP-2']
    target.x, target.y, target.width, target.height = 500, 450, 200, 100
    target.input_side, target.input_side_locked = 'top', True
    edge = next(e for e in design.connections if e.target.device_id == target.ref)
    source = doc.elements['device:'+edge.source.device_id]
    source.x, source.y, source.width, source.height = 0, 0, 160, 100
    route = doc.routes[edge.id]
    route.points = [(40,100),(40,200),(600,200),(600,450)]
    route.manual = True
    route.label_offset, route.label_manual, route.label_hidden = (21, 17), True, True
    return design, controller, blocker, edge


def intersects(controller, edge, element):
    points = controller.document.routes[edge.id].points
    return any(_segment_hits_rect(a,b,element,clearance=0)
               for a,b in zip(points,points[1:]))


@pytest.mark.parametrize('action', ['move', 'group', 'resize'])
def test_geometry_edit_repairs_unrelated_feed_and_undo_restores_everything(action):
    design, controller, blocker, edge = crossing_scene()
    if action == 'resize':
        blocker.x, blocker.width = 500, 50
    before = copy.deepcopy(controller.document)
    wiring = copy.deepcopy(design.connections)
    assert not intersects(controller, edge, blocker)
    if action == 'move':
        controller.move_element(blocker.id, -300, 0)
    elif action == 'group':
        controller.move_selection([('element',blocker.id)], -300, 0)
    else:
        controller.resize_element(blocker.id, 200, 100)
    assert not intersects(controller, edge, blocker), 'The unrelated feed cuts through the moved device'
    repaired = controller.document.routes[edge.id]
    assert repaired.points[-1] == port_point(controller.document.elements['device:RSP-2'],'IN',output=False)
    assert (repaired.manual,repaired.label_offset,repaired.label_manual,repaired.label_hidden) == (True,(21,17),True,True)
    # Clear, unrelated custom wires retain their exact geometry and appearance.
    affected = {e.id for e in design.connections if e.target.device_id in {'RSP-1','RSP-2'}}
    assert all(controller.document.routes[key] == route for key,route in before.routes.items() if key not in affected)
    assert design.connections == wiring
    assert controller.undo()
    assert controller.document == before
    assert not controller.can_undo
    assert controller.redo()
    assert not intersects(controller,edge,controller.document.elements[blocker.id])


def test_mouse_up_repairs_unrelated_feed_without_routing_it_during_drag(editor):
    frame,_ = editor
    tab = frame.riser_tab
    design, controller, blocker, edge = crossing_scene()
    tab.design = design
    tab.session.design = design
    tab.controller = controller
    tab.zoom = 1
    tab.snap_enabled = False
    tab.redraw()
    before = copy.deepcopy(controller.document)
    tab._on_press(_event_for_world(tab,(850,330)))
    for x in (750,650,550):
        tab._on_drag(_event_for_world(tab,(x,330)))
        assert controller.document.routes[edge.id] == before.routes[edge.id]
    assert intersects(controller,edge,blocker)  # Preview intentionally defers the repair.
    tab._on_release(_event_for_world(tab,(550,330)))
    assert not intersects(controller,edge,blocker)
    assert controller.undo()
    assert controller.document == before


def test_location_group_drop_repairs_another_locations_feed():
    _,controller,blocker,edge = crossing_scene()
    before = copy.deepcopy(controller.document)
    controller.move_element(blocker.location_id,-300,0)
    assert not intersects(controller,edge,blocker)
    assert controller.undo()
    assert controller.document == before


def test_keypad_caption_moved_onto_a_wire_is_protected():
    from riser_symbols import caption_boxes
    design,controller,_,edge = crossing_scene()
    keypad = controller.document.elements['device:KEYPAD-2']
    keypad.x,keypad.y,keypad.width,keypad.height = 800,280,180,144
    keypad.symbol_style = 'detailed'
    target = controller.document.elements['device:RSP-2']
    target.y = 650
    controller.document.routes[edge.id].points = [(40,100),(40,446),(600,446),(600,650)]
    before = copy.deepcopy(controller.document)
    controller.move_element(keypad.id,-300,0)
    assert not intersects(controller,edge,keypad)
    for left,top,right,bottom in caption_boxes(design,keypad):
        caption = RiserElement('caption','caption','caption',left,top,right-left,bottom-top)
        assert not intersects(controller,edge,caption)
    assert controller.undo()
    assert controller.document == before
