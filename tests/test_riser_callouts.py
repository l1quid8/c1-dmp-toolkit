"""Callout editing must never mutate the electrical connection or route."""
import copy
import json
import xml.etree.ElementTree as ET

import fitz
import pytest

from test_riser_scene import branched_design
from test_release_editor_integration import editor, button
from test_riser_app_integration import _event_for_world
from riser_editor import RiserEditorController
from riser_render import render_svg, render_pdf
from riser_scene import layout_riser, route_label_point, _route_label_box
from session import Session, save_session, load_session


def setup_callout():
    design = branched_design()
    controller = RiserEditorController(design, layout_riser(design))
    edge = design.connections[0]
    edge.custom_label = 'UNIQUE-CALLOUT'
    return design, controller, edge.id


def test_label_changes_roundtrip_undo_and_preserve_wiring(tmp_path):
    design, c, key = setup_callout()
    before = copy.deepcopy(c.document.routes[key])
    connections = copy.deepcopy(design.connections)
    c.update_label(key, offset=(90, -36), hidden=True)
    assert c.document.routes[key].label_offset == (90, -36)
    assert c.document.routes[key].label_hidden
    assert c.document.routes[key].label_manual
    assert c.document.routes[key].points == before.points
    assert design.connections == connections
    save_session(Session(design=design), tmp_path / 'labels.dmps')
    loaded = load_session(tmp_path / 'labels.dmps').design
    assert loaded.riser_document.routes[key] == c.document.routes[key]
    assert c.undo()
    assert c.document.routes[key] == before
    assert c.redo()
    assert c.document.routes[key].label_hidden
    c.update_label(key, hidden=False, reset=True)
    assert c.document.routes[key].label_offset == (0, 0)
    assert not c.document.routes[key].label_hidden


def test_rerouting_preserves_manual_callout_state():
    d, c, key = setup_callout()
    c.update_label(key, offset=(90, -36), hidden=True)
    c._reroute(key)
    assert c.document.routes[key].label_offset == (90, -36)
    assert c.document.routes[key].label_hidden
    assert c.document.routes[key].label_manual


def test_hidden_callout_is_absent_from_both_exports_not_wire(tmp_path):
    d, c, key = setup_callout()
    c.update_label(key, hidden=True)
    assert _route_label_box(d.connections[0], c.document.routes[key]) is None
    svg = tmp_path / 'hidden.svg'
    render_svg(d, c.document, svg)
    root = ET.parse(svg).getroot()
    assert 'UNIQUE-CALLOUT' not in ''.join(root.itertext())
    assert root.find(f'.//*[@id="route-{key}"]') is not None
    for profile in ('24x36', '11x17'):
        pdf = tmp_path / f'{profile}.pdf'
        render_pdf(d, c.document, pdf, profile=profile)
        with fitz.open(pdf) as rendered:
            assert 'UNIQUE-CALLOUT' not in rendered[0].get_text()
            assert rendered[0].get_drawings()


