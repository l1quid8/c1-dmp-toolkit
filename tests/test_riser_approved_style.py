"""Approved visual target at the scene, editor, persistence and export boundaries."""
import copy
import xml.etree.ElementTree as ET

import fitz
import pytest

from test_riser_scene import branched_design
from test_release_editor_integration import editor
from riser_editor import RiserEditorController
from riser_drawing import device_text
from riser_scene import layout_riser, sync_riser_document, validate_riser
from riser_render import render_svg, render_pdf
from session import Session, save_session, load_session


def approved_scene():
    design = branched_design()
    controller = RiserEditorController(design, layout_riser(design))
    return design, controller, controller.preview_layout()


def test_preview_uses_compact_710s_and_electrical_levels_without_changing_project():
    d = branched_design()
    old = layout_riser(d)
    snapshot = copy.deepcopy((d.connections, old))
    c = RiserEditorController(d, old)
    preview = c.preview_layout()
    assert preview.layout_version == 3
    assert (preview.elements['device:710-LX500-1'].width,
            preview.elements['device:710-LX500-1'].height) == (210, 135)
    assert preview.elements['device:MSP'].width == 440
    assert (d.connections, old) == snapshot
    assert not preview.show_location_frames
    assert preview.elements['device:KEYPAD-1'].x < preview.elements['device:MSP'].x
    assert preview.elements['device:710-KP-1'].x < preview.elements['device:710-LX500-1'].x
    for e in d.connections:
        if e.source.port_id != 'KP BUS':
            assert preview.elements['device:'+e.target.device_id].y > preview.elements['device:'+e.source.device_id].y


def test_compact_splitter_caption_remains_inside_and_readable():
    d, _, doc = approved_scene()
    element = doc.elements['device:710-LX500-2']
    runs = device_text(d, element, small=True)
    assert any(run.text == 'CLASSROOM 27' for run in runs)
    for run in runs:
        width = fitz.get_text_length(run.text, fontname='hebo' if run.bold else 'helv', fontsize=run.size)
        left = run.x-width/2 if run.anchor=='middle' else run.x
        assert element.x <= left and left+width <= element.x+element.width
        assert element.y <= run.y-run.size and run.y+run.size*.25 <= element.y+element.height
        assert run.size*11/24 >= 7


def test_svg_contains_keypad_face_chamfered_splitters_and_structured_sheet(tmp_path):
    d, _, doc = approved_scene()
    root = ET.parse(render_svg(d, doc, tmp_path/'approved.svg')).getroot()
    ns = {'s':'http://www.w3.org/2000/svg'}
    keypad = root.find(".//s:g[@id='device:KEYPAD-1']",ns)
    assert len(keypad.findall('s:ellipse',ns)) >= 12
    splitter = root.find(".//s:g[@id='device:710-LX500-1']",ns)
    assert splitter.find('s:polygon',ns) is not None
    assert len(splitter.findall('s:ellipse',ns))==4
    assert not root.findall(".//s:g[@class='location']",ns)
    assert root.find(".//s:g[@id='sheet-frame']",ns) is not None
    title = root.find(".//s:g[@id='title-block']",ns)
    assert len(title.findall('s:line',ns)) >= 5
    assert 'REVISION RECORD' in ''.join(root.itertext())
    assert any('data:image/png;base64,' in v for node in root.iter() for v in node.attrib.values())


def test_style_and_frame_visibility_roundtrip_apply_undo(tmp_path):
    d,c,preview = approved_scene()
    assert getattr(preview,'show_location_frames',True) is False
    old=copy.deepcopy(c.document)
    c.apply_layout(preview)
    save_session(Session(d),tmp_path/'style.dmps')
    loaded=load_session(tmp_path/'style.dmps').design.riser_document
    assert loaded==c.document
    assert loaded.elements['device:KEYPAD-1'].symbol_style=='detailed'
    c.undo(); assert c.document==old
    c.redo(); assert c.document==loaded


def test_canvas_keypad_decorations_move_as_one_device(editor):
    frame,_=editor
    tab=frame.riser_tab
    tab.controller.apply_layout(tab.controller.preview_layout())
    tab.redraw()
    items=tab.canvas.find_withtag('visual|device:KEYPAD-1')
    assert sum(tab.canvas.type(i)=='oval' for i in items)>=12
    before={i:tab.canvas.coords(i) for i in items}
    tab.canvas.move('visual|device:KEYPAD-1',18,36)
    for i,coords in before.items():
        assert tab.canvas.coords(i)==pytest.approx([v+(18 if j%2==0 else 36) for j,v in enumerate(coords)])


