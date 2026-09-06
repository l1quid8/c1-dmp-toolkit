"""Acceptance starts with an old saved scene, not a fresh layout preview."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from riser_model import RiserAnnotation, RiserElement, RiserRoute
from riser_scene import layout_riser, sync_riser_document, validate_riser
from riser_scene import _overlap
from riser_editor import RiserEditorController, RiserTab
from session import Session, save_session, load_session
from test_riser_scene import branched_design
from test_riser_drag_performance import tab
from editor_frame import EditorFrame


def outdated_scene():
    design = branched_design()
    doc = layout_riser(design)
    doc.title_block.drawn_by = 'FIELD TEAM'
    doc.annotations.append(RiserAnnotation('keep-note', 'text', [(100,100)], text='KEEP THIS'))
    doc.z_order.append('keep-note')
    for e in doc.elements.values():
        if e.kind == 'device':
            e.location_id = None  # pre-ownership saved drawing
    doc.elements['old-room'] = RiserElement('old-room','location','OLD ROOM',100,100,300,300)
    doc.z_order.insert(0, 'old-room')
    doc.routes['removed-edge'] = RiserRoute('removed-edge', [(1,1),(300,1)])
    design.riser_document = doc
    return design


def test_open_old_session_repairs_generated_scene_and_roundtrips(tab, tmp_path):
    design = outdated_scene()
    connections = copy.deepcopy(design.connections)
    path = tmp_path / 'old.dmps'
    save_session(Session(design=design), path)
    original_file = path.read_bytes()
    loaded = load_session(path)
    edits = []
    opened = RiserTab(tab.master, loaded, lambda: edits.append(True))
    opened.pack()
    opened.update()
    try:
        doc = opened.controller.document
        assert 'old-room' not in doc.elements
        assert 'removed-edge' not in doc.routes
        assert all(e.location_id in doc.elements for e in doc.elements.values() if e.kind == 'device')
        assert doc.title_block.drawn_by == 'FIELD TEAM'
        assert doc.annotations[0].text == 'KEEP THIS'
        assert loaded.design.connections == connections
        assert edits, 'An in-memory recovery must make Save available'
        assert path.read_bytes() == original_file  # opening never writes the source
        save_session(loaded, tmp_path/'repaired.dmps')
        restored = load_session(tmp_path/'repaired.dmps')
        assert restored.design.riser_document == doc
        again_edits = []
        again = RiserTab(tab.master, restored, lambda: again_edits.append(True))
        again.update()
        assert again.controller.document == doc
        assert not again_edits
        again.destroy()
    finally:
        opened.destroy()


def test_sync_removes_unused_generated_frames_and_dead_routes_but_not_manual_work():
    design = outdated_scene()
    doc = design.riser_document
    device = doc.elements['device:RSP-2']
    device.x, device.y, device.manual = 1500, 1000, True
    edge_id = design.connections[0].id
    doc.routes[edge_id].manual = True
    route = copy.deepcopy(doc.routes[edge_id])
    sync_riser_document(design, doc)
    assert 'old-room' not in doc.elements
    assert 'old-room' not in doc.z_order
    assert 'removed-edge' not in doc.routes
    assert (device.x, device.y) == (1500,1000)
    assert doc.routes[edge_id] == route
    assert doc.annotations[0].text == 'KEEP THIS'


def test_repair_through_full_editor_marks_dirty_after_construction(tab, tmp_path):
    session = Session(design=outdated_scene(), saved_at='2026-09-02T12:00:00', path=tmp_path/'saved.dmps')
    frame = EditorFrame(tab.master, tab.master, session)
    frame.pack()
    frame.update()
    try:
        assert frame.dirty
        assert 'removed-edge' not in frame.riser_tab.controller.document.routes
        assert 'old-room' not in frame.riser_tab.controller.document.elements
    finally:
        frame.destroy()


def test_manual_unused_or_overlapping_frames_are_preserved_but_warned():
    design = branched_design()
    doc = layout_riser(design)
    owner = doc.elements['location:MDF']
    spare = RiserElement('manual-box', 'location', 'EMPTY ROOM', owner.x, owner.y, 100,100, manual=True)
    doc.elements[spare.id] = spare
    doc.z_order.append(spare.id)
    sync_riser_document(design, doc)
    assert doc.elements[spare.id] == spare
    codes = {i.code for i in validate_riser(design, doc)}
    assert {'scene.unused_location', 'scene.location_overlap'} <= codes


def test_open_repairs_generated_overlaps_even_when_ownership_ids_are_current(tab):
    design = branched_design()
    doc = layout_riser(design)
    root = doc.elements['location:MDF']
    room = doc.elements['location:CLASSROOM 27']
    room.x, room.y = root.x, root.y
    design.riser_document = doc
    opened = RiserTab(tab.master, Session(design=design), lambda: None)
    try:
        assert not _overlap(doc.elements['location:MDF'], doc.elements['location:CLASSROOM 27'])
    finally:
        opened.destroy()


def test_auto_layout_does_not_duplicate_a_renamed_location_frame():
    design = branched_design()
    doc = layout_riser(design)
    owner = doc.elements[doc.elements['device:RSP-2'].location_id]
    owner.manual = True
    # Stable logical ID can retain an older spelling after a room rename.
    old_id = owner.id
    owner.ref = 'RENAMED ROOM'
    design.rsps[-1].location = 'RENAMED ROOM'
    design.splitters[-1].location = 'RENAMED ROOM'
    controller = RiserEditorController(design, doc)
    controller.auto_layout()
    matching = [e for e in doc.elements.values() if e.kind == 'location' and e.ref == 'RENAMED ROOM']
    assert [e.id for e in matching] == [old_id]


@pytest.mark.parametrize('location', ['MDF', 'MSP', 'AT MSP', 'SAME AS MSP'])
def test_rsp_at_msp_shares_panel_location_without_number_based_assumptions(location):
    design = branched_design()
    design.rsps[0].location = location
    doc = layout_riser(design)
    assert doc.elements['device:RSP-1'].location_id == doc.elements['device:MSP'].location_id
    assert doc.elements['device:RSP-2'].location_id != doc.elements['device:MSP'].location_id
    sync_riser_document(design, doc)
    assert doc.elements['device:RSP-1'].location_id == doc.elements['device:MSP'].location_id
    assert design.rsps[0].location == location  # no domain rewrites


def test_both_rsp_one_and_two_can_share_the_msp_module_after_reopen(tab, tmp_path):
    design = branched_design()
    design.rsps[0].location = 'MSP'
    design.rsps[1].location = 'AT MSP'
    design.riser_document = layout_riser(design)
    save_session(Session(design=design), tmp_path/'shared-room.dmps')
    session = load_session(tmp_path/'shared-room.dmps')
    opened = RiserTab(tab.master, session, lambda: None)
    try:
        doc = opened.controller.document
        assert len({doc.elements[f'device:{ref}'].location_id for ref in ('MSP','RSP-1','RSP-2')}) == 1
        assert not [i for i in validate_riser(session.design, doc) if i.code == 'scene.location_mismatch']
    finally:
        opened.destroy()
