import copy
import xml.etree.ElementTree as ET
import pytest
import fitz

from test_riser_scene import branched_design
from riser_scene import _overlap
import riser_scene
from project_locations import sync_project_locations, edit_location
from riser_render import render_svg, generate_riser_bundle


def test_cluster_layout_is_pure_and_groups_only_real_locations():
    d = branched_design()
    d.rsps[0].location = 'MSP'
    d.keypads[0].location = 'MDF (Service Keypad)'
    before = copy.deepcopy(d)
    doc = riser_scene.layout_clusters(d)
    assert d == before
    assert doc.layout_version == 2
    assert doc.elements['device:MSP'].location_id == doc.elements['device:RSP-1'].location_id
    assert doc.elements['device:MSP'].location_id == doc.elements['device:KEYPAD-1'].location_id
    assert doc.elements['device:RSP-2'].location_id != doc.elements['device:MSP'].location_id
    frames = [e for e in doc.elements.values() if e.kind == 'location']
    assert not any(_overlap(a, b) for i, a in enumerate(frames) for b in frames[i+1:])
    for device in (e for e in doc.elements.values() if e.kind == 'device'):
        frame = doc.elements[device.location_id]
        assert device.y >= frame.y + frame.heading_height


def test_unknown_cluster_labels_identify_individual_devices():
    d = branched_design()
    d.rsps[0].location = d.rsps[1].location = 'UNKNOWN'
    doc = riser_scene.layout_clusters(d)
    assert doc.elements['device:RSP-1'].location_id != doc.elements['device:RSP-2'].location_id
    frame = doc.elements[doc.elements['device:RSP-1'].location_id]
    assert 'RSP-1' in frame.heading_lines
    assert 'LOCATION UNCONFIRMED' in frame.heading_lines


def test_shuffled_input_is_deterministic():
    d = branched_design()
    original = riser_scene.layout_clusters(d)
    d.splitters.reverse()
    d.connections.reverse()
    d.rsps.reverse()
    assert riser_scene.layout_clusters(d) == original


def test_structured_headings_export_without_clipping(tmp_path):
    d = branched_design()
    sync_project_locations(d)
    edit_location(d, d.device_location_ids['MSP'], building='MAIN BUILDING',
                  floor='1st Floor', room='Nurse Office')
    doc = riser_scene.layout_clusters(d)
    frame = doc.elements[doc.elements['device:MSP'].location_id]
    assert frame.heading_lines == ['MAIN BUILDING', '1st Floor · Nurse Office']
    render_svg(d, doc, tmp_path / 'clusters.svg')
    ET.parse(tmp_path / 'clusters.svg')
    for path in generate_riser_bundle(d, doc, tmp_path)[:2]:
        with fitz.open(path) as pdf:
            assert 'MAIN BUILDING' in pdf[0].get_text()
            assert 'Nurse Office' in pdf[0].get_text()
            fonts = [font[3] for font in pdf[0].get_fonts()]
            assert all('Times' not in font for font in fonts)


def test_dense_clusters_fit_without_shrinking_symbols():
    from test_riser_workflows import seven_rsp_design
    d = seven_rsp_design()
    # A multi-level head end plus remote equipment is the tall-sheet case.
    for splitter in d.splitters[:3]:
        splitter.location = 'MDF'
    for rsp in d.rsps[:3]:
        rsp.location = 'MDF'
    doc = riser_scene.layout_clusters(d)
    assert all(e.y + e.height <= doc.page_height - riser_scene.PAGE_MARGIN
               for e in doc.elements.values())
    assert doc.elements['device:MSP'].width == 220


def test_cable_label_bounds_use_small_profile_font_metrics():
    d = branched_design()
    doc = riser_scene.layout_clusters(d)
    edge = d.connections[0]
    edge.custom_label = 'WWWWWWWWWWWWWWWW'
    box = riser_scene._route_label_box(edge, doc.routes[edge.id])
    assert box[2] - box[0] >= fitz.get_text_length(edge.label, fontname='hebo', fontsize=15.3) + 8
