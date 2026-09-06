"""Protect canonical wiring while combining older project/editor pathways."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hardware import HardwareError, renumber_splitter
from parse_dmp_worksheet import DMPDesign, Keypad, RSP, Splitter
from riser_model import DevicePortRef, RiserRoute, TopologyConnection
from riser_scene import layout_riser
from session import Session, load_recovery, load_session, save_session, write_recovery
from topology_service import (
    connect, disconnect, project_legacy_topology, refresh_connections_from_legacy,
)


def test_legacy_splitter_collision_is_rejected_before_wiring_or_layout_changes():
    design = DMPDesign(splitters=[
        Splitter("LX-710-1", "LX", "MDF",
                 {"LX-Bus In": "600 BUS IN FROM XR/550"},
                 ["To LX-710-2", "Spare", "Spare"]),
        Splitter("LX-710-2", "LX", "WING",
                 {"LX-Bus In": "From LX-710-1"}, ["Spare"] * 3),
    ])
    connect(design, DevicePortRef("LX-710-1", "OUT1"), DevicePortRef("LX-710-2", "IN"))
    design.riser_document = layout_riser(design)
    before = copy.deepcopy(design)

    with pytest.raises(HardwareError, match="already exists"):
        renumber_splitter(design, "LX-710-2", 1)

    assert design == before


@pytest.mark.parametrize("recovery", [False, True])
def test_save_initializes_only_missing_drawing_not_existing_connections(tmp_path, recovery):
    # Directly supplied canonical graph, before any drawing/editor exists.
    design = DMPDesign(
        keypads=[Keypad(2)],
        connections=[TopologyConnection(
            "custom-edge", DevicePortRef("MSP", "KP BUS"),
            DevicePortRef("KEYPAD-2", "IN"), cable_type="CUSTOM", quantity=2,
            custom_label="KEEP WIRING")],
    )
    before = copy.deepcopy(design.connections)
    session = Session(design=design, path=tmp_path / "no-drawing.dmps")

    if recovery:
        write_recovery(session)
        restored = load_recovery(session.path)
    else:
        save_session(session)
        restored = load_session(session.path)

    assert design.connections == before
    assert restored.design.connections == before
    assert restored.design.riser_document is not None
    assert restored.design.keypads[0].source == "MSP"


@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("recovery", [False, True])
def test_disconnected_graph_stays_empty_when_saved_before_editor_open(tmp_path, supplied, recovery):
    design = DMPDesign(keypads=[Keypad(1, "MSP")])
    if supplied:
        edge = TopologyConnection("imported-edge", DevicePortRef("MSP", "KP BUS"),
                                  DevicePortRef("KEYPAD-1", "IN"))
        design.connections = [edge]
    else:
        edge = connect(design, DevicePortRef("MSP", "KP BUS"), DevicePortRef("KEYPAD-1", "IN"))
    disconnect(design, edge.id)
    session = Session(design=design, path=tmp_path / "disconnected.dmps")

    if recovery:
        write_recovery(session)
        restored = load_recovery(session.path)
    else:
        save_session(session)
        restored = load_session(session.path)

    assert restored.design.connections == []
    assert restored.design.keypads[0].source is None


def test_legacy_refresh_cannot_discard_canonical_duplicate_port_draft():
    design = DMPDesign(
        splitters=[Splitter("710-LX500-1", "LX", outputs=["Spare"] * 3)],
        rsps=[RSP(1), RSP(2)],
    )
    for number in (1, 2):
        design.connections.append(TopologyConnection(
            f"import-{number}", DevicePortRef("710-LX500-1", "OUT1"),
            DevicePortRef(f"RSP-{number}", "IN"), custom_label=f"DRAFT {number}"))
    design.riser_document = layout_riser(design)
    for edge in design.connections:
        design.riser_document.routes[edge.id] = RiserRoute(
            edge.id, [(100.0, 100.0), (100.0, 200.0)], manual=True)
    project_legacy_topology(design)
    before = copy.deepcopy(design)

    refresh_connections_from_legacy(design)

    assert design == before
