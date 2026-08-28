"""Editor card mutations must go through the canonical topology service."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from editor_tabs import KeypadsTab, SplittersTab  # noqa: E402
from editor_frame import EditorFrame  # noqa: E402
from hardware import remove_expander, renumber_splitter  # noqa: E402
from parse_dmp_worksheet import DMPDesign, Splitter  # noqa: E402
from riser_scene import layout_riser, port_point, sync_riser_document  # noqa: E402
from topology_service import set_splitter_output  # noqa: E402
from session import Session  # noqa: E402
from test_topology_service_regressions import connected_design  # noqa: E402


def bare_tab(cls, design):
    tab = object.__new__(cls)
    tab.session = Session(design=design)
    tab.on_change_calls = 0

    def changed():
        tab.on_change_calls += 1

    tab.on_change = changed
    tab._schedule_topology_rebuild = lambda: None
    return tab


def test_splitter_card_output_reconnect_preserves_canonical_edge_metadata():
    design = connected_design()
    tab = bare_tab(SplittersTab, design)
    splitter = next(item for item in design.splitters
                    if item.id == "710-LX500-1")
    before = next(edge for edge in design.connections
                  if edge.source.device_id == splitter.id
                  and edge.source.port_id == "OUT1")

    tab._set_output(splitter, 0, "RSP-3")

    after = next(edge for edge in design.connections if edge.id == before.id)
    assert after.target.device_id == "RSP-3"
    assert (after.cable_type, after.status, after.quantity, after.custom_label) == (
        "CUSTOM", "existing", 2, "KEEP ME")
    assert tab.on_change_calls == 1


def test_splitter_card_preserves_manual_input_wording_as_legacy_wiring():
    design = connected_design()
    tab = bare_tab(SplittersTab, design)
    splitter = next(item for item in design.splitters
                    if item.id == "710-LX500-2")

    tab._set_input(splitter, "FROM EXISTING FIELD TAP")

    assert splitter.inputs == {"LX-Bus In": "FROM EXISTING FIELD TAP"}
    assert not any(edge.target.device_id == splitter.id
                   for edge in design.connections)
    assert tab.on_change_calls == 1


def test_splitter_card_choices_are_bus_compatible():
    design = connected_design()
    tab = bare_tab(SplittersTab, design)
    lx = next(item for item in design.splitters if item.id == "710-LX500-1")
    kp = next(item for item in design.splitters if item.id == "710-KP-1")

    lx_outputs = tab._output_choices(lx)
    kp_outputs = tab._output_choices(kp)
    lx_inputs = tab._input_choices(lx)
    kp_inputs = tab._input_choices(kp)

    assert "RSP-1" in lx_outputs and "KEYPAD #2" not in lx_outputs
    assert "KEYPAD #2" in kp_outputs and "RSP-1" not in kp_outputs
    assert "From 710-KP-1" not in lx_inputs
    assert "From 710-LX500-1" not in kp_inputs


def test_splitter_card_choices_follow_declared_bus_for_legacy_lx_ids():
    bus_500 = Splitter(
        "710-LX500-1", "LX", inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
        outputs=["Spare"] * 3)
    bus_600 = Splitter(
        "710-LX600-1", "LX", inputs={"LX-Bus In": "600 BUS IN FROM XR/550"},
        outputs=["Spare"] * 3)
    legacy_child = Splitter(
        "LX-710-2", "LX", inputs={"LX-Bus In": "From 710-LX600-1"},
        outputs=["Spare"] * 3)
    design = DMPDesign(splitters=[bus_500, bus_600, legacy_child])
    tab = bare_tab(SplittersTab, design)

    choices = tab._input_choices(legacy_child)

    assert choices[0] == "600 BUS IN FROM XR/550"
    assert "From 710-LX600-1" in choices
    assert "From 710-LX500-1" not in choices


def test_keypad_card_source_reconnects_graph_and_preserves_metadata():
    design = connected_design()
    tab = bare_tab(KeypadsTab, design)
    keypad = next(item for item in design.keypads if item.number == 2)
    before = next(edge for edge in design.connections
                  if edge.target.device_id == "KEYPAD-2")

    tab._set_source(keypad, "710-KP-2")

    after = next(edge for edge in design.connections if edge.id == before.id)
    assert after.source.device_id == "710-KP-2"
    assert (after.cable_type, after.status, after.quantity, after.custom_label) == (
        "KP-CUSTOM", "existing", 2, "KEEP KP")
    assert keypad.source == "710-KP-2"
    assert tab.on_change_calls == 1


def test_hardware_removal_prunes_incident_graph_edges_before_refresh():
    design = connected_design()
    frame = object.__new__(EditorFrame)
    frame.session = Session(design=design)
    frame.mark_dirty = lambda: None
    frame.refresh_validation = lambda: None
    frame.refresh_all_tabs = lambda: None
    frame._report_structure_change = lambda _changes: None

    frame.apply_hardware_change(lambda: remove_expander(design, 2))

    assert all("RSP-2" not in {edge.source.device_id, edge.target.device_id}
               for edge in design.connections)


def test_splitter_renumber_preserves_graph_edge_ids_routes_and_geometry():
    design = connected_design()
    design.riser_document = layout_riser(design)
    before_edges = {
        edge.id: (edge.cable_type, edge.status, edge.quantity, edge.custom_label)
        for edge in design.connections
        if "710-LX500-2" in {edge.source.device_id, edge.target.device_id}
    }
    old_element = design.riser_document.elements["device:710-LX500-2"]
    old_geometry = (old_element.x, old_element.y, old_element.width, old_element.height)

    renumber_splitter(design, "710-LX500-2", 4)

    assert "device:710-LX500-2" not in design.riser_document.elements
    renamed = design.riser_document.elements["device:710-LX500-4"]
    assert (renamed.x, renamed.y, renamed.width, renamed.height) == old_geometry
    assert set(before_edges) <= set(design.riser_document.routes)
    for edge_id, metadata in before_edges.items():
        edge = next(item for item in design.connections if item.id == edge_id)
        assert "710-LX500-2" not in {edge.source.device_id, edge.target.device_id}
        assert "710-LX500-4" in {edge.source.device_id, edge.target.device_id}
        assert (edge.cable_type, edge.status, edge.quantity, edge.custom_label) == metadata


def test_external_reconnect_reattaches_existing_manual_route_to_new_endpoint():
    design = connected_design()
    document = layout_riser(design)
    design.riser_document = document
    edge = next(item for item in design.connections
                if item.source.device_id == "710-LX500-1"
                and item.source.port_id == "OUT1")
    route = document.routes[edge.id]
    route.points = [route.points[0], (route.points[0][0], 900),
                    (route.points[-1][0], 900), route.points[-1]]
    route.manual = True

    set_splitter_output(design, "710-LX500-1", 0, "RSP-3")
    sync_riser_document(design, document)

    updated = next(item for item in design.connections if item.id == edge.id)
    target = document.elements["device:RSP-3"]
    assert document.routes[edge.id].manual
    assert document.routes[edge.id].points[-1] == port_point(
        target, updated.target.port_id, output=False)