def test_approved_export_labels_searchable_and_small_profile_legible(tmp_path):
    d,_,doc=approved_scene()
    assert doc.layout_version==3
    for profile,expected in [('24x36',(2592,1728)),('11x17',(1224,792))]:
        with fitz.open(render_pdf(d,doc,tmp_path/f'{profile}.pdf',profile=profile)) as pdf:
            page=pdf[0]
            assert (page.rect.width,page.rect.height)==expected
            text=page.get_text()
            assert 'KEYPAD-1' in text and 'CLASSROOM 27' in text and '710-LX500-2' in text
            spans=[s for b in page.get_text('dict')['blocks'] if 'lines' in b for l in b['lines'] for s in l['spans']]
            assert min(s['size'] for s in spans)>=6
    bad={'scene.overlap','scene.cable_through_device','scene.label_overlap','scene.off_page'}
    assert not [i for i in validate_riser(d,doc) if i.code in bad]


def test_old_manual_geometry_and_classic_style_are_not_silently_replaced():
    d=branched_design(); doc=layout_riser(d)
    doc.elements['device:710-KP-1'].manual=True
    before=copy.deepcopy(doc.elements)
    sync_riser_document(d,doc)
    assert doc.elements==before
    assert getattr(doc.elements['device:710-KP-1'],'symbol_style',None)=='classic'


def test_service_keypad_feed_is_horizontal_and_panel_outputs_are_distinct():
    d,_,doc=approved_scene()
    edge=next(e for e in d.connections if e.source.port_id=='KP BUS')
    points=doc.routes[edge.id].points
    assert len({y for _,y in points})==1
    from riser_scene import port_point
    panel=doc.elements['device:MSP']
    a=port_point(panel,'PROG',output=True)
    b=port_point(panel,'LX500',output=True)
    assert b[0]-a[0] > panel.width*.1


def test_frame_toggle_updates_export_and_is_undoable(editor,tmp_path):
    frame,_=editor; tab=frame.riser_tab
    tab.controller.apply_layout(tab.controller.preview_layout()); tab.redraw()
    assert not tab.canvas.find_withtag('visual|'+next(k for k in tab.controller.document.elements if k.startswith('location:')))
    tab._toggle_layer('Locations')
    assert tab.controller.document.show_location_frames
    root=ET.parse(render_svg(tab.design,tab.controller.document,tmp_path/'frames.svg')).getroot()
    assert any(e.attrib.get('class')=='location' for e in root.iter())
    tab.controller.undo(); tab.redraw()
    assert not tab.controller.document.show_location_frames
    assert tab._layer_switches['Locations'].get()==0


def test_long_locations_expand_body_and_remain_inside_after_resize():
    d=branched_design()
    d.splitters[1].location='MAIN BUILDING THIRD FLOOR VERY LONG ELECTRICAL EQUIPMENT STORAGE ROOM'
    c=RiserEditorController(d,layout_riser(d)); c.apply_layout(c.preview_layout())
    e=c.document.elements['device:710-LX500-1']
    assert e.height>135
    c.resize_element(e.id,40,30)
    runs=device_text(d,e,small=True)
    assert all(e.y<=r.y-r.size and r.y+r.size*.25<=e.y+e.height for r in runs)


def test_validation_catches_caption_wire_overlap_and_overlong_title():
    d,_,doc=approved_scene()
    kp=doc.elements['device:KEYPAD-2']
    route=next(iter(doc.routes.values()))
    route.points=[(kp.x-80,kp.y+kp.height+23),(kp.x+kp.width+80,kp.y+kp.height+23)]
    doc.title_block.school_name='SCHOOL '*300
    codes={i.code for i in validate_riser(d,doc)}
    assert 'scene.caption_overlap' in codes
    assert 'title.overflow' in codes


