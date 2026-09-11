"""Compact expansion must preserve existing zone addresses and ownership."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hardware import HardwareError, add_expander, remove_expander
from parse_dmp_worksheet import DMPDesign, RSP, Zone, ZoneInfo
from session import sync_master_zones


@pytest.mark.parametrize("models, ranges", [
    (["714-16", "714-8", "714-16"], [(501, 516), (517, 524), (525, 540)]),
    (["714-8", "714-16", "714-8"], [(501, 508), (509, 524), (525, 532)]),
    (["714-8"] * 12 + ["714-16"],
     [(501, 508), (509, 516), (517, 524), (525, 532), (533, 540), (541, 548),
      (549, 556), (557, 564), (565, 572), (573, 580), (581, 588), (589, 596),
      (601, 616)]),
])
def test_model_size_packs_complete_modules_and_rolls_to_next_bus(models, ranges):
    design = DMPDesign()
    for model, (start, end) in zip(models, ranges):
        rsp = add_expander(design, model)
        assert rsp.zones == list(range(start, end + 1))
        rows = {zone.number: zone for zone in design.zones}
        assert rows[end - 1].location == f"PS-{rsp.number}: A/C LOSS"
        assert rows[end].location == f"PS-{rsp.number}: BATT. TRBL"
        assert all(rows[number].location == "SPARE" for number in range(start, end - 1))
    numbers = [zone.number for zone in design.zones]
    assert len(numbers) == len(set(numbers))


def test_module_id_is_independent_of_its_zone_address():
    original = RSP(12, "EXISTING ROOM", list(range(501, 517)))
    design = DMPDesign(rsps=[original])
    rsp = add_expander(design, "714-8")
    assert rsp.number == 1
    assert rsp.zones == list(range(517, 525))
    assert original.zones == list(range(501, 517))


def test_remove_packed_module_preserves_neighbors_and_their_master_rows():
    design = DMPDesign(
        rsps=[RSP(1, "ROOM 1", list(range(501, 517))),
              RSP(2, "ROOM 2", list(range(517, 525)), "714-8"),
              RSP(3, "ROOM 3", list(range(525, 541)))],
        zones=[ZoneInfo(n, f"POINT {n}", "Motion", 2, "NT") for n in range(501, 541)],
    )
    sync_master_zones(design)
    expected = deepcopy([z for z in design.zones if z.number not in range(517, 525)])
    expected_master = deepcopy([z for z in design.master_zones if z.number not in range(517, 525)])
    remove_expander(design, 2)
    assert design.zones == expected
    assert design.master_zones == expected_master
    assert design.rsps[-1].zones == list(range(525, 541))


def test_larger_replacement_skips_small_gap_then_smaller_module_can_fill_it():
    design = DMPDesign()
    for model in ("714-16", "714-8", "714-16"):
        add_expander(design, model)
    remove_expander(design, 2)
    originals = deepcopy(design.rsps)
    original_zones = deepcopy(design.zones)
    replacement = add_expander(design, "714-16")
    assert replacement.number == 2
    assert replacement.zones == list(range(541, 557))
    filler = add_expander(design, "714-8")
    assert filler.number == 4
    assert filler.zones == list(range(517, 525))
    assert [r for r in design.rsps if r.number in (1, 3)] == originals
    assert [z for z in design.zones if z.number in {z.number for z in original_zones}] == original_zones


def test_unowned_existing_zone_rows_are_preserved_and_reserve_their_addresses():
    design = DMPDesign()
    add_expander(design, "714-16")
    orphans = [ZoneInfo(517, "SPARE", "Spare", 1),
               ZoneInfo(531, "EXISTING STORAGE", "Motion", 3, "EX")]
    design.zones.extend(deepcopy(orphans))
    rsp = add_expander(design, "714-16")
    assert rsp.zones == list(range(532, 548))
    assert [z for z in design.zones if z.number in (517, 531)] == orphans


@pytest.mark.parametrize("occupied, model, expected", [
    (range(500, 592), "714-8", range(592, 600)),
    (range(500, 592), "714-16", range(601, 617)),
    (range(508, 600), "714-8", range(500, 508)),
    (range(500, 900), "714-16", range(901, 917)),
])
def test_uses_available_addresses_without_crossing_a_bus_boundary(occupied, model, expected):
    design = DMPDesign(zones=[ZoneInfo(n, "EXISTING", "Motion", 1) for n in occupied])
    rsp = add_expander(design, model)
    assert rsp.zones == list(expected)


def test_bus_capacity_failure_keeps_the_design_unchanged():
    design = DMPDesign(master_zones=[Zone(n, f"EXISTING {n}") for n in range(500, 1000)])
    before = deepcopy(design)
    with pytest.raises(HardwareError, match="(?i)(space|available|fit)"):
        add_expander(design, "714-8")
    assert design == before


def test_only_master_rows_still_reserve_existing_addresses():
    design = DMPDesign(master_zones=[Zone(n, f"EXISTING {n}") for n in range(501, 509)])
    rsp = add_expander(design, "714-8")
    assert rsp.zones == list(range(509, 517))
    assert [(z.number, z.location) for z in design.zones if z.number < 509] == [
        (n, f"EXISTING {n}") for n in range(501, 509)]


def test_remove_last_module_does_not_resurrect_its_zones_from_master():
    design = DMPDesign()
    add_expander(design, "714-8")
    sync_master_zones(design)
    remove_expander(design, 1)
    sync_master_zones(design)
    assert design.zones == []
    assert design.master_zones == []
    rsp = add_expander(design, "714-8")
    assert rsp.zones == list(range(501, 509))


def test_removing_an_overlapping_import_keeps_points_owned_by_surviving_module():
    design = DMPDesign(
        rsps=[RSP(2, "BAD IMPORT", list(range(517, 533))),
              RSP(3, "SURVIVOR", list(range(525, 541)))],
        zones=[ZoneInfo(n, f"POINT {n}", "Motion", 1) for n in range(517, 541)],
    )
    remove_expander(design, 2)
    assert [z.number for z in design.zones] == list(range(525, 541))


def test_add_preserves_master_only_rows_in_a_partially_editable_import():
    existing = ZoneInfo(501, "EDITED ROOM", "Motion", 3, "EX")
    design = DMPDesign(zones=[existing], master_zones=[
        Zone(501, "OLD ROOM"), Zone(510, "EXISTING MASTER ROOM")])
    rsp = add_expander(design, "714-8")
    assert rsp.zones == list(range(502, 510))
    sync_master_zones(design)
    assert existing in design.zones
    assert existing.location == "EDITED ROOM" and existing.rl_type == "EX"
    assert any(z.number == 510 and z.description == "EXISTING MASTER ROOM"
               for z in design.master_zones)


def test_remove_preserves_master_only_rows_in_a_partially_editable_import():
    design = DMPDesign(rsps=[RSP(1, "REMOVED ROOM", list(range(501, 509)), "714-8")],
                       zones=[ZoneInfo(501, "EDITED ROOM", "Motion", 3, "EX")],
                       master_zones=[Zone(501, "OLD ROOM"), Zone(510, "EXISTING MASTER ROOM")])
    remove_expander(design, 1)
    sync_master_zones(design)
    assert [(z.number, z.location) for z in design.zones] == [(510, "EXISTING MASTER ROOM")]
    assert [(z.number, z.description) for z in design.master_zones] == [(510, "EXISTING MASTER ROOM")]
