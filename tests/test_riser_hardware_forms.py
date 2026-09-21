"""RISER uses the real shared hardware forms, not a parallel device model."""
import copy

import customtkinter as ctk
import pytest

from test_release_editor_integration import editor, button, descendants


def window(frame, title):
    return next(w for w in frame.winfo_toplevel().winfo_children()
                if isinstance(w, ctk.CTkToplevel) and w.title() == title)


@pytest.mark.parametrize('layout_version', [1, 3])
def test_add_splitter_from_riser_updates_project_and_places_device(editor, layout_version):
    frame, _ = editor
    frame.tabs.set('RISER')
    if layout_version == 3:
        from riser_presentation import layout_presentation
        document = layout_presentation(frame.session.design)
        frame.session.design.riser_document = document
        frame.riser_tab.controller.document = document
        frame.riser_tab.refresh()
    old_positions = copy.deepcopy(frame.riser_tab.controller.document.elements)
    button(frame.riser_tab, 'Add Device').invoke()
    button(window(frame, 'Add Device'), '710 Splitter').invoke()
    button(window(frame, 'Add splitter'), 'Add splitter').invoke()
    assert any(s.id == '710-LX500-3' for s in frame.session.design.splitters)
    assert '710-LX500-3' not in frame.riser_tab.controller.document.unplaced
    assert 'device:710-LX500-3' in frame.riser_tab.controller.document.elements
    assert frame.tabs.get() == 'RISER'
    assert all(frame.riser_tab.controller.document.elements[key] == value
               for key, value in old_positions.items())
    assert not frame.session.topology_confirmed


@pytest.mark.parametrize('bus,want', [
    ('600', '710-LX600-1'), ('900', '710-LX900-1'),
])
def test_riser_shared_splitter_form_uses_selected_lx_bus(editor, bus, want):
    frame, _ = editor
    frame.tabs.set('RISER')
    button(frame.riser_tab, 'Add Device').invoke()
    button(window(frame, 'Add Device'), '710 Splitter').invoke()
    win = window(frame, 'Add splitter')
    menu = next(w for w in descendants(win)
                if isinstance(w, ctk.CTkOptionMenu) and w.get() == '500')
    assert menu.cget('values') == ['500', '600', '700', '800', '900']
    menu._dropdown_callback(bus)
    button(win, 'Add splitter').invoke()
    assert any(s.id == want for s in frame.session.design.splitters)
    assert want not in frame.riser_tab.controller.document.unplaced
    assert f'device:{want}' in frame.riser_tab.controller.document.elements
    assert frame.tabs.get() == 'RISER'


def test_splitter_form_bus_picker_only_shown_for_lx(editor):
    frame, _ = editor
    frame.splitters_tab._add_clicked()
    win = window(frame, 'Add splitter')
    menu = next(w for w in descendants(win)
                if isinstance(w, ctk.CTkOptionMenu) and w.get() == '500')
    holder = menu.master.master
    assert holder.winfo_manager() == 'pack'
    radios = [w for w in descendants(win) if isinstance(w, ctk.CTkRadioButton)]
    next(w for w in radios if w.cget('value') == 'KP').invoke()
    assert holder.winfo_manager() == ''
    next(w for w in radios if w.cget('value') == 'LX').invoke()
    assert holder.winfo_manager() == 'pack'


@pytest.mark.parametrize('source,phrase', [
    ('riser', 'read from the riser'),
    ('auto-derived', 'extraction was incomplete'),
    ('manual', 'authored here'),
    ('', 'extraction was incomplete'),
])
def test_splitter_banner_and_review_control_match_provenance(editor, source, phrase):
    frame, _ = editor
    frame.session.design.topology_source = source
    frame.session.topology_confirmed = False
    header = frame.splitters_tab._build_header_row()
    texts = [w.cget('text') for w in descendants(header)
             if isinstance(w, ctk.CTkLabel)]
    assert any(phrase in text for text in texts)
    checkbox = next(w for w in descendants(header) if isinstance(w, ctk.CTkCheckBox))
    if source == 'manual':
        assert 'complete' in checkbox.cget('text').lower()
        assert all('riser' not in text.lower() and 'extraction' not in text.lower()
                   for text in texts)
    checkbox.toggle()
    assert frame.session.topology_confirmed
    header.destroy()


