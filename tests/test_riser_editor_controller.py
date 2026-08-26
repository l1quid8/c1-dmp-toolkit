"""Display-independent direct editing and undo/redo behavior."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from riser_editor import RiserEditorController  # noqa: E402
from riser_model import DevicePortRef, RiserAnnotation  # noqa: E402
from riser_scene import layout_riser  # noqa: E402
from topology_service import TopologyError  # noqa: E402
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
    assert editor.document.routes[edge_id].points[1] == (321, 456)
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


def test_reconnect_updates_graph_and_undo_restores_endpoint():
    design, editor = controller()
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")

    editor.reconnect(edge.id, target=DevicePortRef("RSP-2", "IN"), replace_target=True)
    assert next(c for c in design.connections if c.id == edge.id).target.device_id == "RSP-2"

    editor.undo()
    assert next(c for c in design.connections if c.id == edge.id).target.device_id == "RSP-1"
    editor.redo()
    assert next(c for c in design.connections if c.id == edge.id).target.device_id == "RSP-2"


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
