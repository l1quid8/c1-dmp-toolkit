"""Changing a keypad address keeps its wiring, programming, and drawing."""

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hardware
from parse_dmp_worksheet import DMPDesign, Keypad, Splitter
from riser_model import DevicePortRef, RiserDocument, RiserElement, RiserRoute, TopologyConnection
from rl_injector.rl_config import RLKeypad, RemoteLinkConfig
from session import Session, save_session, load_session


def _design():
    design = DMPDesign(
        keypads=[Keypad(2, "710-KP-1", "OFFICE", True), Keypad(4, "MSP", "LOBBY")],
        splitters=[Splitter("710-KP-1", "KP", outputs=["KEYPAD #2", "Keypad 20", "Spare"])],
        connections=[TopologyConnection(
            "cable-2", DevicePortRef("710-KP-1", "OUT1"),
            DevicePortRef("KEYPAD-2", "IN"), custom_label="FIELD CABLE")],
        device_location_ids={"KEYPAD-2": "office"},
        location_sync_values={"KEYPAD-2": "OFFICE"},
    )
    design.riser_document = RiserDocument(
        elements={"device:KEYPAD-2": RiserElement(
            "device:KEYPAD-2", "device", "KEYPAD-2", 100, 200, 148, 72)},
        routes={"cable-2": RiserRoute("cable-2", [(1, 2), (3, 4)], manual=True)},
        z_order=["device:KEYPAD-2"],
        unplaced=["KEYPAD-4"],
    )
    return design


def test_renumber_keypad_preserves_wiring_and_drawing():
    design = _design()
    keypad = design.keypads[0]
    route = copy.deepcopy(design.riser_document.routes["cable-2"])

    hardware.renumber_keypad(design, 2, 7)

    assert keypad.number == 7
    assert (keypad.source, keypad.location, keypad.global_keypad) == (
        "710-KP-1", "OFFICE", True)
    assert [item.number for item in design.keypads] == [4, 7]
    assert design.splitters[0].outputs == ["KEYPAD #7", "Keypad 20", "Spare"]
    assert design.connections[0].id == "cable-2"
    assert design.connections[0].target == DevicePortRef("KEYPAD-7", "IN")
    assert design.connections[0].custom_label == "FIELD CABLE"
    assert design.device_location_ids == {"KEYPAD-7": "office"}
    assert design.location_sync_values == {"KEYPAD-7": "OFFICE"}
    assert design.riser_document.elements["device:KEYPAD-7"].ref == "KEYPAD-7"
    assert (design.riser_document.elements["device:KEYPAD-7"].x,
            design.riser_document.elements["device:KEYPAD-7"].y) == (100, 200)
    assert design.riser_document.z_order == ["device:KEYPAD-7"]
    assert design.riser_document.routes["cable-2"] == route


@pytest.mark.parametrize("new_number", [0, 29, 4, "7", True])
def test_invalid_keypad_number_leaves_design_unchanged(new_number):
    design = _design()
    before = copy.deepcopy(design)
    with pytest.raises(hardware.HardwareError):
        hardware.renumber_keypad(design, 2, new_number)
    assert design == before


def test_session_renumber_moves_remotelink_programming_and_survives_save(tmp_path):
    from session import renumber_session_keypad

    path = tmp_path / "keypads.dmps"
    session = Session(
        design=_design(), path=path,
        remotelink=RemoteLinkConfig(keypads={2: RLKeypad(
            name="OFFICE", device_type="fire", disp_areas="00000003")}))

    renumber_session_keypad(session, 2, 7)
    save_session(session)
    restored = load_session(path)

    assert 2 not in restored.remotelink.keypads
    assert restored.remotelink.keypads[7].name == "OFFICE"
    assert restored.remotelink.keypads[7].device_type == "fire"
    assert restored.remotelink.keypads[7].disp_areas == "00000003"
    assert restored.design.keypads[-1].number == 7
    assert restored.design.connections[0].target.device_id == "KEYPAD-7"
