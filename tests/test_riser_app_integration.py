"""Production editor/app integration contract for the RISER surface."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from types import SimpleNamespace

import customtkinter as ctk
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from editor_frame import TAB_TITLES  # noqa: E402
from riser_editor import RiserTab  # noqa: E402
from riser_model import DevicePortRef, RiserAnnotation  # noqa: E402
from riser_scene import port_point  # noqa: E402
from session import Session  # noqa: E402
from test_riser_scene import branched_design  # noqa: E402
from topology_service import set_splitter_output  # noqa: E402


def _event_for_world(tab, point):
    """Build the widget-relative event coordinates used by canvas handlers."""
    canvas_x, canvas_y = tab._xy(point)
    return SimpleNamespace(
        x=canvas_x - tab.canvas.canvasx(0),
        y=canvas_y - tab.canvas.canvasy(0),
    )


def test_riser_is_a_normal_production_tab():
    assert TAB_TITLES[-1] == "RISER"


def test_connecting_to_occupied_port_is_rejected_in_editor(monkeypatch):
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        design = branched_design()
        tab = RiserTab(root, Session(design=design), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        monkeypatch.setattr(messagebox, "askyesno", lambda *args, **kwargs: False)
        warnings = []
        monkeypatch.setattr(messagebox, "showwarning", lambda *args, **kwargs: warnings.append(args))

        target = DevicePortRef("RSP-2", "IN")
        tab._connect_source = DevicePortRef("710-LX500-1", "OUT3")
        tab._connect_click((target, False))

        assert sum(edge.target == target for edge in design.connections) == 1
        assert warnings and "occupied" in warnings[0][1]
    finally:
        root.destroy()


def test_app_exposes_revisioned_riser_generation_action():
    source = (ROOT / "scripts" / "app.py").read_text(encoding="utf-8")
    assert "def _generate_riser(self):" in source
    assert "generate_riser_bundle" in source
    assert 'on_generate_riser=self._generate_riser' in source


def test_riser_toolbar_stays_compact_and_initial_sheet_width_fits_canvas():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        root.geometry("1600x850")
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        root.update_idletasks()

        assert tab.toolbar.winfo_height() == 84
        assert tab.canvas.winfo_height() > 610
        left, top, right, bottom = tab.canvas.bbox("page")
        assert left >= 20 and top >= 20
        assert right <= tab.canvas.winfo_width()
        assert bottom > tab.canvas.winfo_height()
    finally:
        root.destroy()


def test_riser_toolbar_keeps_every_tool_visible_at_narrow_supported_width():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        root.geometry("900x700")
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        root.update_idletasks()

        toolbar_width = tab.toolbar.winfo_width()
        assert all(button.winfo_ismapped() for button in tab._tool_buttons.values())
        assert all(0 <= button.winfo_x()
                   and button.winfo_x() + button.winfo_width() <= toolbar_width
                   for button in tab._tool_buttons.values())
    finally:
        root.destroy()


def test_hit_testing_prefers_visually_topmost_route_over_location_frame():
    tags_top_to_bottom = [
        "route|edge-1", "selectable",
        "element|location:MDF", "selectable",
    ]

    assert RiserTab._parse_selectable(None, tags_top_to_bottom) == (
        "route", "edge-1")


def test_undoing_new_markup_clears_stale_selection_without_crashing():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        note = RiserAnnotation("new-note", "text", [(100, 100)], text="NOTE")
        tab.controller.add_annotation(note)
        tab.selected = ("annotation", note.id)
        tab.redraw()

        tab.undo()

        assert tab.selected is None
        assert all(a.id != note.id for a in tab.controller.document.annotations)
    finally:
        root.destroy()


def test_fit_mode_refits_sheet_after_canvas_is_resized():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        root.geometry("1200x760")
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        root.update_idletasks()

        # Full-sheet fit is an explicit mode.  Once requested it should remain
        # active as the containing application window changes size.
        tab.fit_to_view()

        root.geometry("900x620")
        root.update()
        root.update_idletasks()

        left, top, right, bottom = tab.canvas.bbox("page")
        assert left >= 20 and top >= 20
        assert right <= tab.canvas.winfo_width()
        assert bottom <= tab.canvas.winfo_height()
    finally:
        root.destroy()


def test_initial_view_keeps_the_drawing_readable_in_a_short_workspace():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        root.geometry("1400x650")
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        root.update_idletasks()

        assert tab.zoom >= 0.35
        assert tab._fit_mode is False

        locations = [element for element in tab.controller.document.elements.values()
                     if element.kind == "location"]
        top = min(element.y for element in locations)
        bottom = max(element.y + element.height for element in locations)
        origin_y = tab._page_origin()[1]
        viewport_top = tab.canvas.canvasy(0)
        screen_top = origin_y + top * tab.zoom - viewport_top
        screen_bottom = origin_y + bottom * tab.zoom - viewport_top
        assert screen_top >= 0
        assert screen_bottom <= tab.canvas.winfo_height()
    finally:
        root.destroy()


def test_hidden_layers_do_not_leave_selection_handles_floating():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()

        tab.selected = ("element", "device:MSP")
        tab.layer_visibility["Devices"] = False
        tab.redraw()
        assert not tab.canvas.find_withtag("resize-handle")

        edge_id = next(iter(tab.controller.document.routes))
        tab.selected = ("route", edge_id)
        tab.layer_visibility["Cables"] = False
        tab.redraw()
        assert not any(tag.startswith("route-handle|")
                       for item in tab.canvas.find_all()
                       for tag in tab.canvas.gettags(item))
    finally:
        root.destroy()


def test_stationary_selection_click_does_not_mutate_or_mark_dirty():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    edits = []
    try:
        tab = RiserTab(
            root, Session(design=branched_design()), lambda: edits.append("edit"))
        tab.pack(fill="both", expand=True)
        root.update()

        element = tab.controller.document.elements["device:RSP-2"]
        click = _event_for_world(tab, (element.x + 20, element.y + 20))
        before = (element.x, element.y, element.manual)

        tab._on_press(click)
        tab._on_release(click)

        assert tab.selected == ("element", element.id)
        assert (element.x, element.y, element.manual) == before
        assert not tab.controller.can_undo
        assert edits == []
    finally:
        root.destroy()


def test_external_topology_refresh_clears_stale_riser_undo_history():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        design = branched_design()
        tab = RiserTab(root, Session(design=design), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()

        # Create presentation-only RISER history, then make a canonical wiring
        # change through the same legacy projection path used by SPLITTERS.
        tab.controller.move_element("device:MSP", 18, 0)
        assert tab.controller.can_undo
        removed = next(
            edge for edge in design.connections
            if edge.source.device_id.startswith("710-")
            and edge.source.port_id.startswith("OUT"))
        splitter = next(
            item for item in design.splitters
            if item.id == removed.source.device_id)
        output_index = int(removed.source.port_id.removeprefix("OUT")) - 1
        set_splitter_output(design, splitter.id, output_index, "Spare")
        assert all(edge.id != removed.id for edge in design.connections)

        tab.refresh()

        assert not tab.controller.can_undo
        tab.undo()
        assert all(edge.id != removed.id for edge in design.connections)
        assert splitter.outputs[output_index] == "Spare"
    finally:
        root.destroy()


def test_every_msp_port_has_a_distinct_exact_center_hit_target():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        root.geometry("1200x760")
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        tab.set_tool("Connect")
        tab.fit_to_view()
        root.update()

        msp = tab.controller.document.elements["device:MSP"]
        centers = {}
        collisions = []
        mismatches = []
        for ref, is_output in tab._ports_for("MSP"):
            point = port_point(msp, ref.port_id, output=is_output)
            if point in centers:
                collisions.append((centers[point], ref.port_id, point))
            centers[point] = ref.port_id
            parsed = tab._parse_port(
                tab._tags_at_event(_event_for_world(tab, point)))
            if parsed != (ref, is_output):
                mismatches.append((ref.port_id, parsed[0].port_id if parsed else None))

        assert not collisions and not mismatches, (
            f"coordinate collisions={collisions}; center-hit mismatches={mismatches}")
    finally:
        root.destroy()


def test_canvas_draws_vertical_hop_for_independent_endpoint_contact():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        horizontal, vertical = list(tab.controller.document.routes)[:2]
        tab.controller.document.routes[horizontal].points = [
            (100.0, 200.0), (300.0, 200.0)]
        tab.controller.document.routes[vertical].points = [
            (300.0, 100.0), (300.0, 400.0)]

        tab.redraw()

        crossing_x, _ = tab._xy((300.0, 200.0))
        curves = [tab.canvas.coords(item) for item in tab.canvas.find_all()
                  if tab.canvas.type(item) == "line"
                  and f"route|{vertical}" in tab.canvas.gettags(item)
                  and len(tab.canvas.coords(item)) > 4]
        assert curves and max(curves[0][::2]) > crossing_x
        assert curves[0][0] == pytest.approx(crossing_x)
        assert curves[0][-2] == pytest.approx(crossing_x)
    finally:
        root.destroy()


def test_overview_hides_secondary_symbol_detail_but_connect_reveals_ports():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        design = branched_design()
        tab = RiserTab(root, Session(design=design), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        tab._set_zoom(0.40)

        visible_text = {
            tab.canvas.itemcget(item, "text")
            for item in tab.canvas.find_all()
            if tab.canvas.type(item) == "text"
        }
        assert "RSP-1" in visible_text
        assert "REMOTE POWER SUPPLY" not in visible_text
        assert "LX800" not in visible_text

        used_msp_ports = {edge.source.port_id for edge in design.connections
                          if edge.source.device_id == "MSP"}
        assert {ref.port_id for ref, _output in tab._ports_for("MSP")} == used_msp_ports

        tab.set_tool("Connect")
        assert {ref.port_id for ref, _output in tab._ports_for("MSP")} == {
            "KP BUS", "PROG", "LX500", "LX600", "LX700", "LX800", "LX900"
        }
    finally:
        root.destroy()


def test_duplicate_shortcut_stops_before_global_app_binding():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    propagated = []
    try:
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        note = RiserAnnotation("note", "text", [(100, 100)], text="NOTE")
        tab.controller.add_annotation(note)
        tab.selected = ("annotation", note.id)
        tab.redraw()
        root.bind_all(
            "<Control-d>", lambda _event: propagated.append("global"), add="+")
        tab.canvas.focus_force()
        root.update()

        tab.canvas.event_generate("<Control-d>", when="tail")
        root.update()

        assert len(tab.controller.document.annotations) == 2
        assert propagated == []
    finally:
        root.destroy()


def test_selected_markup_can_be_dragged_and_undone_as_one_edit():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    edits = []
    try:
        tab = RiserTab(
            root, Session(design=branched_design()), lambda: edits.append("edit"))
        tab.pack(fill="both", expand=True)
        root.update()
        note = RiserAnnotation(
            "drag-box", "rectangle", [(100, 100), (220, 180)])
        tab.controller.add_annotation(note)
        tab.controller.clear_history()
        edits.clear()

        start = _event_for_world(tab, (150, 140))
        end = _event_for_world(tab, (186, 194))
        tab._on_press(start)
        tab._on_drag(end)
        tab._on_release(end)

        moved = next(item for item in tab.controller.document.annotations
                     if item.id == note.id)
        assert moved.points == [(136, 154), (256, 234)]
        assert edits == ["edit"]
        tab.undo()
        restored = next(item for item in tab.controller.document.annotations
                        if item.id == note.id)
        assert restored.points == [(100, 100), (220, 180)]
    finally:
        root.destroy()


def test_polyline_tool_keeps_all_clicked_vertices():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        tab.set_tool("Polyline")

        for point in ((90, 90), (180, 90), (180, 198), (306, 198)):
            event = _event_for_world(tab, point)
            tab._on_press(event)
            tab._on_release(event)
        tab._finish_polyline()

        annotation = tab.controller.document.annotations[-1]
        assert annotation.kind == "polyline"
        assert annotation.points == [
            (90, 90), (180, 90), (180, 198), (306, 198)]
    finally:
        root.destroy()


def test_title_validation_jump_focuses_the_matching_title_field():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()

        tab.goto_issue(SimpleNamespace(ref="school_name"))
        root.update()

        assert root.focus_get() == tab._title_entries["school_name"]._entry
    finally:
        root.destroy()
