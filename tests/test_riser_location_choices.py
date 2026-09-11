"""Location assignment offers usable rooms in a bounded, searchable picker."""
import copy
from types import SimpleNamespace

import pytest

from test_release_editor_integration import editor, button
from parse_dmp_worksheet import Keypad
from project_locations import sync_project_locations
from ui_widgets import SearchableComboBox


def location_picker(tab, device='KEYPAD-2'):
    tab.selected = ('element', 'device:' + device)
    tab._refresh_selection_properties()
    picks = [widget for widget in tab.properties.winfo_children()
             if isinstance(widget, SearchableComboBox)]
    assert len(picks) == 1, 'Location assignment must use the bounded searchable picker'
    return picks[0]


def test_location_search_assigns_only_after_apply_and_remains_undoable(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    before = copy.deepcopy(frame.session.design)
    picker = location_picker(tab)
    assert picker.get() == 'OFFICE'
    picker._entry.delete(0, 'end')
    picker._entry.insert(0, 'classroom')
    picker._on_key_release(SimpleNamespace(keysym='m'))
    assert picker._popup.get(0, 'end') == ('CLASSROOM 27',)
    picker._popup.selection_set(0)
    picker._accept()
    assert frame.session.design == before

    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **kw: True)
    button(tab.properties, 'Assign location').invoke()

    assert frame.session.design.keypads[1].location == 'CLASSROOM 27'
    assert tab.controller.undo()
    assert frame.session.design == before


def test_location_popup_stays_bounded_with_long_names_and_many_rooms(editor):
    frame, _ = editor
    tab = frame.riser_tab
    frame.session.design.keypads.extend(
        Keypad(n, location=f'BUILDING {n} ' + 'LONG ROOM NAME ' * 12) for n in range(3, 50))
    sync_project_locations(frame.session.design)
    picker = location_picker(tab)
    frame.update_idletasks()

    picker._clicked()
    frame.update_idletasks()

    assert picker._popup.size() == 50
    assert int(picker._popup.cget('height')) <= 8
    assert picker._popup.winfo_width() <= max(picker.winfo_width(), 120)


def test_cable_polluted_current_location_cannot_be_reassigned_to_other_devices(editor):
    frame, _ = editor
    tab = frame.riser_tab
    design = frame.session.design
    design.keypads[1].location = '(E)WP240 KEYPAD 8 BUILDING B'
    sync_project_locations(design)

    picker = location_picker(tab)

    assert set(picker.cget('values')) == {'MDF', 'CLASSROOM 27'}
    assert 'review' in picker.get().lower()
    assert button(tab.properties, 'Assign location').cget('state') == 'disabled'
    picker._choose('MDF')
    assert button(tab.properties, 'Assign location').cget('state') == 'normal'


@pytest.mark.parametrize('finish_entry', ['click', 'return', 'focus_out'])
def test_custom_location_is_created_only_on_assign_and_undo_removes_it(
        editor, monkeypatch, tmp_path, finish_entry):
    from session import save_session, load_session
    frame, _ = editor
    tab = frame.riser_tab
    design = frame.session.design
    before = copy.deepcopy(design)
    picker = location_picker(tab, '710-LX500-1')
    for text in ('Ma', 'Manual Input', '  Manual   Input Test  '):
        picker._entry.delete(0, 'end')
        picker._entry.insert(0, text)
        picker._on_key_release(SimpleNamespace(keysym='t'))
        assert design == before
    if finish_entry == 'return':
        picker._commit_entry()
    elif finish_entry == 'focus_out':
        picker._finish_focus_out()
    assert picker.get().strip() == 'Manual   Input Test'
    assert design == before
    assert button(tab.properties, 'Assign location').cget('state') == 'normal'
    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **kw: True)

    button(tab.properties, 'Assign location').invoke()

    identity = design.device_location_ids['710-LX500-1']
    assert design.equipment_locations[identity].full_label == 'Manual Input Test'
    assert set(design.equipment_locations) - set(before.equipment_locations) == {identity}
    assert design.site_info.xr550_location == 'MDF'
    assert design.rsps[0].location == 'MDF'
    assert design.connections == before.connections
    assert design.riser_document.routes == before.riser_document.routes
    for key, element in design.riser_document.elements.items():
        if element.kind == 'device':
            old = before.riser_document.elements[key]
            assert (element.x, element.y) == (old.x, old.y)
    assert 'Manual Input Test' in location_picker(tab).cget('values')
    after = copy.deepcopy(design)
    assert tab.controller.undo()
    assert design == before
    assert tab.controller.redo()
    assert design == after
    save_session(frame.session, tmp_path / 'custom.dmps')
    reopened = load_session(tmp_path / 'custom.dmps').design
    assert reopened.equipment_locations[identity].full_label == 'Manual Input Test'
    assert reopened.device_location_ids['710-LX500-1'] == identity


def test_typing_existing_location_reuses_its_identity_without_selecting_a_result(editor, monkeypatch):
    frame, _ = editor
    design = frame.session.design
    expected = design.device_location_ids['RSP-2']
    old_ids = set(design.equipment_locations)
    picker = location_picker(frame.riser_tab)
    picker._entry.delete(0, 'end')
    picker._entry.insert(0, '  classroom   27  ')
    picker._on_key_release(SimpleNamespace(keysym='7'))
    assert button(frame.riser_tab.properties, 'Assign location').cget('state') == 'normal'
    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **kw: True)

    button(frame.riser_tab.properties, 'Assign location').invoke()

    assert design.device_location_ids['KEYPAD-2'] == expected
    assert design.keypads[1].location == 'CLASSROOM 27'
    assert set(design.equipment_locations) <= old_ids


def test_cancelling_custom_assignment_leaves_no_location_or_undo_entry(editor, monkeypatch):
    frame, _ = editor
    before = copy.deepcopy(frame.session.design)
    picker = location_picker(frame.riser_tab)
    picker._entry.delete(0, 'end')
    picker._entry.insert(0, 'New storage room')
    assert button(frame.riser_tab.properties, 'Assign location').cget('state') == 'normal'
    monkeypatch.setattr('riser_editor.messagebox.askyesno', lambda *a, **kw: False)

    button(frame.riser_tab.properties, 'Assign location').invoke()

    assert frame.session.design == before
    assert not frame.riser_tab.controller.can_undo


def test_switching_selection_releases_the_discarded_draft_listener(editor):
    frame, _ = editor
    picker = location_picker(frame.riser_tab)
    draft = picker.cget('variable')
    draft.set('Unsaved room')

    location_picker(frame.riser_tab, '710-LX500-1')

    assert draft.trace_info() == []
    assert all(record.full_label != 'Unsaved room'
               for record in frame.session.design.equipment_locations.values())