def test_first_msp_keypad_form_explains_service_convention_and_persists(editor, tmp_path):
    from editor_tabs import prompt_add_keypad
    from parse_dmp_worksheet import SiteInfo
    from riser_model import DevicePortRef
    from session import create_blank_session, load_session, save_session
    frame, _ = editor
    session = create_blank_session(SiteInfo(school_name='MANUAL SITE'))
    done = []
    win = prompt_add_keypad(frame.root, session, lambda: done.append(True))
    texts = [w.cget('text') for w in descendants(win) if isinstance(w, ctk.CTkLabel)]
    assert any('service keypad' in text.lower() and 'MSP' in text for text in texts)
    button(win, 'Add keypad').invoke()
    assert done == [True]
    assert [(k.number, k.source) for k in session.design.keypads] == [(1, 'MSP')]
    edge, = session.design.connections
    assert edge.source == DevicePortRef('MSP', 'KP BUS')
    assert edge.target == DevicePortRef('KEYPAD-1', 'IN')
    saved = save_session(session, tmp_path / 'manual-service.dmps')
    reopened = load_session(saved)
    assert reopened.design.connections == session.design.connections
    assert [(k.number, k.source) for k in reopened.design.keypads] == [(1, 'MSP')]


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
    if key.startswith('KEYPAD-'):
        field = next(w for w in descendants(win)
                     if isinstance(w, ctk.CTkTextbox)
                     and w.get('1.0', 'end-1c') == location)
        field.delete('1.0', 'end')
        field.insert('1.0', 'NEW EQUIPMENT ROOM\n(Service Keypad)')
        frame.update()
        expected = 'NEW EQUIPMENT ROOM\n(Service Keypad)'
    else:
        field = next(w for w in descendants(win)
                     if isinstance(w, ctk.CTkEntry) and w.get() == location)
        field.delete(0, 'end')
        field.insert(0, 'NEW EQUIPMENT ROOM')
        expected = 'NEW EQUIPMENT ROOM'
    button(win, 'Done').invoke()
    from project_locations import location_values
    assert location_values(frame.session.design)[key] == expected
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


@pytest.mark.parametrize('choice,title', [
    ('Keypad', 'Add keypad'), ('RSP + Power Supply', 'Add expander'),
])
def test_add_hardware_automatically_places_all_new_symbols(editor, choice, title, monkeypatch):
    import editor_tabs
    def fail_dialog(*args, **kwargs):
        pytest.fail(str(args))
    monkeypatch.setattr(editor_tabs.messagebox, 'showerror', fail_dialog)
    frame, _ = editor
    frame.tabs.set('RISER')
    from riser_presentation import layout_presentation
    document = layout_presentation(frame.session.design)
    frame.session.design.riser_document = document
    frame.riser_tab.controller.document = document
    frame.riser_tab.refresh()
    old_positions = copy.deepcopy(document.elements)
    button(frame.riser_tab, 'Add Device').invoke()
    button(window(frame, 'Add Device'), choice).invoke()
    if choice == 'Keypad':
        menu = next(w for w in descendants(window(frame, title))
                    if isinstance(w, ctk.CTkOptionMenu) and w.get() == 'MSP')
        menu.set('710-KP-1')
    button(window(frame, title), title).invoke()
    added = [e for key, e in document.elements.items()
             if key not in old_positions and e.kind == 'device']
    assert len(added) == 1
    if choice == 'RSP + Power Supply':
        number = int(added[0].ref.split('-')[1])
        assert any(ps.number == number for ps in frame.session.design.power_supplies)
    assert not document.unplaced
    assert all(document.elements[key] == value for key, value in old_positions.items())
    assert frame.tabs.get() == 'RISER'
    assert frame.riser_tab.controller.undo()
    assert all(e.ref in document.unplaced for e in added)
    assert frame.riser_tab.controller.redo()
    assert not document.unplaced


