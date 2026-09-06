"""Direct riser editing controller and the CustomTkinter RISER workspace."""

from __future__ import annotations

import copy
import contextlib
import re
import tkinter as tk
import uuid
from dataclasses import fields
from tkinter import messagebox, simpledialog

import customtkinter as ctk

import theme
from riser_model import DevicePortRef, RiserAnnotation, RiserDocument
from riser_scene import (
    BRIDGE_HALF_WIDTH,
    GRID,
    TITLE_BLOCK_WIDTH,
    device_location_element_ids,
    find_bridges,
    layout_riser,
    port_point,
    reattach_route_endpoint,
    reserved_route_segments,
    route_label_point,
    route_topology_connection,
    sync_riser_document,
    validate_riser,
)
from topology_service import TopologyError
from topology_service import connect as topology_connect
from topology_service import disconnect as topology_disconnect
from topology_service import reconnect as topology_reconnect
from topology_service import project_legacy_topology


class RiserEditorController:
    """Display-independent command controller used by the Tk canvas."""

    def __init__(self, design, document: RiserDocument):
        self.design = design
        self.document = document
        self.design.riser_document = document
        self._undo: list[tuple[tuple, tuple]] = []
        self._redo: list[tuple[tuple, tuple]] = []
        self._observed_shared_state = self._shared_state()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _snapshot(self):
        return (copy.deepcopy(self.design.connections), copy.deepcopy(self.document))

    def _shared_state(self):
        """State an old riser snapshot could overwrite or regroup."""
        return copy.deepcopy((
            self.design.connections,
            [
                (item.id, item.splitter_type, item.location,
                 item.inputs, item.outputs)
                for item in self.design.splitters
            ],
            [
                (item.number, item.location, item.model)
                for item in self.design.rsps
            ],
            [
                (item.number, item.source, item.location, item.global_keypad)
                for item in self.design.keypads
            ],
            self.design.site_info.xr550_location,
            self.document,
        ))

    def _rebase_observed_state(self) -> None:
        self._observed_shared_state = self._shared_state()

    def _restore(self, snapshot) -> None:
        connections, document = copy.deepcopy(snapshot)
        self.design.connections = connections
        for field in fields(RiserDocument):
            setattr(self.document, field.name, getattr(document, field.name))
        self.design.riser_document = self.document
        project_legacy_topology(self.design)
        self._rebase_observed_state()

    def _mutate(self, operation):
        before = self._snapshot()
        try:
            result = operation()
        except Exception:
            self._restore(before)
            raise
        after = self._snapshot()
        if before != after:
            self._undo.append((before, after))
            self._redo.clear()
        self._rebase_observed_state()
        return result

    def clear_history(self) -> None:
        """Rebase undo/redo after another editor changes shared topology."""
        self._undo.clear()
        self._redo.clear()
        self._rebase_observed_state()

    def sync_from_design(self) -> bool:
        """Synchronize external edits and invalidate only stale history."""
        changed_externally = self._shared_state() != self._observed_shared_state
        if changed_externally:
            self._undo.clear()
            self._redo.clear()
        sync_riser_document(self.design, self.document)
        self._rebase_observed_state()
        return changed_externally

    def undo(self) -> bool:
        if not self._undo:
            return False
        before, after = self._undo.pop()
        self._restore(before)
        self._redo.append((before, after))
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        before, after = self._redo.pop()
        self._restore(after)
        self._undo.append((before, after))
        return True

    def move_element(self, element_id: str, dx: float, dy: float) -> bool:
        if not dx and not dy:
            return False

        def operation():
            element = self.document.elements[element_id]
            members = []
            shared_routes = {}
            if element.kind == "location":
                location_ids = device_location_element_ids(self.design)
                members = [
                    candidate for candidate in self.document.elements.values()
                    if candidate.kind == "device"
                    and location_ids.get(candidate.ref) == element.id
                ]
                member_refs = {member.ref for member in members}
                shared_routes = {
                    edge.id: list(self.document.routes[edge.id].points)
                    for edge in self.design.connections
                    if edge.source.device_id in member_refs
                    and edge.target.device_id in member_refs
                    and edge.id in self.document.routes
                }
            element.x += dx
            element.y += dy
            element.manual = True
            if element.kind == "device":
                self._reattach_routes(element.ref)
            else:
                for member in members:
                    member.x += dx
                    member.y += dy
                    member.manual = True
                for member in members:
                    self._reattach_routes(member.ref)
                for route_id, points in shared_routes.items():
                    route = self.document.routes.get(route_id)
                    if route is not None:
                        route.points = [(x + dx, y + dy) for x, y in points]
                        route.manual = True
        self._mutate(operation)
        return True

    def resize_element(self, element_id: str, width: float, height: float) -> bool:
        element = self.document.elements[element_id]
        minimum_height = 54.0 if element.kind == "location" else 28.0
        width, height = max(36.0, width), max(minimum_height, height)
        if element.width == width and element.height == height:
            return False

        def operation():
            element = self.document.elements[element_id]
            element.width = width
            element.height = height
            element.manual = True
            if element.kind == "device":
                self._reattach_routes(element.ref)
        self._mutate(operation)
        return True

    def _reattach_routes(self, device_id: str) -> None:
        for edge in self.design.connections:
            if device_id not in {edge.source.device_id, edge.target.device_id}:
                continue
            route = self.document.routes.get(edge.id)
            if route is None or not route.manual or len(route.points) < 2:
                self._reroute(edge.id)
                continue
            if edge.source.device_id == device_id:
                element = self.document.elements[f"device:{device_id}"]
                new = port_point(element, edge.source.port_id, output=True)
                route.points = reattach_route_endpoint(
                    route.points, new, at_start=True)
            if edge.target.device_id == device_id:
                element = self.document.elements[f"device:{device_id}"]
                new = port_point(element, edge.target.port_id, output=False)
                route.points = reattach_route_endpoint(
                    route.points, new, at_start=False)

    def move_route_point(self, connection_id: str, index: int,
                         point: tuple[float, float]) -> bool:
        route = self.document.routes[connection_id]
        if not 0 < index < len(route.points) - 1:
            raise ValueError("Only interior route bends can be moved directly")
        if route.points[index] == tuple(point):
            return False

        def operation():
            route = self.document.routes[connection_id]
            route.points = self.adjusted_route_points(route.points, index, point)
            route.manual = True
        self._mutate(operation)
        return True

    @staticmethod
    def adjusted_route_points(points, index, point):
        points = list(points)
        previous, old, following = points[index - 1:index + 2]
        x, y = point

        # A single L-shaped bend has no free two-dimensional corner while
        # both electrical endpoints stay fixed. Convert it to a dogleg so
        # the dragged bend can move and every segment remains orthogonal.
        if len(points) == 3:
            if previous[0] == old[0]:
                points = [previous, (previous[0], y), (following[0], y), following]
            else:
                points = [previous, (x, previous[1]), (x, following[1]), following]
            return [candidate for offset, candidate in enumerate(points)
                    if offset == 0 or candidate != points[offset - 1]]

        # Keep cable endpoints fixed. A bend beside an endpoint is constrained
        # to that endpoint's axis and moves the neighboring segment.
        if index == 1:
            if previous[0] == old[0]:
                x = previous[0]
            else:
                y = previous[1]
        elif previous[0] == old[0]:
            points[index - 1] = (x, previous[1])
        else:
            points[index - 1] = (previous[0], y)

        if index == len(points) - 2:
            if following[0] == old[0]:
                x = following[0]
            else:
                y = following[1]
        elif following[0] == old[0]:
            points[index + 1] = (x, following[1])
        else:
            points[index + 1] = (following[0], y)

        points[index] = (x, y)
        return [candidate for offset, candidate in enumerate(points)
                if offset == 0 or candidate != points[offset - 1]]

    def reconnect(self, connection_id: str, *, source: DevicePortRef | None = None,
                  target: DevicePortRef | None = None,
                  replace_target: bool = False):
        current = next(c for c in self.design.connections if c.id == connection_id)
        if ((source is None or source == current.source)
                and (target is None or target == current.target)):
            return current

        def operation():
            if replace_target and target is not None:
                occupied = next((edge for edge in self.design.connections
                                 if edge.id != connection_id and edge.target == target), None)
                if occupied:
                    topology_disconnect(self.design, occupied.id)
                    self.document.routes.pop(occupied.id, None)
            edge = topology_reconnect(self.design, connection_id, source=source, target=target)
            project_legacy_topology(self.design)
            sync_riser_document(self.design, self.document)
            return edge
        return self._mutate(operation)

    def connect(self, source: DevicePortRef, target: DevicePortRef, *,
                replace_target: bool = False):
        existing = next((edge for edge in self.design.connections
                         if edge.source == source and edge.target == target), None)
        if existing is not None:
            return existing

        def operation():
            if replace_target:
                occupied = next((edge for edge in self.design.connections
                                 if edge.target == target), None)
                if occupied:
                    topology_disconnect(self.design, occupied.id)
                    self.document.routes.pop(occupied.id, None)
            edge = topology_connect(self.design, source, target)
            project_legacy_topology(self.design)
            sync_riser_document(self.design, self.document)
            return edge
        return self._mutate(operation)

    def disconnect(self, connection_id: str) -> None:
        def operation():
            topology_disconnect(self.design, connection_id)
            project_legacy_topology(self.design)
            self.document.routes.pop(connection_id, None)
        self._mutate(operation)

    def update_connection(self, connection_id: str, **changes) -> None:
        allowed = {"cable_type", "status", "quantity", "custom_label"}
        if set(changes) - allowed:
            raise ValueError("Unsupported connection metadata")

        def operation():
            edge = next(c for c in self.design.connections if c.id == connection_id)
            if "status" in changes and changes["status"] not in {"new", "existing"}:
                raise ValueError("Cable status must be new or existing")
            if "quantity" in changes and int(changes["quantity"]) < 1:
                raise ValueError("Cable quantity must be at least 1")
            for name, value in changes.items():
                if name == "quantity":
                    value = int(value)
                if name == "custom_label":
                    value = (value or "").strip() or None
                setattr(edge, name, value)
        self._mutate(operation)

    def update_title_block(self, **changes) -> None:
        allowed = {field.name for field in fields(type(self.document.title_block))}
        if set(changes) - allowed:
            raise ValueError("Unsupported title-block field")

        def operation():
            for name, value in changes.items():
                setattr(self.document.title_block, name, value)
        self._mutate(operation)

    def _reroute(self, connection_id: str) -> None:
        edge = next((c for c in self.design.connections if c.id == connection_id), None)
        if edge is None:
            self.document.routes.pop(connection_id, None)
            return
        source = self.document.elements.get(f"device:{edge.source.device_id}")
        target = self.document.elements.get(f"device:{edge.target.device_id}")
        if source is None or target is None:
            self.document.routes.pop(connection_id, None)
            return
        obstacles = [e for e in self.document.elements.values() if e.kind == "device"]
        points = route_topology_connection(
            edge,
            source,
            target,
            obstacles,
            reserved_segments=reserved_route_segments(
                self.document,
                exclude_id=connection_id,
                live_ids={connection.id for connection in self.design.connections},
            ),
        )
        from riser_model import RiserRoute
        previous = self.document.routes.get(connection_id)
        label_offset = previous.label_offset if previous is not None else (0.0, 0.0)
        self.document.routes[connection_id] = RiserRoute(
            connection_id, points, label_offset=label_offset)

    def add_annotation(self, annotation: RiserAnnotation) -> None:
        def operation():
            if any(a.id == annotation.id for a in self.document.annotations):
                raise ValueError(f"Annotation already exists: {annotation.id}")
            self.document.annotations.append(copy.deepcopy(annotation))
            self.document.z_order.append(annotation.id)
        self._mutate(operation)

    def duplicate_annotation(self, annotation_id: str,
                             offset: tuple[float, float] = (18.0, 18.0)) -> RiserAnnotation:
        source = next(a for a in self.document.annotations if a.id == annotation_id)
        duplicate = copy.deepcopy(source)
        duplicate.id = f"{source.id}-copy-{uuid.uuid4().hex[:6]}"
        duplicate.points = [(x + offset[0], y + offset[1]) for x, y in duplicate.points]
        self.add_annotation(duplicate)
        return duplicate

    def delete_annotation(self, annotation_id: str) -> None:
        def operation():
            self.document.annotations = [a for a in self.document.annotations
                                         if a.id != annotation_id]
            self.document.z_order = [item for item in self.document.z_order
                                     if item != annotation_id]
        self._mutate(operation)

    def update_annotation(self, annotation_id: str, **changes) -> None:
        allowed = {field.name for field in fields(RiserAnnotation)} - {"id", "kind"}
        if set(changes) - allowed:
            raise ValueError("Unsupported annotation property")
        if "alignment" in changes and changes["alignment"] not in {"left", "center", "right"}:
            raise ValueError("Annotation alignment must be left, center, or right")
        if "font_weight" in changes and changes["font_weight"] not in {"normal", "bold"}:
            raise ValueError("Annotation font weight must be normal or bold")
        for name in ("stroke_width", "font_size"):
            if name in changes and float(changes[name]) <= 0:
                raise ValueError(f"{name.replace('_', ' ').title()} must be positive")
        for name in ("stroke", "fill"):
            value = changes.get(name)
            if value is None or (name == "fill" and value == ""):
                continue
            if not isinstance(value, str) or not re.fullmatch(
                    r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value):
                raise ValueError(f"{name.title()} color must be #RGB or #RRGGBB")

        def operation():
            annotation = next(a for a in self.document.annotations if a.id == annotation_id)
            for name, value in changes.items():
                if name in {"stroke_width", "font_size"}:
                    value = float(value)
                setattr(annotation, name, copy.deepcopy(value))
        self._mutate(operation)

    def arrange_annotation(self, annotation_id: str, where: str) -> None:
        if where not in {"front", "back", "forward", "backward"}:
            raise ValueError("Unsupported arrangement")

        def operation():
            order = self.document.z_order
            if annotation_id not in order:
                order.append(annotation_id)
            index = order.index(annotation_id)
            order.pop(index)
            if where == "front":
                order.append(annotation_id)
            elif where == "back":
                order.insert(0, annotation_id)
            elif where == "forward":
                order.insert(min(index + 1, len(order)), annotation_id)
            else:
                order.insert(max(index - 1, 0), annotation_id)
        self._mutate(operation)

    def place_unplaced(self, device_id: str, x: float, y: float):
        def operation():
            if device_id not in self.document.unplaced:
                raise ValueError(f"Device is not unplaced: {device_id}")
            template = layout_riser(self.design).elements[f"device:{device_id}"]
            template.x, template.y, template.manual = x, y, True
            self.document.elements[template.id] = template
            self.document.z_order.append(template.id)
            self.document.unplaced.remove(device_id)
            for edge in self.design.connections:
                if device_id in {edge.source.device_id, edge.target.device_id}:
                    self._reroute(edge.id)
            return template
        return self._mutate(operation)

    def relayout(self) -> None:
        def operation():
            replacement = layout_riser(self.design, title_source=self.document)
            for field in fields(RiserDocument):
                setattr(self.document, field.name, getattr(replacement, field.name))
            self.design.riser_document = self.document
        self._mutate(operation)

    def auto_layout(self) -> None:
        """Place only missing items while retaining every existing object/route."""
        def operation():
            replacement = layout_riser(self.design, title_source=self.document)
            for element_id, element in replacement.elements.items():
                if element_id not in self.document.elements:
                    self.document.elements[element_id] = element
                    self.document.z_order.append(element_id)
            for route_id in replacement.routes:
                if route_id not in self.document.routes:
                    # Replacement routes terminate at replacement geometry.
                    # Retained manual devices and locations may have moved, so
                    # route against the merged live scene instead of copying a
                    # route whose electrical endpoint is now detached.
                    self._reroute(route_id)
            placed = {element.ref for element in self.document.elements.values()
                      if element.kind == "device" and not element.stale}
            self.document.unplaced = [ref for ref in self.document.unplaced
                                      if ref not in placed]
        self._mutate(operation)


