"""Renumber paired hardware without changing addresses or cable geometry."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from hardware import HardwareError, add_expander
from parse_dmp_worksheet import DMPDesign, SiteInfo, Splitter
from session import sync_master_zones


def test_renumber_preserves_zones_and_rewrites_pair_references(tmp_path):
    from hardware import renumber_expander
    from riser_model import TopologyConnection, DevicePortRef
    from riser_scene import layout_riser
    design = DMPDesign(site_info=SiteInfo(school_name='Renumber'))
    rsp = add_expander(design, '714-16', 'Room A')
    design.power_supplies[0].relays = {2: 'AC Trouble Zone 515 (714-16 Expander #1)'}
    design.device_location_ids = {'RSP-1': 'room-a', 'PS-1': 'room-a'}
    design.location_sync_values = {'RSP-1': 'Room A', 'PS-1': 'Room A'}
    design.splitters = [Splitter('710-LX500-1', 'LX', outputs=['RSP-1', 'RSP 1', 'RSP-10'])]
    sync_master_zones(design)
    design.connections = [TopologyConnection(id='cable-a', source=DevicePortRef('MSP', 'LX500'),
                                     target=DevicePortRef('RSP-1', 'LX IN'))]
    design.riser_document = layout_riser(design)
    old_element = copy.deepcopy(design.riser_document.elements['device:RSP-1'])
    old_routes = copy.deepcopy(design.riser_document.routes)
    addresses = list(rsp.zones)
    renumber_expander(design, 1, 7)
    assert rsp.number == design.power_supplies[0].number == 7
    assert rsp.zones == addresses
    assert design.power_supplies[0].location == 'Room A'
    assert design.power_supplies[0].relays[2] == 'AC Trouble Zone 515 (714-16 Expander #7)'
    assert design.device_location_ids == {'RSP-7': 'room-a', 'PS-7': 'room-a'}
    assert design.location_sync_values == {'RSP-7': 'Room A', 'PS-7': 'Room A'}
    assert design.splitters[0].outputs == ['RSP-7', 'RSP-7', 'RSP-10']
    assert design.zones[-2].location == 'PS-7: A/C LOSS'
    assert design.zones[-1].location == 'PS-7: BATT. TRBL'
    assert all(z.rsp_number != 1 for z in design.master_zones)
    assert design.connections[0].id == 'cable-a'
    assert design.connections[0].target.device_id == 'RSP-7'
    element = design.riser_document.elements['device:RSP-7']
    assert (element.x, element.y, element.width, element.height) == (old_element.x, old_element.y, old_element.width, old_element.height)
    assert design.riser_document.routes == old_routes
    assert 'device:RSP-1' not in design.riser_document.elements
    assert 'device:RSP-7' in design.riser_document.z_order
    from session import Session, save_session, load_session
    path = tmp_path / 'renumber.dmps'
    save_session(Session(design=design, path=path))
    restored = load_session(path).design
    assert restored.rsps[0].number == restored.power_supplies[0].number == 7
    assert restored.rsps[0].zones == addresses
    assert restored.connections[0].target.device_id == 'RSP-7'
    from generate_dmp_ws import write_dmp_xlsx
    from parse_dmp_worksheet import parse_dmp_worksheet
    output = tmp_path / 'renumber.xlsx'
    write_dmp_xlsx(restored, Path(__file__).resolve().parents[1] / 'DMP Installation Worksheet_template_blank.xlsx', output)
    exported = parse_dmp_worksheet(output)
    assert exported.rsps[0].number == exported.power_supplies[0].number == 7
    assert exported.rsps[0].zones == addresses


@pytest.mark.parametrize('new_number', [0, 16, 2, '3', True])
def test_invalid_renumber_is_atomic(new_number):
    from hardware import renumber_expander
    design = DMPDesign(site_info=SiteInfo())
    add_expander(design, '714-8')
    add_expander(design, '714-16')
    before = copy.deepcopy(design)
    with pytest.raises(HardwareError):
        renumber_expander(design, 1, new_number)
    assert design == before


def test_batch_renumber_allows_swap():
    from hardware import renumber_expanders
    design = DMPDesign(site_info=SiteInfo())
    first = add_expander(design, '714-16', 'First')
    second = add_expander(design, '714-16', 'Second')
    design.splitters = [Splitter('710-LX500-1', 'LX', outputs=['RSP-1', 'RSP-2'])]
    renumber_expanders(design, {1: 2, 2: 1})
    assert first.number == 2 and second.number == 1
    assert design.splitters[0].outputs == ['RSP-2', 'RSP-1']
    assert design.zones[14].location == 'PS-2: A/C LOSS'
    assert design.zones[30].location == 'PS-1: A/C LOSS'
