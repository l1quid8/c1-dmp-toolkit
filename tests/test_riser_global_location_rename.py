"""Location renames are project edits, not cosmetic drawing labels."""
import copy
from pathlib import Path

import customtkinter as ctk
import pytest

from test_riser_scene import branched_design
from test_release_editor_integration import editor, descendants, button
from parse_dmp_worksheet import PowerSupply, parse_dmp_worksheet
from riser_editor import RiserEditorController
from riser_model import RiserAnnotation
from riser_scene import layout_riser
from session import Session, save_session, load_session
from generate_dmp_ws import write_dmp_xlsx


def setup_design():
    design = branched_design()
    design.rsps[0].location = 'AT MSP'
    design.keypads[0].location = 'MDF (Service Keypad)'
    design.power_supplies = [PowerSupply(1, 'MDF'), PowerSupply(2, 'CLASSROOM 27')]
    doc = layout_riser(design)
    return design, doc, RiserEditorController(design, doc)


def test_global_rename_updates_all_matching_hardware_and_preserves_scene_and_wiring():
    design, doc, controller = setup_design()
    before = copy.deepcopy(design)
    controller.rename_location('location:MDF', 'MAIN BUILDING / MDF')
    assert design.site_info.xr550_location == 'MAIN BUILDING / MDF'
    assert [s.location for s in design.splitters] == ['MAIN BUILDING / MDF', 'MAIN BUILDING / MDF', 'CLASSROOM 27']
    assert [r.location for r in design.rsps] == ['MAIN BUILDING / MDF', 'CLASSROOM 27']
    assert [k.location for k in design.keypads] == ['MAIN BUILDING / MDF (Service Keypad)', 'OFFICE']
    assert [p.location for p in design.power_supplies] == ['MAIN BUILDING / MDF', 'CLASSROOM 27']
    assert design.connections == before.connections
    assert doc.routes == before.riser_document.routes
    assert set(doc.elements) == set(before.riser_document.elements)
    for key, old in before.riser_document.elements.items():
        new = doc.elements[key]
        assert (new.x, new.y, new.width, new.height) == (old.x, old.y, old.width, old.height)
    assert doc.elements['location:MDF'].ref == 'MAIN BUILDING MDF'
    assert controller.undo()
    assert design == before
    assert controller.redo()
    assert design.site_info.xr550_location == 'MAIN BUILDING / MDF'


@pytest.mark.parametrize('name', ['', '   ', '\n'])
def test_empty_global_rename_is_atomic(name):
    design, doc, controller = setup_design()
    before = copy.deepcopy(design)
    with pytest.raises(ValueError):
        controller.rename_location('location:MDF', name)
    assert design == before
    assert not controller.can_undo


def test_rename_into_existing_room_requires_explicit_merge():
    design, doc, controller = setup_design()
    before = copy.deepcopy(design)
    with pytest.raises(ValueError, match='exist'):
        controller.rename_location('location:MDF', 'OFFICE')
    assert design == before
    controller.rename_location('location:MDF', 'OFFICE', allow_merge=True)
    assert doc.elements['device:MSP'].location_id == doc.elements['device:KEYPAD-2'].location_id
    assert controller.undo()
    assert design == before


def test_presentation_undo_does_not_overwrite_an_external_location_edit():
    design, doc, controller = setup_design()
    controller.add_annotation(RiserAnnotation('note', 'text', [(30, 30)], text='CHECK'))
    design.rsps[1].location = 'NEW ROOM'
    controller.sync_external()
    assert controller.undo()
    assert design.rsps[1].location == 'NEW ROOM'
    assert doc.annotations == []


def test_external_location_change_rebases_global_rename_history():
    design, doc, controller = setup_design()
    controller.rename_location('location:MDF', 'HEAD END')
    design.rsps[0].location = 'TECH CORRECTION'
    controller.sync_external()
    assert not controller.can_undo
    assert design.rsps[0].location == 'TECH CORRECTION'