class RiserTab(ctk.CTkFrame):
    """Production riser drawing workspace backed by the shared topology graph."""

    TOOLS = ("Select", "Connect", "Route", "Text", "Polyline",
             "Rectangle", "Ellipse", "Arrow")

    def __init__(self, master, session, on_edit, on_generate=None):
        super().__init__(master, fg_color=theme.APP_BG, corner_radius=0)
        self.session = session
        self.design = session.design
        if self.design.riser_document is None:
            self.design.riser_document = layout_riser(self.design)
        elif not self.design.riser_document.elements:
            self.design.riser_document = layout_riser(
                self.design, title_source=self.design.riser_document)
        sync_riser_document(self.design, self.design.riser_document)
        self.controller = RiserEditorController(
            self.design, self.design.riser_document)
        self.on_edit = on_edit
        self.on_generate = on_generate or (lambda: None)
        self.tool = "Select"
        self.zoom = 0.42
        self.snap_enabled = True
        self.grid_enabled = True
        self.selected: tuple[str, str] | None = None
        self._gesture = None
        self._connect_source: DevicePortRef | None = None
        self._preview_item = None
        self._initial_fit_done = False
        self._fit_mode = False
        self._resize_after_id = None
        self._tool_buttons: dict[str, ctk.CTkButton] = {}
        self._title_vars: dict[str, tk.StringVar] = {}
        self._title_entries: dict[str, ctk.CTkEntry] = {}
        self.layer_visibility = {
            "Locations": True, "Cables": True, "Devices": True,
            "Markup": True, "Title block": True,
        }

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_toolbar()
        self._build_workspace()
        self._bind_keys()
        self.redraw()

    # ---- construction -------------------------------------------------

    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, fg_color=theme.CHROME, corner_radius=0,
                           height=84)
        self.toolbar = bar
        bar.grid(row=0, column=0, sticky="ew")
        bar.pack_propagate(False)

        tools_row = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=0,
                                 height=42)
        tools_row.pack(side="top", fill="x")
        tools_row.pack_propagate(False)
        for name in self.TOOLS:
            button = ctk.CTkButton(
                tools_row, text=name, width=max(58, len(name) * 8), height=28,
                corner_radius=theme.RADIUS["button"],
                fg_color=theme.ACCENT_TINT if name == self.tool else "transparent",
                hover_color=theme.HOVER_SUBTLE, text_color=theme.TEXT,
                command=lambda value=name: self.set_tool(value))
            button.pack(side="left", padx=(6 if name == "Select" else 1, 0), pady=7)
            self._tool_buttons[name] = button

        ctk.CTkFrame(tools_row, width=1, height=26, fg_color=theme.BORDER,
                     corner_radius=0).pack(side="left", padx=8, pady=8)
        for text, command in (("Duplicate", self.duplicate_selected),
                              ("Delete", self.delete_selected)):
            ctk.CTkButton(
                tools_row, text=text, width=72, height=28,
                fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                text_color=theme.TEXT, command=command).pack(side="left", padx=1)

        actions_row = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=0,
                                   height=42)
        actions_row.pack(side="top", fill="x")
        actions_row.pack_propagate(False)
        for text, command in (("Undo", self.undo), ("Redo", self.redo)):
            ctk.CTkButton(
                actions_row, text=text, width=68, height=28,
                fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                text_color=theme.TEXT, command=command).pack(
                    side="left", padx=(6 if text == "Undo" else 1, 0), pady=7)
        self._snap_switch = ctk.CTkSwitch(
            actions_row, text="Snap", width=66, command=self._toggle_snap,
            font=theme.ui_font(theme.SIZE["meta"]))
        self._snap_switch.select()
        self._snap_switch.pack(side="left", padx=(8, 2))
        self._grid_switch = ctk.CTkSwitch(
            actions_row, text="Grid", width=66, command=self._toggle_grid,
            font=theme.ui_font(theme.SIZE["meta"]))
        self._grid_switch.select()
        self._grid_switch.pack(side="left", padx=2)

        ctk.CTkButton(
            actions_row, text="Generate Riser", width=122, height=28,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            text_color="#ffffff", command=self.on_generate).pack(
                side="right", padx=(2, 8), pady=7)
        ctk.CTkButton(actions_row, text="Re-layout All", width=96, height=28,
                      fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                      text_color=theme.TEXT, command=self.relayout).pack(
                          side="right", padx=(0, 2), pady=7)
        ctk.CTkButton(actions_row, text="Auto-layout", width=90, height=28,
                      fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                      text_color=theme.TEXT, command=self.auto_layout).pack(
                          side="right", pady=7)
        self._zoom_label = ctk.CTkLabel(
            actions_row, text="42%", width=46, text_color=theme.TEXT_SECOND,
            font=theme.ui_font(theme.SIZE["meta"]))
        self._zoom_label.pack(side="right", padx=(2, 8))
        ctk.CTkButton(actions_row, text="+", width=28, height=28,
                      fg_color="transparent", text_color=theme.TEXT,
                      command=lambda: self.set_zoom(self.zoom * 1.2)).pack(
                          side="right", pady=7)
        ctk.CTkButton(actions_row, text="−", width=28, height=28,
                      fg_color="transparent", text_color=theme.TEXT,
                      command=lambda: self.set_zoom(self.zoom / 1.2)).pack(
                          side="right", pady=7)
        ctk.CTkButton(actions_row, text="Fit", width=42, height=28,
                      fg_color=theme.SURFACE_CHIP,
                      hover_color=theme.HOVER_SUBTLE, text_color=theme.TEXT,
                      command=self.fit_to_view).pack(
                          side="right", padx=(8, 2), pady=7)

    def _build_workspace(self):
        body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        canvas_wrap = ctk.CTkFrame(body, fg_color=theme.SURFACE_CHIP,
                                   corner_radius=0)
        canvas_wrap.grid(row=0, column=0, sticky="nsew")
        canvas_wrap.columnconfigure(0, weight=1)
        canvas_wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            canvas_wrap, bg=theme.resolve(theme.SURFACE_CHIP), highlightthickness=0,
            xscrollincrement=1, yscrollincrement=1)
        xbar = ctk.CTkScrollbar(canvas_wrap, orientation="horizontal",
                                command=self.canvas.xview)
        ybar = ctk.CTkScrollbar(canvas_wrap, orientation="vertical",
                                command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        xbar.grid(row=1, column=0, sticky="ew")
        ybar.grid(row=0, column=1, sticky="ns")

        self.inspector = ctk.CTkScrollableFrame(
            body, width=302, fg_color=theme.SURFACE, corner_radius=0,
            scrollbar_button_color=theme.BORDER_STRONG)
        self.inspector.grid(row=0, column=1, sticky="nsew")
        self.inspector.columnconfigure(0, weight=1)
        self._build_inspector()

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Motion>", self._on_pointer_motion)
        self.canvas.bind("<Button-3>", lambda _event: self._finish_polyline())
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self.set_zoom(self.zoom * 1.1))
        self.canvas.bind("<Button-5>", lambda _e: self.set_zoom(self.zoom / 1.1))
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")

    def _section(self, text, row):
        label = ctk.CTkLabel(
            self.inspector, text=theme.tracked(text), anchor="w",
            text_color=theme.TEXT_SECOND,
            font=theme.ui_font(theme.SIZE["label"], "bold"))
        label.grid(row=row, column=0, sticky="ew", padx=12, pady=(14, 5))
        return label

    def _build_inspector(self):
        self._section("Selection", 0)
        self.properties = ctk.CTkFrame(
            self.inspector, fg_color=theme.SURFACE_SUBTLE,
            border_width=1, border_color=theme.BORDER,
            corner_radius=theme.RADIUS["card"])
        self.properties.grid(row=1, column=0, sticky="ew", padx=10)
        self.properties.columnconfigure(0, weight=1)

        self._section("Layers", 2)
        layers = ctk.CTkFrame(self.inspector, fg_color="transparent")
        layers.grid(row=3, column=0, sticky="ew", padx=10)
        for column, name in enumerate(self.layer_visibility):
            switch = ctk.CTkSwitch(
                layers, text=name, width=82,
                font=theme.ui_font(theme.SIZE["meta"]),
                command=lambda n=name: self._toggle_layer(n))
            switch.select()
            switch.grid(row=column // 2, column=column % 2, sticky="w", pady=2)

        self._section("Title block", 4)
        title = ctk.CTkFrame(self.inspector, fg_color="transparent")
        title.grid(row=5, column=0, sticky="ew", padx=10)
        title.columnconfigure(0, weight=1)
        fields_to_show = (
            ("school_name", "School"), ("local_code", "Local code"),
            ("address", "Address"), ("project_title", "Project"),
            ("drawing_title", "Drawing"), ("system", "System"),
            ("sheet_number", "Sheet"), ("drawn_by", "Drawn by"),
            ("checked_by", "Checked by"), ("issue_date", "Issue date"),
            ("revisions", "Revisions"),
        )
        for row, (name, label) in enumerate(fields_to_show):
            ctk.CTkLabel(title, text=label, anchor="w", width=74,
                         text_color=theme.TEXT_SECOND,
                         font=theme.ui_font(theme.SIZE["meta"])).grid(
                row=row, column=0, sticky="w", pady=2)
            raw_value = getattr(self.controller.document.title_block, name)
            if name == "revisions":
                raw_value = " | ".join(raw_value)
            var = tk.StringVar(value=raw_value)
            entry = ctk.CTkEntry(title, textvariable=var, height=27,
                                 font=theme.ui_font(theme.SIZE["meta"]))
            entry.grid(row=row, column=1, sticky="ew", padx=(5, 0), pady=2)
            entry.bind("<FocusOut>", lambda _e, n=name, v=var: self._save_title(n, v))
            entry.bind("<Return>", lambda _e, n=name, v=var: self._save_title(n, v))
            title.columnconfigure(1, weight=1)
            self._title_vars[name] = var
            self._title_entries[name] = entry

        self._section("Validation", 6)
        self.validation_frame = ctk.CTkFrame(self.inspector, fg_color="transparent")
        self.validation_frame.grid(row=7, column=0, sticky="ew", padx=10)
        self.validation_frame.columnconfigure(0, weight=1)
        self._section("Unplaced", 8)
        self.unplaced_frame = ctk.CTkFrame(self.inspector, fg_color="transparent")
        self.unplaced_frame.grid(row=9, column=0, sticky="ew", padx=10,
                                 pady=(0, 14))
        self.unplaced_frame.columnconfigure(0, weight=1)

    def _bind_keys(self):
        self.canvas.bind("<Escape>", lambda _e: self._run_key_command(self.cancel))
        self.canvas.bind("<Delete>", lambda _e: self._run_key_command(self.delete_selected))
        self.canvas.bind("<BackSpace>", lambda _e: self._run_key_command(self.delete_selected))
        self.canvas.bind("<Control-z>", lambda _e: self._run_key_command(self.undo))
        self.canvas.bind("<Control-y>", lambda _e: self._run_key_command(self.redo))
        self.canvas.bind("<Control-d>", lambda _e: self._run_key_command(self.duplicate_selected))
        self.canvas.bind("<Command-z>", lambda _e: self._run_key_command(self.undo))
        self.canvas.bind("<Command-Shift-Z>", lambda _e: self._run_key_command(self.redo))
        self.canvas.bind("<Command-d>", lambda _e: self._run_key_command(self.duplicate_selected))
        self.canvas.bind("<Return>", lambda _e: self._run_key_command(self._finish_polyline))
        for key, delta in (("Left", (-GRID, 0)), ("Right", (GRID, 0)),
                           ("Up", (0, -GRID)), ("Down", (0, GRID))):
            self.canvas.bind(
                f"<{key}>",
                lambda _e, d=delta: self._run_key_command(lambda: self.nudge(*d)))

    @staticmethod
    def _run_key_command(command):
        command()
        return "break"

    # ---- drawing ------------------------------------------------------

    def _xy(self, point):
        origin_x, origin_y = self._page_origin()
        return point[0] * self.zoom + origin_x, point[1] * self.zoom + origin_y

    def _page_origin(self):
        doc = self.controller.document
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        return (
            max(24.0, (canvas_width - doc.page_width * self.zoom) / 2),
            max(24.0, (canvas_height - doc.page_height * self.zoom) / 2),
        )

    def _world(self, event):
        origin_x, origin_y = self._page_origin()
        return ((self.canvas.canvasx(event.x) - origin_x) / self.zoom,
                (self.canvas.canvasy(event.y) - origin_y) / self.zoom)

    def _snap(self, point):
        if not self.snap_enabled:
            return point
        return tuple(round(value / GRID) * GRID for value in point)

    def redraw(self, *, inspector: bool = True):
        self._reconcile_selection()
        self.canvas.delete("all")
        doc = self.controller.document
        width, height = doc.page_width * self.zoom, doc.page_height * self.zoom
        origin_x, origin_y = self._page_origin()
        self.canvas.create_rectangle(origin_x, origin_y,
                                     origin_x + width, origin_y + height,
                                     fill="#ffffff", outline="#c7cdd4", width=1,
                                     tags=("page",))
        if self.grid_enabled and self.zoom >= 0.25:
            step = GRID * self.zoom
            x = origin_x
            while x <= origin_x + width:
                self.canvas.create_line(x, origin_y, x, origin_y + height,
                                        fill="#edf0f2", tags=("grid",))
                x += step
            y = origin_y
            while y <= origin_y + height:
                self.canvas.create_line(origin_x, y, origin_x + width, y,
                                        fill="#edf0f2", tags=("grid",))
                y += step

        if self.layer_visibility["Locations"]:
            for element_id in doc.z_order:
                element = doc.elements.get(element_id)
                if element and element.kind == "location":
                    self._draw_element(element)
        if self.layer_visibility["Cables"]:
            self._draw_routes()
        if self.layer_visibility["Devices"]:
            for element_id in doc.z_order:
                element = doc.elements.get(element_id)
                if element and element.kind == "device":
                    self._draw_element(element)
        if self.layer_visibility["Markup"]:
            for annotation in sorted(
                    doc.annotations,
                    key=lambda a: doc.z_order.index(a.id) if a.id in doc.z_order else 9999):
                self._draw_annotation(annotation)
        if self.layer_visibility["Title block"]:
            self._draw_title_block()
        self._draw_selection()
        self.canvas.configure(scrollregion=(
            0, 0, max(self.canvas.winfo_width(), origin_x + width + 24),
            max(self.canvas.winfo_height(), origin_y + height + 24)))
        if inspector:
            self._refresh_inspector()

    def _draw_element(self, element):
        x1, y1 = self._xy((element.x, element.y))
        x2, y2 = self._xy((element.x + element.width, element.y + element.height))
        tag = f"element|{element.id}"
        selected = self.selected == ("element", element.id)
        if element.kind == "location":
            self.canvas.create_rectangle(
                x1, y1, x2, y2, fill="#fbfcfd", outline="#99a6b2",
                dash=(5, 4), width=2 if selected else 1,
                tags=(tag, "selectable"))
            self.canvas.create_line(
                x1, y1 + 34 * self.zoom, x2, y1 + 34 * self.zoom,
                fill="#d7dde3", width=1, tags=(tag, "selectable"))
            self.canvas.create_text(x1 + 8, y1 + 7, text=element.ref,
                                    anchor="nw", fill="#4f5964",
                                    font=("TkDefaultFont", max(5, round(8 * self.zoom))),
                                    tags=(tag, "selectable"))
            return
        fill = "#fff7e8" if element.stale else "#ffffff"
        self.canvas.create_rectangle(
            x1, y1, x2, y2, fill=fill,
            outline="#4a7bb8" if selected else "#1b2430",
            width=3 if selected else 1.4, tags=(tag, "selectable"))
        show_detail = self.zoom >= 0.50 or selected or self.tool == "Connect"
        if show_detail:
            kind = self._device_kind_label(element.ref)
            self.canvas.create_text((x1 + x2) / 2, y1 + 12 * self.zoom,
                                    text=kind, fill="#5c6570",
                                    font=("TkDefaultFont", max(6, round(8 * self.zoom))),
                                    tags=(tag, "selectable"))
        ref_y = ((y1 + y2) / 2 + 5 * self.zoom) if show_detail else (y1 + y2) / 2
        self.canvas.create_text((x1 + x2) / 2, ref_y,
                                text=element.ref, fill="#111111",
                                font=("TkDefaultFont", max(7, round(13 * self.zoom)), "bold"),
                                tags=(tag, "selectable"))
        for ref, output in self._ports_for(element.ref):
            px, py = self._xy(port_point(element, ref.port_id, output=output))
            port_tag = f"port|{ref.device_id}|{ref.port_id}|{'out' if output else 'in'}"
            radius = 4 if show_detail else 3
            self.canvas.create_oval(px - radius, py - radius,
                                    px + radius, py + radius,
                                    fill="#4a7bb8" if output else "#ffffff",
                                    outline=("#d96524" if ref == self._connect_source
                                             else "#1b2430"),
                                    width=3 if ref == self._connect_source else 1,
                                    tags=(port_tag, "port"))
            if (element.ref == "MSP" and
                    (self.zoom >= 0.55 or selected or self.tool == "Connect")):
                self.canvas.create_text(
                    px, py - 6, text=ref.port_id, anchor="s", fill="#4f5964",
                    font=("TkDefaultFont", max(5, round(6 * self.zoom))),
                    tags=(port_tag, "port"))

    def _device_kind_label(self, device_id):
        if device_id == "MSP":
            return "XR550 CONTROL PANEL"
        if any(splitter.id == device_id for splitter in self.design.splitters):
            return "DMP 710 MODULE"
        if device_id.startswith("RSP-"):
            return "REMOTE POWER SUPPLY"
        return "KEYPAD"

    def _ports_for(self, device_id):
        if device_id == "MSP":
            used = {c.source.port_id for c in self.design.connections
                    if c.source.device_id == "MSP"}
            order = ("KP BUS", "PROG", "LX500", "LX600", "LX700", "LX800", "LX900")
            # In the overview, unused MSP targets are visual noise.  Connect
            # mode reveals the complete compatible target set for creation or
            # endpoint dragging; existing endpoints remain visible otherwise.
            available = set(order) if self.tool == "Connect" else set()
            ports = [port for port in order if port in used | available]
            ports.extend(sorted((used | available) - set(order)))
            return [(DevicePortRef("MSP", port), True) for port in ports]
        if any(splitter.id == device_id for splitter in self.design.splitters):
            return [(DevicePortRef(device_id, "IN"), False)] + [
                (DevicePortRef(device_id, f"OUT{i}"), True) for i in range(1, 4)]
        return [(DevicePortRef(device_id, "IN"), False)]

    def _draw_routes(self):
        edges = {edge.id: edge for edge in self.design.connections}
        selected_id = self.selected[1] if self.selected and self.selected[0] == "route" else None
        bridges = find_bridges({key: value.points for key, value
                                in self.controller.document.routes.items()})
        for route_id, route in self.controller.document.routes.items():
            if len(route.points) < 2:
                continue
            tag = f"route|{route_id}"
            width = 3 if route_id == selected_id else 1.5
            route_bridges = [bridge for bridge in bridges
                             if bridge.connection_id == route_id]
            for start, end in zip(route.points, route.points[1:]):
                if start[1] == end[1]:
                    forward = end[0] >= start[0]
                    crossings = [bridge for bridge in route_bridges
                                 if bridge.orientation == "horizontal"
                                 and min(start[0], end[0]) + BRIDGE_HALF_WIDTH <= bridge.x
                                 <= max(start[0], end[0]) - BRIDGE_HALF_WIDTH
                                 and abs(bridge.y - start[1]) < 0.01]
                    crossings.sort(key=lambda bridge: bridge.x, reverse=not forward)
                    cursor = start
                    for bridge in crossings:
                        before_x = (bridge.x - BRIDGE_HALF_WIDTH if forward
                                    else bridge.x + BRIDGE_HALF_WIDTH)
                        after_x = (bridge.x + BRIDGE_HALF_WIDTH if forward
                                   else bridge.x - BRIDGE_HALF_WIDTH)
                        before = (before_x, start[1])
                        self.canvas.create_line(
                            *self._xy(cursor), *self._xy(before), fill="#111111",
                            width=width, tags=(tag, "selectable"))
                        center_x, center_y = self._xy((bridge.x, bridge.y))
                        radius = max(3, BRIDGE_HALF_WIDTH * self.zoom)
                        self.canvas.create_arc(
                            center_x - radius, center_y - radius,
                            center_x + radius, center_y + radius,
                            start=0, extent=180, style="arc", outline="#111111",
                            width=width, tags=(tag, "selectable"))
                        cursor = (after_x, start[1])
                    self.canvas.create_line(
                        *self._xy(cursor), *self._xy(end), fill="#111111",
                        width=width, tags=(tag, "selectable"))
                elif start[0] == end[0]:
                    forward = end[1] >= start[1]
                    crossings = [bridge for bridge in route_bridges
                                 if bridge.orientation == "vertical"
                                 and min(start[1], end[1]) + BRIDGE_HALF_WIDTH <= bridge.y
                                 <= max(start[1], end[1]) - BRIDGE_HALF_WIDTH
                                 and abs(bridge.x - start[0]) < 0.01]
                    crossings.sort(key=lambda bridge: bridge.y, reverse=not forward)
                    cursor = start
                    for bridge in crossings:
                        before_y = (bridge.y - BRIDGE_HALF_WIDTH if forward
                                    else bridge.y + BRIDGE_HALF_WIDTH)
                        after_y = (bridge.y + BRIDGE_HALF_WIDTH if forward
                                   else bridge.y - BRIDGE_HALF_WIDTH)
                        before = (start[0], before_y)
                        self.canvas.create_line(
                            *self._xy(cursor), *self._xy(before), fill="#111111",
                            width=width, tags=(tag, "selectable"))
                        center_x, center_y = self._xy((bridge.x, bridge.y))
                        radius = max(3, BRIDGE_HALF_WIDTH * self.zoom)
                        self.canvas.create_arc(
                            center_x - radius, center_y - radius,
                            center_x + radius, center_y + radius,
                            start=270, extent=180, style="arc", outline="#111111",
                            width=width, tags=(tag, "selectable"))
                        cursor = (start[0], after_y)
                    self.canvas.create_line(
                        *self._xy(cursor), *self._xy(end), fill="#111111",
                        width=width, tags=(tag, "selectable"))
                else:
                    self.canvas.create_line(
                        *self._xy(start), *self._xy(end), fill="#111111",
                        width=width, tags=(tag, "selectable"))
            edge = edges.get(route_id)
            if edge and self.zoom >= 0.45:
                label_point = route_label_point(route.points)
                if label_point is None:
                    continue
                lx, ly = self._xy((label_point[0] + route.label_offset[0],
                                   label_point[1] + route.label_offset[1]))
                self.canvas.create_text(
                    lx, ly - 5, text=edge.label, anchor="s", fill="#111111",
                    font=("TkDefaultFont", max(6, round(9 * self.zoom))),
                    tags=(tag, "selectable"))

    def _draw_annotation(self, annotation):
        if not annotation.points:
            return
        tag = f"annotation|{annotation.id}"
        points = [self._xy(point) for point in annotation.points]
        coords = [v for point in points for v in point]
        stroke = annotation.stroke or "#111111"
        fill = annotation.fill or ""
        width = max(1, annotation.stroke_width * self.zoom)
        if annotation.kind == "text":
            anchor = {"left": "w", "center": "center", "right": "e"}[annotation.alignment]
            self.canvas.create_text(
                *points[0], text=annotation.text, anchor=anchor, fill=stroke,
                font=("TkDefaultFont", max(6, round(annotation.font_size * self.zoom)),
                      "bold" if annotation.font_weight == "bold" else "normal"),
                tags=(tag, "selectable"))
        elif annotation.kind == "rectangle" and len(points) > 1:
            self.canvas.create_rectangle(*coords[:4], outline=stroke, fill=fill,
                                         width=width, tags=(tag, "selectable"))
        elif annotation.kind == "ellipse" and len(points) > 1:
            self.canvas.create_oval(*coords[:4], outline=stroke, fill=fill,
                                    width=width, tags=(tag, "selectable"))
        else:
            self.canvas.create_line(*coords, fill=stroke, width=width,
                                    arrow="last" if annotation.kind == "arrow" else "none",
                                    tags=(tag, "selectable"))

    def _draw_title_block(self):
        doc = self.controller.document
        left = doc.page_width - TITLE_BLOCK_WIDTH
        x1, y1 = self._xy((left, 0))
        x2, y2 = self._xy((doc.page_width, doc.page_height))
        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#ffffff",
                                     outline="#111111", width=1.2,
                                     tags=("titleblock",))
        title = doc.title_block
        values = ["C1", title.school_name, f"LOCAL CODE  {title.local_code}",
                  title.address, title.project_title,
                  title.drawing_title, title.system, f"SHEET  {title.sheet_number}",
                  f"DRAWN  {title.drawn_by}", f"CHECKED  {title.checked_by}",
                  f"ISSUE  {title.issue_date}",
                  *(f"REV  {revision}" for revision in title.revisions[-6:])]
        y = y1 + 22
        for index, value in enumerate(values):
            self.canvas.create_text(
                (x1 + x2) / 2, y, text=value, width=max(20, x2 - x1 - 12),
                fill="#111111", justify="center",
                font=("TkDefaultFont", max(7, round((18 if index == 0 else 9) * self.zoom)),
                      "bold" if index in {0, 1, 4, 5} else "normal"), tags=("titleblock",))
            y += (50 if index in {0, 1, 2, 3, 4, 5} else 35) * self.zoom

    def _draw_selection(self):
        if not self.selected:
            return
        kind, ref = self.selected
        if kind == "element":
            element = self.controller.document.elements.get(ref)
            if (not element or
                    (element.kind == "device" and not self.layer_visibility["Devices"]) or
                    (element.kind == "location" and not self.layer_visibility["Locations"])):
                return
            x, y = self._xy((element.x + element.width, element.y + element.height))
            self.canvas.create_rectangle(x - 5, y - 5, x + 5, y + 5,
                                         fill="#ffffff", outline="#4a7bb8", width=2,
                                         tags=("resize-handle",))
        elif kind == "route":
            if not self.layer_visibility["Cables"]:
                return
            route = self.controller.document.routes.get(ref)
            if route:
                for index, point in enumerate(route.points):
                    x, y = self._xy(point)
                    self.canvas.create_rectangle(
                        x - 4, y - 4, x + 4, y + 4, fill="#ffffff",
                        outline="#4a7bb8", width=2,
                        tags=(f"route-handle|{ref}|{index}",))
        elif kind == "annotation" and self.layer_visibility["Markup"]:
            annotation = next((item for item in self.controller.document.annotations
                               if item.id == ref), None)
            if not annotation or not annotation.points:
                return
            points = [self._xy(point) for point in annotation.points]
            xs, ys = [point[0] for point in points], [point[1] for point in points]
            self.canvas.create_rectangle(
                min(xs) - 5, min(ys) - 5, max(xs) + 5, max(ys) + 5,
                outline="#4a7bb8", dash=(3, 2), width=1,
                tags=("annotation-selection",))

    # ---- interaction --------------------------------------------------

    def set_tool(self, name):
        self.cancel()
        self.tool = name
        for tool, button in self._tool_buttons.items():
            button.configure(fg_color=theme.ACCENT_TINT if tool == name else "transparent")
        self.canvas.configure(cursor={"Connect": "crosshair", "Route": "crosshair",
                                      "Text": "xterm"}.get(name, "arrow"))

    def _tags_at_event(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        items = self.canvas.find_overlapping(x - 3, y - 3, x + 3, y + 3)
        return [tag for item in reversed(items) for tag in self.canvas.gettags(item)]

    def _parse_port(self, tags):
        token = next((tag for tag in tags if tag.startswith("port|")), None)
        if not token:
            return None
        _, device, port, direction = token.split("|", 3)
        return DevicePortRef(device, port), direction == "out"

    def _parse_selectable(self, tags):
        prefixes = {"annotation": "annotation", "element": "element", "route": "route"}
        for token in tags:
            prefix, separator, ref = token.partition("|")
            if separator and prefix in prefixes:
                return prefixes[prefix], ref
        return None

    def _reconcile_selection(self):
        if not self.selected:
            return
        kind, ref = self.selected
        exists = (
            (kind == "element" and ref in self.controller.document.elements)
            or (kind == "route" and ref in self.controller.document.routes)
            or (kind == "annotation" and any(
                item.id == ref for item in self.controller.document.annotations))
        )
        if not exists:
            self.selected = None

    def _annotation_at_point(self, point):
        """Return the topmost markup hit, including unfilled shape interiors."""
        document = self.controller.document
        annotations = sorted(
            document.annotations,
            key=lambda item: (document.z_order.index(item.id)
                              if item.id in document.z_order else len(document.z_order)),
            reverse=True)
        x, y = point
        tolerance = max(6.0, 5.0 / max(self.zoom, 0.01))

        def segment_distance(first, second):
            dx, dy = second[0] - first[0], second[1] - first[1]
            if not dx and not dy:
                return ((x - first[0]) ** 2 + (y - first[1]) ** 2) ** 0.5
            amount = max(0.0, min(1.0, ((x - first[0]) * dx +
                                        (y - first[1]) * dy) / (dx * dx + dy * dy)))
            nearest = (first[0] + amount * dx, first[1] + amount * dy)
            return ((x - nearest[0]) ** 2 + (y - nearest[1]) ** 2) ** 0.5

        for annotation in annotations:
            if not annotation.points:
                continue
            if annotation.kind in {"rectangle", "ellipse"} and len(annotation.points) >= 2:
                (x1, y1), (x2, y2) = annotation.points[:2]
                left, right = sorted((x1, x2))
                top, bottom = sorted((y1, y2))
                if annotation.kind == "rectangle" and left <= x <= right and top <= y <= bottom:
                    return annotation.id
                if annotation.kind == "ellipse" and right > left and bottom > top:
                    cx, cy = (left + right) / 2, (top + bottom) / 2
                    if ((x - cx) / ((right - left) / 2)) ** 2 + \
                            ((y - cy) / ((bottom - top) / 2)) ** 2 <= 1:
                        return annotation.id
            elif annotation.kind == "text":
                px, py = annotation.points[0]
                width = max(annotation.font_size, len(annotation.text) * annotation.font_size * 0.6)
                left = {"left": px, "center": px - width / 2,
                        "right": px - width}.get(annotation.alignment, px)
                if left - tolerance <= x <= left + width + tolerance and \
                        py - annotation.font_size - tolerance <= y <= py + tolerance:
                    return annotation.id
            elif any(segment_distance(first, second) <= tolerance
                     for first, second in zip(annotation.points, annotation.points[1:])):
                return annotation.id
        return None

    def _on_press(self, event):
        self.canvas.focus_set()
        point = self._snap(self._world(event))
        tags = self._tags_at_event(event)
        port = self._parse_port(tags)
        if self.tool == "Connect":
            self._connect_click(port)
            return
        if self.tool == "Text":
            text = simpledialog.askstring("Add text", "Text:", parent=self.winfo_toplevel())
            if text:
                annotation = RiserAnnotation(f"annotation-{uuid.uuid4().hex[:8]}",
                                             "text", [point], text=text)
                self.controller.add_annotation(annotation)
                self.selected = ("annotation", annotation.id)
                self._changed()
            return
        if self.tool == "Polyline":
            if self._gesture and self._gesture[0] == "polyline":
                if point != self._gesture[1][-1]:
                    self._gesture[1].append(point)
            else:
                self._gesture = ("polyline", [point])
            self._update_polyline_preview(point)
            return
        if self.tool in {"Rectangle", "Ellipse", "Arrow"}:
            self._gesture = ("draw", point)
            return
        handle = next((tag for tag in tags if tag == "resize-handle" or
                       tag.startswith("route-handle|")), None)
        if handle == "resize-handle" and self.selected and self.selected[0] == "element":
            element = self.controller.document.elements[self.selected[1]]
            self._gesture = ("resize", self.selected[1], point, element.width, element.height)
            return
        if handle and handle.startswith("route-handle|"):
            _, route_id, index = handle.split("|")
            point_index = int(index)
            old_points = copy.deepcopy(self.controller.document.routes[route_id].points)
            self._gesture = ("route", route_id, point_index, old_points)
            return
        annotation_id = self._annotation_at_point(self._world(event))
        picked = (("annotation", annotation_id) if annotation_id
                  else self._parse_selectable(tags))
        self.selected = picked
        if picked and picked[0] == "element" and self.tool == "Select":
            element = self.controller.document.elements[picked[1]]
            self._gesture = ("move", picked[1], point, element.x, element.y)
        elif picked and picked[0] == "annotation" and self.tool == "Select":
            annotation = next(item for item in self.controller.document.annotations
                              if item.id == picked[1])
            self._gesture = ("move-annotation", picked[1], point,
                             copy.deepcopy(annotation.points))
        self.redraw()

    def _on_drag(self, event):
        if not self._gesture:
            return
        point = self._snap(self._world(event))
        kind = self._gesture[0]
        if kind == "draw":
            start = self._gesture[1]
            if self._preview_item:
                self.canvas.delete(self._preview_item)
            coords = (*self._xy(start), *self._xy(point))
            if self.tool == "Rectangle":
                self._preview_item = self.canvas.create_rectangle(*coords, outline="#4a7bb8")
            elif self.tool == "Ellipse":
                self._preview_item = self.canvas.create_oval(*coords, outline="#4a7bb8")
            else:
                self._preview_item = self.canvas.create_line(
                    *coords, fill="#4a7bb8", arrow="last" if self.tool == "Arrow" else "none")
        elif kind == "move":
            _, element_id, start, old_x, old_y = self._gesture
            element = self.controller.document.elements[element_id]
            element.x, element.y = old_x + point[0] - start[0], old_y + point[1] - start[1]
            self.redraw(inspector=False)
        elif kind == "move-annotation":
            _, annotation_id, start, old_points = self._gesture
            annotation = next(item for item in self.controller.document.annotations
                              if item.id == annotation_id)
            dx, dy = point[0] - start[0], point[1] - start[1]
            annotation.points = [(x + dx, y + dy) for x, y in old_points]
            self.redraw(inspector=False)
        elif kind == "resize":
            _, element_id, start, old_w, old_h = self._gesture
            element = self.controller.document.elements[element_id]
            element.width = max(36, old_w + point[0] - start[0])
            minimum_height = 54 if element.kind == "location" else 28
            element.height = max(minimum_height, old_h + point[1] - start[1])
            self.redraw(inspector=False)
        elif kind == "route":
            _, route_id, index, old_points = self._gesture
            route = self.controller.document.routes[route_id]
            if index in {0, len(old_points) - 1}:
                route.points = list(old_points)
                route.points[index] = point
            else:
                route.points = self.controller.adjusted_route_points(
                    old_points, index, point)
            self.redraw(inspector=False)

    def _on_release(self, event):
        if not self._gesture:
            return
        if self._gesture[0] == "polyline":
            return
        gesture, self._gesture = self._gesture, None
        point = self._snap(self._world(event))
        kind = gesture[0]
        if kind == "draw":
            start = gesture[1]
            if start != point:
                annotation_kind = self.tool.lower()
                annotation = RiserAnnotation(
                    f"annotation-{uuid.uuid4().hex[:8]}", annotation_kind,
                    [start, point])
                self.controller.add_annotation(annotation)
                self.selected = ("annotation", annotation.id)
                self._changed()
        elif kind == "move":
            _, element_id, _start, old_x, old_y = gesture
            element = self.controller.document.elements[element_id]
            new_x, new_y = element.x, element.y
            element.x, element.y = old_x, old_y
            if self.controller.move_element(element_id, new_x - old_x, new_y - old_y):
                self._changed()
        elif kind == "move-annotation":
            _, annotation_id, _start, old_points = gesture
            annotation = next(item for item in self.controller.document.annotations
                              if item.id == annotation_id)
            new_points = copy.deepcopy(annotation.points)
            annotation.points = old_points
            if new_points != old_points:
                self.controller.update_annotation(annotation_id, points=new_points)
                self._changed()
        elif kind == "resize":
            _, element_id, _start, old_w, old_h = gesture
            element = self.controller.document.elements[element_id]
            new_w, new_h = element.width, element.height
            element.width, element.height = old_w, old_h
            if self.controller.resize_element(element_id, new_w, new_h):
                self._changed()
        elif kind == "route":
            _, route_id, index, old_points = gesture
            route = self.controller.document.routes[route_id]
            # Drag preview already wrote the final point. Restore from the undo
            # snapshot's latest state, then issue one semantic command.
            route.points = old_points
            is_endpoint = index in {0, len(old_points) - 1}
            if is_endpoint:
                port = self._parse_port(self._tags_at_event(event))
                if port:
                    ref, is_output = port
                    edge = next(edge for edge in self.design.connections
                                if edge.id == route_id)
                    try:
                        if index == 0 and is_output:
                            if ref != edge.source:
                                self.controller.reconnect(route_id, source=ref)
                                self._changed()
                        elif index == len(old_points) - 1 and not is_output:
                            if ref == edge.target:
                                self.redraw()
                                return
                            self.controller.reconnect(route_id, target=ref)
                            self._changed()
                    except TopologyError as exc:
                        messagebox.showwarning("Connection not allowed", str(exc))
            else:
                if self.controller.move_route_point(route_id, index, point):
                    self._changed()
        self._preview_item = None
        self.redraw()

    def _connect_click(self, port):
        if not port:
            return
        ref, is_output = port
        if self._connect_source is None:
            if not is_output:
                messagebox.showinfo("Choose a source", "Start at an output port.")
                return
            self._connect_source = ref
            self.redraw()
            return
        if is_output:
            self._connect_source = ref
            self.redraw()
            return
        try:
            existing = next((edge for edge in self.design.connections
                             if edge.source == self._connect_source and edge.target == ref), None)
            if existing is not None:
                self.selected = ("route", existing.id)
                self._connect_source = None
                self.redraw()
                return
            edge = self.controller.connect(self._connect_source, ref)
            self.selected = ("route", edge.id)
            self._connect_source = None
            self._changed()
        except TopologyError as exc:
            messagebox.showwarning("Connection not allowed", str(exc))

    def _on_double_click(self, event):
        if self.tool == "Polyline" and self._gesture and self._gesture[0] == "polyline":
            self._finish_polyline()
            return "break"
        picked = self._parse_selectable(self._tags_at_event(event))
        if not picked:
            return
        kind, ref = picked
        if kind == "route":
            edge = next((edge for edge in self.design.connections if edge.id == ref), None)
            if edge:
                text = simpledialog.askstring("Cable label", "Custom label (blank = automatic):",
                                              initialvalue=edge.custom_label or "",
                                              parent=self.winfo_toplevel())
                if text is not None:
                    self.controller.update_connection(ref, custom_label=text)
                    self._changed()
        elif kind == "annotation":
            annotation = next(a for a in self.controller.document.annotations if a.id == ref)
            if annotation.kind == "text":
                text = simpledialog.askstring("Edit text", "Text:", initialvalue=annotation.text,
                                              parent=self.winfo_toplevel())
                if text is not None:
                    self.controller.update_annotation(ref, text=text)
                    self._changed()

    def _on_pointer_motion(self, event):
        if self._gesture and self._gesture[0] == "polyline":
            self._update_polyline_preview(self._snap(self._world(event)))
        elif self.tool == "Connect" and self._connect_source is not None:
            source = self.controller.document.elements.get(
                f"device:{self._connect_source.device_id}")
            if source is None:
                return
            start = port_point(source, self._connect_source.port_id, output=True)
            if self._preview_item:
                self.canvas.delete(self._preview_item)
            self._preview_item = self.canvas.create_line(
                *self._xy(start), self.canvas.canvasx(event.x),
                self.canvas.canvasy(event.y), fill="#d96524",
                width=2, dash=(5, 3))

    def _update_polyline_preview(self, cursor):
        if not self._gesture or self._gesture[0] != "polyline":
            return
        if self._preview_item:
            self.canvas.delete(self._preview_item)
        points = [*self._gesture[1], cursor]
        coords = [coordinate for point in points for coordinate in self._xy(point)]
        self._preview_item = self.canvas.create_line(
            *coords, fill="#4a7bb8", width=2, joinstyle="round")

    def _finish_polyline(self):
        if not self._gesture or self._gesture[0] != "polyline":
            return
        points = []
        for point in self._gesture[1]:
            if not points or point != points[-1]:
                points.append(point)
        self._gesture = None
        if self._preview_item:
            self.canvas.delete(self._preview_item)
            self._preview_item = None
        if len(points) < 2:
            self.redraw()
            return
        annotation = RiserAnnotation(
            f"annotation-{uuid.uuid4().hex[:8]}", "polyline", points)
        self.controller.add_annotation(annotation)
        self.selected = ("annotation", annotation.id)
        self._changed()

    def _pan_start(self, event):
        self.canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_wheel(self, event):
        if event.state & 0x4 or event.state & 0x8:
            self.set_zoom(self.zoom * (1.1 if event.delta > 0 else 1 / 1.1))
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ---- commands / inspector ----------------------------------------

    def _changed(self):
        self.on_edit()
        self.redraw()

    def undo(self):
        if self.controller.undo():
            self._changed()

    def redo(self):
        if self.controller.redo():
            self._changed()

    def cancel(self, redraw=True):
        if self._gesture:
            if self._gesture[0] == "route":
                _, route_id, _index, old_points = self._gesture
                route = self.controller.document.routes.get(route_id)
                if route:
                    route.points = old_points
            elif self._gesture[0] == "move":
                _, element_id, _start, old_x, old_y = self._gesture
                element = self.controller.document.elements.get(element_id)
                if element:
                    element.x, element.y = old_x, old_y
            elif self._gesture[0] == "move-annotation":
                _, annotation_id, _start, old_points = self._gesture
                annotation = next((item for item in self.controller.document.annotations
                                   if item.id == annotation_id), None)
                if annotation:
                    annotation.points = old_points
            elif self._gesture[0] == "resize":
                _, element_id, _start, old_width, old_height = self._gesture
                element = self.controller.document.elements.get(element_id)
                if element:
                    element.width, element.height = old_width, old_height
        self._gesture = None
        self._connect_source = None
        if self._preview_item:
            self.canvas.delete(self._preview_item)
            self._preview_item = None
        if redraw:
            self.redraw()

    def delete_selected(self):
        if not self.selected:
            return
        kind, ref = self.selected
        if kind == "annotation":
            self.controller.delete_annotation(ref)
        elif kind == "route" and messagebox.askyesno(
                "Disconnect cable?", "Remove this electrical connection?"):
            self.controller.disconnect(ref)
        else:
            return
        self.selected = None
        self._changed()

    def duplicate_selected(self):
        if self.selected and self.selected[0] == "annotation":
            duplicate = self.controller.duplicate_annotation(self.selected[1])
            self.selected = ("annotation", duplicate.id)
            self._changed()

    def nudge(self, dx, dy):
        if not self.selected:
            return
        kind, ref = self.selected
        if kind == "element":
            self.controller.move_element(ref, dx, dy)
        elif kind == "annotation":
            annotation = next(a for a in self.controller.document.annotations if a.id == ref)
            self.controller.update_annotation(
                ref, points=[(x + dx, y + dy) for x, y in annotation.points])
        else:
            return
        self._changed()

    def relayout(self):
        if not messagebox.askyesno(
                "Re-layout all?", "This discards manual device geometry and cable routes.\n\n"
                "Markup and title-block data will be preserved."):
            return
        self.controller.relayout()
        self.selected = None
        self._changed()

    def auto_layout(self):
        self.controller.auto_layout()
        self._changed()

    def set_zoom(self, value):
        self._fit_mode = False
        self._set_zoom(value)

    def _set_zoom(self, value):
        self.zoom = min(1.75, max(0.18, value))
        self._zoom_label.configure(text=f"{round(self.zoom * 100)}%")
        self.redraw()

    def fit_to_view(self):
        """Fit the canonical sheet inside the current canvas without cropping."""
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 100 or height < 100:
            return
        document = self.controller.document
        fitted = min((width - 48) / document.page_width,
                     (height - 48) / document.page_height)
        self._set_zoom(fitted)
        self._fit_mode = True
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def _frame_initial_view(self):
        """Open at a readable drafting scale with the diagram in view.

        Fitting the *entire* 24x36 sheet into the relatively short editor pane
        made device and cable labels collapse into one another.  The initial
        view instead fits the sheet width (capped at the established 42%
        drafting scale) and scrolls vertically to the electrical content.
        Full-sheet fit remains available as an explicit toolbar action.
        """
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 100 or height < 100:
            return
        document = self.controller.document
        initial_zoom = min(0.42, (width - 48) / document.page_width)
        self._set_zoom(initial_zoom)
        self._fit_mode = False

        locations = [element for element in document.elements.values()
                     if element.kind == "location" and not element.stale]
        content = locations or [element for element in document.elements.values()
                                if element.kind == "device" and not element.stale]
        if not content:
            return
        content_top = min(element.y for element in content)
        content_bottom = max(element.y + element.height for element in content)
        _origin_x, origin_y = self._page_origin()
        content_center = origin_y + (content_top + content_bottom) * self.zoom / 2
        total_height = max(
            height,
            origin_y + document.page_height * self.zoom + 24,
        )
        desired_top = content_center - height / 2
        maximum_top = max(0.0, total_height - height)
        desired_top = min(maximum_top, max(0.0, desired_top))
        self.canvas.yview_moveto(desired_top / total_height)

    def _on_canvas_configure(self, event):
        if event.width < 100 or event.height < 100:
            return
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        if not self._initial_fit_done:
            self._initial_fit_done = True
            callback = self._frame_initial_view
        else:
            callback = self.fit_to_view if self._fit_mode else self.redraw
        self._resize_after_id = self.after_idle(self._finish_canvas_resize, callback)

    def _finish_canvas_resize(self, callback):
        self._resize_after_id = None
        callback()

    def _toggle_snap(self):
        self.snap_enabled = bool(self._snap_switch.get())

    def _toggle_grid(self):
        self.grid_enabled = bool(self._grid_switch.get())
        self.redraw()

    def _toggle_layer(self, name):
        self.layer_visibility[name] = not self.layer_visibility[name]
        self.redraw()

    def _save_title(self, name, variable):
        value = variable.get()
        if name == "revisions":
            value = [item.strip() for item in value.split("|") if item.strip()]
        if getattr(self.controller.document.title_block, name) != value:
            self.controller.update_title_block(**{name: value})
            self._changed()

    def _clear_frame(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def _refresh_inspector(self):
        self._clear_frame(self.properties)
        if not self.selected:
            ctk.CTkLabel(self.properties, text="Select a device, cable, or markup object.",
                         wraplength=250, justify="left", text_color=theme.TEXT_SECOND,
                         font=theme.ui_font(theme.SIZE["meta"])).grid(
                row=0, column=0, sticky="w", padx=10, pady=10)
        else:
            kind, ref = self.selected
            ctk.CTkLabel(self.properties, text=ref, anchor="w",
                         text_color=theme.TEXT,
                         font=theme.mono_font(theme.SIZE["meta"], "bold")).grid(
                row=0, column=0, sticky="ew", padx=10, pady=(9, 3))
            if kind == "route":
                self._build_cable_properties(ref)
            elif kind == "annotation":
                self._build_markup_properties(ref)

        self._clear_frame(self.validation_frame)
        issues = validate_riser(self.design, self.controller.document)
        if not issues:
            ctk.CTkLabel(self.validation_frame, text="✓ No riser warnings",
                         anchor="w", text_color=theme.SUCCESS).grid(
                row=0, column=0, sticky="ew")
        for row, issue in enumerate(issues):
            button = ctk.CTkButton(
                self.validation_frame, text=f"⚠  {issue.message}", anchor="w",
                height=30, fg_color=theme.WARNING_TINT, hover_color=theme.WARNING_ROW,
                text_color=theme.WARNING, command=lambda i=issue: self.goto_issue(i))
            button.grid(row=row, column=0, sticky="ew", pady=2)

        self._clear_frame(self.unplaced_frame)
        if not self.controller.document.unplaced:
            ctk.CTkLabel(self.unplaced_frame, text="No unplaced devices",
                         anchor="w", text_color=theme.TEXT_TERTIARY).grid(
                row=0, column=0, sticky="ew")
        for row, device_id in enumerate(self.controller.document.unplaced):
            ctk.CTkButton(
                self.unplaced_frame, text=f"Place  {device_id}", anchor="w",
                height=30, fg_color=theme.SURFACE_CHIP,
                hover_color=theme.HOVER_SUBTLE, text_color=theme.TEXT,
                command=lambda d=device_id: self._place_unplaced(d)).grid(
                    row=row, column=0, sticky="ew", pady=2)

    def _entry_row(self, row, label, value, callback):
        ctk.CTkLabel(self.properties, text=label, anchor="w",
                     text_color=theme.TEXT_SECOND,
                     font=theme.ui_font(theme.SIZE["meta"])).grid(
            row=row, column=0, sticky="w", padx=10, pady=(4, 1))
        variable = tk.StringVar(value=value)
        entry = ctk.CTkEntry(self.properties, textvariable=variable, height=27)
        entry.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 4))
        entry.bind("<FocusOut>", lambda _e: callback(variable.get()))
        entry.bind("<Return>", lambda _e: callback(variable.get()))
        return row + 2

    def _build_cable_properties(self, edge_id):
        edge = next((e for e in self.design.connections if e.id == edge_id), None)
        if not edge:
            return
        row = self._entry_row(1, "Cable type", edge.cable_type,
                              lambda v: self._set_edge(edge_id, cable_type=v))
        row = self._entry_row(row, "Quantity", str(edge.quantity),
                              lambda v: self._set_edge(edge_id, quantity=v))
        row = self._entry_row(row, "Custom label", edge.custom_label or "",
                              lambda v: self._set_edge(edge_id, custom_label=v))
        status = tk.StringVar(value=edge.status)
        ctk.CTkSegmentedButton(
            self.properties, values=["new", "existing"], variable=status,
            command=lambda value: self._set_edge(edge_id, status=value)).grid(
                row=row, column=0, sticky="ew", padx=10, pady=(4, 10))

    def _set_edge(self, edge_id, **changes):
        try:
            self.controller.update_connection(edge_id, **changes)
            self._changed()
        except (ValueError, TypeError) as exc:
            messagebox.showwarning("Invalid cable metadata", str(exc))
            self.redraw()

    def _build_markup_properties(self, annotation_id):
        annotation = next((a for a in self.controller.document.annotations
                           if a.id == annotation_id), None)
        if annotation is None:
            self.selected = None
            return
        row = 1
        if annotation.kind == "text":
            row = self._entry_row(row, "Text", annotation.text,
                                  lambda v: self._set_annotation(annotation_id, text=v))
            row = self._entry_row(
                row, "Font size", str(annotation.font_size),
                lambda v: self._set_annotation_number(annotation_id, "font_size", v))
            alignment = tk.StringVar(value=annotation.alignment)
            ctk.CTkSegmentedButton(
                self.properties, values=["left", "center", "right"],
                variable=alignment,
                command=lambda value: self._set_annotation(
                    annotation_id, alignment=value)).grid(
                        row=row, column=0, sticky="ew", padx=10, pady=4)
            row += 1
            weight = tk.StringVar(value=annotation.font_weight)
            ctk.CTkSegmentedButton(
                self.properties, values=["normal", "bold"], variable=weight,
                command=lambda value: self._set_annotation(
                    annotation_id, font_weight=value)).grid(
                        row=row, column=0, sticky="ew", padx=10, pady=4)
            row += 1
        row = self._entry_row(row, "Stroke", annotation.stroke,
                              lambda v: self._set_annotation(annotation_id, stroke=v))
        row = self._entry_row(
            row, "Stroke width", str(annotation.stroke_width),
            lambda v: self._set_annotation_number(annotation_id, "stroke_width", v))
        if annotation.kind in {"rectangle", "ellipse"}:
            row = self._entry_row(row, "Fill (blank = none)", annotation.fill,
                                  lambda v: self._set_annotation(annotation_id, fill=v))
        buttons = ctk.CTkFrame(self.properties, fg_color="transparent")
        buttons.grid(row=row, column=0, sticky="ew", padx=8, pady=(4, 8))
        for text, where in (("Back", "back"), ("Backward", "backward"),
                            ("Forward", "forward"), ("Front", "front")):
            ctk.CTkButton(
                buttons, text=text, width=54, height=25, fg_color=theme.SURFACE_CHIP,
                text_color=theme.TEXT, command=lambda w=where: self._arrange(annotation_id, w)
            ).pack(side="left", padx=1)

    def _set_annotation(self, annotation_id, **changes):
        try:
            self.controller.update_annotation(annotation_id, **changes)
            self._changed()
        except ValueError as exc:
            messagebox.showwarning("Invalid markup", str(exc))

    def _set_annotation_number(self, annotation_id, name, value):
        try:
            self._set_annotation(annotation_id, **{name: float(value)})
        except ValueError:
            messagebox.showwarning("Invalid markup", f"{name.replace('_', ' ').title()} must be a number")
            self.redraw()

    def _arrange(self, annotation_id, where):
        self.controller.arrange_annotation(annotation_id, where)
        self._changed()

    def _place_unplaced(self, device_id):
        x = self.controller.document.page_width / 2
        y = self.controller.document.page_height / 2
        element = self.controller.place_unplaced(device_id, x, y)
        self.selected = ("element", element.id)
        self._changed()

    def goto_issue(self, issue):
        ref = issue.ref
        if ref in self._title_entries:
            with contextlib.suppress(Exception):
                self.inspector._parent_canvas.yview_moveto(0.25)
            # Validation jumps are explicit navigation commands.  On macOS a
            # normal focus_set() can be ignored while another toplevel is
            # relinquishing focus, leaving the user at the right inspector
            # section but unable to type.  Force focus onto the actual Tk
            # entry so the jump is reliable across dialog/window transitions.
            self._title_entries[ref]._entry.focus_force()
            return
        if ref in self.controller.document.elements:
            self.selected = ("element", ref)
        elif ref in self.controller.document.routes:
            self.selected = ("route", ref)
        elif any(a.id == ref for a in self.controller.document.annotations):
            self.selected = ("annotation", ref)
        elif ref in self.controller.document.unplaced:
            with contextlib.suppress(Exception):
                self.inspector._parent_canvas.yview_moveto(1.0)
            return
        self.redraw()
        self.after_idle(self._center_ref_in_view, ref)

    def _center_ref_in_view(self, ref):
        points = []
        element = self.controller.document.elements.get(ref)
        if element:
            points = [(element.x, element.y),
                      (element.x + element.width, element.y + element.height)]
        route = self.controller.document.routes.get(ref)
        if route:
            points = route.points
        annotation = next((item for item in self.controller.document.annotations
                           if item.id == ref), None)
        if annotation:
            points = annotation.points
        if not points:
            return
        center = ((min(point[0] for point in points) + max(point[0] for point in points)) / 2,
                  (min(point[1] for point in points) + max(point[1] for point in points)) / 2)
        canvas_x, canvas_y = self._xy(center)
        region = self.canvas.cget("scrollregion").split()
        if len(region) != 4:
            return
        left, top, right, bottom = map(float, region)
        width, height = max(1.0, right - left), max(1.0, bottom - top)
        x_fraction = (canvas_x - self.canvas.winfo_width() / 2 - left) / width
        y_fraction = (canvas_y - self.canvas.winfo_height() / 2 - top) / height
        self.canvas.xview_moveto(max(0.0, min(1.0, x_fraction)))
        self.canvas.yview_moveto(max(0.0, min(1.0, y_fraction)))

    def refresh(self):
        self.cancel(redraw=False)
        self.controller.sync_from_design()
        self._reconcile_selection()
        for name, variable in self._title_vars.items():
            value = getattr(self.controller.document.title_block, name)
            variable.set(" | ".join(value) if name == "revisions" else str(value))
        self.redraw()
