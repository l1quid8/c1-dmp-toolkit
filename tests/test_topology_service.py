"""Shared topology mutation and compatibility projection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from parse_dmp_worksheet import DMPDesign, Keypad, RSP, Splitter  # noqa: E402
from riser_model import DevicePortRef  # noqa: E402
from topology_service import (  # noqa: E402
    TopologyError,
    connect,
    disconnect,
    ensure_explicit_topology,
    project_legacy_topology,
    refresh_connections_from_legacy,
    reconnect,
)


def design() -> DMPDesign:
    return DMPDesign(
        splitters=[
            Splitter("710-LX500-1", "LX", outputs=["Spare"] * 3),
            Splitter("710-LX500-2", "LX", outputs=["Spare"] * 3),
            Splitter("710-KP-1", "KP", outputs=["Spare"] * 3),
        ],
        rsps=[RSP(1), RSP(2)],
        keypads=[Keypad(1), Keypad(2)],
    )


def test_connect_enforces_bus_compatibility_and_port_occupancy():
    d = design()
    edge = connect(d, DevicePortRef("MSP", "LX500"),
                   DevicePortRef("710-LX500-1", "IN"))

    assert edge in d.connections
    with pytest.raises(TopologyError, match="already connected"):
        connect(d, DevicePortRef("MSP", "LX500"),
                DevicePortRef("710-LX500-2", "IN"))
    with pytest.raises(TopologyError, match="KP splitter"):
        connect(d, DevicePortRef("MSP", "LX500"),
                DevicePortRef("710-KP-1", "IN"))


def test_downstream_cycle_is_rejected():
    d = design()
    connect(d, DevicePortRef("MSP", "LX500"), DevicePortRef("710-LX500-1", "IN"))
    connect(d, DevicePortRef("710-LX500-1", "OUT1"), DevicePortRef("710-LX500-2", "IN"))

    with pytest.raises(TopologyError, match="cycle"):
        connect(d, DevicePortRef("710-LX500-2", "OUT1"),
                DevicePortRef("710-LX500-1", "IN"), allow_occupied_target=True)


def test_disconnect_and_reconnect_preserve_connection_metadata():
    d = design()
    edge = connect(d, DevicePortRef("710-LX500-1", "OUT1"), DevicePortRef("RSP-1", "IN"),
                   cable_type="WP240AQC", status="existing", quantity=2)

    moved = reconnect(d, edge.id, target=DevicePortRef("RSP-2", "IN"))
    assert moved.id == edge.id
    assert moved.cable_type == "WP240AQC"
    assert moved.status == "existing"
    assert moved.quantity == 2

    removed = disconnect(d, edge.id)
    assert removed.id == edge.id
    assert d.connections == []


def test_projection_rebuilds_splitter_and_keypad_legacy_fields():
    d = design()
    connect(d, DevicePortRef("MSP", "LX500"), DevicePortRef("710-LX500-1", "IN"))
    connect(d, DevicePortRef("710-LX500-1", "OUT1"), DevicePortRef("RSP-1", "IN"))
    connect(d, DevicePortRef("710-LX500-1", "OUT2"), DevicePortRef("710-LX500-2", "IN"))
    connect(d, DevicePortRef("MSP", "PROG"), DevicePortRef("710-KP-1", "IN"))
    connect(d, DevicePortRef("710-KP-1", "OUT1"), DevicePortRef("KEYPAD-2", "IN"))
    connect(d, DevicePortRef("MSP", "KP BUS"), DevicePortRef("KEYPAD-1", "IN"))

    project_legacy_topology(d)

    lx1, lx2, kp = d.splitters
    assert lx1.inputs == {"LX-Bus In": "500 BUS IN FROM XR/550"}
    assert lx1.outputs == ["RSP-1", "To 710-LX500-2", "Spare"]
    assert lx2.inputs == {"LX-Bus In": "From 710-LX500-1"}
    assert kp.inputs == {"KP-Bus In": "KEYPAD BUS IN FROM XR/550"}
    assert kp.outputs == ["KEYPAD #2", "Spare", "Spare"]
    assert d.keypads[0].source == "MSP"
    assert d.keypads[1].source == "710-KP-1"


def test_projection_does_not_discard_unrelated_design_data():
    d = design()
    d.rsps[0].location = "MDF"
    project_legacy_topology(d)
    assert d.rsps[0].location == "MDF"


def test_refresh_from_legacy_preserves_metadata_for_unchanged_endpoint():
    d = design()
    d.splitters[0].inputs = {"LX-Bus In": "500 BUS IN FROM XR/550"}
    d.splitters[0].outputs = ["RSP-1", "Spare", "Spare"]
    refresh_connections_from_legacy(d)
    edge = next(c for c in d.connections if c.target.device_id == "RSP-1")
    edge.cable_type = "WP240AQC"
    edge.status = "existing"

    d.splitters[0].outputs[1] = "RSP-2"
    refresh_connections_from_legacy(d)

    preserved = next(c for c in d.connections if c.target.device_id == "RSP-1")
    assert preserved.id == edge.id
    assert preserved.cable_type == "WP240AQC"
    assert preserved.status == "existing"
    assert any(c.target.device_id == "RSP-2" for c in d.connections)


def test_ensure_explicit_topology_initializes_imported_design_once():
    d = design()
    d.splitters[0].inputs = {"LX-Bus In": "500 BUS IN FROM XR/550"}
    d.splitters[0].outputs = ["RSP-1", "Spare", "Spare"]

    ensure_explicit_topology(d)
    first_ids = [c.id for c in d.connections]
    ensure_explicit_topology(d)

    assert first_ids
    assert [c.id for c in d.connections] == first_ids
    assert d.riser_document is not None