def test_undo_cannot_overwrite_an_external_edit_before_the_debounced_refresh():
    design, doc, controller = setup_design()
    controller.rename_location('location:MDF', 'HEAD END')
    design.rsps[0].location = 'TECH CORRECTION'
    assert not controller.undo()
    assert design.rsps[0].location == 'TECH CORRECTION'


def test_site_location_edit_refreshes_riser_and_invalidates_rename_undo(editor):
    frame, _ = editor
    tab = frame.riser_tab
    tab.controller.rename_location('location:MDF', 'HEAD END')
    tab._changed()
    frame._site_vars['xr550_location'].set('NEW PANEL ROOM')
    frame.flush_design_refresh()
    panel = tab.controller.document.elements['device:MSP']
    assert tab.controller.document.elements[panel.location_id].ref == 'NEW PANEL ROOM'
    assert not tab.controller.can_undo


def test_rename_includes_unplaced_matching_hardware_but_not_similar_other_rooms():
    from parse_dmp_worksheet import Splitter
    design, doc, controller = setup_design()
    design.splitters.extend([Splitter('710-LX600-9', 'LX', 'MDF'),
                             Splitter('710-LX600-10', 'LX', 'MDF ANNEX')])
    controller.sync_external()
    controller.rename_location('location:MDF', 'HEAD END')
    assert design.splitters[-2].location == 'HEAD END'
    assert design.splitters[-1].location == 'MDF ANNEX'
    assert '710-LX600-9' in doc.unplaced


def test_rename_save_reopen_and_worksheet_use_shared_locations(tmp_path):
    design, doc, controller = setup_design()
    controller.rename_location('location:MDF', 'HEAD END')
    save_session(Session(design=design), tmp_path / 'renamed.dmps')
    restored = load_session(tmp_path / 'renamed.dmps')
    assert restored.design == design
    output = tmp_path / 'renamed.xlsx'
    template = Path(__file__).resolve().parents[1] / 'DMP Installation Worksheet_template_blank.xlsx'
    write_dmp_xlsx(restored.design, template, output)
    reread = parse_dmp_worksheet(output)
    assert reread.site_info.xr550_location == 'HEAD END'
    assert reread.rsps[0].location == 'HEAD END'
    assert reread.power_supplies[0].location == 'HEAD END'
    assert next(s for s in reread.splitters if s.id == '710-LX500-1').location == 'HEAD END'


def test_inspector_rename_updates_other_tabs_and_undo_without_invalidating_wiring_review(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    tab.selected = ('element', 'location:MDF')
    tab._refresh_selection_properties()
    monkeypatch.setattr('riser_editor.simpledialog.askstring', lambda *a, **k: 'HEAD END')
    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **k: True)
    button(tab.properties, 'Rename location globally…').invoke()
    assert frame.session.design.site_info.xr550_location == 'HEAD END'
    assert frame._site_vars['xr550_location'].get() == 'HEAD END'
    assert frame.power_tab._location_vars[1].get() == 'HEAD END'
    assert any(isinstance(w, ctk.CTkEntry) and w.get() == 'HEAD END'
               for w in descendants(frame.splitters_tab))
    assert frame.session.topology_confirmed
    assert frame.dirty
    tab.undo()
    assert frame.power_tab._location_vars[1].get() == 'MDF'
    assert frame._site_vars['xr550_location'].get() == 'MDF'
    tab.redo()
    assert frame.power_tab._location_vars[1].get() == 'HEAD END'


def test_cancel_rename_confirmation_changes_nothing(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    before = copy.deepcopy(frame.session.design)
    tab.selected = ('element', 'location:MDF')
    tab._refresh_selection_properties()
    monkeypatch.setattr('riser_editor.simpledialog.askstring', lambda *a, **k: 'HEAD END')
    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **k: False)
    button(tab.properties, 'Rename location globally…').invoke()
    assert frame.session.design == before
