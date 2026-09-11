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
    assert raw['schema_version'] == 8
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


def test_live_typing_keeps_current_locations_without_accumulating_fragments():
    d = branched_design()
    locations.sync_project_locations(d)
    for text in ('(', '(E', '(E)', '(E)WP240', '', 'S', 'ST', 'STORAGE'):
        d.splitters[0].location = text
        locations.sync_project_locations(d)

    assert {record.full_label for record in d.equipment_locations.values()} == {
        'MDF', 'CLASSROOM 27', 'OFFICE', 'STORAGE',
    }
    assert set(d.device_location_ids.values()) == set(d.equipment_locations)


def test_old_session_discards_unused_fragments_but_keeps_deliberate_locations(tmp_path):
    from location_model import EquipmentLocation
    from riser_model import RiserElement
    from riser_scene import layout_clusters
    d = branched_design()
    d.riser_document = layout_clusters(d)
    path = tmp_path / 'old.dmps'
    save_session(Session(design=d), path)
    raw = json.loads(path.read_text())
    raw['design']['equipment_locations']['junk'] = vars(EquipmentLocation('junk', '(E)WP24'))
    raw['design']['equipment_locations']['known'] = vars(EquipmentLocation(
        'known', 'BUILDING B STORAGE', building='BUILDING B', room='STORAGE', confirmed=True))
    raw['design']['equipment_locations']['drawn'] = vars(EquipmentLocation('drawn', 'EMPTY ROOM'))
    raw['design']['riser_document']['elements']['empty'] = vars(RiserElement(
        'empty', 'location', 'EMPTY ROOM', 10, 20, 300, 200,
        manual=True, physical_location_id='drawn'))
    path.write_text(json.dumps(raw))
    original = path.read_bytes()

    reopened = load_session(path).design

    assert 'junk' not in reopened.equipment_locations
    assert reopened.equipment_locations['known'].room == 'STORAGE'
    assert reopened.equipment_locations['drawn'].full_label == 'EMPTY ROOM'
    assert reopened.riser_document.elements['empty'].x == 10
    assert path.read_bytes() == original


def test_location_choices_exclude_cables_and_unknowns_without_removing_east_suffix():
    from parse_dmp_worksheet import Keypad
    d = branched_design()
    d.splitters[0].location = '(E)WP240 KEYPAD 8 BUILDING B'
    d.keypads.extend([Keypad(3, location='UNKNOWN'), Keypad(4, location='HALLWAY (E)')])
    locations.sync_project_locations(d)
    choose = getattr(locations, 'equipment_location_choices', None)
    assert choose is not None, 'The picker needs validated location choices'

    choices = choose(d)

    assert set(choices) == {'MDF', 'CLASSROOM 27', 'OFFICE', 'HALLWAY (E)'}
    assert choices['HALLWAY (E)'] == d.device_location_ids['KEYPAD-4']
    # Invalid source text remains available for explicit correction.
    assert d.splitters[0].location == '(E)WP240 KEYPAD 8 BUILDING B'


@pytest.mark.parametrize('device_id', ['RSP-1', 'PS-1'])
def test_assign_custom_name_moves_the_rsp_power_pair_and_creates_a_visible_room(device_id):
    from riser_editor import RiserEditorController
    from riser_scene import layout_clusters
    d = branched_design()
    d.power_supplies = [PowerSupply(1, 'MDF')]
    c = RiserEditorController(d, layout_clusters(d))
    before = copy.deepcopy(d)
    assign = getattr(c, 'assign_location_name', None)
    assert assign is not None, 'Named location assignment must be one undoable command'

    assign(device_id, '  Equipment   Store  ')

    identity = d.device_location_ids['RSP-1']
    assert d.device_location_ids['PS-1'] == identity
    assert d.rsps[0].location == d.power_supplies[0].location == 'Equipment Store'
    assert d.site_info.xr550_location == 'MDF'
    assert d.riser_document.elements[d.riser_document.elements['device:RSP-1'].location_id].heading_lines == [
        'Equipment Store']
    assert d.riser_document.routes == before.riser_document.routes
    assert c.undo()
    assert d == before


@pytest.mark.parametrize('name', ['', '  ', '***', '(E)WP240', 'WP240', 'UNKNOWN', 'Location needs review'])
def test_invalid_custom_location_leaves_the_project_unchanged(name):
    from riser_editor import RiserEditorController
    from riser_scene import layout_riser
    d = branched_design()
    c = RiserEditorController(d, layout_riser(d))
    before = copy.deepcopy(d)
    assign = getattr(c, 'assign_location_name', None)
    assert assign is not None, 'Named location assignment is missing'

    with pytest.raises(ValueError, match='location name'):
        assign('KEYPAD-2', name)

    assert d == before
    assert not c.can_undo


def test_named_assignment_preserves_the_existing_msp_location_alias():
    from riser_editor import RiserEditorController
    from riser_scene import layout_riser
    d = branched_design()
    d.power_supplies = [PowerSupply(2, 'CLASSROOM 27')]
    c = RiserEditorController(d, layout_riser(d))
    old_ids = set(d.equipment_locations)

    c.assign_location_name('RSP-2', 'AT MSP')

    assert d.rsps[1].location == d.power_supplies[0].location == 'MDF'
    assert d.device_location_ids['RSP-2'] == d.device_location_ids['PS-2'] == d.device_location_ids['MSP']
    assert set(d.equipment_locations) <= old_ids