def test_callout_drag_is_incremental_and_delete_only_hides(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    key = frame.session.design.connections[0].id
    route = tab.controller.document.routes[key]
    route.label_offset = (0, -180)  # isolated text for unambiguous pointer hit
    tab.redraw()
    origin = route_label_point(route.points)
    start = (origin[0], origin[1] - 185)
    before = copy.deepcopy(frame.session.design.connections)
    points = copy.deepcopy(route.points)
    tab._on_press(_event_for_world(tab, start))
    assert tab.selected == ('label', key)
    redraw = tab.redraw
    monkeypatch.setattr(tab, 'redraw', lambda *a, **k: pytest.fail('full redraw during label drag'))
    tab._on_drag(_event_for_world(tab, (start[0] + 90, start[1] + 36)))
    monkeypatch.setattr(tab, 'redraw', redraw)
    tab._on_release(_event_for_world(tab, (start[0] + 90, start[1] + 36)))
    assert route.label_offset == (90, -144)
    assert route.points == points
    assert frame.session.design.connections == before
    tab.delete_selected()
    assert route.label_hidden
    assert frame.session.design.connections == before
    assert not tab.canvas.find_withtag(f'label|{key}')
    tab.undo()
    assert not tab.controller.document.routes[key].label_hidden
    tab.undo()
    assert tab.controller.document.routes[key].label_offset == (0, -180)


def test_label_escape_and_nudge(editor):
    frame, _ = editor
    tab = frame.riser_tab
    key = frame.session.design.connections[0].id
    route = tab.controller.document.routes[key]
    offset = route.label_offset
    tab.selected = ('label', key)
    tab._gesture = ('move-label', key, (0, 0), offset)
    tab._on_drag(_event_for_world(tab, (90, 36)))
    tab.cancel()
    assert route.label_offset == offset
    tab.nudge(18, 0)
    assert route.label_offset == (offset[0] + 18, offset[1])
    tab.undo()
    assert tab.controller.document.routes[key].label_offset == offset


def test_schema_five_labels_default_visible_without_losing_position(tmp_path):
    d, c, key = setup_callout()
    c.document.routes[key].label_offset = (72, -28)
    path = tmp_path / 'old.dmps'
    save_session(Session(design=d), path)
    raw = json.loads(path.read_text())
    raw['schema_version'] = 5
    route = raw['design']['riser_document']['routes'][key]
    route.pop('label_hidden', None)
    route.pop('label_manual', None)
    path.write_text(json.dumps(raw))
    loaded = load_session(path).design.riser_document.routes[key]
    assert loaded.label_offset == (72, -28)
    assert not loaded.label_hidden


def test_reset_restore_and_preview_guards(editor):
    frame, _ = editor
    tab = frame.riser_tab
    key = frame.session.design.connections[0].id
    tab.controller.update_label(key, hidden=True, offset=(90, -36))
    tab.selected = ('route', key)
    tab.redraw()
    button(tab.properties, 'Restore Label').invoke()
    assert not tab.controller.document.routes[key].label_hidden
    button(tab.properties, 'Reset Label Position').invoke()
    assert tab.controller.document.routes[key].label_offset == (0, 0)
    tab.relayout()
    before = copy.deepcopy(tab.controller.document)
    tab._set_label(key, hidden=True)
    assert tab.controller.document == before


def test_callout_position_controls_svg_and_searchable_pdf(tmp_path):
    d, c, key = setup_callout()
    route = c.document.routes[key]
    anchor = route_label_point(route.points)
    c.update_label(key, offset=(600-anchor[0], 120-anchor[1]))
    path = tmp_path / 'moved.svg'
    render_svg(d, c.document, path)
    root = ET.parse(path).getroot()
    label = next(node for node in root.iter() if node.text == 'UNIQUE-CALLOUT')
    assert float(label.attrib['x']) == 600
    assert float(label.attrib['y']) == 120
    for profile, scale in [('24x36', 1), ('11x17', 11/24)]:
        pdf = tmp_path / f'moved-{profile}.pdf'
        render_pdf(d, c.document, pdf, profile=profile)
        with fitz.open(pdf) as rendered:
            assert rendered[0].search_for('UNIQUE-CALLOUT')


def test_layout_preview_and_apply_keep_hidden_callouts():
    d, c, key = setup_callout()
    c.update_label(key, hidden=True)
    preview = c.preview_layout()
    assert preview.routes[key].label_hidden
    c.apply_layout(preview)
    assert c.document.routes[key].label_hidden


def test_undo_cancels_pending_callout_drag(editor):
    frame, _ = editor
    tab = frame.riser_tab
    key = frame.session.design.connections[0].id
    route = tab.controller.document.routes[key]
    before = route.label_offset
    tab.selected = ('label', key)
    tab.nudge(18, 0)
    tab._gesture = ('move-label', key, (0, 0), route.label_offset)
    tab.undo()
    tab.cancel()
    assert tab.controller.document.routes[key].label_offset == before


def test_off_page_callout_warns_but_hidden_callout_does_not():
    from riser_scene import validate_riser
    d, c, key = setup_callout()
    c.update_label(key, offset=(10000, 10000))
    assert any(i.code == 'scene.off_page' and i.ref == key for i in validate_riser(d, c.document))
    c.update_label(key, hidden=True)
    assert not any(i.code == 'scene.off_page' and i.ref == key for i in validate_riser(d, c.document))


def test_legacy_repair_does_not_discard_manual_callout_position():
    from riser_scene import repair_generated_scene
    d, c, key = setup_callout()
    c.update_label(key, offset=(90, -36))
    owner = c.document.elements[c.document.elements['device:MSP'].location_id]
    owner.width = 1  # an old generated frame needing repair
    assert not repair_generated_scene(d, c.document)
    assert c.document.routes[key].label_offset == (90, -36)


def test_delete_cancels_pending_label_motion_before_hiding(editor):
    frame, _ = editor
    tab = frame.riser_tab
    key = frame.session.design.connections[0].id
    route = tab.controller.document.routes[key]
    offset = route.label_offset
    tab.selected = ('label', key)
    tab._gesture = ('move-label', key, (0, 0), offset)
    tab._on_drag(_event_for_world(tab, (90, 36)))
    tab.delete_selected()
    assert route.label_offset == offset
    assert route.label_hidden
    tab.undo()
    assert not tab.controller.document.routes[key].label_hidden
    assert tab.controller.document.routes[key].label_offset == offset


def test_undo_without_history_restores_preview_canvas(editor):
    frame, _ = editor
    tab = frame.riser_tab
    key = frame.session.design.connections[0].id
    tab.redraw()
    box = tab.canvas.bbox(f'label|{key}')
    route = tab.controller.document.routes[key]
    tab.selected = ('label', key)
    tab._gesture = ('move-label', key, (0, 0), route.label_offset)
    tab._on_drag(_event_for_world(tab, (90, 36)))
    tab.undo()
    assert tab.canvas.bbox(f'label|{key}') == box
