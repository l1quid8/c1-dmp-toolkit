"""MSP terminal order must not depend on which buses are connected."""
import copy

import pytest

from test_riser_scene import branched_design
from riser_presentation import layout_presentation
from riser_scene import port_point, sync_riser_document
from parse_dmp_worksheet import Splitter
from riser_model import DevicePortRef
from topology_service import connect

PORTS = ('PROG', 'LX500', 'LX600', 'LX700', 'LX800', 'LX900')


@pytest.mark.parametrize('active', [(), ('KP BUS',), ('LX500',),
                                    ('LX700',), ('LX500', 'LX900'),
                                    ('PROG', 'LX500'), PORTS])
def test_generated_panel_ports_keep_numerical_order(active):
    design = branched_design()
    design.connections = [edge for edge in design.connections
                          if edge.source.device_id != 'MSP' or edge.source.port_id in active]
    for name in active:
        if name.startswith('LX') and not any(edge.source == DevicePortRef('MSP', name)
                                            for edge in design.connections):
            ref = f'710-{name}-1'
            design.splitters.append(Splitter(ref, 'LX', name, outputs=['Spare']*3))
            connect(design, DevicePortRef('MSP', name), DevicePortRef(ref, 'IN'))
    panel = layout_presentation(design).elements['device:MSP']
    xs = [port_point(panel, name, output=True)[0] for name in PORTS]
    assert all(a < b for a, b in zip(xs, xs[1:]))
    assert min(b-a for a, b in zip(xs, xs[1:])) > panel.width * .1


@pytest.mark.parametrize('manual', [False, True])
def test_saved_scrambled_ports_repair_without_rewiring_or_moving_devices(manual):
    design = branched_design()
    doc = layout_presentation(design)
    panel = doc.elements['device:MSP']
    panel.port_x = {'PROG': .08, 'LX500': .92, 'LX600': .5,
                    'LX700': .4, 'LX800': .6, 'LX900': .84}
    sync_riser_document(design, doc)
    # Reproduce a saved route ending at the old physical LX500 terminal.
    edge = next(e for e in design.connections if e.source.port_id == 'LX500')
    route = doc.routes[edge.id]
    route.points[0] = (panel.x + panel.width*.92, panel.y + panel.height)
    route.manual = manual
    route.label_offset = (31, 17)
    route.label_manual = True
    panel.port_x = {'PROG': .08, 'LX500': .92, 'LX600': .5,
                    'LX700': .4, 'LX800': .6, 'LX900': .84}
    wiring = copy.deepcopy(design.connections)
    geometry = {key: (e.x, e.y, e.width, e.height) for key, e in doc.elements.items()}

    sync_riser_document(design, doc)

    xs = [port_point(panel, name, output=True)[0] for name in PORTS]
    assert all(a < b for a, b in zip(xs, xs[1:]))
    assert design.connections == wiring
    assert {key: (e.x, e.y, e.width, e.height) for key, e in doc.elements.items()} == geometry
    assert route.points[0] == port_point(panel, 'LX500', output=True)
    assert route.manual == manual and route.label_manual
    assert route.label_offset == (31, 17)
    repaired = copy.deepcopy(doc)
    sync_riser_document(design, doc)
    assert doc == repaired


def test_already_ordered_custom_port_spacing_survives_sync():
    design = branched_design()
    doc = layout_presentation(design)
    positions = {name: .1 + i*.15 for i, name in enumerate(PORTS)}
    doc.elements['device:MSP'].port_x = positions.copy()
    sync_riser_document(design, doc)
    assert doc.elements['device:MSP'].port_x == positions
