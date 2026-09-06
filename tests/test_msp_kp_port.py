import copy
import xml.etree.ElementTree as ET
from test_release_editor_integration import editor

from test_riser_scene import branched_design
from riser_model import RiserElement
from riser_scene import port_point, layout_riser, sync_riser_document, _segment_hits_rect


def test_kp_bus_is_centered_on_left_edge_and_other_ports_stay_bottom():
    panel = RiserElement('device:MSP', 'device', 'MSP', 100, 200, 400, 160)
    assert port_point(panel, 'KP BUS', output=True) == (100, 280)
    for port in ('PROG', 'LX500', 'LX600', 'LX700', 'LX800', 'LX900'):
        assert port_point(panel, port, output=True)[1] == 360


def test_kp_wire_exits_left_without_crossing_panel():
    d = branched_design()
    doc = layout_riser(d)
    panel = doc.elements['device:MSP']
    edge = next(e for e in d.connections if e.source.port_id == 'KP BUS')
    points = doc.routes[edge.id].points
    assert points[0] == (panel.x, panel.y + panel.height / 2)
    assert points[1][0] < points[0][0]
    assert points[1][1] == points[0][1]
    assert not any(_segment_hits_rect(a, b, panel, clearance=0) for a, b in zip(points, points[1:]))


def test_saved_bottom_port_route_moves_to_left_without_losing_callout():
    d = branched_design()
    doc = layout_riser(d)
    panel = doc.elements['device:MSP']
    edge = next(e for e in d.connections if e.source.port_id == 'KP BUS')
    route = doc.routes[edge.id]
    end = route.points[-1]
    old = (panel.x + panel.width * .06, panel.y + panel.height)
    route.points = [old, (old[0], old[1]+36), (end[0], old[1]+36), end]
    route.manual = True
    route.label_hidden = True
    route.label_manual = True
    route.label_offset = (90, -36)
    before = copy.deepcopy(d.connections)
    sync_riser_document(d, doc)
    assert route.points[0] == (panel.x, panel.y + panel.height / 2)
    assert route.points[1][0] < panel.x
    assert route.points[1][1] == panel.y + panel.height / 2
    assert route.points[-1] == end
    assert route.label_hidden and route.label_manual
    assert route.label_offset == (90, -36)
    assert d.connections == before


def test_kp_label_export_uses_left_midpoint(tmp_path):
    from riser_render import render_svg
    d = branched_design()
    doc = layout_riser(d)
    panel = doc.elements['device:MSP']
    path = tmp_path / 'left.svg'
    render_svg(d, doc, path)
    root = ET.parse(path).getroot()
    label = next((node for node in root.iter() if node.text == 'KP BUS'), None)
    assert label is not None
    assert float(label.attrib['x']) == panel.x + 12
    assert float(label.attrib['y']) == panel.y + panel.height / 2 - 7
    assert label.attrib['text-anchor'] == 'start'


def test_canvas_kp_target_is_left_midpoint(editor):
    frame, _ = editor
    tab = frame.riser_tab
    panel = tab.controller.document.elements['device:MSP']
    tab.redraw()
    items = tab.canvas.find_withtag('port|MSP|KP BUS|out')
    circle = next(item for item in items if tab.canvas.type(item) == 'oval')
    x1, y1, x2, y2 = tab.canvas.coords(circle)
    assert ((x1+x2)/2, (y1+y2)/2) == tab._xy((panel.x, panel.y + panel.height / 2))
