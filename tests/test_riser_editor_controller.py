"""Display-independent direct editing and undo/redo behavior."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from riser_editor import RiserEditorController  # noqa: E402
from riser_model import DevicePortRef, RiserAnnotation  # noqa: E402
from hardware import remove_expander  # noqa: E402
from parse_dmp_worksheet import RSP  # noqa: E402
from riser_scene import layout_riser, port_point, validate_riser  # noqa: E402
from topology_service import (  # noqa: E402
    TopologyError,
    prune_unknown_connections,
    set_splitter_output,
)
from test_riser_scene import branched_design  # noqa: E402


def controller():
    design = branched_design()
    document = layout_riser(design)
    design.riser_document = document
    return design, RiserEditorController(design, document)


def test_move_resize_and_route_edits_are_undoable():
    design, editor = controller()
    element = editor.document.elements["device:RSP-1"]
    original = (element.x, element.y, element.width, element.height)
    edge_id = next(iter(editor.document.routes))
    original_point = editor.document.routes[edge_id].points[1]

    editor.move_element("device:RSP-1", 18, 36)
    editor.resize_element("device:RSP-1", 240, 120)
    editor.move_route_point(edge_id, 1, (321, 456))

    assert editor.document.elements["device:RSP-1"].manual
    edited_points = editor.document.routes[edge_id].points
    assert edited_points[1] != original_point
    assert all(a[0] == b[0] or a[1] == b[1]
               for a, b in zip(edited_points, edited_points[1:]))
    editor.undo()
    assert editor.document.routes[edge_id].points[1] == original_point
    editor.undo()
    editor.undo()
    element = editor.document.elements["device:RSP-1"]
    assert (element.x, element.y, element.width, element.height) == original
    editor.redo()
    assert editor.document.elements["device:RSP-1"].x == original[0] + 18


def test_moving_device_keeps_connected_route_attached_to_its_port():
    design, editor = controller()
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")
    before = editor.document.routes[edge.id].points[-1]

    editor.move_element("device:RSP-1", 36, 54)

    after = editor.document.routes[edge.id].points[-1]
    assert after != before
    assert after[0] == before[0] + 36
    assert after[1] == before[1] + 54


def test_moving_device_preserves_clear_generated_cable_label_position():
    design, editor = controller()
    edge = next(c for c in design.connections
                if c.source == DevicePortRef("MSP", "LX500"))
    before = editor.document.routes[edge.id].label_offset

    editor.move_element("device:710-LX500-1", 36.0, 54.0)

    assert editor.document.routes[edge.id].label_offset == before
    assert not [
        issue for issue in validate_riser(design, editor.document)
        if issue.code == "scene.label_overlap" and issue.ref == edge.id
    ]


def test_reconnect_updates_graph_and_undo_restores_endpoint():
    design, editor = controller()
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")

    editor.reconnect(edge.id, target=DevicePortRef("RSP-2", "IN"), replace_target=True)
    assert next(c for c in design.connections if c.id == edge.id).target.device_id == "RSP-2"

    editor.undo()
    assert next(c for c in design.connections if c.id == edge.id).target.device_id == "RSP-1"
    editor.redo()
    assert next(c for c in design.connections if c.id == edge.id).target.device_id == "RSP-2"


def test_reconnect_preserves_manual_route_and_label_position():
    design, editor = controller()
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")
    route = editor.document.routes[edge.id]
    route.points = [route.points[0], (route.points[0][0], 900.0),
                    (route.points[-1][0], 900.0), route.points[-1]]
    route.manual = True
    route.label_offset = (42.0, -18.0)
    retained_bend = route.points[1]

    editor.reconnect(
        edge.id,
        target=DevicePortRef("RSP-2", "IN"),
        replace_target=True,
    )

    updated = editor.document.routes[edge.id]
    assert updated.manual
    assert updated.label_offset == (42.0, -18.0)
    assert updated.points[1] == retained_bend
    assert updated.points[-1] == port_point(
        editor.document.elements["device:RSP-2"], "IN", output=False)


def test_moving_source_of_two_point_manual_route_keeps_target_attached():
    design, editor = controller()
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")
    source = editor.document.elements[f"device:{edge.source.device_id}"]
    target = editor.document.elements["device:RSP-1"]
    source_point = port_point(source, edge.source.port_id, output=True)
    target.x = source_point[0] - target.width / 2
    target_point = port_point(target, edge.target.port_id, output=False)
    route = editor.document.routes[edge.id]
    route.points = [source_point, target_point]
    route.manual = True

    editor.move_element(source.id, 36.0, 18.0)

    updated = editor.document.routes[edge.id]
    assert updated.points[-1] == target_point
    assert len(updated.points) == 3
    assert all(a[0] == b[0] or a[1] == b[1]
               for a, b in zip(updated.points, updated.points[1:]))


def test_markup_add_delete_duplicate_and_undo():
    _design, editor = controller()
    note = RiserAnnotation("note-1", "text", [(100, 100)], text="FIELD NOTE")

    editor.add_annotation(note)
    duplicate = editor.duplicate_annotation("note-1", offset=(18, 18))
    editor.delete_annotation("note-1")

    assert [a.id for a in editor.document.annotations] == [duplicate.id]
    editor.undo()
    assert {a.id for a in editor.document.annotations} == {"note-1", duplicate.id}


def test_relayout_is_undoable_and_keeps_title_and_markup():
    design, editor = controller()
    editor.document.title_block.drawn_by = "TC"
    editor.document.annotations.append(
        RiserAnnotation("note-1", "text", [(100, 100)], text="KEEP"))
    editor.move_element("device:RSP-1", 500, 0)
    moved_x = editor.document.elements["device:RSP-1"].x

    editor.relayout()
    assert editor.document.elements["device:RSP-1"].x != moved_x
    assert editor.document.title_block.drawn_by == "TC"
    assert editor.document.annotations[0].text == "KEEP"
    editor.undo()
    assert editor.document.elements["device:RSP-1"].x == moved_x


def test_direct_create_delete_and_metadata_edits_are_undoable():
    design, editor = controller()
    editor.document.routes.clear()
    edge = editor.connect(
        DevicePortRef("710-KP-1", "OUT2"), DevicePortRef("KEYPAD-1", "IN"),
        replace_target=True)
    editor.update_connection(edge.id, cable_type="WP240AQC", status="existing",
                             quantity=2, custom_label="EXISTING TRUNK")
    editor.update_title_block(drawn_by="TC", checked_by="JD")

    assert next(c for c in design.connections if c.id == edge.id).custom_label == "EXISTING TRUNK"
    assert editor.document.title_block.drawn_by == "TC"
    editor.undo()
    assert editor.document.title_block.drawn_by == ""
    editor.undo()
    assert next(c for c in design.connections if c.id == edge.id).cable_type == "WP240R"
    editor.disconnect(edge.id)
    assert all(c.id != edge.id for c in design.connections)


def test_canvas_rewire_immediately_projects_to_legacy_splitter_cards_and_undo():
    design, editor = controller()
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")
    splitter = next(s for s in design.splitters if s.id == edge.source.device_id)
    output_index = int(edge.source.port_id.removeprefix("OUT")) - 1

    editor.reconnect(edge.id, target=DevicePortRef("RSP-2", "IN"), replace_target=True)
    assert splitter.outputs[output_index] == "RSP-2"

    editor.undo()
    assert splitter.outputs[output_index] == "RSP-1"


def test_failed_reconnect_restores_replaced_edge_and_does_not_add_undo_step():
    design, editor = controller()
    before = [(edge.id, edge.source, edge.target) for edge in design.connections]
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")

    try:
        editor.reconnect(
            edge.id, target=DevicePortRef("KEYPAD-1", "IN"), replace_target=True)
    except TopologyError:
        pass
    else:
        raise AssertionError("LX-to-keypad reconnect should be rejected")

    assert [(item.id, item.source, item.target) for item in design.connections] == before
    assert not editor.can_undo


def test_markup_properties_and_arrangement_are_undoable():
    _design, editor = controller()
    editor.add_annotation(RiserAnnotation("a", "rectangle", [(10, 10), (50, 50)]))
    editor.add_annotation(RiserAnnotation("b", "text", [(20, 20)], text="OLD"))

    editor.update_annotation("b", text="FIELD NOTE", stroke="#ff0000",
                             font_size=18, alignment="center")
    editor.arrange_annotation("a", "front")

    note = next(a for a in editor.document.annotations if a.id == "b")
    assert (note.text, note.stroke, note.font_size, note.alignment) == (
        "FIELD NOTE", "#ff0000", 18, "center")
    assert editor.document.z_order[-1] == "a"
    editor.undo()
    assert editor.document.z_order[-1] == "b"


def test_unplaced_device_can_be_placed_without_relaying_out_existing_geometry():
    design, editor = controller()
    removed = editor.document.elements.pop("device:RSP-2")
    editor.document.z_order.remove("device:RSP-2")
    editor.document.unplaced.append("RSP-2")
    existing_x = editor.document.elements["device:RSP-1"].x

    placed = editor.place_unplaced("RSP-2", 900, 1200)

    assert (placed.x, placed.y) == (900, 1200)
    assert "RSP-2" not in editor.document.unplaced
    assert editor.document.elements["device:RSP-1"].x == existing_x
    editor.undo()
    assert "device:RSP-2" not in editor.document.elements
    assert editor.document.unplaced == ["RSP-2"]


def test_auto_layout_places_new_items_but_preserves_existing_manual_geometry():
    design, editor = controller()
    editor.move_element("device:RSP-1", 333, 0)
    manual_x = editor.document.elements["device:RSP-1"].x
    editor.document.elements.pop("device:RSP-2")
    editor.document.z_order.remove("device:RSP-2")
    editor.document.unplaced.append("RSP-2")

    editor.auto_layout()

    assert editor.document.elements["device:RSP-1"].x == manual_x
    assert "device:RSP-2" in editor.document.elements
    assert "RSP-2" not in editor.document.unplaced


def test_auto_layout_attaches_new_routes_to_retained_manual_geometry():
    design, editor = controller()
    editor.move_element("location:CLASSROOM 27", 180, 54)
    location_before = editor.document.elements["location:CLASSROOM 27"]
    location_geometry = (
        location_before.x, location_before.y,
        location_before.width, location_before.height,
    )
    source_before = editor.document.elements["device:710-LX500-2"]
    source_geometry = (
        source_before.x, source_before.y,
        source_before.width, source_before.height,
    )
    existing_edge = next(
        edge for edge in design.connections
        if edge.source.device_id == "710-LX500-2"
        and edge.target.device_id == "RSP-2"
    )
    existing_route = editor.document.routes[existing_edge.id]
    existing_points = list(existing_route.points)
    assert existing_route.manual

    design.rsps.append(RSP(3, "GYM", [533]))
    new_edge = editor.connect(
        DevicePortRef("710-LX500-2", "OUT2"),
        DevicePortRef("RSP-3", "IN"),
    )
    assert "RSP-3" in editor.document.unplaced
    assert new_edge.id not in editor.document.routes

    editor.auto_layout()

    location_after = editor.document.elements["location:CLASSROOM 27"]
    assert (location_after.x, location_after.y,
            location_after.width, location_after.height) == location_geometry
    source_after = editor.document.elements["device:710-LX500-2"]
    assert (source_after.x, source_after.y,
            source_after.width, source_after.height) == source_geometry
    assert editor.document.routes[existing_edge.id].points == existing_points
    assert editor.document.routes[existing_edge.id].manual
    assert "RSP-3" not in editor.document.unplaced

    for edge in design.connections:
        route = editor.document.routes[edge.id]
        source = editor.document.elements[f"device:{edge.source.device_id}"]
        target = editor.document.elements[f"device:{edge.target.device_id}"]
        assert route.points[0] == port_point(
            source, edge.source.port_id, output=True)
        assert route.points[-1] == port_point(
            target, edge.target.port_id, output=False)


def test_relayout_preserves_markup_stacking_order():
    _design, editor = controller()
    editor.add_annotation(RiserAnnotation(
        "annotation-a", "rectangle", [(10, 10), (80, 80)]))
    editor.add_annotation(RiserAnnotation(
        "annotation-b", "ellipse", [(20, 20), (70, 70)]))
    editor.arrange_annotation("annotation-a", "front")
    editor.arrange_annotation("annotation-b", "back")
    stacking_before = list(editor.document.z_order)

    editor.relayout()

    assert editor.document.z_order == stacking_before


def test_moving_location_module_moves_its_devices_and_attached_routes_together():
    design, editor = controller()
    location = editor.document.elements["location:MDF"]
    member = editor.document.elements["device:RSP-1"]
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")
    route = editor.document.routes[edge.id]
    start, end = route.points[0], route.points[-1]
    route.points = [start, (start[0], 900), (end[0], 900), end]
    route.manual = True
    before_member = (member.x, member.y)
    before_endpoint = route.points[-1]
    before_route = list(route.points)

    editor.move_element(location.id, 36, 54)

    assert (member.x, member.y) == (before_member[0] + 36, before_member[1] + 54)
    assert editor.document.routes[edge.id].points[-1] == (
        before_endpoint[0] + 36, before_endpoint[1] + 54)
    assert editor.document.routes[edge.id].points == [
        (x + 36, y + 54) for x, y in before_route]


def test_location_move_keeps_member_after_device_leaves_and_box_shrinks():
    design, editor = controller()
    location = editor.document.elements["location:MDF"]
    member = editor.document.elements["device:RSP-1"]
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")

    editor.move_element(member.id, location.width + 180, 0)
    editor.resize_element(location.id, 90, 54)
    before_member = (member.x, member.y)
    before_endpoint = editor.document.routes[edge.id].points[-1]

    editor.move_element(location.id, 36, 54)

    assert (member.x, member.y) == (before_member[0] + 36, before_member[1] + 54)
    assert editor.document.routes[edge.id].points[-1] == (
        before_endpoint[0] + 36, before_endpoint[1] + 54)


def test_overlapping_location_box_moves_only_its_design_location_members():
    _design, editor = controller()
    mdf = editor.document.elements["location:MDF"]
    office = editor.document.elements["location:OFFICE"]
    mdf_device = editor.document.elements["device:RSP-1"]
    office_device = editor.document.elements["device:KEYPAD-2"]
    office.x, office.y = mdf_device.x - 10, mdf_device.y - 10
    office.width = mdf_device.width + 20
    office.height = mdf_device.height + 20
    before_mdf = (mdf_device.x, mdf_device.y)
    before_office = (office_device.x, office_device.y)

    editor.move_element(office.id, 36, 0)

    assert (mdf_device.x, mdf_device.y) == before_mdf
    assert (office_device.x, office_device.y) == (before_office[0] + 36,
                                                  before_office[1])

    editor.move_element(mdf.id, 18, 0)

    assert (mdf_device.x, mdf_device.y) == (before_mdf[0] + 18, before_mdf[1])


def test_hardware_location_edit_switches_group_without_moving_device_geometry():
    design, editor = controller()
    mdf = editor.document.elements["location:MDF"]
    office = editor.document.elements["location:OFFICE"]
    member = editor.document.elements["device:RSP-1"]
    before = (member.x, member.y)

    design.rsps[0].location = "OFFICE"
    editor.sync_from_design()
    assert (member.x, member.y) == before

    editor.move_element(mdf.id, 18, 0)
    assert (member.x, member.y) == before

    editor.move_element(office.id, 36, 0)
    assert (member.x, member.y) == (before[0] + 36, before[1])


def test_manual_route_bend_edit_remains_orthogonal_and_is_undoable():
    _design, editor = controller()
    edge_id = next(iter(editor.document.routes))
    before = list(editor.document.routes[edge_id].points)

    editor.move_route_point(edge_id, 1, (before[1][0] + 47, before[1][1] + 31))

    points = editor.document.routes[edge_id].points
    assert points != before
    assert all(a[0] == b[0] or a[1] == b[1]
               for a, b in zip(points, points[1:]))
    editor.undo()
    assert editor.document.routes[edge_id].points == before


def test_invalid_markup_color_is_rejected_without_poisoning_document():
    _design, editor = controller()
    editor.add_annotation(RiserAnnotation(
        "box", "rectangle", [(10, 10), (50, 50)], stroke="#111111"))

    try:
        editor.update_annotation("box", stroke="definitely-not-a-color")
    except ValueError as exc:
        assert "color" in str(exc).lower()
    else:
        raise AssertionError("Invalid canvas colors must be rejected")

    box = next(a for a in editor.document.annotations if a.id == "box")
    assert box.stroke == "#111111"


def test_connecting_existing_pair_is_a_noop_that_preserves_metadata_and_route():
    design, editor = controller()
    edge = design.connections[0]
    edge.cable_type = "WP240AQC"
    edge.status = "existing"
    edge.quantity = 3
    edge.custom_label = "KEEP ME"
    route_before = list(editor.document.routes[edge.id].points)

    returned = editor.connect(edge.source, edge.target, replace_target=True)

    assert returned is edge
    assert (edge.cable_type, edge.status, edge.quantity, edge.custom_label) == (
        "WP240AQC", "existing", 3, "KEEP ME")
    assert editor.document.routes[edge.id].points == route_before
    assert not editor.can_undo


def test_reconnecting_to_current_endpoint_preserves_manual_route_without_history():
    design, editor = controller()
    edge = design.connections[0]
    route = editor.document.routes[edge.id]
    route.points = [(100, 100), (100, 200), (300, 200), (300, 400)]
    route.manual = True

    returned = editor.reconnect(edge.id, target=edge.target)

    assert returned is edge
    assert editor.document.routes[edge.id].points == [
        (100, 100), (100, 200), (300, 200), (300, 400)]
    assert editor.document.routes[edge.id].manual
    assert not editor.can_undo


def test_sync_from_design_preserves_undo_and_redo_when_shared_state_is_unchanged():
    _design, editor = controller()
    element = editor.document.elements["device:MSP"]
    original_x = element.x
    editor.move_element(element.id, 18, 0)

    editor.sync_from_design()

    assert editor.can_undo
    editor.undo()
    assert editor.document.elements[element.id].x == original_x
    assert editor.can_redo

    editor.sync_from_design()

    assert editor.can_redo
    editor.redo()
    assert editor.document.elements[element.id].x == original_x + 18


def test_sync_from_design_clears_history_after_external_topology_change():
    design, editor = controller()
    editor.move_element("device:MSP", 18, 0)

    set_splitter_output(design, "710-LX500-1", 0, "Spare")
    editor.sync_from_design()

    assert not editor.can_undo


def test_sync_from_design_clears_history_after_external_hardware_change():
    design, editor = controller()
    editor.move_element("device:MSP", 18, 0)

    remove_expander(design, 2)
    prune_unknown_connections(design)
    editor.sync_from_design()

    assert not editor.can_undo
    assert "device:RSP-2" not in editor.document.elements


def test_sync_from_design_clears_history_after_external_legacy_field_change():
    design, editor = controller()
    editor.move_element("device:MSP", 18, 0)
    splitter = next(item for item in design.splitters
                    if item.id == "710-LX500-2")

    splitter.outputs[2] = "LEGACY RISER NOTE"
    editor.sync_from_design()

    assert not editor.can_undo
    assert splitter.outputs[2] == "LEGACY RISER NOTE"
