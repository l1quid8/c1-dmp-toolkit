"""User workflows across the editor, project persistence and deliverables."""

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import font as tkfont

import fitz
import pytest

from test_release_editor_integration import editor, descendants
from test_riser_scene import branched_design, three_rsp_chain_design
from test_riser_app_integration import _event_for_world
from parse_dmp_worksheet import DMPDesign, RSP, Splitter, SiteInfo, parse_dmp_worksheet
from generate_dmp_ws import write_dmp_xlsx
from riser_model import DevicePortRef, RiserAnnotation
from riser_scene import layout_riser, port_point
from riser_editor import RiserEditorController
from riser_render import generate_riser_bundle, render_svg
from session import Session, load_session, save_session, SessionLoadError
from topology_service import connect, project_legacy_topology

ROOT = Path(__file__).resolve().parents[1]


def seven_rsp_design():
    design = DMPDesign(site_info=SiteInfo(school_name='SEVEN RSP DEMO', school_code='9999',
                                         xr550_location='MDF', address_line1='1 DEMO ST'),
        splitters=[Splitter(f'710-LX500-{n}', 'LX', f'WING {1 if n < 4 else 2} ROOM {n}',
                            outputs=['Spare'] * 3) for n in range(1, 8)],
        rsps=[RSP(n, f'WING {1 if n < 4 else 2} ROOM {n}', [501 + (n-1)*8]) for n in range(1, 8)])
    connect(design, DevicePortRef('MSP', 'LX500'), DevicePortRef('710-LX500-1', 'IN'))
    for n in range(1, 8):
        connect(design, DevicePortRef(f'710-LX500-{n}', 'OUT1'), DevicePortRef(f'RSP-{n}', 'IN'))
        if n < 7:
            connect(design, DevicePortRef(f'710-LX500-{n}', 'OUT2'), DevicePortRef(f'710-LX500-{n+1}', 'IN'))
    return design


@pytest.mark.parametrize('factory', [branched_design, three_rsp_chain_design, seven_rsp_design])
def test_assign_rewire_move_undo_save_reopen_and_export(factory, tmp_path):
    design = factory()
    assigned_location = design.rsps[-1].location
    design.rsps[-1].location = 'UNKNOWN'
    doc = layout_riser(design)
    controller = RiserEditorController(design, doc)
    controller.add_annotation(RiserAnnotation('field-note', 'text', [(150, 150)], text='FIELD CHECK'))
    ref = f'RSP-{design.rsps[-1].number}'
    device = doc.elements[f'device:{ref}']
    original_xy = (device.x, device.y)
    design.rsps[-1].location = assigned_location
    controller.sync_external()
    assert (device.x, device.y) == original_xy
    assert controller.can_undo
    assert doc.elements[device.location_id].ref == assigned_location

    edge = next(e for e in design.connections if e.target.device_id == ref)
    edge_id = edge.id
    controller.update_connection(edge_id, status='existing', quantity=2, custom_label='FIELD CABLE')
    old_source = edge.source
    controller.reconnect(edge_id, source=DevicePortRef(old_source.device_id, 'OUT3'))
    assert controller.undo()
    assert next(e for e in design.connections if e.id == edge_id).source == old_source
    assert controller.redo()
    owner = device.location_id
    unrelated = {e.id: (e.x, e.y) for e in doc.elements.values()
                 if e.kind == 'device' and e.location_id != owner}
    controller.move_element(owner, 18, 18)
    assert all((doc.elements[key].x, doc.elements[key].y) == xy for key, xy in unrelated.items())
    path = tmp_path / 'school.dmps'
    save_session(Session(design=design, path=path))
    restored = load_session(path)
    assert restored.design.connections == design.connections
    assert restored.design.riser_document == doc
    assert next(e for e in restored.design.connections if e.id == edge_id).custom_label == 'FIELD CABLE'
    route = doc.routes[edge_id]
    edge = next(e for e in design.connections if e.id == edge_id)
    assert route.points[0] == port_point(doc.elements[f'device:{edge.source.device_id}'], edge.source.port_id, output=True)
    assert route.points[-1] == port_point(doc.elements[f'device:{ref}'], 'IN', output=False)

    workbook = tmp_path / 'worksheet.xlsx'
    write_dmp_xlsx(restored.design, ROOT / 'DMP Installation Worksheet_template_blank.xlsx', workbook)
    reread = parse_dmp_worksheet(workbook)
    assert {s.id: s.outputs for s in reread.splitters} == {s.id: s.outputs for s in restored.design.splitters}
    outputs = generate_riser_bundle(restored.design, restored.design.riser_document, tmp_path)
    assert len(outputs) == 3
    for pdf in outputs[:2]:
        with fitz.open(pdf) as rendered:
            assert 'FIELD CABLE' in rendered[0].get_text()
            assert 'FIELD CHECK' in rendered[0].get_text()


