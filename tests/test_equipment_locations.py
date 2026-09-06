import copy
import json

import pytest

from test_riser_scene import branched_design
from parse_dmp_worksheet import PowerSupply
from session import Session, save_session, load_session
import project_locations as locations


def test_migration_keeps_strings_and_resolves_only_explicit_identity():
    d = branched_design()
    d.rsps[0].location = 'AT MSP'
    d.rsps[1].location = 'MDF 2ND FLOOR'
    d.power_supplies = [PowerSupply(1, 'Original PS wording')]
    before = locations.location_values(d)
    locations.sync_project_locations(d)
    assert locations.location_values(d) == before
    assert d.device_location_ids['RSP-1'] == d.device_location_ids['MSP']
    assert d.device_location_ids['PS-1'] == d.device_location_ids['RSP-1']
    assert d.device_location_ids['RSP-2'] != d.device_location_ids['MSP']
    assert d.equipment_locations[d.device_location_ids['MSP']].confirmed is False


def test_unknowns_are_not_one_physical_room_and_ids_ignore_input_order():
    d = branched_design()
    d.rsps[0].location = d.rsps[1].location = 'UNKNOWN'
    shuffled = copy.deepcopy(d)
    shuffled.rsps.reverse()
    shuffled.splitters.reverse()
    locations.sync_project_locations(d)
    locations.sync_project_locations(shuffled)
    assert d.device_location_ids == shuffled.device_location_ids
    assert d.device_location_ids['RSP-1'] != d.device_location_ids['RSP-2']


def test_structured_edit_retains_id_and_updates_assigned_hardware_only():
    d = branched_design()
    locations.sync_project_locations(d)
    identity = d.device_location_ids['MSP']
    locations.edit_location(d, identity, building='MAIN BUILDING', floor='1st Floor', room='MDF')
    assert d.device_location_ids['MSP'] == identity
    assert d.site_info.xr550_location == 'MAIN BUILDING 1st Floor MDF'
    assert d.rsps[0].location == 'MAIN BUILDING 1st Floor MDF'
    assert d.rsps[1].location == 'CLASSROOM 27'
    assert d.equipment_locations[identity].confirmed
    d.rsps[0].location = 'NEW ROOM'
    locations.sync_project_locations(d)
    assert d.device_location_ids['RSP-1'] != identity
    assert d.site_info.xr550_location == 'MAIN BUILDING 1st Floor MDF'


def test_existing_location_merge_requires_confirmation():
    d = branched_design()
    locations.sync_project_locations(d)
    source = d.device_location_ids['MSP']
    target = d.device_location_ids['KEYPAD-2']
    before = copy.deepcopy(d)
    with pytest.raises(ValueError, match='exists'):
        locations.edit_location(d, source, building='', floor='', room='OFFICE')
    assert d == before
    locations.edit_location(d, source, building='', floor='', room='OFFICE', allow_merge=True)
    assert d.device_location_ids['MSP'] == target


def test_legacy_rename_previews_merge_of_distinct_records_in_one_frame():
    from riser_scene import layout_riser
    from riser_editor import RiserEditorController
    d = branched_design()
    d.site_info.xr550_location = 'MAIN BUILDING SUPPLY ROOM'
    d.rsps[0].location = 'MAIN BUILDING 1ST FLOOR SUPPLY ROOM'
    doc = layout_riser(d)
    c = RiserEditorController(d, doc)
    frame = doc.elements['device:MSP'].location_id
    assert doc.elements['device:RSP-1'].location_id == frame
    _, merging = locations.plan_location_rename(d, doc, frame, d.rsps[0].location)
    assert merging
    c.rename_location(frame, d.rsps[0].location, allow_merge=True)
    assert d.device_location_ids['MSP'] == d.device_location_ids['RSP-1']


def test_schema_five_roundtrip_and_schema_four_migration(tmp_path):
    d = branched_design()
    locations.sync_project_locations(d)
    locations.edit_location(d, d.device_location_ids['MSP'], building='MAIN', floor='1', room='MDF')
    path = tmp_path / 'new.dmps'
    save_session(Session(design=d), path)
    raw = json.loads(path.read_text())
    assert raw['schema_version'] == 7
    assert load_session(path).design == d
    for key in ('equipment_locations', 'device_location_ids', 'location_sync_values'):
        raw['design'].pop(key)
    raw['schema_version'] = 4
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    migrated = load_session(path)
    assert migrated.design.device_location_ids
    assert migrated.design.site_info.xr550_location == 'MAIN 1 MDF'
    assert path.read_bytes() == before
