"""Regression coverage for shared wiring, drawing ownership and edit isolation."""

import copy
from types import SimpleNamespace

import pytest

from test_riser_scene import branched_design
from parse_dmp_worksheet import DMPDesign, RSP
from riser_editor import RiserEditorController, RiserTab
from riser_model import DevicePortRef, RiserAnnotation, RiserDocument, RiserElement, TopologyConnection
from riser_scene import layout_riser, sync_riser_document
from topology_service import (TopologyError, connect, reconnect, project_legacy_topology,
                              set_splitter_output)


@pytest.mark.parametrize('source,target', [
    (('710-LX500-1', 'OUT1'), ('RSP-9', 'IN')),
    (('710-LX500-1', 'OUT3'), ('RSP-2', 'IN')),
])
def test_occupied_port_edit_is_rejected_without_mutation(source, target):
    design = branched_design()
    before = copy.deepcopy(design)
    with pytest.raises(TopologyError, match='occupied'):
        connect(design, DevicePortRef(*source), DevicePortRef(*target))
    assert design == before


def test_failed_occupied_reconnect_preserves_original_edge_and_metadata():
    design = branched_design()
    edge = next(e for e in design.connections if e.target.device_id == 'RSP-1')
    before = copy.deepcopy(design)
    with pytest.raises(TopologyError, match='occupied'):
        reconnect(design, edge.id, target=DevicePortRef('RSP-2', 'IN'))
    assert design == before


def test_imported_conflict_has_same_explicit_projection_before_and_after_save():
    design = branched_design()
    port = DevicePortRef('710-LX500-1', 'OUT1')
    design.connections.append(TopologyConnection('import-conflict', port, DevicePortRef('RSP-2', 'IN')))
    # Existing ambiguity is retained for review, never resolved by list order.
    set_splitter_output(design, port.device_id, 0, 'RSP-1')
    splitter = next(s for s in design.splitters if s.id == port.device_id)
    before = splitter.outputs[0]
    assert before.startswith('CONFLICT:')
    assert 'RSP-1' in before and 'RSP-2' in before
    project_legacy_topology(design)
    assert splitter.outputs[0] == before
    design.connections.reverse()
    project_legacy_topology(design)
    assert splitter.outputs[0] == before


def test_overlapping_frames_move_only_their_logical_members():
    design = DMPDesign(rsps=[RSP(1, 'ROOM A', [501]), RSP(2, 'ROOM B', [517])])
    doc = RiserDocument(elements={
        'location:A': RiserElement('location:A', 'location', 'ROOM A', 0, 0, 400, 400),
        'location:B': RiserElement('location:B', 'location', 'ROOM B', 250, 0, 400, 400),
        'device:RSP-1': RiserElement('device:RSP-1', 'device', 'RSP-1', 40, 100, 50, 50),
        'device:RSP-2': RiserElement('device:RSP-2', 'device', 'RSP-2', 300, 100, 50, 50),
    })
    sync_riser_document(design, doc)  # Also migrates old drawings without ownership.
    controller = RiserEditorController(design, doc)
    controller.move_element('location:A', 18, 0)
    assert doc.elements['device:RSP-1'].x == 58
    assert doc.elements['device:RSP-2'].x == 300
    assert doc.elements['device:RSP-2'].location_id == 'location:B'


def test_location_assignment_retains_geometry_and_updates_module_ownership():
    design = branched_design()
    design.rsps[1].location = 'UNKNOWN'
    doc = layout_riser(design)
    device = doc.elements['device:RSP-2']
    xy = (device.x, device.y)
    design.rsps[1].location = 'CLASSROOM 27'
    sync_riser_document(design, doc)
    assert (device.x, device.y) == xy
    assert doc.elements[device.location_id].ref == 'CLASSROOM 27'


def test_unrelated_refresh_preserves_markup_undo():
    design = branched_design()
    controller = RiserEditorController(design, layout_riser(design))
    controller.add_annotation(RiserAnnotation('note', 'text', [(100, 100)], text='CHECK'))
    proxy = SimpleNamespace(design=design, controller=controller,
                            cancel=lambda **kw: None, _reconcile_selection=lambda: None,
                            _title_vars={}, redraw=lambda **kw: None)
    RiserTab.refresh(proxy)
    assert controller.can_undo
    controller.undo()
    assert not controller.document.annotations
