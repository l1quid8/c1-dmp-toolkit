"""Exercise actual editor actions, including their unsupported-selection feedback."""
import copy
from tkinter import messagebox

import pytest

from test_riser_drag_performance import tab
from test_riser_app_integration import _event_for_world
from riser_model import RiserAnnotation
from test_release_editor_integration import editor
from tkinter import simpledialog
from types import SimpleNamespace


@pytest.mark.parametrize('ref', ['device:RSP-3', 'device:MSP', 'location:WING 1 ROOM 3'])
def test_delete_hardware_explains_domain_removal_without_altering_project(tab, monkeypatch, ref):
    tab.selected = ('element', ref)
    before = copy.deepcopy(tab.controller.document)
    wiring = copy.deepcopy(tab.design.connections)
    messages = []
    monkeypatch.setattr(messagebox, 'showinfo', lambda title, message, **kw: messages.append(message))
    tab.delete_selected()
    assert messages, 'Delete silently ignored a hardware/location selection'
    assert tab.controller.document == before
    assert tab.design.connections == wiring
    assert not tab.controller.can_undo


def test_delete_markup_via_toolbar_is_undoable(tab):
    note = RiserAnnotation('delete-me', 'rectangle', [(50, 50), (100, 100)])
    tab.controller.add_annotation(note)
    tab.selected = ('annotation', note.id)
    button = next(w for row in tab.toolbar.winfo_children() for w in row.winfo_children()
                  if hasattr(w, 'invoke') and w.cget('text') == 'Delete')
    button.invoke()
    assert not tab.controller.document.annotations
    tab.undo()
    assert tab.controller.document.annotations == [note]
    tab.redo()
    assert not tab.controller.document.annotations


@pytest.mark.parametrize('confirm', [False, True])
def test_delete_cable_confirmation_preserves_or_disconnects_shared_topology(tab, monkeypatch, confirm):
    edge = next(e for e in tab.design.connections if e.source.port_id == 'OUT1')
    edge_id = edge.id
    before = copy.deepcopy(tab.design.connections)
    tab.selected = ('route', edge_id)
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **kw: confirm)
    tab.delete_selected()
    assert (edge_id in tab.controller.document.routes) is not confirm
    if confirm:
        splitter = next(s for s in tab.design.splitters if s.id == edge.source.device_id)
        assert splitter.outputs[0] == 'Spare'
        tab.undo()
    assert tab.design.connections == before


@pytest.mark.parametrize('ref', ['device:710-LX500-2', 'device:KEYPAD-2', 'device:RSP-2'])
@pytest.mark.parametrize('confirm', [False, True])
def test_delete_device_uses_domain_confirmation_and_cascade(editor, monkeypatch, ref, confirm):
    frame, _ = editor
    tab = frame.riser_tab
    before = copy.deepcopy(tab.design)
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **kw: confirm)
    # The post-removal report is another modal UI, not part of mutation.
    monkeypatch.setattr(frame, '_report_structure_change', lambda changes: None)
    tab.selected = ('element', ref)
    tab.delete_selected()
    if not confirm:
        assert tab.design == before
    else:
        hardware = {s.id for s in tab.design.splitters} | {f'RSP-{r.number}' for r in tab.design.rsps} | {f'KEYPAD-{k.number}' for k in tab.design.keypads}
        assert ref.removeprefix('device:') not in hardware
        assert not any(ref == f'device:{e.source.device_id}' or ref == f'device:{e.target.device_id}'
                       for e in tab.design.connections)
        assert not frame.session.topology_confirmed