def test_long_panel_locations_do_not_collide_with_ports_or_zone_ranges():
    from riser_symbols import text_bounds
    from riser_scene import _boxes_overlap
    d=branched_design()
    location='MAIN BUILDING THIRD FLOOR VERY LONG ELECTRICAL EQUIPMENT STORAGE ROOM'
    d.site_info.xr550_location=location
    for r in d.rsps: r.location=location
    c=RiserEditorController(d,layout_riser(d)); doc=c.preview_layout()
    for ref in ('MSP','RSP-1','RSP-2'):
        runs=device_text(d,doc.elements['device:'+ref])
        assert not [(a.text,b.text) for i,a in enumerate(runs) for b in runs[i+1:]
                    if _boxes_overlap(text_bounds(a),text_bounds(b))]


def test_disconnected_equipment_never_pushes_service_keypad_into_panel():
    from parse_dmp_worksheet import RSP
    from riser_scene import _overlap
    d=branched_design()
    d.rsps.extend(RSP(10+i,'ROOM '+str(i),[]) for i in range(6))
    c=RiserEditorController(d,layout_riser(d)); doc=c.preview_layout()
    assert not _overlap(doc.elements['device:MSP'],doc.elements['device:KEYPAD-1'])


def test_connect_mode_labels_unused_msp_ports(editor):
    frame,_=editor; tab=frame.riser_tab
    tab.controller.apply_layout(tab.controller.preview_layout())
    tab.set_tool('Connect')
    labels=[tab.canvas.itemcget(i,'text') for i in tab.canvas.find_withtag('visual|device:MSP')
            if tab.canvas.type(i)=='text']
    for name in ('LX600','LX700','LX800','LX900'):
        assert name in labels


def test_global_caption_edit_grows_detailed_body_and_reattaches_ports():
    from riser_symbols import text_bounds
    from riser_scene import port_point
    d,c,doc=approved_scene(); c.apply_layout(doc)
    ref='710-LX500-2'; element=c.document.elements['device:'+ref]
    d.splitters[2].location='MAIN BUILDING THIRD FLOOR VERY LONG ELECTRICAL EQUIPMENT STORAGE ROOM'
    c.sync_external()
    assert element.height>135
    assert all(text_bounds(r)[3]<element.y+element.height for r in device_text(d,element))
    edge=next(e for e in d.connections if e.source.device_id==ref)
    assert c.document.routes[edge.id].points[0]==port_point(element,edge.source.port_id,output=True)


def test_absent_document_defaults_to_approved_style(editor):
    from riser_editor import RiserTab
    frame,_=editor; d=branched_design(); d.riser_document=None
    tab=RiserTab(frame,Session(d),lambda:None)
    try:
        assert tab.controller.document.layout_version==3
    finally: tab.destroy()


def test_six_active_panel_outputs_have_nonoverlapping_labels():
    from parse_dmp_worksheet import Splitter
    from riser_model import DevicePortRef
    from topology_service import connect
    from riser_symbols import text_bounds
    from riser_scene import _boxes_overlap
    d=branched_design()
    for bus in (600,700,800,900):
        ref=f'710-LX{bus}-1'; d.splitters.append(Splitter(ref,'LX','MDF',outputs=['Spare']*3))
        connect(d,DevicePortRef('MSP',f'LX{bus}'),DevicePortRef(ref,'IN'))
    c=RiserEditorController(d,layout_riser(d)); doc=c.preview_layout()
    runs=device_text(d,doc.elements['device:MSP'])
    assert not [(a.text,b.text) for i,a in enumerate(runs) for b in runs[i+1:] if _boxes_overlap(text_bounds(a),text_bounds(b))]


def test_deep_branching_scene_packs_inside_page_at_full_symbol_size():
    from test_riser_scene import three_rsp_chain_design
    from parse_dmp_worksheet import Keypad,RSP,Splitter
    from riser_model import DevicePortRef
    from topology_service import connect
    d=three_rsp_chain_design()
    for n in (4,5):
        ref=f'710-LX500-{n}'; d.splitters.append(Splitter(ref,'LX',f'ROOM {n}',outputs=['Spare']*3))
        d.rsps.append(RSP(n,f'ROOM {n}',[500+n*16]))
        connect(d,DevicePortRef(f'710-LX500-{n-1}','OUT2'),DevicePortRef(ref,'IN'))
        connect(d,DevicePortRef(ref,'OUT1'),DevicePortRef(f'RSP-{n}','IN'))
    c=RiserEditorController(d,layout_riser(d)); doc=c.preview_layout()
    assert not [i for i in validate_riser(d,doc) if i.code=='scene.off_page']
