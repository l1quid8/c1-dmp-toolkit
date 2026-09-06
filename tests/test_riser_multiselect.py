"""Multi-selection moves shared equipment once and remains one undoable edit."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from riser_editor import RiserEditorController
from riser_model import RiserAnnotation
from riser_scene import layout_riser
from test_riser_scene import branched_design
from test_release_editor_integration import editor, button
from test_riser_app_integration import _event_for_world


def test_group_move_preserves_internal_routes_and_undo():
    design = branched_design()
    c = RiserEditorController(design, layout_riser(design))
    before = copy.deepcopy(c.document)
    connections = copy.deepcopy(design.connections)
    selection = [('element', key) for key in c.document.elements]
    c.move_selection(selection, 36, 18)
    for key, old in before.elements.items():
        assert (c.document.elements[key].x, c.document.elements[key].y) == (old.x + 36, old.y + 18)
    for key, old in before.routes.items():
        assert c.document.routes[key].points == [(x + 36, y + 18) for x, y in old.points]
    assert design.connections == connections
    after = copy.deepcopy(c.document)
    assert c.undo()
    assert c.document == before
    assert not c.can_undo
    assert c.redo()
    assert c.document == after


def test_select_all_skips_hidden_layers_and_escape_clears(editor):
    frame, _ = editor
    tab = frame.riser_tab
    tab.layer_visibility['Cables'] = False
    tab.redraw()
    before = copy.deepcopy(tab.controller.document)
    button(tab, 'Select All').invoke()
    assert len(tab.selection) > 1
    assert all(kind not in {'route', 'label'} for kind, _ in tab.selection)
    tab.clear_selection()
    assert not tab.selection
    assert tab.controller.document == before
    assert not tab.controller.can_undo


def test_modifier_toggle_and_group_drag(editor):
    frame, _ = editor
    tab = frame.riser_tab
    notes = [RiserAnnotation('multi-a', 'rectangle', [(100, 100), (140, 140)]),
             RiserAnnotation('multi-b', 'rectangle', [(200, 100), (240, 140)])]
    tab.controller.document.annotations.extend(notes)
    tab.redraw()
    tab.zoom = 1
    tab.redraw()
    tab.snap_enabled = False
    def click(point, modifier=False):
        event = _event_for_world(tab, point)
        event.state = 8 if tab.tk.call('tk', 'windowingsystem') == 'aqua' else 4
        if not modifier:
            event.state = 0
        tab._on_press(event)
        tab._on_release(event)
    click((120, 120))
    click((220, 120), True)
    assert set(tab.selection) == {('annotation', 'multi-a'), ('annotation', 'multi-b')}
    click((220, 120), True)
    assert tab.selection == [('annotation', 'multi-a')]
    click((220, 120), True)
    before = copy.deepcopy(tab.controller.document)
    tab._on_press(_event_for_world(tab, (120, 120)))
    tab._on_drag(_event_for_world(tab, (156, 138)))
    tab._on_release(_event_for_world(tab, (156, 138)))
    for annotation in tab.controller.document.annotations[-2:]:
        old = next(a for a in before.annotations if a.id == annotation.id)
        assert annotation.points == [(x + 36, y + 18) for x, y in old.points]
    assert tab.controller.undo()
    assert tab.controller.document == before


def test_marquee_selects_contained_items_without_edit(editor):
    frame, _ = editor
    tab = frame.riser_tab
    tab.controller.document.annotations.extend([
        RiserAnnotation('box-a', 'rectangle', [(-300, -300), (-260, -260)]),
        RiserAnnotation('box-b', 'rectangle', [(-200, -300), (-160, -260)])])
    tab.redraw()
    before = copy.deepcopy(tab.controller.document)
    tab._on_press(_event_for_world(tab, (-330, -330)))
    tab._on_release(_event_for_world(tab, (-130, -230)))
    assert set(tab.selection) == {('annotation', 'box-a'), ('annotation', 'box-b')}
    assert tab.controller.document == before


def test_group_drag_updates_in_place_and_escape_restores(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    tab.controller.document.annotations.extend([
        RiserAnnotation('cancel-a', 'rectangle', [(100, 100), (140, 140)]),
        RiserAnnotation('cancel-b', 'rectangle', [(200, 100), (240, 140)])])
    tab.selection = [('annotation', 'cancel-a'), ('annotation', 'cancel-b')]
    tab.redraw()
    before = copy.deepcopy(tab.controller.document)
    stationary = {item: tab.canvas.coords(item) for item in tab.canvas.find_withtag('page')}
    tab._on_press(_event_for_world(tab, (120, 120)))
    with monkeypatch.context() as patch:
        patch.setattr(tab, 'redraw', lambda **kw: (_ for _ in ()).throw(AssertionError('full redraw during group drag')))
        tab._on_drag(_event_for_world(tab, (156, 138)))
    assert {item: tab.canvas.coords(item) for item in stationary} == stationary
    tab.clear_selection()
    assert tab.controller.document == before
    assert not tab.controller.can_undo
    assert not tab.selection


def test_select_all_callouts_move_once_with_their_wires():
    from riser_scene import route_label_point
    design = branched_design()
    c = RiserEditorController(design, layout_riser(design))
    selection = [('element', key) for key in c.document.elements]
    selection += [('route', key) for key in c.document.routes]
    selection += [('label', key) for key in c.document.routes]
    before = copy.deepcopy(c.document)
    c.move_selection(selection, 36, 18)
    for key, old in before.routes.items():
        route = c.document.routes[key]
        old_anchor = route_label_point(old.points)
        anchor = route_label_point(route.points)
        assert (anchor[0] + route.label_offset[0], anchor[1] + route.label_offset[1]) == (
            old_anchor[0] + old.label_offset[0] + 36, old_anchor[1] + old.label_offset[1] + 18)


def test_moving_subset_keeps_external_wire_endpoints_attached():
    from riser_scene import port_point
    design = branched_design()
    c = RiserEditorController(design, layout_riser(design))
    edge = design.connections[0]
    c.move_selection([('element', f'device:{edge.source.device_id}'), ('route', edge.id)], 36, 18)
    route = c.document.routes[edge.id]
    assert route.points[0] == port_point(c.document.elements[f'device:{edge.source.device_id}'], edge.source.port_id, output=True)
    assert route.points[-1] == port_point(c.document.elements[f'device:{edge.target.device_id}'], edge.target.port_id, output=False)


def test_keyboard_select_all_nudge_escape_and_text_focus(editor):
    frame, _ = editor
    tab = frame.riser_tab
    root = frame.winfo_toplevel()
    root.geometry('1400x900')
    frame.tabs.set('RISER')
    root.deiconify()
    root.update()
    tab.canvas.focus_force()
    root.update()
    shortcut = '<Command-a>' if tab.tk.call('tk', 'windowingsystem') == 'aqua' else '<Control-a>'
    tab.canvas.event_generate(shortcut)
    root.update()
    assert len(tab.selection) > 1
    before = copy.deepcopy(tab.controller.document)
    tab.canvas.event_generate('<Right>')
    root.update()
    assert tab.controller.document != before
    assert tab.controller.undo()
    assert tab.controller.document == before
    assert not tab.controller.can_undo
    tab.canvas.event_generate('<Escape>')
    root.update()
    assert not tab.selection
    entry = next(iter(tab._title_entries.values()))
    entry.focus_force()
    root.update()
    entry._entry.event_generate(shortcut)
    root.update()
    assert not tab.selection
    assert entry._entry.selection_present()


def test_preview_does_not_allow_select_all_or_group_movement(editor):
    frame, _ = editor
    tab = frame.riser_tab
    tab.select_all()
    tab.relayout()
    before = copy.deepcopy(tab.controller.document)
    tab.select_all()
    tab.nudge(36, 18)
    assert not tab.selection
    assert tab.controller.document == before


def test_additive_marquee_preserves_existing_selection(editor):
    frame, _ = editor
    tab = frame.riser_tab
    tab.controller.document.annotations.extend([
        RiserAnnotation('keep', 'rectangle', [(-400, -300), (-360, -260)]),
        RiserAnnotation('add', 'rectangle', [(-200, -300), (-160, -260)])])
    tab.selected = ('annotation', 'keep')
    tab.redraw()
    event = _event_for_world(tab, (-230, -330))
    event.state = 8 if tab.tk.call('tk', 'windowingsystem') == 'aqua' else 4
    tab._on_press(event)
    tab._on_release(_event_for_world(tab, (-130, -230)))
    assert set(tab.selection) == {('annotation', 'keep'), ('annotation', 'add')}


def test_rapid_native_modifier_clicks_toggle_without_opening_editor(editor, monkeypatch):
    from riser_editor import simpledialog
    frame, _ = editor
    tab = frame.riser_tab
    root = frame.winfo_toplevel()
    root.geometry('1400x900')
    frame.tabs.set('RISER')
    root.deiconify()
    root.update()
    tab.controller.document.annotations.append(
        RiserAnnotation('rapid', 'text', [(100, 100)], text='Toggle me'))
    tab.redraw()
    box = tab.canvas.bbox('annotation|rapid')
    x = round((box[0]+box[2])/2 - tab.canvas.canvasx(0))
    y = round((box[1]+box[3])/2 - tab.canvas.canvasy(0))
    dialogs = []
    monkeypatch.setattr(simpledialog, 'askstring', lambda *a, **kw: dialogs.append(a))
    modifier = 8 if tab.tk.call('tk', 'windowingsystem') == 'aqua' else 4
    for timestamp, expected in [(10000, [('annotation', 'rapid')]), (10100, [])]:
        tab.canvas.event_generate('<ButtonPress-1>', x=x, y=y, state=modifier, time=timestamp)
        tab.canvas.event_generate('<ButtonRelease-1>', x=x, y=y, state=modifier, time=timestamp+10)
        root.update()
        assert tab.selection == expected
    assert not dialogs
