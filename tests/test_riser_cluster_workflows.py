import copy
import pytest
from test_riser_scene import branched_design
from test_release_editor_integration import editor, button
from riser_editor import RiserEditorController
from riser_model import RiserAnnotation
from riser_scene import layout_riser, layout_clusters, sync_riser_document, repair_generated_scene
from session import Session, save_session, load_session


def controller():
    d = branched_design()
    from topology_service import project_legacy_topology
    project_legacy_topology(d)
    doc = layout_riser(d)
    doc.elements['device:MSP'].manual = True
    doc.title_block.drawn_by = 'FIELD TEAM'
    doc.annotations.append(RiserAnnotation('note', 'text', [(40, 40)], text='KEEP'))
    doc.z_order.append('note')
    return d, RiserEditorController(d, doc)


def test_preview_is_noop_and_apply_undo_restores_exact_state(tmp_path):
    d, c = controller()
    before = copy.deepcopy(d)
    preview = c.preview_layout()
    assert d == before
    assert not c.can_undo
    c.apply_layout(preview)
    assert c.document.layout_version == 3
    assert c.document.annotations == before.riser_document.annotations
    assert c.document.title_block == before.riser_document.title_block
    assert c.undo()
    assert d == before
    assert c.redo()
    after = copy.deepcopy(d)
    save_session(Session(design=d), tmp_path/'clustered.dmps')
    reopened = load_session(tmp_path/'clustered.dmps').design
    sync_riser_document(reopened, reopened.riser_document)
    assert reopened == after
    assert not repair_generated_scene(reopened, reopened.riser_document)


def test_preview_cannot_apply_after_a_project_edit():
    d, c = controller()
    preview = c.preview_layout()
    c.add_annotation(RiserAnnotation('later', 'text', [(40, 80)], text='LATER'))
    before = copy.deepcopy(d)
    with pytest.raises(ValueError, match='changed'):
        c.apply_layout(preview)
    assert d == before


def test_preview_rejects_changed_equipment_details():
    d, c = controller()
    preview = c.preview_layout()
    d.rsps[0].zones.append(599)
    with pytest.raises(ValueError, match='changed'):
        c.apply_layout(preview)


def test_cluster_rename_and_assignment_preserve_manual_geometry_and_undo():
    d, c = controller()
    c.apply_layout(c.preview_layout())
    frame_id = c.document.elements['device:MSP'].location_id
    identity = c.document.elements[frame_id].physical_location_id
    c.move_element(frame_id, 18, 18)
    before = copy.deepcopy(d)
    c.edit_location(identity, building='MAIN', floor='1st Floor', room='MDF')
    assert d.device_location_ids['MSP'] == identity
    assert c.document.elements[frame_id].heading_lines == ['MAIN', '1st Floor · MDF']
    assert c.document.routes == before.riser_document.routes
    assert c.undo()
    assert d == before
    target = d.device_location_ids['RSP-2']
    c.assign_location('KEYPAD-2', target)
    assert d.keypads[1].location == 'CLASSROOM 27'
    for key, old in before.riser_document.elements.items():
        if old.kind == 'device':
            assert (c.document.elements[key].x, c.document.elements[key].y) == (old.x, old.y)


def test_legacy_open_does_not_convert_to_new_layout():
    d, c = controller()
    before = copy.deepcopy(c.document)
    sync_riser_document(d, c.document)
    assert c.document == before
    assert c.document.layout_version == 1


def test_cluster_auto_layout_preserves_version_and_existing_geometry():
    d, c = controller()
    c.apply_layout(c.preview_layout())
    before = copy.deepcopy(c.document)
    c.auto_layout()
    assert c.document == before
    c.relayout()
    assert c.document.layout_version == 3