@pytest.mark.parametrize('tool', ['Rectangle', 'Ellipse', 'Arrow', 'Text', 'Polyline'])
def test_markup_tools_create_duplicate_arrange_delete_and_restore(tab, monkeypatch, tool):
    monkeypatch.setattr(simpledialog, 'askstring', lambda *a, **kw: 'FIELD NOTE')
    tab._tool_buttons[tool].invoke()
    tab._on_press(_event_for_world(tab, (54, 54)))
    if tool == 'Polyline':
        tab._on_press(_event_for_world(tab, (144, 54)))
        tab._on_press(_event_for_world(tab, (144, 144)))
        tab._finish_polyline()
    elif tool != 'Text':
        tab._on_release(_event_for_world(tab, (144, 144)))
    note = copy.deepcopy(tab.controller.document.annotations[0])
    assert note.kind == tool.lower()
    tab.duplicate_selected()
    duplicate = tab.controller.document.annotations[-1]
    assert duplicate.id != note.id
    tab._arrange(duplicate.id, 'back')
    assert tab.controller.document.z_order[0] == duplicate.id
    tab._set_annotation(duplicate.id, stroke='#123456')
    assert duplicate.stroke == '#123456'
    tab.delete_selected()
    assert tab.controller.document.annotations == [note]
    tab.undo()
    assert len(tab.controller.document.annotations) == 2


def test_title_edit_undo_redo_updates_visible_fields(tab):
    old = tab.controller.document.title_block.drawn_by
    tab._title_vars['drawn_by'].set('TC')
    tab._save_title('drawn_by', tab._title_vars['drawn_by'])
    assert tab.controller.document.title_block.drawn_by == 'TC'
    tab.undo()
    assert tab._title_vars['drawn_by'].get() == old
    tab.redo()
    assert tab._title_vars['drawn_by'].get() == 'TC'


@pytest.mark.parametrize('name,tag', [('Locations','element|location:WING 1 ROOM 3'),
    ('Devices','element|device:RSP-3'), ('Title block','titleblock')])
def test_layer_toggles_remove_and_restore_canvas_objects(tab, name, tag):
    assert tab.canvas.find_withtag(tag)
    tab._toggle_layer(name)
    assert not tab.canvas.find_withtag(tag)
    tab._toggle_layer(name)
    assert tab.canvas.find_withtag(tag)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_markup_numbers_cannot_poison_scene(tab, value):
    tab.controller.add_annotation(RiserAnnotation('note', 'text', [(54,54)], text='NOTE'))
    before = copy.deepcopy(tab.controller.document)
    with pytest.raises(ValueError):
        tab.controller.update_annotation('note', font_size=value)
    assert tab.controller.document == before


def test_duplicate_hardware_explains_why_instead_of_silently_doing_nothing(tab, monkeypatch):
    tab.selected = ('element', 'device:RSP-3')
    before = copy.deepcopy(tab.design)
    messages = []
    monkeypatch.setattr(messagebox, 'showinfo', lambda title, message, **kw: messages.append(message))
    tab.duplicate_selected()
    assert messages
    assert tab.design == before


def test_zoom_fit_pan_grid_and_snap_do_not_change_saved_geometry(tab):
    before = copy.deepcopy(tab.controller.document)
    tab.set_zoom(.8)
    assert tab.zoom == .8
    tab._pan_start(SimpleNamespace(x=200,y=200))
    initial_view = tab.canvas.yview()
    tab._pan_move(SimpleNamespace(x=150,y=100))
    assert tab.canvas.yview() != initial_view
    tab.fit_to_view()
    assert tab.zoom < .8
    tab._grid_switch.deselect()
    tab._toggle_grid()
    assert not tab.canvas.find_withtag('grid')
    tab._snap_switch.deselect()
    tab._toggle_snap()
    assert tab._snap((55,55)) == (55,55)
    tab._snap_switch.select()
    tab._toggle_snap()
    assert tab._snap((55,55)) == (54,54)
    assert tab.controller.document == before


def test_double_click_text_edit_and_undo(tab, monkeypatch):
    note = RiserAnnotation('editable', 'text', [(54,100)], text='OLD')
    tab.controller.add_annotation(note)
    tab.redraw()
    item = tab.canvas.find_withtag('annotation|editable')[0]
    x1,y1,x2,y2 = tab.canvas.bbox(item)
    event = SimpleNamespace(x=(x1+x2)/2-tab.canvas.canvasx(0),
                            y=(y1+y2)/2-tab.canvas.canvasy(0))
    monkeypatch.setattr(simpledialog, 'askstring', lambda *a, **kw: 'NEW')
    tab._on_double_click(event)
    assert tab.controller.document.annotations[0].text == 'NEW'
    tab.undo()
    assert tab.controller.document.annotations[0].text == 'OLD'
