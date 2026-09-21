"""The one-sheet proposal keeps rooms together and the electrical graph intact."""
import copy

from project_locations import sync_project_locations
from riser_drawing import device_text
from riser_model import RiserElement
from riser_presentation import layout_presentation
from riser_scene import port_point, validate_riser
from riser_symbols import symbol_parts, terminal_parts, text_bounds
from test_riser_dense_presentation import dense_school
from test_riser_scene import (DMPDesign, SiteInfo, Keypad, Splitter,
                              DevicePortRef, connect, branched_design)
from session import Session, load_session, save_session
from test_release_editor_integration import descendants, editor

import customtkinter as ctk


def test_grouped_symbol_keeps_readable_ports_without_repeating_room():
    design = dense_school()
    splitter = RiserElement('device:710-LX500-1', 'device', '710-LX500-1',
                            100, 100, 170, 95, symbol_style='grouped')
    runs = device_text(design, splitter, small=True)
    assert {'IN', '1', '2', '3', '710-LX500-1'} <= {run.text for run in runs}
    assert all(run.size * 11 / 24 >= 7 for run in runs)
    assert all(100 <= text_bounds(run)[0] and text_bounds(run)[2] <= 270
               for run in runs)
    assert not any('NORTH BLDG' in run.text for run in runs)
    assert symbol_parts(design, splitter)[0].kind == 'polygon'
    assert len(terminal_parts(design, splitter)) == 4


def test_preview_groups_canonical_rooms_and_preserves_each_connection():
    design = dense_school()
    sync_project_locations(design)
    before = copy.deepcopy(design)
    doc = layout_presentation(design)
    assert design == before
    assert not doc.show_location_frames
    assert len([e for e in doc.elements.values() if e.kind == 'device']) == 27
    assert set(doc.routes) == {edge.id for edge in design.connections}
    for ref, identity in design.device_location_ids.items():
        device = doc.elements['device:' + ref]
        group = doc.elements['location:' + identity]
        assert device.location_id == group.id
        assert group.x < device.x and device.x + device.width < group.x + group.width
        assert group.y < device.y
        assert device.y + device.height < group.y + group.height
    for edge in design.connections:
        route = doc.routes[edge.id]
        assert route.points[0] == port_point(doc.elements['device:' + edge.source.device_id],
                                              edge.source.port_id, output=True)
        assert route.points[-1] == port_point(doc.elements['device:' + edge.target.device_id],
                                               edge.target.port_id, output=False)


def test_preview_is_deterministic_and_does_not_shrink_critical_text():
    design = dense_school()
    first = layout_presentation(design)
    second = layout_presentation(design)
    assert first == second
    for device in (e for e in first.elements.values() if e.kind == 'device'):
        assert device.symbol_style == 'detailed'
        assert all(run.size * 11 / 24 >= 7
                   for run in device_text(design, device, small=True))


def test_toolbar_keeps_preview_but_hides_manual_auto_layout(editor):
    frame, _ = editor
    labels = {child.cget('text') for child in descendants(frame.riser_tab)
              if isinstance(child, ctk.CTkButton)}
    assert 'Preview layout' in labels
    assert 'Auto-layout' not in labels
    assert callable(frame.riser_tab.controller.auto_layout)


def test_overfull_proposal_stays_one_sheet_with_specific_fit_warning(tmp_path):
    design = DMPDesign(site_info=SiteInfo(school_name='OVERFULL SCHOOL',
                                          xr550_location='MAIN OFFICE'),
                       keypads=[Keypad(i, 'MSP', f'BUILDING A ROOM {i}')
                                for i in range(1, 25)])
    doc = layout_presentation(design)
    assert (doc.page_width, doc.page_height) == (2592, 1728)
    assert doc.fit_warnings
    assert any('location:' in warning or 'device:' in warning
               for warning in doc.fit_warnings)
    assert any(issue.code == 'layout.fit' for issue in validate_riser(design, doc))
    for device in (e for e in doc.elements.values() if e.kind == 'device'):
        assert all(run.size * 11 / 24 >= 7
                   for run in device_text(design, device, small=True))
    design.riser_document = doc
    path = tmp_path / 'overfull.dmps'
    save_session(Session(design=design), path)
    assert load_session(path).design.riser_document.fit_warnings == doc.fit_warnings


def test_mixed_bus_room_remains_one_group_with_separate_feeds():
    design = branched_design()
    design.splitters.append(Splitter('710-KP-2', 'KP', 'CLASSROOM 27',
                                     outputs=['Spare'] * 3))
    connect(design, DevicePortRef('710-KP-1', 'OUT2'),
            DevicePortRef('710-KP-2', 'IN'))
    before = copy.deepcopy(design)
    doc = layout_presentation(design)
    assert design == before
    assert doc.elements['device:710-KP-2'].location_id == doc.elements['device:710-LX500-2'].location_id
    assert doc.elements['device:KEYPAD-2'].location_id != doc.elements['device:710-KP-2'].location_id
    assert not [issue for issue in validate_riser(design, doc)
                if issue.code in {'scene.off_page', 'scene.overlap',
                                  'scene.cable_through_device',
                                  'scene.uncovered_intersection'}]


def test_disconnected_kp_splitter_stays_on_kp_side():
    design = branched_design()
    design.splitters.append(Splitter('KP-710-9', 'KP', 'ANNEX ROOM',
                                     outputs=['Spare'] * 3))
    doc = layout_presentation(design)
    assert doc.elements['device:KP-710-9'].x < doc.elements['device:MSP'].x


def test_preview_keeps_original_symbol_hierarchy_with_kp_tree_to_left():
    """A presentation regression must not flatten the riser into room cards."""
    design = dense_school()
    doc = layout_presentation(design)
    devices = {e.ref: e for e in doc.elements.values() if e.kind == 'device'}
    assert not doc.show_location_frames
    assert all(device.symbol_style == 'detailed' for device in devices.values())
    assert max(devices[ref].x + devices[ref].width
               for ref in devices if ref.startswith(('710-KP-', 'KEYPAD-'))) < min(
                   devices[ref].x for ref in devices if ref.startswith(('710-LX', 'RSP-')))
    assert all(devices[edge.target.device_id].y >
               devices[edge.source.device_id].y + devices[edge.source.device_id].height
               for edge in design.connections)
