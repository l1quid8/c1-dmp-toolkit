"""Direct riser editing controller and the CustomTkinter RISER workspace."""

from __future__ import annotations

import copy
import tkinter as tk
import uuid
from dataclasses import fields
from tkinter import messagebox, simpledialog

import customtkinter as ctk

import theme
from riser_model import DevicePortRef, RiserAnnotation, RiserDocument
from riser_scene import (
    GRID,
    TITLE_BLOCK_WIDTH,
    find_bridges,
    layout_riser,
    port_point,
    route_connection,
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

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _snapshot(self):
        return (copy.deepcopy(self.design.connections), copy.deepcopy(self.document))

    def _restore(self, snapshot) -> None:
        connections, document = copy.deepcopy(snapshot)
        self.design.connections = connections
        for field in fields(RiserDocument):
            setattr(self.document, field.name, getattr(document, field.name))
        self.design.riser_document = self.document
        project_legacy_topology(self.design)

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
        return result

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

    def move_element(self, element_id: str, dx: float, dy: float) -> None:
        def operation():
            element = self.document.elements[element_id]
            element.x += dx
            element.y += dy
            element.manual = True
            if element.kind == "device":
                self._reattach_routes(element.ref)
        self._mutate(operation)

    def resize_element(self, element_id: str, width: float, height: float) -> None:
        def operation():
            element = self.document.elements[element_id]
            element.width = max(36.0, width)
            element.height = max(28.0, height)
            element.manual = True
            if element.kind == "device":
                self._reattach_routes(element.ref)
        self._mutate(operation)

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
                old, adjacent = route.points[0], route.points[1]
                route.points[0] = new
                route.points[1] = ((new[0], adjacent[1]) if old[0] == adjacent[0]
                                   else (adjacent[0], new[1]))
            if edge.target.device_id == device_id:
                element = self.document.elements[f"device:{device_id}"]
                new = port_point(element, edge.target.port_id, output=False)
                old, adjacent = route.points[-1], route.points[-2]
                route.points[-1] = new
                route.points[-2] = ((new[0], adjacent[1]) if old[0] == adjacent[0]
                                    else (adjacent[0], new[1]))

    def move_route_point(self, connection_id: str, index: int,
                         point: tuple[float, float]) -> None:
        def operation():
            route = self.document.routes[connection_id]
            route.points[index] = tuple(point)
            route.manual = True
        self._mutate(operation)

    def reconnect(self, connection_id: str, *, source: DevicePortRef | None = None,
                  target: DevicePortRef | None = None,
                  replace_target: bool = False):
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
            self._reroute(edge.id)
            return edge
        return self._mutate(operation)

    def connect(self, source: DevicePortRef, target: DevicePortRef, *,
                replace_target: bool = False):
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
            self._reroute(edge.id)
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
        start = port_point(source, edge.source.port_id, output=True)
        end = port_point(target, edge.target.port_id, output=False)
        points = route_connection(start, end, obstacles,
                                  source_ref=source.ref, target_ref=target.ref)
        from riser_model import RiserRoute
        self.document.routes[connection_id] = RiserRoute(connection_id, points)

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
            for route_id, route in replacement.routes.items():
                self.document.routes.setdefault(route_id, route)
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
        self._tool_buttons: dict[str, ctk.CTkButton] = {}
        self._title_vars: dict[str, tk.StringVar] = {}
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
                           height=48)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        for name in self.TOOLS:
            button = ctk.CTkButton(
                bar, text=name, width=max(58, len(name) * 8), height=28,
                corner_radius=theme.RADIUS["button"],
                fg_color=theme.ACCENT_TINT if name == self.tool else "transparent",
                hover_color=theme.HOVER_SUBTLE, text_color=theme.TEXT,
                command=lambda value=name: self.set_tool(value))
            button.pack(side="left", padx=(6 if name == "Select" else 1, 0), pady=10)
            self._tool_buttons[name] = button

        ctk.CTkFrame(bar, width=1, fg_color=theme.BORDER).pack(
            side="left", fill="y", padx=8, pady=9)
        for text, command in (("Undo", self.undo), ("Redo", self.redo),
                              ("Duplicate", self.duplicate_selected),
                              ("Delete", self.delete_selected)):
            ctk.CTkButton(
                bar, text=text, width=68, height=28,
                fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                text_color=theme.TEXT, command=command).pack(side="left", padx=1)
        self._snap_switch = ctk.CTkSwitch(
            bar, text="Snap", width=66, command=self._toggle_snap,
            font=theme.ui_font(theme.SIZE["meta"]))
        self._snap_switch.select()
        self._snap_switch.pack(side="left", padx=(8, 2))
        self._grid_switch = ctk.CTkSwitch(
            bar, text="Grid", width=66, command=self._toggle_grid,
            font=theme.ui_font(theme.SIZE["meta"]))
        self._grid_switch.select()
        self._grid_switch.pack(side="left", padx=2)

        ctk.CTkButton(bar, text="Generate Riser", width=116, height=30,
                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                      command=self.on_generate).pack(side="right", padx=10)
        ctk.CTkButton(bar, text="Re-layout All", width=96, height=28,
                      fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                      text_color=theme.TEXT, command=self.relayout).pack(side="right")
        ctk.CTkButton(bar, text="Auto-layout", width=90, height=28,
                      fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                      text_color=theme.TEXT, command=self.auto_layout).pack(side="right")
        self._zoom_label = ctk.CTkLabel(
            bar, text="42%", width=46, text_color=theme.TEXT_SECOND,
            font=theme.ui_font(theme.SIZE["meta"]))
        self._zoom_label.pack(side="right", padx=(2, 8))
        ctk.CTkButton(bar, text="+", width=28, height=28,
                      fg_color="transparent", text_color=theme.TEXT,
                      command=lambda: self.set_zoom(self.zoom * 1.2)).pack(side="right")
        ctk.CTkButton(bar, text="−", width=28, height=28,
                      fg_color="transparent", text_color=theme.TEXT,
                      command=lambda: self.set_zoom(self.zoom / 1.2)).pack(side="right")

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
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self.set_zoom(self.zoom * 1.1))
        self.canvas.bind("<Button-5>", lambda _e: self.set_zoom(self.zoom / 1.1))

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
        self.canvas.bind("<Escape>", lambda _e: self.cancel())
        self.canvas.bind("<Delete>", lambda _e: self.delete_selected())
        self.canvas.bind("<BackSpace>", lambda _e: self.delete_selected())
        self.canvas.bind("<Control-z>", lambda _e: self.undo())
        self.canvas.bind("<Control-y>", lambda _e: self.redo())
        self.canvas.bind("<Control-d>", lambda _e: self.duplicate_selected())
        self.canvas.bind("<Command-z>", lambda _e: self.undo())
        self.canvas.bind("<Command-Shift-Z>", lambda _e: self.redo())
        self.canvas.bind("<Command-d>", lambda _e: self.duplicate_selected())
        for key, delta in (("Left", (-GRID, 0)), ("Right", (GRID, 0)),
                           ("Up", (0, -GRID)), ("Down", (0, GRID))):
            self.canvas.bind(f"<{key}>", lambda _e, d=delta: self.nudge(*d))

    # ---- drawing ------------------------------------------------------

    def _xy(self, point):
        return point[0] * self.zoom + 24, point[1] * self.zoom + 24

    def _world(self, event):
        return ((self.canvas.canvasx(event.x) - 24) / self.zoom,
                (self.canvas.canvasy(event.y) - 24) / self.zoom)

    def _snap(self, point):
        if not self.snap_enabled:
            return point
        return tuple(round(value / GRID) * GRID for value in point)

    def redraw(self):
        self.canvas.delete("all")
        doc = self.controller.document
        width, height = doc.page_width * self.zoom, doc.page_height * self.zoom
        self.canvas.create_rectangle(24, 24, 24 + width, 24 + height,
                                     fill="#ffffff", outline="#c7cdd4", width=1,
                                     tags=("page",))
        if self.grid_enabled and self.zoom >= 0.25:
            step = GRID * self.zoom
            x = 24.0
            while x <= 24 + width:
                self.canvas.create_line(x, 24, x, 24 + height,
                                        fill="#edf0f2", tags=("grid",))
                x += step
            y = 24.0
            while y <= 24 + height:
                self.canvas.create_line(24, y, 24 + width, y,
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
        self.canvas.configure(scrollregion=(0, 0, width + 48, height + 48))
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
            self.canvas.create_text(x1 + 8, y1 + 7, text=element.ref,
                                    anchor="nw", fill="#4f5964",
                                    font=("TkDefaultFont", max(7, round(10 * self.zoom))),
                                    tags=(tag, "selectable"))
            return
        fill = "#fff7e8" if element.stale else "#ffffff"
        self.canvas.create_rectangle(
            x1, y1, x2, y2, fill=fill,
            outline="#4a7bb8" if selected else "#1b2430",
            width=3 if selected else 1.4, tags=(tag, "selectable"))
        kind = self._device_kind_label(element.ref)
        self.canvas.create_text((x1 + x2) / 2, y1 + 12 * self.zoom,
                                text=kind, fill="#5c6570",
                                font=("TkDefaultFont", max(6, round(8 * self.zoom))),
                                tags=(tag, "selectable"))
        self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2 + 5 * self.zoom,
                                text=element.ref, fill="#111111",
                                font=("TkDefaultFont", max(7, round(13 * self.zoom)), "bold"),
                                tags=(tag, "selectable"))
        for ref, output in self._ports_for(element.ref):
            px, py = self._xy(port_point(element, ref.port_id, output=output))
            port_tag = f"port|{ref.device_id}|{ref.port_id}|{'out' if output else 'in'}"
            self.canvas.create_oval(px - 4, py - 4, px + 4, py + 4,
                                    fill="#4a7bb8" if output else "#ffffff",
                                    outline="#1b2430", width=1,
                                    tags=(port_tag, "port"))

    def _device_kind_label(self, device_id):
        if device_id == "MSP":
            return "XR550 CONTROL PANEL"
        if device_id.startswith("710-"):
            return "DMP 710 MODULE"
        if device_id.startswith("RSP-"):
            return "REMOTE POWER SUPPLY"
        return "KEYPAD"

    def _ports_for(self, device_id):
        if device_id == "MSP":
            used = {c.source.port_id for c in self.design.connections
                    if c.source.device_id == "MSP"}
            available = {"KP BUS", "PROG", "LX500", "LX600", "LX700", "LX800", "LX900"}
            return [(DevicePortRef("MSP", p), True) for p in sorted(used | available)]
        if device_id.startswith("710-"):
            return [(DevicePortRef(device_id, "IN"), False)] + [
                (DevicePortRef(device_id, f"OUT{i}"), True) for i in range(1, 4)]
        return [(DevicePortRef(device_id, "IN"), False)]

    def _draw_routes(self):
        edges = {edge.id: edge for edge in self.design.connections}
        selected_id = self.selected[1] if self.selected and self.selected[0] == "route" else None
        for route_id, route in self.controller.document.routes.items():
            if len(route.points) < 2:
                continue
            coords = [value for point in route.points for value in self._xy(point)]
            tag = f"route|{route_id}"
            self.canvas.create_line(
                *coords, fill="#111111", width=3 if route_id == selected_id else 1.5,
                joinstyle="round", tags=(tag, "selectable"))
            edge = edges.get(route_id)
            if edge:
                midpoint = route.points[len(route.points) // 2]
                lx, ly = self._xy((midpoint[0] + route.label_offset[0],
                                   midpoint[1] + route.label_offset[1]))
                self.canvas.create_text(
                    lx, ly - 5, text=edge.label, anchor="s", fill="#111111",
                    font=("TkDefaultFont", max(6, round(9 * self.zoom))),
                    tags=(tag, "selectable"))
        for bridge in find_bridges({key: value.points for key, value
                                    in self.controller.document.routes.items()}):
            x, y = self._xy((bridge.x, bridge.y))
            radius = max(3, 7 * self.zoom)
            self.canvas.create_arc(x - radius, y - radius, x + radius, y + radius,
                                   start=0, extent=180, style="arc", outline="#111111",
                                   width=1.5, tags=(f"route|{bridge.connection_id}",))

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
        values = ["C1", title.school_name, title.address, title.project_title,
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
                      "bold" if index in {0, 1, 4} else "normal"), tags=("titleblock",))
            y += (50 if index in {0, 1, 2, 3, 4} else 35) * self.zoom

    def _draw_selection(self):
        if not self.selected:
            return
        kind, ref = self.selected
        if kind == "element":
            element = self.controller.document.elements.get(ref)
            if not element:
                return
            x, y = self._xy((element.x + element.width, element.y + element.height))
            self.canvas.create_rectangle(x - 5, y - 5, x + 5, y + 5,
                                         fill="#ffffff", outline="#4a7bb8", width=2,
                                         tags=("resize-handle",))
        elif kind == "route":
            route = self.controller.document.routes.get(ref)
            if route:
                for index, point in enumerate(route.points):
                    x, y = self._xy(point)
                    self.canvas.create_rectangle(
                        x - 4, y - 4, x + 4, y + 4, fill="#ffffff",
                        outline="#4a7bb8", width=2,
                        tags=(f"route-handle|{ref}|{index}",))

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
        for prefix, kind in (("annotation|", "annotation"), ("element|", "element"),
                             ("route|", "route")):
            token = next((tag for tag in tags if tag.startswith(prefix)), None)
            if token:
                return kind, token.split("|", 1)[1]
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
        if self.tool in {"Polyline", "Rectangle", "Ellipse", "Arrow"}:
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
            old_point = self.controller.document.routes[route_id].points[point_index]
            self._gesture = ("route", route_id, point_index, old_point)
            return
        picked = self._parse_selectable(tags)
        self.selected = picked
        if picked and picked[0] == "element" and self.tool == "Select":
            element = self.controller.document.elements[picked[1]]
            self._gesture = ("move", picked[1], point, element.x, element.y)
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
            self.redraw()
        elif kind == "resize":
            _, element_id, start, old_w, old_h = self._gesture
            element = self.controller.document.elements[element_id]
            element.width = max(36, old_w + point[0] - start[0])
            element.height = max(28, old_h + point[1] - start[1])
            self.redraw()
        elif kind == "route":
            _, route_id, index, _old_point = self._gesture
            route = self.controller.document.routes[route_id]
            route.points[index] = point
            self.redraw()

    def _on_release(self, event):
        if not self._gesture:
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
            self.controller.move_element(element_id, new_x - old_x, new_y - old_y)
            self._changed()
        elif kind == "resize":
            _, element_id, _start, old_w, old_h = gesture
            element = self.controller.document.elements[element_id]
            new_w, new_h = element.width, element.height
            element.width, element.height = old_w, old_h
            self.controller.resize_element(element_id, new_w, new_h)
            self._changed()
        elif kind == "route":
            _, route_id, index, old = gesture
            route = self.controller.document.routes[route_id]
            # Drag preview already wrote the final point. Restore from the undo
            # snapshot's latest state, then issue one semantic command.
            route.points[index] = old
            is_endpoint = index in {0, len(route.points) - 1}
            if is_endpoint:
                port = self._parse_port(self._tags_at_event(event))
                if port:
                    ref, is_output = port
                    edge = next(edge for edge in self.design.connections
                                if edge.id == route_id)
                    try:
                        if index == 0 and is_output:
                            self.controller.reconnect(route_id, source=ref)
                            self._changed()
                        elif index == len(route.points) - 1 and not is_output:
                            occupied = any(e.id != edge.id and e.target == ref
                                           for e in self.design.connections)
                            replace = occupied and messagebox.askyesno(
                                "Reconnect port?",
                                f"{ref.device_id} {ref.port_id} is connected. Replace it?")
                            if not occupied or replace:
                                self.controller.reconnect(
                                    route_id, target=ref, replace_target=replace)
                                self._changed()
                    except TopologyError as exc:
                        messagebox.showwarning("Connection not allowed", str(exc))
            else:
                self.controller.move_route_point(route_id, index, point)
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
            return
        if is_output:
            self._connect_source = ref
            return
        try:
            occupied = any(edge.target == ref for edge in self.design.connections)
            replace = occupied and messagebox.askyesno(
                "Reconnect port?", f"{ref.device_id} {ref.port_id} is connected. Replace it?")
            if occupied and not replace:
                return
            edge = self.controller.connect(self._connect_source, ref,
                                           replace_target=replace)
            self.selected = ("route", edge.id)
            self._connect_source = None
            self._changed()
        except TopologyError as exc:
            messagebox.showwarning("Connection not allowed", str(exc))

    def _on_double_click(self, event):
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
                _, route_id, index, old = self._gesture
                route = self.controller.document.routes.get(route_id)
                if route and index < len(route.points):
                    route.points[index] = old
            elif self._gesture[0] == "move":
                _, element_id, _start, old_x, old_y = self._gesture
                element = self.controller.document.elements.get(element_id)
                if element:
                    element.x, element.y = old_x, old_y
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
        self.zoom = min(1.75, max(0.18, value))
        self._zoom_label.configure(text=f"{round(self.zoom * 100)}%")
        self.redraw()

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
        annotation = next(a for a in self.controller.document.annotations
                          if a.id == annotation_id)
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
        if ref in self.controller.document.elements:
            self.selected = ("element", ref)
        elif ref in self.controller.document.routes:
            self.selected = ("route", ref)
        elif any(a.id == ref for a in self.controller.document.annotations):
            self.selected = ("annotation", ref)
        elif ref in self.controller.document.unplaced:
            return
        self.redraw()

    def refresh(self):
        sync_riser_document(self.design, self.controller.document)
        for name, variable in self._title_vars.items():
            value = getattr(self.controller.document.title_block, name)
            variable.set(" | ".join(value) if name == "revisions" else str(value))
        self.redraw()
