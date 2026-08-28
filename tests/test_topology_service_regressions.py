"""Regression contracts for card-oriented topology and hardware mutations.

The card APIs deliberately accept the values the editor already owns:

* splitter output indexes are zero-based, matching ``SplittersTab`` callbacks;
* splitter input changes name an exact upstream ``DevicePortRef``;
* keypad source changes name a device and allocate its first compatible free
  output while retaining the logical connection's ID and cable metadata.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from hardware import (  # noqa: E402
    add_keypad,
    add_splitter,
    remove_expander,
    remove_splitter,
)
from parse_dmp_worksheet import DMPDesign, Keypad, RSP, Splitter  # noqa: E402
from riser_model import DevicePortRef  # noqa: E402
import topology_service as topology  # noqa: E402


def connected_design() -> DMPDesign:
    design = DMPDesign(
        splitters=[
            Splitter("710-LX500-1", "LX", "MDF", outputs=["Spare"] * 3),
            Splitter("710-LX500-2", "LX", "CLASSROOM", outputs=["Spare"] * 3),
            Splitter("710-KP-1", "KP", "MDF", outputs=["Spare"] * 3),
            Splitter("710-KP-2", "KP", "OFFICE", outputs=["Spare"] * 3),
        ],
        rsps=[RSP(1, "MDF"), RSP(2, "CLASSROOM"), RSP(3, "GYM")],
        keypads=[
            Keypad(1, "MSP", "MDF"),
            Keypad(2, "710-KP-1", "OFFICE"),
            Keypad(3, "710-KP-2", "LOBBY"),
        ],
    )
    topology.connect(
        design, DevicePortRef("MSP", "LX500"),
        DevicePortRef("710-LX500-1", "IN"))
    topology.connect(
        design, DevicePortRef("710-LX500-1", "OUT1"),
        DevicePortRef("RSP-1", "IN"), cable_type="CUSTOM",
        status="existing", quantity=2, custom_label="KEEP ME")
    topology.connect(
        design, DevicePortRef("710-LX500-1", "OUT2"),
        DevicePortRef("710-LX500-2", "IN"))
    topology.connect(
        design, DevicePortRef("710-LX500-2", "OUT1"),
        DevicePortRef("RSP-2", "IN"))
    topology.connect(
        design, DevicePortRef("MSP", "KP BUS"),
        DevicePortRef("KEYPAD-1", "IN"))
    topology.connect(
        design, DevicePortRef("MSP", "PROG"),
        DevicePortRef("710-KP-1", "IN"))
    topology.connect(
        design, DevicePortRef("710-KP-1", "OUT1"),
        DevicePortRef("KEYPAD-2", "IN"), cable_type="KP-CUSTOM",
        status="existing", quantity=2, custom_label="KEEP KP")
    topology.connect(
        design, DevicePortRef("710-KP-1", "OUT2"),
        DevicePortRef("710-KP-2", "IN"))
    topology.connect(
        design, DevicePortRef("710-KP-2", "OUT1"),
        DevicePortRef("KEYPAD-3", "IN"))
    topology.project_legacy_topology(design)
    return design


def _state(design: DMPDesign):
    """Graph and compatibility projections for atomic-failure assertions."""
    return copy.deepcopy((
        design.connections,
        [(splitter.id, splitter.inputs, splitter.outputs)
         for splitter in design.splitters],
        [(keypad.number, keypad.source) for keypad in design.keypads],
    ))


def test_splitter_output_spare_disconnects_edge_and_projects_immediately():
    design = connected_design()

    topology.set_splitter_output(design, "710-LX500-1", 0, "Spare")

    assert not any(
        edge.source == DevicePortRef("710-LX500-1", "OUT1")
        for edge in design.connections)
    splitter = next(s for s in design.splitters if s.id == "710-LX500-1")
    assert splitter.outputs[0] == "Spare"


def test_splitter_output_reconnect_preserves_id_and_cable_metadata():
    design = connected_design()
    before = next(
        edge for edge in design.connections
        if edge.source == DevicePortRef("710-LX500-1", "OUT1"))
    expected = (before.id, before.cable_type, before.status,
                before.quantity, before.custom_label)

    topology.set_splitter_output(design, "710-LX500-1", 0, "RSP-3")

    after = next(edge for edge in design.connections if edge.id == before.id)
    assert after.target == DevicePortRef("RSP-3", "IN")
    assert (after.id, after.cable_type, after.status,
            after.quantity, after.custom_label) == expected
    splitter = next(s for s in design.splitters if s.id == "710-LX500-1")
    assert splitter.outputs[0] == "RSP-3"


def test_splitter_output_accepts_riser_only_rsp_not_in_structured_inventory():
    splitter = Splitter(
        "710-LX500-1", "LX", "MDF",
        inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
        outputs=["RSP-4", "Spare", "Spare"],
    )
    design = DMPDesign(splitters=[splitter])

    edge = topology.set_splitter_output(
        design, splitter.id, 0, "RSP-4")

    assert edge.target == DevicePortRef("RSP-4", "IN")
    assert splitter.outputs[0] == "RSP-4"


def test_splitter_output_accepts_riser_only_keypad_not_in_inventory():
    splitter = Splitter(
        "710-KP-1", "KP", "MDF",
        inputs={"KP-Bus In": "KEYPAD BUS IN FROM XR/550"},
        outputs=["KEYPAD #2", "Spare", "Spare"],
    )
    design = DMPDesign(splitters=[splitter])

    edge = topology.set_splitter_output(
        design, splitter.id, 0, "KEYPAD #2")

    assert edge.target == DevicePortRef("KEYPAD-2", "IN")
    assert splitter.outputs[0] == "KEYPAD #2"


def test_splitter_output_edit_preserves_unrelated_legacy_wiring_text():
    design = connected_design()
    unrelated = next(
        splitter for splitter in design.splitters
        if splitter.id == "710-LX500-2")
    unrelated.inputs = {"LX-Bus In": "FROM EXISTING FIELD TAP"}
    unrelated.outputs[2] = "LEGACY RISER NOTE"

    topology.set_splitter_output(design, "710-LX500-1", 0, "RSP-3")

    assert unrelated.inputs == {"LX-Bus In": "FROM EXISTING FIELD TAP"}
    assert unrelated.outputs[2] == "LEGACY RISER NOTE"


def test_full_legacy_projection_preserves_an_unmodeled_manual_input_note():
    splitter = Splitter(
        "710-LX500-1", "LX", "MDF",
        inputs={"LX-Bus In": "FROM EXISTING FIELD TAP"},
        outputs=["Spare"] * 3,
    )
    design = DMPDesign(splitters=[splitter])

    topology.project_legacy_topology(design)

    assert splitter.inputs == {"LX-Bus In": "FROM EXISTING FIELD TAP"}


def test_full_projection_preserves_manual_input_note_beside_graph_edge():
    design = connected_design()
    splitter = next(item for item in design.splitters
                    if item.id == "710-LX500-2")
    splitter.inputs = {"LX-Bus In": "EXISTING FIELD TAP — VERIFY"}

    topology.project_legacy_topology(design)

    assert splitter.inputs == {"LX-Bus In": "EXISTING FIELD TAP — VERIFY"}


def test_full_projection_preserves_unmodeled_output_note_until_port_changes():
    splitter = Splitter(
        "710-LX500-1", "LX", "MDF",
        inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
        outputs=["LEGACY RISER NOTE", "Spare", "Spare"],
    )
    design = DMPDesign(splitters=[splitter])

    topology.project_legacy_topology(design)
    assert splitter.outputs[0] == "LEGACY RISER NOTE"

    topology.set_splitter_output(design, splitter.id, 0, "RSP-4")
    assert splitter.outputs[0] == "RSP-4"


def test_recreating_old_endpoint_pair_after_reconnect_gets_a_unique_id():
    design = connected_design()
    moved = next(
        edge for edge in design.connections
        if edge.source == DevicePortRef("710-LX500-1", "OUT1"))
    original_id = moved.id

    topology.reconnect(
        design, moved.id,
        source=DevicePortRef("710-LX500-2", "OUT2"),
        target=DevicePortRef("RSP-3", "IN"),
    )
    recreated = topology.connect(
        design,
        DevicePortRef("710-LX500-1", "OUT1"),
        DevicePortRef("RSP-1", "IN"),
    )

    assert recreated.id != original_id
    assert len({edge.id for edge in design.connections}) == len(design.connections)


def test_splitter_output_rejects_incompatible_target_atomically():
    design = connected_design()
    before = _state(design)

    with pytest.raises(topology.TopologyError):
        topology.set_splitter_output(design, "710-KP-1", 2, "RSP-3")

    assert _state(design) == before


def test_splitter_output_allows_occupied_target_until_generation_validation():
    design = connected_design()

    topology.set_splitter_output(design, "710-LX500-1", 2, "RSP-2")

    target = DevicePortRef("RSP-2", "IN")
    assert sum(edge.target == target for edge in design.connections) == 2
    splitter = next(s for s in design.splitters if s.id == "710-LX500-1")
    assert splitter.outputs[2] == "RSP-2"


def test_splitter_input_reconnects_exact_port_and_preserves_metadata():
    design = connected_design()
    before = next(
        edge for edge in design.connections
        if edge.target == DevicePortRef("710-LX500-2", "IN"))
    before.cable_type = "INPUT-CUSTOM"
    before.custom_label = "KEEP INPUT"

    topology.set_splitter_input(
        design, "710-LX500-2",
        DevicePortRef("710-LX500-1", "OUT3"))

    after = next(edge for edge in design.connections if edge.id == before.id)
    assert after.source == DevicePortRef("710-LX500-1", "OUT3")
    assert (after.cable_type, after.custom_label) == ("INPUT-CUSTOM", "KEEP INPUT")
    parent = next(s for s in design.splitters if s.id == "710-LX500-1")
    child = next(s for s in design.splitters if s.id == "710-LX500-2")
    assert parent.outputs[1] == "Spare"
    assert parent.outputs[2] == "To 710-LX500-2"
    assert child.inputs == {"LX-Bus In": "From 710-LX500-1"}


def test_splitter_input_rejects_incompatible_parent_atomically():
    design = connected_design()
    before = _state(design)

    with pytest.raises(topology.TopologyError):
        topology.set_splitter_input(
            design, "710-LX500-2",
            DevicePortRef("710-KP-2", "OUT2"))

    assert _state(design) == before


def test_keypad_source_reconnect_uses_free_output_and_preserves_metadata():
    design = connected_design()
    before = next(
        edge for edge in design.connections
        if edge.target == DevicePortRef("KEYPAD-2", "IN"))
    expected = (before.id, before.cable_type, before.status,
                before.quantity, before.custom_label)

    topology.set_keypad_source(design, 2, "710-KP-2")

    after = next(edge for edge in design.connections if edge.id == before.id)
    assert after.source == DevicePortRef("710-KP-2", "OUT2")
    assert (after.id, after.cable_type, after.status,
            after.quantity, after.custom_label) == expected
    old_parent = next(s for s in design.splitters if s.id == "710-KP-1")
    new_parent = next(s for s in design.splitters if s.id == "710-KP-2")
    assert old_parent.outputs[0] == "Spare"
    assert new_parent.outputs[1] == "KEYPAD #2"
    assert next(k for k in design.keypads if k.number == 2).source == "710-KP-2"


def test_keypad_source_allows_occupied_msp_bus_until_generation_validation():
    design = connected_design()

    topology.set_keypad_source(design, 2, "MSP")

    source = DevicePortRef("MSP", "KP BUS")
    assert sum(edge.source == source for edge in design.connections) == 2
    assert next(k for k in design.keypads if k.number == 2).source == "MSP"


def test_prune_unknown_connections_removes_edges_incident_to_deleted_expander():
    design = connected_design()
    remove_expander(design, 2)

    topology.prune_unknown_connections(design)
    topology.project_legacy_topology(design)

    assert all("RSP-2" not in {edge.source.device_id, edge.target.device_id}
               for edge in design.connections)
    splitter = next(s for s in design.splitters if s.id == "710-LX500-2")
    assert splitter.outputs[0] == "Spare"


def test_prune_unknown_connections_removes_both_sides_of_deleted_splitter():
    design = connected_design()
    remove_splitter(design, "710-LX500-2")

    topology.prune_unknown_connections(design)
    topology.project_legacy_topology(design)

    assert all("710-LX500-2" not in {edge.source.device_id, edge.target.device_id}
               for edge in design.connections)
    parent = next(s for s in design.splitters if s.id == "710-LX500-1")
    assert parent.outputs[1] == "Spare"


def test_prune_preserves_riser_only_endpoint_still_named_by_legacy_wiring():
    splitter = Splitter(
        "710-LX500-1", "LX", "MDF",
        inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
        outputs=["RSP-4", "Spare", "Spare"],
    )
    design = DMPDesign(splitters=[splitter])
    edge = topology.connect(
        design, DevicePortRef(splitter.id, "OUT1"),
        DevicePortRef("RSP-4", "IN"))

    removed = topology.prune_unknown_connections(design)

    assert removed == []
    assert design.connections == [edge]


def test_refresh_after_blank_hardware_additions_does_not_fabricate_connections():
    design = DMPDesign()
    splitter = add_splitter(design, "LX", "NEW ROOM")
    keypad = add_keypad(design, "NEW OFFICE", source=None)

    topology.refresh_connections_from_legacy(design)

    assert design.connections == []
    assert not any(edge.target.device_id in {splitter.id, f"KEYPAD-{keypad.number}"}
                   for edge in design.connections)


def test_legacy_lx_splitter_uses_declared_bus_for_endpoint_validation():
    design = DMPDesign(splitters=[
        Splitter(
            "LX-710-1", "LX", "MDF",
            inputs={"LX-Bus In": "600 BUS IN FROM XR/550"},
            outputs=["Spare"] * 3,
        ),
        Splitter(
            "LX-710-2", "LX", "WING",
            inputs={"LX-Bus In": "From LX-710-1"},
            outputs=["Spare"] * 3,
        ),
    ])

    topology.connect(
        design, DevicePortRef("MSP", "LX600"),
        DevicePortRef("LX-710-1", "IN"))
    topology.connect(
        design, DevicePortRef("LX-710-1", "OUT1"),
        DevicePortRef("LX-710-2", "IN"))

    assert len(design.connections) == 2


def test_legacy_lx_splitter_rejects_the_wrong_declared_bus():
    design = DMPDesign(splitters=[
        Splitter(
            "LX-710-1", "LX", "MDF",
            inputs={"LX-Bus In": "600 BUS IN FROM XR/550"},
            outputs=["Spare"] * 3,
        ),
    ])

    with pytest.raises(topology.TopologyError):
        topology.connect(
            design, DevicePortRef("MSP", "LX500"),
            DevicePortRef("LX-710-1", "IN"))