def test_unplaced_section_is_removed_and_failed_placement_is_actionable(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    texts = [w.cget('text') for w in descendants(tab.inspector)
             if isinstance(w, (ctk.CTkLabel, ctk.CTkButton))]
    assert all(text.replace('\u2009', '').upper() != 'UNPLACED' for text in texts)
    assert 'No unplaced devices' not in texts
    warnings = []
    import editor_frame
    monkeypatch.setattr(editor_frame.messagebox, 'showwarning',
                        lambda title, message, **kwargs: warnings.append(message))
    original = tab.controller.auto_layout
    monkeypatch.setattr(tab.controller, 'auto_layout', lambda **kwargs: None)
    tab.add_device()
    button(window(frame, 'Add Device'), '710 Splitter').invoke()
    button(window(frame, 'Add splitter'), 'Add splitter').invoke()
    assert len(warnings) == 1
    assert '710-LX500-3' in warnings[0]
    assert 'make room' in warnings[0].lower()
    retry = next(w for w in descendants(tab.validation_frame)
                 if isinstance(w, ctk.CTkButton) and 'retry automatic placement' in w.cget('text'))
    monkeypatch.setattr(tab.controller, 'auto_layout', original)
    retry.invoke()
    assert '710-LX500-3' not in tab.controller.document.unplaced


def test_edit_device_bus_updates_id_and_preserves_riser_position(editor):
    frame, _ = editor
    frame.tabs.set('RISER')
    frame._edit_riser_hardware('710-LX500-2')
    win = window(frame, 'Edit 710-LX500-2')
    before = copy.deepcopy(frame.riser_tab.controller.document.elements['device:710-LX500-2'])
    bus_entry = next(w for w in descendants(win)
                     if isinstance(w, ctk.CTkEntry) and w.get() == '500')
    bus_entry.delete(0, 'end')
    bus_entry.insert(0, '600')
    button(win, 'Done').invoke()
    assert any(s.id == '710-LX600-2' for s in frame.session.design.splitters)
    assert not win.winfo_exists()  # ID edits use the shared structure refresh.
    after = frame.riser_tab.controller.document.elements['device:710-LX600-2']
    before.id = after.id
    before.ref = after.ref
    assert before == after
    assert not frame.session.topology_confirmed


def test_rejected_bus_resets_before_alert_and_ignores_reentrant_commit(editor, monkeypatch):
    from parse_dmp_worksheet import Splitter
    frame, _ = editor
    frame.session.design.splitters.append(Splitter('710-LX600-2', 'LX'))
    frame._edit_riser_hardware('710-LX500-2')
    win = window(frame, 'Edit 710-LX500-2')
    bus = next(w for w in descendants(win) if isinstance(w, ctk.CTkEntry) and w.get() == '500')
    card = bus.master.master
    warnings = []

    def warning(*args, **kwargs):
        warnings.append(args)
        assert bus.get() == '500'
        assert kwargs['parent'] == win
        card.commit_pending()  # A native alert can deliver another FocusOut.

    monkeypatch.setattr('editor_tabs.messagebox.showwarning', warning)
    bus.delete(0, 'end')
    bus.insert(0, '600')
    card.commit_pending()
    assert len(warnings) == 1
    assert bus.get() == '500'
    assert win.winfo_exists()


def test_edit_id_waits_for_done_while_bus_and_number_are_changed(editor, monkeypatch):
    from parse_dmp_worksheet import Splitter
    frame, _ = editor
    frame.session.design.splitters.append(Splitter('710-LX600-2', 'LX'))
    frame._edit_riser_hardware('710-LX500-2')
    win = window(frame, 'Edit 710-LX500-2')
    entries = [w for w in descendants(win) if isinstance(w, ctk.CTkEntry)]
    bus = next(w for w in entries if w.get() == '500')
    number = next(w for w in entries if w.get() == '2')
    warnings = []
    monkeypatch.setattr('editor_tabs.messagebox.showwarning', lambda *a, **k: warnings.append(a))
    bus.delete(0, 'end')
    bus.insert(0, '600')
    assert 'commit_id' not in bus._entry.bind('<FocusOut>')
    bus._entry.event_generate('<FocusOut>')
    frame.root.update()
    assert warnings == []
    assert bus.get() == '600'
    assert any(s.id == '710-LX500-2' for s in frame.session.design.splitters)
    number.delete(0, 'end')
    number.insert(0, '3')
    button(win, 'Done').invoke()
    assert warnings == []
    assert any(s.id == '710-LX600-3' for s in frame.session.design.splitters)


def test_rsp_number_edit_from_power_tab_preserves_addresses(editor):
    frame, _ = editor
    frame.tabs.set('RSP/POWER')
    rsp = frame.session.design.rsps[0]
    from parse_dmp_worksheet import PowerSupply
    frame.session.design.power_supplies.append(PowerSupply(rsp.number, rsp.location))
    addresses = list(rsp.zones)
    entry = next(w for w in descendants(frame.power_tab)
                 if isinstance(w, ctk.CTkEntry) and w.get() == str(rsp.number))
    entry.delete(0, 'end')
    entry.insert(0, '7')
    card = next(w for w in descendants(frame.power_tab) if hasattr(w, 'commit_pending'))
    assert card.commit_pending() is True
    frame.update()
    assert rsp.number == 7
    assert rsp.zones == addresses
    assert any(ps.number == 7 for ps in frame.session.design.power_supplies)
    assert not frame.session.topology_confirmed


def test_rsp_dialog_done_commits_number_without_focus_change(editor):
    frame, _ = editor
    frame.tabs.set('RISER')
    rsp = frame.session.design.rsps[0]
    old_number = rsp.number
    frame._edit_riser_hardware(f'RSP-{old_number}')
    win = window(frame, f'Edit RSP-{old_number}')
    entry = next(w for w in descendants(win)
                 if isinstance(w, ctk.CTkEntry) and w.get() == str(old_number))
    entry.delete(0, 'end')
    entry.insert(0, '7')
    button(win, 'Done').invoke()
    assert rsp.number == 7
    assert f'device:RSP-{old_number}' not in frame.riser_tab.controller.document.elements
    assert 'device:RSP-7' in frame.riser_tab.controller.document.elements
    assert frame.tabs.get() == 'RISER'


def test_rsp_dialog_rejects_duplicate_and_keeps_editor_open(editor, monkeypatch):
    frame, _ = editor
    rsp = frame.session.design.rsps[0]
    duplicate = frame.session.design.rsps[1].number
    frame._edit_riser_hardware(f'RSP-{rsp.number}')
    win = window(frame, f'Edit RSP-{rsp.number}')
    entry = next(w for w in descendants(win)
                 if isinstance(w, ctk.CTkEntry) and w.get() == str(rsp.number))
    warnings = []
    monkeypatch.setattr('editor_tabs.messagebox.showwarning', lambda *a, **k: warnings.append(a))
    entry.delete(0, 'end')
    entry.insert(0, str(duplicate))
    button(win, 'Done').invoke()
    assert len(warnings) == 1
    assert entry.get() == str(rsp.number)
    assert win.winfo_exists()


def test_rsp_numbers_wait_for_save_and_allow_swap(editor, monkeypatch):
    frame, _ = editor
    first, second = frame.session.design.rsps[:2]
    old_first, old_second = first.number, second.number
    warnings = []
    monkeypatch.setattr('editor_tabs.messagebox.showwarning', lambda *a, **k: warnings.append(a))
    first_var = frame.power_tab._number_vars[old_first]
    second_var = frame.power_tab._number_vars[old_second]
    first_var.set(str(old_second))
    frame.update()
    assert warnings == []
    assert first.number == old_first
    second_var.set(str(old_first))
    assert frame.save() is True
    assert warnings == []
    assert first.number == old_second and second.number == old_first
    from session import load_session
    restored = load_session(frame.session.path).design
    assert next(r for r in restored.rsps if r.location == first.location).number == old_second


def test_rsp_save_warning_preserves_pending_edits(editor, monkeypatch):
    frame, _ = editor
    first, second = frame.session.design.rsps[:2]
    warnings = []
    monkeypatch.setattr('editor_tabs.messagebox.showwarning', lambda *a, **k: warnings.append(a))
    variable = frame.power_tab._number_vars[first.number]
    variable.set(str(second.number))
    frame.update()
    assert warnings == []
    assert frame.save() is False
    assert len(warnings) == 1
    assert variable.get() == str(second.number)
    assert first.number != second.number
