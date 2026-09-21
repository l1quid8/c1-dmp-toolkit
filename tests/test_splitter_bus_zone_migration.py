"""Changing an LX feed carries the attached expanders' physical addresses."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hardware import HardwareError, renumber_splitter, sync_rsp_zone_buses
from parse_dmp_worksheet import DMPDesign, PowerSupply, RSP, Splitter, Zone, ZoneInfo


def _five_rsp_design():
    design = DMPDesign()
    design.splitters = [
        Splitter("710-LX500-1", "LX", outputs=["RSP-1", "RSP-2", "Spare"]),
        Splitter("710-LX500-2", "LX", inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
                 outputs=["RSP-3", "RSP-4", "RSP-5"]),
    ]
    for number in range(1, 6):
        start = 501 + (number - 1) * 16
        addresses = list(range(start, start + 16))
        design.rsps.append(RSP(number, f"Room {number}", addresses))
        design.power_supplies.append(PowerSupply(number, relays={
            2: f"AC Trouble Zone {start + 14} (714-16 Expander #{number})",
            3: f"Battery Trouble Zone {start + 15} (714-16 Expander #{number})",
        }))
        for address in addresses:
            description = f"POINT {address}" if address < start + 14 else f"PS-{number}: A/C LOSS" if address == start + 14 else f"PS-{number}: BATT. TRBL"
            design.zones.append(ZoneInfo(address, description, "Motion", 2, "EX"))
            design.master_zones.append(Zone(address, description, number))
    return design


def test_changing_splitter_bus_moves_all_attached_rsp_ranges_and_zone_details():
    design = _five_rsp_design()
    renumber_splitter(design, "710-LX500-1", 1, lx_bus="600")
    renumber_splitter(design, "710-LX500-2", 2, lx_bus="600")
    assert [(r.number, min(r.zones), max(r.zones)) for r in design.rsps] == [
        (1, 601, 616), (2, 617, 632), (3, 633, 648), (4, 649, 664), (5, 665, 680)]
    assert [(z.number, z.location, z.device_type, z.partition, z.rl_type)
            for z in design.zones[:2]] == [
                (601, "POINT 501", "Motion", 2, "EX"),
                (602, "POINT 502", "Motion", 2, "EX")]
    assert design.master_zones[0].number == 601
    assert design.power_supplies[0].relays[2].startswith("AC Trouble Zone 615 ")


def test_occupied_destination_rejects_entire_bus_change():
    design = _five_rsp_design()
    design.zones.append(ZoneInfo(620, "EXISTING", "Motion", 1))
    before = copy.deepcopy(design)
    with pytest.raises(HardwareError, match="(?i)(occupied|conflict)"):
        renumber_splitter(design, "710-LX500-1", 1, lx_bus="600")
    assert design == before


def test_sync_repairs_ranges_after_splitter_bus_was_changed_earlier():
    design = _five_rsp_design()
    for splitter in design.splitters:
        splitter.id = splitter.id.replace("LX500", "LX600")
        splitter.inputs = {key: value.replace("500 BUS", "600 BUS")
                           for key, value in splitter.inputs.items()}
    assert sync_rsp_zone_buses(design) == 5
    assert [min(rsp.zones) for rsp in design.rsps] == [601, 617, 633, 649, 665]
    assert sync_rsp_zone_buses(design) == 0


def test_sync_uses_actual_msp_feed_when_riser_wiring_changes_bus():
    from riser_model import DevicePortRef, TopologyConnection

    design = _five_rsp_design()
    design.connections = [TopologyConnection(
        id="feed", source=DevicePortRef("MSP", "LX600"),
        target=DevicePortRef("710-LX500-1", "IN"))]
    assert sync_rsp_zone_buses(design) == 2
    assert [min(rsp.zones) for rsp in design.rsps] == [601, 617, 533, 549, 565]


def test_bus_change_moves_attached_rsp_from_imported_nonmatching_range():
    design = _five_rsp_design()
    for rsp in design.rsps[:2]:
        rsp.zones = [zone - 300 for zone in rsp.zones]
    for zone in design.zones[:32]:
        zone.number -= 300
    for zone in design.master_zones[:32]:
        zone.number -= 300
    renumber_splitter(design, "710-LX500-1", 1, lx_bus="600")
    assert [min(rsp.zones) for rsp in design.rsps[:2]] == [601, 617]


def test_existing_lx600_feed_repacks_five_rsps_when_last_crosses_bus_boundary():
    design = DMPDesign(splitters=[Splitter(
        "710-LX600-2", "LX", outputs=["RSP-7", "RSP-3", "RSP-4"]),
        Splitter("710-LX600-3", "LX", outputs=["RSP-5", "RSP-6", "Spare"])])
    for number, start, count in [(1, 501, 16), (2, 517, 8),
                                 (7, 525, 16), (3, 541, 16),
                                 (4, 557, 16), (5, 573, 16), (6, 589, 16)]:
        design.rsps.append(RSP(number, zones=list(range(start, start + count))))
        for address in range(start, start + count):
            design.zones.append(ZoneInfo(address, f"POINT {address}", "Motion", 2))
            design.master_zones.append(Zone(address, f"POINT {address}", number))

    assert sync_rsp_zone_buses(design) == 5
    assert {rsp.number: (min(rsp.zones), max(rsp.zones)) for rsp in design.rsps} == {
        1: (501, 516), 2: (517, 524), 3: (601, 616), 4: (617, 632),
        5: (633, 648), 6: (649, 664), 7: (665, 680)}
    assert next(zone for zone in design.zones if zone.number == 664).location == "POINT 604"
    assert next(zone for zone in design.master_zones if zone.number == 601).description == "POINT 541"


def test_already_moved_lx600_ranges_are_reordered_by_rsp_number():
    design = DMPDesign(
        splitters=[Splitter("710-LX600-1", "LX", outputs=["RSP-7", "RSP-3", "RSP-4"]),
                   Splitter("710-LX600-2", "LX", outputs=["RSP-5", "RSP-6", "Spare"])],
        rsps=[RSP(7, zones=list(range(601, 617))),
              RSP(3, zones=list(range(617, 633))),
              RSP(4, zones=list(range(633, 649))),
              RSP(5, zones=list(range(649, 665))),
              RSP(6, zones=list(range(665, 681)))],
        zones=[ZoneInfo(n, f"POINT {n}") for n in range(601, 681)])
    assert sync_rsp_zone_buses(design) == 5
    assert [(rsp.number, min(rsp.zones), max(rsp.zones)) for rsp in sorted(design.rsps, key=lambda r: r.number)] == [
        (3, 601, 616), (4, 617, 632), (5, 633, 648), (6, 649, 664), (7, 665, 680)]
    assert next(zone for zone in design.zones if zone.number == 665).location == "POINT 601"


def test_splitter_bus_change_refreshes_downstream_rsp_ranges_immediately():
    from types import SimpleNamespace
    from editor_frame import EditorFrame
    from riser_model import DevicePortRef, TopologyConnection
    from session import Session

    design = DMPDesign(
        splitters=[Splitter("710-LX500-1", "LX", outputs=["To 710-LX500-2"]),
                   Splitter("710-LX500-2", "LX", inputs={"LX-Bus In": "From 710-LX500-1"},
                            outputs=["RSP-2", "RSP-1"])],
        rsps=[RSP(1, zones=list(range(517, 533))),
              RSP(2, zones=list(range(501, 517)))],
        zones=[ZoneInfo(n, f"POINT {n}") for n in range(501, 533)],
        connections=[
            TopologyConnection("feed", DevicePortRef("MSP", "LX500"),
                               DevicePortRef("710-LX500-1", "IN")),
            TopologyConnection("chain", DevicePortRef("710-LX500-1", "OUT1"),
                               DevicePortRef("710-LX500-2", "IN")),
            TopologyConnection("rsp2", DevicePortRef("710-LX500-2", "OUT1"),
                               DevicePortRef("RSP-2", "IN")),
            TopologyConnection("rsp1", DevicePortRef("710-LX500-2", "OUT2"),
                               DevicePortRef("RSP-1", "IN"))])
    renumber_splitter(design, "710-LX500-1", 1, lx_bus="600")
    frame = SimpleNamespace(
        session=Session(design=design),
        riser_tab=SimpleNamespace(controller=SimpleNamespace(
            document=SimpleNamespace(unplaced=[], elements={}))),
        _close_hardware_dialog=lambda: None, mark_dirty=lambda: None,
        refresh_validation=lambda: None, refresh_all_tabs=lambda: None)
    EditorFrame._on_structure_change(frame)
    assert [(r.number, min(r.zones), max(r.zones)) for r in sorted(design.rsps, key=lambda r: r.number)] == [
        (1, 601, 616), (2, 617, 632)]
    assert next(z for z in design.zones if z.number == 601).location == "POINT 517"


def test_opening_saved_project_repairs_ranges_without_user_action(tmp_path):
    import tkinter as tk
    import customtkinter as ctk
    from editor_frame import EditorFrame
    from session import Session
    from tk_compat import install_scrollbar_redraw_fix

    install_scrollbar_redraw_fix()
    try:
        root = ctk.CTk()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    root.withdraw()
    design = _five_rsp_design()
    for splitter in design.splitters:
        splitter.id = splitter.id.replace("LX500", "LX600")
    try:
        frame = EditorFrame(root, root, Session(design=design,
            saved_at="2026-09-17T08:46:00", path=tmp_path / "existing.dmps"))
        assert [min(rsp.zones) for rsp in design.rsps] == [601, 617, 633, 649, 665]
        assert frame.dirty
    finally:
        root.destroy()