def test_auto_layout_places_new_room_around_retained_manual_geometry():
    from parse_dmp_worksheet import Keypad
    from riser_scene import _overlap
    d, c = controller()
    c.apply_layout(c.preview_layout())
    d.keypads.append(Keypad(3, 'MSP', 'NEW ROOM'))
    c.sync_external()
    generated = layout_clusters(d)
    proposed = generated.elements['device:KEYPAD-3']
    panel = c.document.elements['device:MSP']
    c.move_element(panel.id, proposed.x - panel.x, proposed.y - panel.y)
    before = copy.deepcopy(c.document)
    c.auto_layout()
    assert not _overlap(c.document.elements['device:KEYPAD-3'], panel)
    assert c.document.elements[panel.id] == before.elements[panel.id]
    assert all(c.document.routes[key] == route for key, route in before.routes.items())


def test_preview_blocks_all_generation_actions(editor, monkeypatch):
    frame, calls = editor
    monkeypatch.setattr('editor_frame.messagebox.showinfo', lambda *a, **k: None)
    frame.riser_tab.relayout()
    frame._on_generate_worksheet()
    frame._on_generate_chart()
    frame._on_generate_remotelink()
    frame._on_generate_riser()
    assert calls == []


def test_new_scene_defaults_to_clusters(editor):
    from riser_editor import RiserTab
    frame, _ = editor
    d = branched_design()
    tab = RiserTab(frame, Session(design=d), lambda: None)
    try:
        assert tab.controller.document.layout_version == 3
    finally:
        tab.destroy()


def test_cluster_drag_is_incremental_and_undoable(editor, monkeypatch):
    from test_riser_app_integration import _event_for_world
    frame, _ = editor
    tab = frame.riser_tab
    tab.controller.apply_layout(tab.controller.preview_layout())
    tab._toggle_layer('Locations')
    tab.redraw()
    doc = tab.controller.document
    location = doc.elements[doc.elements['device:RSP-2'].location_id]
    before = copy.deepcopy(doc)
    start = (location.x + 18, location.y + 18)
    tab._on_press(_event_for_world(tab, start))
    assert tab._gesture[0] == 'move'
    page = tab.canvas.find_withtag('page')
    original = tab.redraw
    monkeypatch.setattr(tab, 'redraw', lambda *a, **k: pytest.fail('Full redraw during cluster drag'))
    for i in range(1, 11):
        tab._on_drag(_event_for_world(tab, (start[0] + i * 18, start[1] + i * 18)))
    assert tab.canvas.find_withtag('page') == page
    monkeypatch.setattr(tab, 'redraw', original)
    tab._on_release(_event_for_world(tab, (start[0] + 180, start[1] + 180)))
    tab.undo()
    assert tab.controller.document == before


def test_real_editor_preview_cancel_apply_and_undo(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    before = copy.deepcopy(frame.session.design)
    dirty_before = frame.dirty
    button(tab, 'Preview layout').invoke()
    assert tab._layout_preview is not None
    assert frame.session.design == before
    assert frame.dirty == dirty_before
    tab.cancel()
    assert tab._layout_preview is None
    assert frame.session.design == before
    button(tab, 'Preview layout').invoke()
    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **k: True)
    button(tab, 'Apply layout').invoke()
    assert frame.session.design.riser_document.layout_version == 3
    assert frame.dirty
    tab.undo()
    assert frame.session.design == before


def test_inspector_building_room_edit_updates_shared_project(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    tab.controller.apply_layout(tab.controller.preview_layout())
    owner = tab.controller.document.elements['device:MSP'].location_id
    tab.selected = ('element', owner)
    tab._refresh_selection_properties()
    tab._structured_location_vars['building'].set('MAIN BUILDING')
    tab._structured_location_vars['floor'].set('1st Floor')
    tab._structured_location_vars['room'].set('Main Office')
    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **k: True)
    button(tab.properties, 'Apply location fields').invoke()
    assert frame._site_vars['xr550_location'].get() == 'MAIN BUILDING 1st Floor Main Office'
    assert frame.power_tab._location_vars[1].get() == 'MAIN BUILDING 1st Floor Main Office'


def test_preview_blocks_canvas_and_toolbar_mutations(editor):
    frame, _ = editor
    tab = frame.riser_tab
    tab.relayout()
    before = copy.deepcopy(frame.session.design)
    tab.undo()
    tab.auto_layout()
    tab.delete_selected()
    tab.set_tool('Rectangle')
    assert frame.session.design == before
    assert tab._layout_preview is not None
