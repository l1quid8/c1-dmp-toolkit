"""RISER uses the real shared hardware forms, not a parallel device model."""
import copy

import customtkinter as ctk
import pytest

from test_release_editor_integration import editor, button, descendants


def window(frame, title):
    return next(w for w in frame.winfo_toplevel().winfo_children()
                if isinstance(w, ctk.CTkToplevel) and w.title() == title)


def test_add_splitter_from_riser_updates_project_and_unplaced(editor):
    frame, _ = editor
    frame.tabs.set('RISER')
    old_positions = copy.deepcopy(frame.riser_tab.controller.document.elements)
    button(frame.riser_tab, 'Add Device').invoke()
    button(window(frame, 'Add Device'), '710 Splitter').invoke()
    button(window(frame, 'Add splitter'), 'Add splitter').invoke()
    assert any(s.id == '710-LX500-3' for s in frame.session.design.splitters)
    assert '710-LX500-3' in frame.riser_tab.controller.document.unplaced
    assert frame.tabs.get() == 'RISER'
    assert frame.riser_tab.controller.document.elements == old_positions
    assert not frame.session.topology_confirmed


@pytest.mark.parametrize('key,location', [
    ('710-LX500-2', 'CLASSROOM 27'), ('KEYPAD-2', 'OFFICE'),
    ('RSP-2', 'CLASSROOM 27'), ('MSP', 'MDF')])
def test_edit_form_location_updates_shared_project(editor, key, location):
    frame, _ = editor
    tab = frame.riser_tab
    frame.tabs.set('RISER')
    tab.selected = ('element', f'device:{key}')
    tab.redraw()
    button(tab.properties, 'Edit Device').invoke()
    win = window(frame, f'Edit {key}')
    field = next(w for w in descendants(win)
                 if isinstance(w, ctk.CTkEntry) and w.get() == location)
    field.delete(0, 'end')
    field.insert(0, 'NEW EQUIPMENT ROOM')
    button(win, 'Done').invoke()
    from project_locations import location_values
    assert location_values(frame.session.design)[key] == 'NEW EQUIPMENT ROOM'
    assert frame.tabs.get() == 'RISER'
    assert frame.session.design.equipment_locations[
        frame.session.design.device_location_ids[key]].full_label == 'NEW EQUIPMENT ROOM'


def test_cancel_add_is_noop_and_preview_blocks_hardware_forms(editor):
    frame, _ = editor
    tab = frame.riser_tab
    before = copy.deepcopy(frame.session.design)
    button(tab, 'Add Device').invoke()
    button(window(frame, 'Add Device'), 'Cancel').invoke()
    assert frame.session.design == before
    tab.relayout()
    tab.add_device()
    assert not any(isinstance(w, ctk.CTkToplevel)
                   for w in frame.winfo_toplevel().winfo_children())


def test_remove_from_riser_requires_confirmation_and_uses_cascade(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    tab.selected = ('element', 'device:KEYPAD-2')
    tab.redraw()
    monkeypatch.setattr('editor_tabs.messagebox.askyesno', lambda *a, **k: False)
    button(tab.properties, 'Remove from Project').invoke()
    assert len(frame.session.design.keypads) == 2
    monkeypatch.setattr('editor_tabs.messagebox.askyesno', lambda *a, **k: True)
    button(tab.properties, 'Remove from Project').invoke()
    assert len(frame.session.design.keypads) == 1
    assert not any(e.target.device_id == 'KEYPAD-2' for e in frame.session.design.connections)
    splitter = next(s for s in frame.session.design.splitters if s.id == '710-KP-1')
    assert splitter.outputs[0] == 'Spare'


def test_done_commits_splitter_number_without_focusout(editor):
    frame, _ = editor
    frame._edit_riser_hardware('710-LX500-2')
    win = window(frame, 'Edit 710-LX500-2')
    number = next(w for w in descendants(win)
                  if isinstance(w, ctk.CTkEntry) and w.get() == '2')
    number.delete(0, 'end')
    number.insert(0, '8')
    button(win, 'Done').invoke()
    assert any(s.id == '710-LX500-8' for s in frame.session.design.splitters)
    assert not any(s.id == '710-LX500-2' for s in frame.session.design.splitters)


def test_rejected_keypad_source_restores_popup_display(editor, monkeypatch):
    frame, _ = editor
    frame._edit_riser_hardware('KEYPAD-2')
    win = window(frame, 'Edit KEYPAD-2')
    menu = next(w for w in descendants(win)
                if isinstance(w, ctk.CTkOptionMenu) and w.get() == '710-KP-1')
    monkeypatch.setattr('editor_tabs.messagebox.showwarning', lambda *a, **k: None)
    menu._dropdown_callback('MSP')  # panel KP port is already occupied
    assert frame.session.design.keypads[1].source == '710-KP-1'
    assert menu.get() == '710-KP-1'


def test_rejected_splitter_output_restores_popup_display(editor, monkeypatch):
    from ui_widgets import SearchableComboBox
    from topology_service import project_legacy_topology
    frame, _ = editor
    project_legacy_topology(frame.session.design)
    frame._edit_riser_hardware('710-LX500-2')
    win = window(frame, 'Edit 710-LX500-2')
    menu = next(w for w in descendants(win)
                if isinstance(w, SearchableComboBox) and w.get() == 'RSP-2')
    monkeypatch.setattr('editor_tabs.messagebox.showwarning', lambda *a, **k: None)
    menu._choose('RSP-1')  # already fed by a different splitter
    splitter = next(s for s in frame.session.design.splitters if s.id == '710-LX500-2')
    assert splitter.outputs[0] == 'RSP-2'
    assert menu.get() == 'RSP-2'


def test_keypad_type_updates_dependent_popup_fields(editor):
    frame, _ = editor
    frame._edit_riser_hardware('KEYPAD-2')
    win = window(frame, 'Edit KEYPAD-2')
    menu = next(w for w in descendants(win)
                if isinstance(w, ctk.CTkOptionMenu) and w.get() == 'Keypad')
    menu._dropdown_callback('Zone expander')
    frame.update_idletasks()
    assert frame.session.remotelink.keypads[2].disp_areas == '00'
    assert any(isinstance(w, ctk.CTkEntry) and w.get() == '00' for w in descendants(win))


def test_closing_project_closes_its_hardware_form(editor):
    frame, _ = editor
    frame._edit_riser_hardware('KEYPAD-2')
    win = window(frame, 'Edit KEYPAD-2')
    frame.destroy()
    assert not win.winfo_exists()


def test_closing_project_closes_pending_hardware_creation(editor):
    frame, _ = editor
    frame.riser_tab.add_device()
    button(window(frame, 'Add Device'), '710 Splitter').invoke()
    win = window(frame, 'Add splitter')
    frame.destroy()
    assert not win.winfo_exists()