def test_rapid_location_edits_coalesce_and_preserve_markup_undo(editor, monkeypatch):
    frame, _ = editor
    tab = frame.riser_tab
    tab.controller.add_annotation(RiserAnnotation('note', 'text', [(100, 100)], text='CHECK'))
    tab._changed()
    calls = []
    original = tab.refresh
    monkeypatch.setattr(tab, 'refresh', lambda: (calls.append('refresh'), original()))
    for value in ['N', 'NE', 'NEW', 'NEW ROOM']:
        frame.session.design.rsps[-1].location = value
        frame._on_design_edit()
    assert calls == []
    frame.flush_design_refresh()
    assert calls == ['refresh']
    assert tab.controller.can_undo
    tab.undo()
    assert not tab.controller.document.annotations
    assert frame.session.design.rsps[-1].location == 'NEW ROOM'
    device = tab.controller.document.elements['device:RSP-2']
    assert tab.controller.document.elements[device.location_id].ref == 'NEW ROOM'


def test_presentation_edits_do_not_rebuild_other_tabs(editor, monkeypatch):
    frame, _ = editor
    def unexpected():
        pytest.fail('Presentation edit rebuilt another tab')
    monkeypatch.setattr(frame.splitters_tab, 'refresh', unexpected)
    monkeypatch.setattr(frame.keypads_tab, 'refresh', unexpected)
    monkeypatch.setattr(frame.remotelink_tab, 'refresh', unexpected)
    frame.riser_tab.controller.add_annotation(RiserAnnotation('note', 'text', [(100, 100)], text='CHECK'))
    frame.riser_tab._changed()
    frame.riser_tab.undo()
    assert frame.session.topology_confirmed


def test_module_drag_preview_moves_members_and_escape_restores_all(editor):
    frame, _ = editor
    tab = frame.riser_tab
    doc = tab.controller.document
    owner = doc.elements['device:RSP-2'].location_id
    module = doc.elements[owner]
    before = copy.deepcopy(doc)
    start = (module.x + 12, module.y + 12)
    tab._on_press(_event_for_world(tab, start))
    assert tab.selected == ('element', owner)
    tab._on_drag(_event_for_world(tab, (start[0] + 36, start[1] + 36)))
    for key, element in before.elements.items():
        if element.kind == 'device':
            offset = 36 if element.location_id == owner else 0
            assert doc.elements[key].x == element.x + offset
    tab.cancel()
    assert doc == before
    assert not tab.controller.can_undo


def test_canvas_symbols_and_title_text_match_svg(editor, tmp_path):
    frame, _ = editor
    tab = frame.riser_tab
    tab.set_zoom(.75)
    design, doc = tab.design, tab.controller.document
    root = ET.parse(render_svg(design, doc, tmp_path / 'preview.svg')).getroot()
    ns = {'s': 'http://www.w3.org/2000/svg'}
    for ref in ['MSP', '710-LX500-1', 'RSP-1', 'KEYPAD-1']:
        group = root.find(f'.//s:g[@id="device:{ref}"]', ns)
        items = tab.canvas.find_withtag(f'element|device:{ref}')
        shape = next(item for item in items if tab.canvas.type(item) in ('rectangle', 'oval'))
        assert tab.canvas.type(shape) == ('oval' if group.find('s:ellipse', ns) is not None else 'rectangle')
        actual = {tab.canvas.itemcget(item, 'text') for item in items if tab.canvas.type(item) == 'text'}
        assert {text.text for text in group.findall('s:text', ns)} == actual
    canvas_text = [item for item in tab.canvas.find_withtag('titleblock') if tab.canvas.type(item) == 'text']
    svg_text = root.findall('.//s:g[@id="title-block"]/s:text', ns)
    assert len(canvas_text) == len(svg_text)
    for item, text in zip(canvas_text, svg_text):
        assert tab.canvas.itemcget(item, 'text') == (text.text or '')
        x, y = tab.canvas.coords(item)
        font = tkfont.nametofont(tab.canvas.itemcget(item, 'font'), root=tab.canvas)
        expected_x, expected_y = tab._xy((float(text.attrib['x']), float(text.attrib['y'])))
        assert x == pytest.approx(expected_x, abs=.02)
        assert y + font.metrics('ascent') == pytest.approx(expected_y, abs=.02)


def test_prior_project_migrates_ownership_and_older_reader_rejects_new_save(tmp_path, monkeypatch):
    import session as session_module
    design = branched_design()
    design.riser_document = layout_riser(design)
    path = save_session(Session(design, path=tmp_path / 'legacy.dmps'))
    raw = json.loads(path.read_text())
    raw['schema_version'] = 3
    for element in raw['design']['riser_document']['elements'].values():
        element.pop('location_id', None)
    path.write_text(json.dumps(raw))
    session = load_session(path)
    controller = RiserEditorController(session.design, session.design.riser_document)
    controller.sync_external()
    assert all(e.location_id for e in controller.document.elements.values() if e.kind == 'device')
    save_session(session)
    monkeypatch.setattr(session_module, 'SCHEMA_VERSION', 3)
    with pytest.raises(SessionLoadError, match='newer version'):
        load_session(path)
