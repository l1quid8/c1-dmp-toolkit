"""Direct riser editing controller and the CustomTkinter RISER workspace."""

from __future__ import annotations

import copy
import contextlib
import math
import re
import tkinter as tk
import uuid
from functools import wraps
from dataclasses import fields, replace
from tkinter import messagebox, simpledialog
from tkinter import font as tkfont

import customtkinter as ctk
from PIL import Image, ImageTk

import theme
from paths import resource_path
from project_locations import (location_values, plan_location_rename, restore_locations,
                               sync_project_locations, edit_location, assign_location,
                               rename_equipment_location, equipment_location_choices,
                               assign_location_name, is_equipment_location_name)
from location_model import location_key
from ui_widgets import SearchableComboBox
from riser_drawing import (TextRun, device_shape, device_text, location_text_runs,
                           title_bounds, logo_bounds, title_text)
from riser_symbols import symbol_parts, modern_title, detailed_size
from riser_presentation import layout_presentation
from riser_model import DevicePortRef, RiserAnnotation, RiserDocument
from riser_scene import (
    GRID,
    INPUT_SIDES,
    input_side_point,
    route_rsp_input,
    find_bridges,
    layout_riser,
    layout_clusters,
    repair_generated_scene,
    _normal_location,
    _segment_hits_rect,
    port_point,
    reattach_route_endpoint,
    reattach_source_route,
    reserved_route_segments,
    routing_obstacles,
    route_label_point,
    route_topology_connection,
    sync_riser_document,
    sync_location_ownership,
    validate_riser,
    wire_segments,
)
from topology_service import TopologyError
from topology_service import connect as topology_connect
from topology_service import disconnect as topology_disconnect
from topology_service import reconnect as topology_reconnect
from topology_service import project_legacy_topology


def live_edit_only(method):
    """Preview is read-only, including toolbar and keyboard command paths."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if self._layout_preview is None:
            return method(self, *args, **kwargs)
    return guarded


class RiserEditorController:
    """Display-independent command controller used by the Tk canvas."""

    def __init__(self, design, document: RiserDocument):
        self.design = design
        self.document = document
        self.design.riser_document = document
        sync_project_locations(self.design)
        self._undo: list[tuple[tuple, tuple]] = []
        self._redo: list[tuple[tuple, tuple]] = []
        self._external_signature = self._topology_signature()
        self._external_locations = location_values(self.design)
        self._external_registry = self._registry_snapshot()
        self._preview_basis = None

    def _registry_snapshot(self):
        return (copy.deepcopy(self.design.equipment_locations),
                dict(self.design.device_location_ids), dict(self.design.location_sync_values))

    def _topology_signature(self):
        return (tuple(sorted((e.id, e.source.device_id, e.source.port_id,
                              e.target.device_id, e.target.port_id) for e in self.design.connections)),
                tuple(sorted(s.id for s in self.design.splitters)),
                tuple(sorted(r.number for r in self.design.rsps)),
                tuple(sorted(k.number for k in self.design.keypads)))

    def sync_external(self):
        self._rebase_external_history()
        sync_riser_document(self.design, self.document)

    def _rebase_external_history(self):
        """Guard commands even before another tab's debounced redraw fires."""
        signature = self._topology_signature()
        locations = location_values(self.design)
        sync_project_locations(self.design)
        registry = self._registry_snapshot()
        has_location_history = any(before[2:] != after[2:]
                                   for before, after in self._undo + self._redo)
        if (signature != self._external_signature or
                (has_location_history and (locations != self._external_locations or
                                          registry != self._external_registry))):
            self.clear_history()
        self._external_signature = signature
        self._external_locations = locations
        self._external_registry = registry

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _snapshot(self):
        return (copy.deepcopy(self.design.connections), copy.deepcopy(self.document),
                location_values(self.design), self._registry_snapshot())

    def _restore(self, snapshot, location_refs=(), restore_registry=False) -> None:
        connections, document, locations, registry = copy.deepcopy(snapshot)
        restore_locations(self.design, locations, location_refs)
        if restore_registry:
            (self.design.equipment_locations, self.design.device_location_ids,
             self.design.location_sync_values) = registry
        topology_changed = self.design.connections != connections
        self.design.connections = connections
        for field in fields(RiserDocument):
            setattr(self.document, field.name, getattr(document, field.name))
        self.design.riser_document = self.document
        if topology_changed:
            project_legacy_topology(self.design)
        sync_location_ownership(self.design, self.document)
        self._external_signature = self._topology_signature()
        self._external_locations = location_values(self.design)
        self._external_registry = self._registry_snapshot()

    def _mutate(self, operation):
        self._rebase_external_history()
        before = self._snapshot()
        try:
            result = operation()
        except Exception:
            self._restore(before, before[2], True)
            raise
        after = self._snapshot()
        self._external_signature = self._topology_signature()
        self._external_locations = location_values(self.design)
        self._external_registry = self._registry_snapshot()
        if before != after:
            self._undo.append((before, after))
            self._redo.clear()
        return result

    def clear_history(self) -> None:
        """Rebase undo/redo after another editor changes shared topology."""
        self._undo.clear()
        self._redo.clear()

    def undo(self) -> bool:
        self._rebase_external_history()
        if not self._undo:
            return False
        before, after = self._undo.pop()
        self._restore(before, [ref for ref in before[2] if before[2][ref] != after[2].get(ref)], before[3] != after[3])
        self._redo.append((before, after))
        return True

    def redo(self) -> bool:
        self._rebase_external_history()
        if not self._redo:
            return False
        before, after = self._redo.pop()
        self._restore(after, [ref for ref in after[2] if after[2][ref] != before[2].get(ref)], before[3] != after[3])
        self._undo.append((before, after))
        return True

    def rename_location(self, element_id: str, new_name: str, *, allow_merge=False) -> bool:
        updates, merging = plan_location_rename(self.design, self.document, element_id, new_name)
        if merging and not allow_merge:
            raise ValueError('That location already exists; confirm combining the location groups.')
        if not updates:
            return False

        def operation():
            frame = self.document.elements[element_id]
            primary = 'MSP' if 'MSP' in updates else next(iter(updates))
            identity = frame.physical_location_id or self.design.device_location_ids[primary]
            identity = rename_equipment_location(self.design, identity, new_name, allow_merge=allow_merge)
            for ref in updates:
                assign_location(self.design, ref, identity)
            sync_location_ownership(self.design, self.document)
        self._mutate(operation)
        return True

    def edit_location(self, identity, **values):
        def operation():
            edit_location(self.design, identity, **values)
            sync_location_ownership(self.design, self.document)
        self._mutate(operation)

    def assign_location(self, device_id, identity):
        def operation():
            assign_location(self.design, device_id, identity)
            sync_location_ownership(self.design, self.document)
        self._mutate(operation)

    def assign_location_name(self, device_id, name):
        def operation():
            assign_location_name(self.design, device_id, name)
            sync_location_ownership(self.design, self.document)
        self._mutate(operation)

    def preview_layout(self):
        preview = layout_presentation(self.design, title_source=self.document)
        self._preview_basis = (copy.deepcopy(self.design), copy.deepcopy(preview))
        return preview

    def apply_layout(self, preview):
        if (self._preview_basis is None or self.design != self._preview_basis[0]
                or preview != self._preview_basis[1]):
            raise ValueError('The project changed. Preview the layout again before applying.')
        def operation():
            for field in fields(RiserDocument):
                setattr(self.document, field.name, copy.deepcopy(getattr(preview, field.name)))
        self._mutate(operation)
        self._preview_basis = None

    def move_element(self, element_id: str, dx: float, dy: float) -> bool:
        if not dx and not dy:
            return False

        def operation():
            element = self.document.elements[element_id]
            members = []
            shared_routes = {}
            if element.kind == "location":
                members = [
                    candidate for candidate in self.document.elements.values()
                    if candidate.kind == "device"
                    and candidate.location_id == element.id
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
                for route_id, points in shared_routes.items():
                    route = self.document.routes.get(route_id)
                    if route is not None:
                        route.points = [(x + dx, y + dy) for x, y in points]
                        route.manual = True
                for member in members:
                    self._reattach_routes(member.ref, skip_routes=shared_routes)
            self._repair_obstructed_routes({element.id, *(member.id for member in members)})
        self._mutate(operation)
        return True

    def move_selection(self, selection, dx, dy):
        """Move a group atomically; location descendants and shared wires move once."""
        if not selection or (not dx and not dy):
            return False
        before = self._snapshot()
        self._mutate(lambda: self._translate_selection(selection, dx, dy))
        return before != self._snapshot()

    def _translate_selection(self, selection, dx, dy, *, preview=False):
        doc = self.document
        selected = set(selection)
        element_ids = {ref for kind, ref in selected if kind == 'element' and ref in doc.elements}
        owners = {key for key in element_ids if doc.elements[key].kind == 'location'}
        element_ids.update(e.id for e in doc.elements.values()
                           if e.kind == 'device' and e.location_id in owners)
        refs = {doc.elements[key].ref for key in element_ids if doc.elements[key].kind == 'device'}
        label_origins = {key: route_label_point(route.points) for key, route in doc.routes.items()
                         if ('label', key) in selected}
        for key in element_ids:
            element = doc.elements[key]
            element.x += dx
            element.y += dy
            element.manual = True
        for edge in self.design.connections:
            route = doc.routes.get(edge.id)
            if route is None or len(route.points) < 2:
                continue
            source_moves = edge.source.device_id in refs
            target_moves = edge.target.device_id in refs
            if not (source_moves or target_moves or ('route', edge.id) in selected):
                continue
            if edge.target.device_id.startswith(('RSP-', 'KEYPAD-')) and not (source_moves and target_moves):
                route.points = self._route_rsp(edge, old_points=route.points if route.manual else (),
                                               preview=preview)
                route.manual = True
                continue
            points = list(route.points)
            if (source_moves and target_moves) or ('route', edge.id) in selected:
                points = [(x + dx, y + dy) for x, y in points]
            for endpoint_ref, at_start in ((edge.source, True), (edge.target, False)):
                element = doc.elements.get(f'device:{endpoint_ref.device_id}')
                if element is not None:
                    endpoint = port_point(element, endpoint_ref.port_id, output=at_start)
                    # Pure group translation preserves the complete internal wire shape.
                    if not (source_moves and target_moves):
                        points = reattach_route_endpoint(points, endpoint, at_start=at_start)
            route.points = points
            route.manual = True
        if not preview:
            self._repair_obstructed_routes(element_ids,
                route_ids={ref for kind, ref in selected if kind == 'route'})
        for key, origin in label_origins.items():
            route = doc.routes[key]
            new_origin = route_label_point(route.points)
            anchor_delta = ((new_origin[0] - origin[0], new_origin[1] - origin[1])
                            if origin is not None and new_origin is not None else (0, 0))
            route.label_offset = (route.label_offset[0] + dx - anchor_delta[0],
                                  route.label_offset[1] + dy - anchor_delta[1])
            route.label_manual = True
        for annotation in doc.annotations:
            if ('annotation', annotation.id) in selected:
                annotation.points = [(x + dx, y + dy) for x, y in annotation.points]

    def resize_element(self, element_id: str, width: float, height: float) -> bool:
        element = self.document.elements[element_id]
        minimum_height = 54.0 if element.kind == "location" else 28.0
        width, height = max(36.0, width), max(minimum_height, height)
        if element.kind == 'device' and element.symbol_style == 'detailed':
            minimum_width, minimum_height = detailed_size(self.design, element.ref)
            width, height = max(width,minimum_width), max(height,minimum_height)
        if element.width == width and element.height == height:
            return False

        def operation():
            element = self.document.elements[element_id]
            element.width = width
            element.height = height
            element.manual = True
            if element.kind == "device":
                self._reattach_routes(element.ref)
            self._repair_obstructed_routes({element.id})
        self._mutate(operation)
        return True

    def set_input_side(self, element_id, side):
        element = self.document.elements[element_id]
        if not element.ref.startswith(('RSP-', 'KEYPAD-')) or side not in (*INPUT_SIDES, 'auto'):
            raise ValueError('Choose Auto or an RSP/keypad input side')
        def operation():
            element.input_side_locked = side != 'auto'
            if side != 'auto':
                element.input_side = side
            self._reattach_routes(element.ref)
        self._mutate(operation)

    def _route_rsp(self, edge, *, old_points=(), preview=False):
        """Share exterior input routing between RSPs and keypads."""
        source = self.document.elements[f'device:{edge.source.device_id}']
        target = self.document.elements[f'device:{edge.target.device_id}']
        # Pointer frames only protect the two endpoint symbols. Searching all
        # devices and reserved wire lanes can block Tk for a dense drawing.
        # Mouse-up restores the original scene and performs full routing once.
        obstacles = [source, target] if preview else routing_obstacles(self.document, self.design)
        reserved = () if preview else reserved_route_segments(
            self.document, exclude_id=edge.id,
            live_ids={connection.id for connection in self.design.connections})
        side, points = route_rsp_input(
            edge, source, target, obstacles,
            reserved_segments=reserved, old_points=old_points)
        target.input_side = side
        return points

    def _reattach_routes(self, device_id: str, *, skip_routes=()) -> None:
        for edge in self.design.connections:
            if edge.id in skip_routes:
                continue
            if device_id not in {edge.source.device_id, edge.target.device_id}:
                continue
            route = self.document.routes.get(edge.id)
            if route is not None and edge.target.device_id.startswith(('RSP-', 'KEYPAD-')):
                route.points = self._route_rsp(edge, old_points=route.points if route.manual else ())
                continue
            if route is None or not route.manual or len(route.points) < 2:
                self._reroute(edge.id)
                continue
            if edge.source.device_id == device_id:
                element = self.document.elements[f"device:{device_id}"]
                route.points = reattach_source_route(
                    edge, element, route.points, routing_obstacles(self.document,self.design))
            if edge.target.device_id == device_id:
                element = self.document.elements[f"device:{device_id}"]
                new = port_point(element, edge.target.port_id, output=False)
                route.points = reattach_route_endpoint(
                    route.points, new, at_start=False)

    def _repair_obstructed_routes(self, element_ids, *, route_ids=()):
        """Repair wires blocked by a completed geometry edit, within its undo step.

        Unrelated wires need checking too: a moved device can land on a feed
        to another device. Keep this out of pointer previews, and leave clear
        routes and all route metadata untouched.
        """
        doc = self.document
        refs = {doc.elements[key].ref for key in element_ids if key in doc.elements
                and doc.elements[key].kind == 'device'}
        obstacles = routing_obstacles(doc, self.design)
        captions = tuple(f'caption:{key}:' for key in element_ids)
        headings = {f'heading:{key}' for key in element_ids}
        changed = [obstacle for obstacle in obstacles
                   if obstacle.id in element_ids
                   or obstacle.id in headings
                   or obstacle.id.startswith(captions)]
        changed_ids = {obstacle.id for obstacle in changed}
        stationary = [obstacle for obstacle in obstacles if obstacle.id not in changed_ids]
        live_ids = {edge.id for edge in self.design.connections}
        for edge in self.design.connections:
            route = doc.routes.get(edge.id)
            source = doc.elements.get(f'device:{edge.source.device_id}')
            target = doc.elements.get(f'device:{edge.target.device_id}')
            if route is None or source is None or target is None:
                continue
            # Reattached wires must clear every device; stationary wires only
            # need checking against the footprints that just moved or grew.
            if source.ref in refs and target.ref in refs:
                # Internal wires and their equipment moved by the same delta.
                # Preserve that relative geometry, checking only fixed objects
                # the translated wire may have newly encountered.
                probes = stationary
            else:
                probes = (obstacles if edge.id in route_ids or
                          refs.intersection((source.ref, target.ref)) else changed)
            if not any(_segment_hits_rect(a, b, obstacle, clearance=0)
                       for a, b in zip(route.points, route.points[1:]) for obstacle in probes):
                continue
            if target.ref.startswith(('RSP-', 'KEYPAD-')):
                route.points = self._route_rsp(edge)
            else:
                route.points = route_topology_connection(
                    edge, source, target, obstacles,
                    reserved_segments=reserved_route_segments(doc, exclude_id=edge.id, live_ids=live_ids))

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
                  replace_target: bool = False, input_side: str | None = None):
        current = next(c for c in self.design.connections if c.id == connection_id)
        if ((source is None or source == current.source)
                and (target is None or target == current.target)):
            if input_side is not None:
                self.set_input_side(f'device:{current.target.device_id}', input_side)
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
            if input_side is not None:
                element = self.document.elements[f'device:{edge.target.device_id}']
                if not element.ref.startswith(('RSP-', 'KEYPAD-')) or input_side not in INPUT_SIDES:
                    raise ValueError('Choose an RSP/keypad input side')
                element.input_side = input_side
                element.input_side_locked = True
                self._reroute(edge.id)
            return edge
        return self._mutate(operation)

    def connect(self, source: DevicePortRef, target: DevicePortRef, *,
                replace_target: bool = False, input_side: str | None = None):
        existing = next((edge for edge in self.design.connections
                         if edge.source == source and edge.target == target), None)
        if existing is not None:
            if input_side is not None:
                self.set_input_side(f'device:{target.device_id}', input_side)
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
            if input_side is not None:
                element = self.document.elements[f'device:{edge.target.device_id}']
                if not element.ref.startswith(('RSP-', 'KEYPAD-')) or input_side not in INPUT_SIDES:
                    raise ValueError('Choose an RSP/keypad input side')
                element.input_side = input_side
                element.input_side_locked = True
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

    def update_label(self, connection_id, *, offset=None, hidden=None, reset=False):
        """Drawing-only callout change; never remove cable data or connectivity."""
        if offset is not None and (len(offset) != 2 or
                                   not all(math.isfinite(v) for v in offset)):
            raise ValueError('Label position must contain two finite coordinates')

        def operation():
            route = self.document.routes[connection_id]
            if offset is not None or reset:
                route.label_offset = (0.0, 0.0) if reset else tuple(offset)
                route.label_manual = not reset
            if hidden is not None:
                route.label_hidden = bool(hidden)
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
        obstacles = routing_obstacles(self.document,self.design)
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
        if target.ref.startswith(('RSP-', 'KEYPAD-')):
            points = self._route_rsp(edge)
        from riser_model import RiserRoute
        previous = self.document.routes.get(connection_id)
        label_offset = previous.label_offset if previous is not None else (0.0, 0.0)
        self.document.routes[connection_id] = RiserRoute(
            connection_id, points, label_offset=label_offset,
            label_hidden=previous.label_hidden if previous else False,
            label_manual=previous.label_manual if previous else False)

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
            if name in changes and (not math.isfinite(float(changes[name])) or float(changes[name]) <= 0):
                raise ValueError(f"{name.replace('_', ' ').title()} must be a finite positive number")
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
            template = self._layout().elements[f"device:{device_id}"]
            template.x, template.y, template.manual = x, y, True
            self.document.elements[template.id] = template
            self.document.z_order.append(template.id)
            self.document.unplaced.remove(device_id)
            sync_location_ownership(self.design, self.document)
            for edge in self.design.connections:
                if device_id in {edge.source.device_id, edge.target.device_id}:
                    self._reroute(edge.id)
            return template
        return self._mutate(operation)

    def _layout(self):
        builder = (layout_presentation if self.document.layout_version >= 3 else
                   layout_clusters if self.document.layout_version >= 2 else layout_riser)
        return builder(self.design, title_source=self.document)

    def relayout(self) -> None:
        def operation():
            replacement = self._layout()
            for field in fields(RiserDocument):
                setattr(self.document, field.name, getattr(replacement, field.name))
            self.design.riser_document = self.document
        self._mutate(operation)

    def _insert_missing_clusters(self, replacement):
        """Fit only new equipment into clear space; retain everything else."""
        from riser_scene import PAGE_MARGIN, TITLE_BLOCK_WIDTH, _boxes_overlap

        def box(item):
            return (item.x, item.y, item.x + item.width, item.y + item.height)

        def position(item, bounds, obstacles):
            left, top, right, bottom = bounds
            right -= item.width
            bottom -= item.height
            if right < left or bottom < top:
                return None
            candidates = [(item.x, item.y)] + [
                (x, y) for y in range(math.ceil(top / GRID), math.floor(bottom / GRID) + 1)
                for x in range(math.ceil(left / GRID), math.floor(right / GRID) + 1)]
            candidates[1:] = [(x * GRID, y * GRID) for x, y in candidates[1:]]
            candidates.sort(key=lambda p: (abs(p[0] - item.x) + abs(p[1] - item.y), p[1], p[0]))
            for x, y in candidates:
                candidate = (x - 8, y - 8, x + item.width + 8, y + item.height + 8)
                if (left <= x <= right and top <= y <= bottom
                        and not any(_boxes_overlap(candidate, rect) for rect in obstacles)):
                    return x, y
            return None

        page = (PAGE_MARGIN, PAGE_MARGIN,
                self.document.page_width - TITLE_BLOCK_WIDTH - PAGE_MARGIN,
                self.document.page_height - PAGE_MARGIN)
        # Existing manual cables are protected too, not rerouted for new items.
        cable_boxes = [(min(a[0], b[0])-4, min(a[1], b[1])-4,
                        max(a[0], b[0])+4, max(a[1], b[1])+4)
                       for route in self.document.routes.values()
                       for a, b in zip(route.points, route.points[1:])]
        existing_frames = {e.physical_location_id: e for e in self.document.elements.values()
                           if e.kind == 'location'}
        for frame in (e for e in replacement.elements.values() if e.kind == 'location'):
            missing = [e for e in replacement.elements.values()
                       if e.kind == 'device' and e.location_id == frame.id
                       and e.id not in self.document.elements]
            if not missing:
                continue
            owner = existing_frames.get(frame.physical_location_id)
            if owner is None:
                obstacles = [box(e) for e in self.document.elements.values()] + cable_boxes
                found = position(frame, page, obstacles)
                if found is None:
                    continue
                dx, dy = found[0] - frame.x, found[1] - frame.y
                frame.x, frame.y = found
                self.document.elements[frame.id] = frame
                self.document.z_order.insert(0, frame.id)
                for item in missing:
                    item.x += dx
                    item.y += dy
                    self.document.elements[item.id] = item
                    self.document.z_order.append(item.id)
            else:
                bounds = (max(page[0], owner.x + 16), max(page[1], owner.y + owner.heading_height + 24),
                          min(page[2], owner.x + owner.width - 16), min(page[3], owner.y + owner.height - 16))
                for item in missing:
                    obstacles = [box(e) for e in self.document.elements.values()
                                 if e.kind == 'device'] + cable_boxes
                    found = position(item, bounds, obstacles)
                    if found is not None:
                        item.x, item.y = found
                        item.location_id = owner.id
                        self.document.elements[item.id] = item
                        self.document.z_order.append(item.id)

    def auto_layout(self) -> None:
        """Place only missing items while retaining every existing object/route."""
        def operation():
            repair_generated_scene(self.design, self.document)
            sync_riser_document(self.design, self.document)
            replacement = self._layout()
            existing_locations = {_normal_location(e.ref) for e in self.document.elements.values()
                                  if e.kind == "location"}
            if self.document.layout_version >= 2:
                self._insert_missing_clusters(replacement)
            for element_id, element in (replacement.elements.items()
                                        if self.document.layout_version < 2 else ()):
                if element_id not in self.document.elements:
                    if element.kind == "location" and _normal_location(element.ref) in existing_locations:
                        continue  # renamed frames keep stable IDs
                    self.document.elements[element_id] = element
                    self.document.z_order.append(element_id)
            sync_location_ownership(self.design, self.document)
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

    @property
    def selected(self):
        """Single-object actions are available only for a single selection."""
        return self.selection[0] if len(self.selection) == 1 else None

    @selected.setter
    def selected(self, value):
        self.selection = [value] if value is not None else []

    def __init__(self, master, session, on_edit, on_generate=None, on_remove_hardware=None,
                 on_add_hardware=None, on_edit_hardware=None, on_toggle_fullscreen=None):
        super().__init__(master, fg_color=theme.APP_BG, corner_radius=0)
        self.session = session
        self.design = session.design
        if self.design.riser_document is None:
            self.design.riser_document = layout_presentation(self.design)
        elif not self.design.riser_document.elements:
            self.design.riser_document = layout_presentation(
                self.design, title_source=self.design.riser_document)
        before_sync = copy.deepcopy(self.design.riser_document)
        repair_generated_scene(self.design, self.design.riser_document)
        sync_riser_document(self.design, self.design.riser_document)
        scene_recovered = self.design.riser_document != before_sync
        self.controller = RiserEditorController(
            self.design, self.design.riser_document)
        self.on_edit = on_edit
        self.on_generate = on_generate or (lambda: None)
        self.on_remove_hardware = on_remove_hardware
        self.on_add_hardware = on_add_hardware
        self.on_edit_hardware = on_edit_hardware
        self.on_toggle_fullscreen = on_toggle_fullscreen
        self._fullscreen = False
        self._panel_visible = True
        self._view_controls_wrapped = None
        self._layout_preview = None
        self.tool = "Select"
        self.zoom = 0.42
        self.snap_enabled = True
        self.grid_enabled = True
        self.selected: tuple[str, str] | None = None
        self._gesture = None
        self._drag_job = None
        self._pending_drag = None
        self._move_preview = None
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
        self.bind("<Destroy>", self._cancel_drag_frame, add="+")
        self.redraw()

        if scene_recovered:
            # The parent editor must finish construction before mark_dirty
            # touches its footer. Opening repairs memory, never the source file.
            self.after_idle(self.on_edit)

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
                tools_row, text=name, width=max(52, len(name) * 8), height=28,
                corner_radius=theme.RADIUS["button"],
                fg_color=theme.ACCENT_TINT if name == self.tool else "transparent",
                hover_color=theme.HOVER_SUBTLE, text_color=theme.TEXT,
                command=lambda value=name: self.set_tool(value))
            button.pack(side="left", padx=(6 if name == "Select" else 1, 0), pady=7)
            self._tool_buttons[name] = button

        ctk.CTkFrame(tools_row, width=1, height=26, fg_color=theme.BORDER,
                     corner_radius=0).pack(side="left", padx=8, pady=8)
        for text, command in (("Select All", self.select_all),
                              ("Duplicate", self.duplicate_selected),
                              ("Delete", self.delete_selected)):
            ctk.CTkButton(
                tools_row, text=text, width=72, height=28,
                fg_color="transparent", hover_color=theme.HOVER_SUBTLE,
                text_color=theme.TEXT, command=command).pack(side="left", padx=1)

        ctk.CTkButton(tools_row, text='Add Device', width=96, height=28,
                     command=self.add_device).pack(side='left', padx=(4, 4))

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
        ctk.CTkButton(actions_row, text="Preview layout", width=104, height=28,
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

        self._view_controls = ctk.CTkFrame(
            bar, fg_color="transparent", corner_radius=0, width=224, height=42)
        self._view_controls.pack_propagate(False)
        self._fullscreen_button = ctk.CTkButton(
            self._view_controls, text="Full screen", width=116, height=28,
            fg_color=theme.SURFACE_CHIP, hover_color=theme.HOVER_SUBTLE,
            text_color=theme.TEXT, command=self._toggle_fullscreen,
            state="normal" if self.on_toggle_fullscreen else "disabled")
        self._fullscreen_button.pack(side="right", padx=(4, 8), pady=7)
        self._panel_button = ctk.CTkButton(
            self._view_controls, text="Hide panel", width=88, height=28,
            fg_color=theme.SURFACE_CHIP, hover_color=theme.HOVER_SUBTLE,
            text_color=theme.TEXT, command=self.toggle_panel)
        self._panel_button.pack(side="right", pady=7)
        bar.bind("<Configure>", self._arrange_view_controls, add="+")

    def _arrange_view_controls(self, event):
        wrapped = event.width < self._apply_widget_scaling(1100)
        if wrapped == self._view_controls_wrapped:
            return
        self._view_controls_wrapped = wrapped
        if wrapped:
            self._view_controls.place_forget()
            self._view_controls.pack(side="top", fill="x")
        else:
            self._view_controls.pack_forget()
            self._view_controls.place(relx=1, x=-8, y=0, anchor="ne")
        self.toolbar.configure(height=126 if wrapped else 84)

    def toggle_panel(self):
        """Give the canvas the inspector's space without rebuilding its fields."""
        self._panel_visible = not self._panel_visible
        if self._panel_visible:
            self.inspector.grid()
        else:
            # CTkScrollableFrame forwards this to its outer grid wrapper.
            self.inspector.grid_remove()
        self._panel_button.configure(
            text="Hide panel" if self._panel_visible else "Show panel")

    def set_fullscreen(self, enabled):
        self._fullscreen = bool(enabled)
        self._fullscreen_button.configure(
            text="Exit full screen" if self._fullscreen else "Full screen")

    def _toggle_fullscreen(self):
        if self.on_toggle_fullscreen is not None:
            self.on_toggle_fullscreen()

    def _escape_view(self):
        if self._fullscreen and self.on_toggle_fullscreen is not None:
            self.on_toggle_fullscreen()
        else:
            self.clear_selection()
        return "break"

    def _build_workspace(self):
        body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        self._preview_bar = ctk.CTkFrame(body, fg_color=theme.ACCENT_TINT)
        self._preview_bar.grid(row=0, column=0, columnspan=2, sticky='ew')
        ctk.CTkLabel(self._preview_bar, text='LAYOUT PREVIEW — project unchanged').pack(side='left', padx=12)
        ctk.CTkButton(self._preview_bar, text='Apply layout', width=105,
                     command=self.apply_preview).pack(side='right', padx=6, pady=6)
        ctk.CTkButton(self._preview_bar, text='Cancel preview', width=110,
                     command=self.cancel).pack(side='right', padx=6)
        self._preview_bar.grid_remove()
        canvas_wrap = ctk.CTkFrame(body, fg_color=theme.SURFACE_CHIP,
                                   corner_radius=0)
        canvas_wrap.grid(row=1, column=0, sticky="nsew")
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
        self.inspector.grid(row=1, column=1, sticky="nsew")
        self.inspector.columnconfigure(0, weight=1)
        self._build_inspector()

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._queue_drag)
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
        self._layer_switches = {}
        for column, name in enumerate(self.layer_visibility):
            switch = ctk.CTkSwitch(
                layers, text=name, width=82,
                font=theme.ui_font(theme.SIZE["meta"]),
                command=lambda n=name: self._toggle_layer(n))
            switch.select()
            self._layer_switches[name] = switch
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
        for shortcut in ("<Control-a>", "<Command-a>"):
            self.canvas.bind(shortcut, lambda _e: self._run_key_command(self.select_all))
        self.canvas.bind("<Escape>", lambda _e: self._escape_view())
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
        doc = self._layout_preview or self.controller.document
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
        doc = self._layout_preview or self.controller.document
        self.layer_visibility['Locations'] = doc.show_location_frames
        switch = self._layer_switches.get('Locations')
        if switch:
            switch.select() if doc.show_location_frames else switch.deselect()
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

        for element_id in doc.z_order:
            element = doc.elements.get(element_id)
            if element and ((element.kind == "location" and self.layer_visibility["Locations"])
                            or (element.kind == "device" and self.layer_visibility["Devices"])):
                self._draw_element(element)
        if self.layer_visibility["Cables"]:
            self._draw_routes()
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

    def _draw_text_run(self, run, tags, color="#111111"):
        size = max(1, round(run.size * self.zoom))
        weight = "bold" if run.bold else "normal"
        if not hasattr(self, "_canvas_fonts"):
            self._canvas_fonts = {}
        key = (size, weight)
        if key not in self._canvas_fonts:
            self._canvas_fonts[key] = tkfont.Font(root=self.canvas, family="Helvetica",
                                                  size=-size, weight=weight)
        font = self._canvas_fonts[key]
        x, baseline = self._xy((run.x, run.y))
        return self.canvas.create_text(
            x, baseline - font.metrics("ascent"), text=run.text, fill=color, font=font,
            anchor={"start": "nw", "middle": "n", "end": "ne"}[run.anchor], tags=tags)

    def _draw_element(self, element):
        x1, y1 = self._xy((element.x, element.y))
        x2, y2 = self._xy((element.x + element.width, element.y + element.height))
        tag = f"element|{element.id}"
        visual_tag = f"visual|{element.id}"
        selected = ("element", element.id) in self.selection
        if element.kind == "location":
            self.canvas.create_rectangle(
                x1, y1, x2, y2, fill="", outline="#8b949e",
                dash=(8, 5), width=2 if selected else max(.35, self.zoom),
                tags=(tag, "selectable", visual_tag))
            self.canvas.create_line(
                x1, y1 + element.heading_height * self.zoom, x2, y1 + element.heading_height * self.zoom,
                fill="#d7dde3", width=1, tags=(tag, "selectable", visual_tag))
            for run in location_text_runs(element):
                self._draw_text_run(run, (tag, "selectable", visual_tag))
            return
        fill = "#fff7e8" if element.stale else "#ffffff"
        for part in symbol_parts(self.design,element):
            coords = [v for point in zip(part.coords[::2],part.coords[1::2]) for v in self._xy(point)]
            color = '#4a7bb8' if selected else '#1b2430'
            options = dict(width=3 if selected and part.fill else max(.35,part.width*self.zoom),
                           tags=(tag,'selectable',visual_tag))
            if part.kind == 'line':
                self.canvas.create_line(*coords,fill=color,**options)
            else:
                draw = {'ellipse':self.canvas.create_oval,'rectangle':self.canvas.create_rectangle,
                        'polygon':self.canvas.create_polygon}[part.kind]
                draw(*coords,fill=fill if part.fill else '',outline=color,**options)
        show_detail = self.zoom >= 0.50 or selected or self.tool == "Connect"
        for run in device_text(self.design, element):
            self._draw_text_run(run, (tag, "selectable", visual_tag))
        if element.ref.startswith(('RSP-', 'KEYPAD-')) and (self.tool in {'Connect', 'Route'} or
                self.selected and self.selected[0] == 'route' or
                self._gesture and self._gesture[0] == 'route'):
            for side in INPUT_SIDES:
                if side == element.input_side:
                    continue
                px, py = self._xy(input_side_point(element, side))
                self.canvas.create_oval(px-5, py-5, px+5, py+5,
                    fill='#ffffff', outline='#4a7bb8', dash=(2, 2),
                    tags=(f'port|{element.ref}|IN|in', f'input-side|{element.id}|{side}',
                          'port', visual_tag))
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
                                    tags=(port_tag, "port", visual_tag,
                          f'input-side|{element.id}|{element.input_side}' if element.ref.startswith(('RSP-', 'KEYPAD-')) and not output else ''))
            unused = not any(e.source == ref for e in self.design.connections)
            if (element.ref == "MSP" and (element.symbol_style != 'detailed' or self.tool == 'Connect' and unused) and
                    ref.port_id != 'KP BUS' and
                    (self.zoom >= 0.55 or selected or self.tool == "Connect")):
                self.canvas.create_text(
                    px, py - 6, text=ref.port_id, anchor='s', fill="#4f5964",
                    font=("TkDefaultFont", max(5, round(6 * self.zoom))),
                    tags=(port_tag, "port", visual_tag))

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
        selected_ids = {ref for kind, ref in self.selection if kind == "route"}
        routes = (self._layout_preview or self.controller.document).routes
        bridges = find_bridges({key: route.points for key, route in routes.items()})
        for route_id, route in routes.items():
            tag = f"route|{route_id}"
            width = 3 if route_id in selected_ids else max(.35, 1.2 * self.zoom)
            for segment in wire_segments(route_id, route.points, bridges):
                if segment[0] == "L":
                    points = segment[1:]
                else:
                    start, control, end = segment[1:]
                    points = [
                        tuple((1-t)**2 * a + 2*(1-t)*t * b + t*t*c
                              for a, b, c in zip(start, control, end))
                        for t in (i / 16 for i in range(17))
                    ]
                self.canvas.create_line(
                    *[coordinate for point in points for coordinate in self._xy(point)],
                    fill="#111111", width=width, tags=(tag, "selectable"))
            edge = edges.get(route_id)
            point = route_label_point(route.points)
            if edge and point is not None and not route.label_hidden:
                self._draw_text_run(TextRun(point[0] + route.label_offset[0],
                                            point[1] + route.label_offset[1],
                                            edge.label, 14, True), (f"label|{route_id}", "selectable"))

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
            anchor = {"left": "start", "center": "middle", "right": "end"}[annotation.alignment]
            self._draw_text_run(TextRun(*annotation.points[0], annotation.text,
                                       annotation.font_size, annotation.font_weight == "bold", anchor),
                                (tag, "selectable"), color=stroke)
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
        doc = self._layout_preview or self.controller.document
        left, top, right, bottom = title_bounds(doc)
        x1, y1 = self._xy((left, top))
        x2, y2 = self._xy((right, bottom))
        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#ffffff",
                                     outline="#111111", width=max(.35, 2 * self.zoom),
                                     tags=("titleblock",))
        if doc.layout_version >= 3:
            self.canvas.create_rectangle(*self._xy((36,36)),
                *self._xy((doc.page_width-36,doc.page_height-36)),outline='#111111',
                width=max(.35,2*self.zoom),tags=('titleblock',))
            for a,b in modern_title(doc)[1]:
                self.canvas.create_line(*self._xy(a),*self._xy(b),fill='#111111',
                    width=max(.35,1.2*self.zoom),tags=('titleblock',))
        lx, ly, lr, lb = logo_bounds(doc)
        logo_size = (max(1, round((lr-lx) * self.zoom)), max(1, round((lb-ly) * self.zoom)))
        if getattr(self, "_logo_size", None) != logo_size:
            try:
                with Image.open(resource_path("logos/c1_logo.png")) as source:
                    logo = source.copy()
                logo.thumbnail(logo_size, Image.Resampling.LANCZOS)
                self._logo_image = ImageTk.PhotoImage(logo, master=self.canvas)
            except OSError:
                self._logo_image = None
            self._logo_size = logo_size
        if self._logo_image is not None:
            self.canvas.create_image(*self._xy(((lx+lr)/2, (ly+lb)/2)),
                                     image=self._logo_image, tags=("titleblock",))
        for run in title_text(doc):
            self._draw_text_run(run, ("titleblock",))

    def _draw_selection(self):
        if len(self.selection) > 1 and self._layout_preview is None:
            for kind, ref in self.selection:
                box = self.canvas.bbox(f'{"visual" if kind == "element" else kind}|{ref}')
                if box:
                    self.canvas.create_rectangle(
                        box[0]-4, box[1]-4, box[2]+4, box[3]+4,
                        outline='#4a7bb8', dash=(3, 2), tags=('selection',))
            return
        if not self.selected or self._layout_preview is not None:
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
                                         tags=("resize-handle", "selection"))
        elif kind == "label":
            if not self.layer_visibility['Cables']:
                return
            box = self.canvas.bbox(f'label|{ref}')
            if box:
                self.canvas.create_rectangle(
                    box[0]-4, box[1]-4, box[2]+4, box[3]+4,
                    outline='#4a7bb8', dash=(3, 2), tags=('selection',))
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
                        tags=(f"route-handle|{ref}|{index}", "selection"))
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
                tags=("annotation-selection", "selection"))

    # ---- interaction --------------------------------------------------

    @live_edit_only
    def set_tool(self, name):
        self.cancel(redraw=False)
        self.tool = name
        for tool, button in self._tool_buttons.items():
            button.configure(fg_color=theme.ACCENT_TINT if tool == name else "transparent")
        self.canvas.configure(cursor={"Connect": "crosshair", "Route": "crosshair",
                                      "Text": "xterm"}.get(name, "arrow"))
        self.redraw(inspector=False)

    def _tags_at_event(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        items = self.canvas.find_overlapping(x - 3, y - 3, x + 3, y + 3)
        return [tag for item in reversed(items) for tag in self.canvas.gettags(item)]

    def _rsp_side_at_event(self, event):
        if not self.layer_visibility['Devices']:
            return None
        point = self._world(event)
        candidates = []
        for element in self.controller.document.elements.values():
            if element.kind == 'device' and element.ref.startswith(('RSP-', 'KEYPAD-')):
                for side in INPUT_SIDES:
                    x, y = input_side_point(element, side)
                    distance = math.hypot(point[0]-x, point[1]-y) * self.zoom
                    if distance <= 10:
                        candidates.append((distance, element.id, side))
        if candidates:
            _, element_id, side = min(candidates)
            return element_id, side
        return None

    def _parse_port(self, tags):
        token = next((tag for tag in tags if tag.startswith("port|")), None)
        if not token:
            return None
        _, device, port, direction = token.split("|", 3)
        return DevicePortRef(device, port), direction == "out"

    def _parse_selectable(self, tags):
        prefixes = {"annotation": "annotation", "element": "element", "route": "route", "label": "label"}
        for token in tags:
            prefix, separator, ref = token.partition("|")
            if separator and prefix in prefixes:
                return prefixes[prefix], ref
        return None

    def _visible_selectables(self):
        doc = self.controller.document
        result = [('element', key) for key, element in doc.elements.items()
                  if self.layer_visibility['Devices' if element.kind == 'device' else 'Locations']]
        if self.layer_visibility['Cables']:
            result.extend(('route', key) for key in doc.routes)
            result.extend(('label', key) for key, route in doc.routes.items() if not route.label_hidden)
        if self.layer_visibility['Markup']:
            result.extend(('annotation', a.id) for a in doc.annotations)
        return result

    def _reconcile_selection(self):
        visible = set(self._visible_selectables())
        self.selection = [item for item in self.selection if item in visible]

    @live_edit_only
    def select_all(self):
        self.cancel(redraw=False)
        self.set_tool('Select')
        self.selection = self._visible_selectables()
        self.canvas.focus_set()
        self.redraw(inspector=False)
        self._refresh_selection_properties()

    def clear_selection(self):
        self.cancel(redraw=False)
        self.selected = None
        self.redraw()

    def _extend_selection(self, event):
        mask = 8 if self.tk.call('tk', 'windowingsystem') == 'aqua' else 4
        return bool(getattr(event, 'state', 0) & mask)

    def _restore_group_preview(self, document):
        # Restore only drawing state; topology and history are untouched during a drag.
        for name in ('elements', 'routes', 'annotations'):
            setattr(self.controller.document, name, copy.deepcopy(getattr(document, name)))

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

    @live_edit_only
    def _on_press(self, event):
        self._cancel_drag_frame()
        self.canvas.focus_set()
        point = self._snap(self._world(event))
        tags = self._tags_at_event(event)
        port = self._parse_port(tags)
        if self.tool == "Connect":
            side_hit = self._rsp_side_at_event(event)
            if side_hit:
                port = (DevicePortRef(self.controller.document.elements[side_hit[0]].ref, 'IN'), False)
            self._connect_click(port, input_side=side_hit[1] if side_hit else None)
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
        if self.tool == 'Select' and self._extend_selection(event):
            handle = None
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
        if self.tool == 'Select':
            if self._extend_selection(event) and picked:
                if picked in self.selection:
                    self.selection.remove(picked)
                else:
                    self.selection.append(picked)
                self._gesture = None
                self.redraw(inspector=False)
                self._refresh_selection_properties()
                return
            if not picked:
                base = list(self.selection) if self._extend_selection(event) else []
                self.selection = base
                self._gesture = ('marquee', self._world(event), base)
                self.redraw(inspector=False)
                self._refresh_selection_properties()
                return
            if picked in self.selection and len(self.selection) > 1:
                self._gesture = ('move-group', point, copy.deepcopy(self.controller.document),
                                 list(self.selection))
                return
        self.selected = picked
        if picked and picked[0] == "element" and self.tool == "Select":
            element = self.controller.document.elements[picked[1]]
            self._gesture = ("move", picked[1], point, element.x, element.y)
            members = [e for e in self.controller.document.elements.values()
                       if e.kind == "device" and (e.id == element.id or e.location_id == element.id)]
            refs = {e.ref for e in members}
            self._move_preview = (
                {e.id: (e.x, e.y) for e in members},
                {edge.id: list(self.controller.document.routes[edge.id].points)
                 for edge in self.design.connections
                 if (edge.source.device_id in refs or edge.target.device_id in refs)
                 and edge.id in self.controller.document.routes}, refs,
                {key: e.input_side for key, e in self.controller.document.elements.items()
                 if e.ref.startswith(('RSP-', 'KEYPAD-'))})
        elif picked and picked[0] == "label" and self.tool == "Select":
            route = self.controller.document.routes[picked[1]]
            self._gesture = ('move-label', picked[1], point, route.label_offset)
        elif picked and picked[0] == "annotation" and self.tool == "Select":
            annotation = next(item for item in self.controller.document.annotations
                              if item.id == picked[1])
            self._gesture = ("move-annotation", picked[1], point,
                             copy.deepcopy(annotation.points))
        # Selection changes do not change validation or the Unplaced tray.
        self.redraw(inspector=False)
        self._refresh_selection_properties()

    def _queue_drag(self, event):
        """Render at most one pending frame, always at the latest pointer."""
        if not self._gesture:
            return
        self._pending_drag = event
        if self._drag_job is None:
            self._drag_job = self.after(16, self._flush_drag)

    def _flush_drag(self):
        self._drag_job = None
        event, self._pending_drag = self._pending_drag, None
        if event is not None and self._gesture:
            self._on_drag(event)

    def _cancel_drag_frame(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self._drag_job is not None:
            self.after_cancel(self._drag_job)
            self._drag_job = None
        self._pending_drag = None

    def _on_drag(self, event):
        if not self._gesture:
            return
        point = self._snap(self._world(event))
        kind = self._gesture[0]
        if kind == 'marquee':
            start = self._gesture[1]
            self.canvas.delete('marquee')
            self.canvas.create_rectangle(*self._xy(start), *self._xy(self._world(event)),
                                         outline='#4a7bb8', dash=(4, 3), tags=('marquee',))
        elif kind == 'move-group':
            _, start, original, selection = self._gesture
            previous = self.controller.document
            positions = {key: (e.x, e.y) for key, e in previous.elements.items()}
            annotations = {a.id: list(a.points) for a in previous.annotations}
            routes = previous.routes
            self._restore_group_preview(original)
            self.controller._translate_selection(selection, point[0]-start[0], point[1]-start[1],
                                                 preview=True)
            doc = self.controller.document
            for key, element in doc.elements.items():
                x, y = positions[key]
                if (element.x, element.y) != (x, y):
                    self.canvas.move(f'visual|{key}', (element.x-x)*self.zoom, (element.y-y)*self.zoom)
            for annotation in doc.annotations:
                old_points = annotations[annotation.id]
                if old_points and annotation.points != old_points:
                    self.canvas.move(f'annotation|{annotation.id}',
                                     (annotation.points[0][0]-old_points[0][0])*self.zoom,
                                     (annotation.points[0][1]-old_points[0][1])*self.zoom)
            for key, route in doc.routes.items():
                if route.points != routes[key].points or route.label_offset != routes[key].label_offset:
                    self._preview_route_canvas(key)
            self._preview_rsp_ports()
            self.canvas.delete('selection')
            self._draw_selection()
        elif kind == "draw":
            start = self._gesture[1]
            coords = (*self._xy(start), *self._xy(point))
            if self._preview_item:
                self.canvas.coords(self._preview_item, *coords)
            elif self.tool == "Rectangle":
                self._preview_item = self.canvas.create_rectangle(*coords, outline="#4a7bb8")
            elif self.tool == "Ellipse":
                self._preview_item = self.canvas.create_oval(*coords, outline="#4a7bb8")
            else:
                self._preview_item = self.canvas.create_line(
                    *coords, fill="#4a7bb8", arrow="last" if self.tool == "Arrow" else "none")
        elif kind == "move":
            _, element_id, start, old_x, old_y = self._gesture
            element = self.controller.document.elements[element_id]
            new_x, new_y = old_x + point[0] - start[0], old_y + point[1] - start[1]
            dx, dy = new_x - element.x, new_y - element.y
            if not dx and not dy:
                return
            element.x, element.y = new_x, new_y
            self._preview_move(point[0] - start[0], point[1] - start[1])
            members = set(self._move_preview[0]) if self._move_preview else set()
            for key in members | {element_id}:
                self.canvas.move(f"visual|{key}", dx * self.zoom, dy * self.zoom)
            self.canvas.move("selection", dx * self.zoom, dy * self.zoom)
            self._preview_rsp_ports()
            if self._move_preview:
                for route_id in self._move_preview[1]:
                    self._preview_route_canvas(route_id)
        elif kind == "move-label":
            _, key, start, old_offset = self._gesture
            route = self.controller.document.routes[key]
            offset = tuple(old_offset[i] + point[i] - start[i] for i in (0, 1))
            delta = tuple((offset[i] - route.label_offset[i]) * self.zoom for i in (0, 1))
            route.label_offset = offset
            self.canvas.move(f'label|{key}', *delta)
            self.canvas.move('selection', *delta)
        elif kind == "move-annotation":
            _, annotation_id, start, old_points = self._gesture
            annotation = next(item for item in self.controller.document.annotations
                              if item.id == annotation_id)
            dx, dy = point[0] - start[0], point[1] - start[1]
            new_points = [(x + dx, y + dy) for x, y in old_points]
            if annotation.points == new_points:
                return
            delta = tuple((new_points[0][i] - annotation.points[0][i]) * self.zoom for i in (0, 1))
            annotation.points = new_points
            self.canvas.move(f"annotation|{annotation_id}", *delta)
            self.canvas.move("selection", *delta)
        elif kind == "resize":
            _, element_id, start, old_w, old_h = self._gesture
            element = self.controller.document.elements[element_id]
            width = max(36, old_w + point[0] - start[0])
            minimum_height = 54 if element.kind == "location" else 28
            height = max(minimum_height, old_h + point[1] - start[1])
            if (element.width, element.height) == (width, height):
                return
            element.width, element.height = width, height
            tag = f"visual|{element_id}"
            items = self.canvas.find_withtag(tag)
            above = self.canvas.find_above(items[-1]) if items else ()
            self.canvas.delete(tag)
            if items:
                self._draw_element(element)
                if above:
                    self.canvas.tag_lower(tag, above[0])
            self.canvas.delete("selection")
            self._draw_selection()
        elif kind == "route":
            _, route_id, index, old_points = self._gesture
            route = self.controller.document.routes[route_id]
            if index in {0, len(old_points) - 1}:
                route.points = list(old_points)
                route.points[index] = point
                side_hit = self._rsp_side_at_event(event) if index == len(old_points)-1 else None
                if side_hit:
                    element_id, side = side_hit
                    target = replace(self.controller.document.elements[element_id],
                                     input_side=side, input_side_locked=True)
                    edge = next(edge for edge in self.design.connections if edge.id == route_id)
                    preview_edge = replace(edge, target=DevicePortRef(target.ref, 'IN'))
                    source = self.controller.document.elements[f'device:{edge.source.device_id}']
                    _, route.points = route_rsp_input(preview_edge, source, target, [source, target])
            else:
                route.points = self.controller.adjusted_route_points(
                    old_points, index, point)
            self._preview_route_canvas(route_id)
            self.canvas.delete("selection")
            self._draw_selection()

    def _preview_rsp_ports(self):
        for element in self.controller.document.elements.values():
            if not element.ref.startswith(('RSP-', 'KEYPAD-')):
                continue
            for item in self.canvas.find_withtag(f'port|{element.ref}|IN|in'):
                tags = self.canvas.gettags(item)
                # Alternate snap points have a dashed outline and stay on their own side.
                if self.canvas.itemcget(item, 'dash'):
                    continue
                x, y = self._xy(input_side_point(element, element.input_side))
                coords = self.canvas.coords(item)
                radius = (coords[2]-coords[0])/2
                self.canvas.coords(item, x-radius, y-radius, x+radius, y+radius)
                self.canvas.itemconfigure(item, tags=tuple(
                    tag for tag in tags if not tag.startswith('input-side|')) +
                    (f'input-side|{element.id}|{element.input_side}',))

    def _preview_route_canvas(self, route_id):
        """Update one cable in-place; compute bridge hops once at mouse-up.

        Reuse its first line and hide any additional bridge segments during
        the gesture. A normal redraw restores final bridges on release/Esc.
        Stationary sheet objects and all unrelated cable items are untouched.
        """
        route = self.controller.document.routes[route_id]
        items = self.canvas.find_withtag(f"route|{route_id}")
        lines = [item for item in items if self.canvas.type(item) == "line"]
        if lines and len(route.points) >= 2:
            self.canvas.coords(lines[0], *[v for p in route.points for v in self._xy(p)])
            self.canvas.itemconfigure(lines[0], state="normal")
            for item in lines[1:]:
                self.canvas.itemconfigure(item, state="hidden")
        point = route_label_point(route.points)
        if point is not None:
            x, y = self._xy((point[0]+route.label_offset[0], point[1]+route.label_offset[1]))
            for item in self.canvas.find_withtag(f'label|{route_id}'):
                if self.canvas.type(item) == "text":
                    font = tkfont.nametofont(self.canvas.itemcget(item, "font"), root=self.canvas)
                    self.canvas.coords(item, x, y-font.metrics("ascent"))

    def _preview_move(self, dx, dy):
        if self._move_preview is None:
            return
        positions, routes, refs, sides = self._move_preview
        doc = self.controller.document
        for key, (x, y) in positions.items():
            doc.elements[key].x, doc.elements[key].y = x + dx, y + dy
        for edge in self.design.connections:
            if edge.id not in routes:
                continue
            points = routes[edge.id]
            if edge.source.device_id in refs and edge.target.device_id in refs:
                points = [(x + dx, y + dy) for x, y in points]
            elif edge.target.device_id.startswith(('RSP-', 'KEYPAD-')):
                target = doc.elements[f'device:{edge.target.device_id}']
                target.input_side = sides[target.id]
                points = self.controller._route_rsp(
                    edge, old_points=points if doc.routes[edge.id].manual else (), preview=True)
            else:
                for ref, at_start in ((edge.source, True), (edge.target, False)):
                    if ref.device_id in refs:
                        endpoint = port_point(doc.elements[f"device:{ref.device_id}"], ref.port_id, output=at_start)
                        points = reattach_route_endpoint(points, endpoint, at_start=at_start)
            doc.routes[edge.id].points = points

    def _restore_move_preview(self):
        if self._move_preview is not None:
            positions, routes, _refs, sides = self._move_preview
            doc = self.controller.document
            for key, (x, y) in positions.items():
                if key in doc.elements:
                    doc.elements[key].x, doc.elements[key].y = x, y
            for key, points in routes.items():
                if key in doc.routes:
                    doc.routes[key].points = points
            for key, side in sides.items():
                if key in doc.elements:
                    doc.elements[key].input_side = side
        self._move_preview = None

    def _on_release(self, event):
        self._cancel_drag_frame()
        if not self._gesture:
            return
        if self._gesture[0] == "polyline":
            return
        # Mouse-up can be newer than the last delivered motion event.
        self._on_drag(event)
        gesture, self._gesture = self._gesture, None
        point = self._snap(self._world(event))
        kind = gesture[0]
        if kind == 'marquee':
            _, start, base = gesture
            end = self._world(event)
            left, right = sorted((self._xy(start)[0], self._xy(end)[0]))
            top, bottom = sorted((self._xy(start)[1], self._xy(end)[1]))
            self.selection = list(base)
            if right-left > 3 or bottom-top > 3:
                for item in self._visible_selectables():
                    kind_name, ref = item
                    tag = f'{"visual" if kind_name == "element" else kind_name}|{ref}'
                    box = self.canvas.bbox(tag)
                    if box and left <= box[0] and top <= box[1] and right >= box[2] and bottom >= box[3]:
                        if item not in self.selection:
                            self.selection.append(item)
            self.canvas.delete('marquee')
        elif kind == 'move-group':
            _, start, original, selection = gesture
            self._restore_group_preview(original)
            if self.controller.move_selection(selection, point[0]-start[0], point[1]-start[1]):
                self.on_edit()
        elif kind == "draw":
            start = gesture[1]
            if start != point:
                annotation_kind = self.tool.lower()
                annotation = RiserAnnotation(
                    f"annotation-{uuid.uuid4().hex[:8]}", annotation_kind,
                    [start, point])
                self.controller.add_annotation(annotation)
                self.selected = ("annotation", annotation.id)
                self.on_edit()
        elif kind == "move":
            _, element_id, _start, old_x, old_y = gesture
            element = self.controller.document.elements[element_id]
            new_x, new_y = element.x, element.y
            self._restore_move_preview()
            element.x, element.y = old_x, old_y
            if self.controller.move_element(element_id, new_x - old_x, new_y - old_y):
                self.on_edit()
        elif kind == "move-label":
            _, key, _start, old_offset = gesture
            route = self.controller.document.routes[key]
            offset = route.label_offset
            route.label_offset = old_offset
            if offset != old_offset:
                self.controller.update_label(key, offset=offset)
                self.on_edit()
        elif kind == "move-annotation":
            _, annotation_id, _start, old_points = gesture
            annotation = next(item for item in self.controller.document.annotations
                              if item.id == annotation_id)
            new_points = copy.deepcopy(annotation.points)
            annotation.points = old_points
            if new_points != old_points:
                self.controller.update_annotation(annotation_id, points=new_points)
                self.on_edit()
        elif kind == "resize":
            _, element_id, _start, old_w, old_h = gesture
            element = self.controller.document.elements[element_id]
            new_w, new_h = element.width, element.height
            element.width, element.height = old_w, old_h
            if self.controller.resize_element(element_id, new_w, new_h):
                self.on_edit()
        elif kind == "route":
            _, route_id, index, old_points = gesture
            route = self.controller.document.routes[route_id]
            # Drag preview already wrote the final point. Restore from the undo
            # snapshot's latest state, then issue one semantic command.
            route.points = old_points
            is_endpoint = index in {0, len(old_points) - 1}
            if is_endpoint:
                side_hit = self._rsp_side_at_event(event) if index == len(old_points)-1 else None
                edge = next(edge for edge in self.design.connections if edge.id == route_id)
                if side_hit and side_hit[0] == f'device:{edge.target.device_id}':
                    self.controller.set_input_side(*side_hit)
                    self.on_edit()
                    self.redraw()
                    return
                port = self._parse_port(self._tags_at_event(event))
                if side_hit:
                    port = (DevicePortRef(self.controller.document.elements[side_hit[0]].ref, 'IN'), False)
                if port:
                    ref, is_output = port
                    edge = next(edge for edge in self.design.connections
                                if edge.id == route_id)
                    try:
                        if index == 0 and is_output:
                            if ref != edge.source:
                                self.controller.reconnect(route_id, source=ref)
                                self.on_edit()
                        elif index == len(old_points) - 1 and not is_output:
                            if ref == edge.target:
                                self.redraw()
                                return
                            self.controller.reconnect(route_id, target=ref,
                                input_side=side_hit[1] if side_hit else None)
                            self.on_edit()
                    except TopologyError as exc:
                        messagebox.showwarning("Connection not allowed", str(exc))
            else:
                if self.controller.move_route_point(route_id, index, point):
                    self.on_edit()
        self._preview_item = None
        self.redraw()

    def _connect_click(self, port, input_side=None):
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
                if input_side is not None:
                    self.controller.set_input_side(f'device:{ref.device_id}', input_side)
                    self.on_edit()
                self.selected = ("route", existing.id)
                self._connect_source = None
                self.redraw()
                return
            edge = self.controller.connect(self._connect_source, ref, input_side=input_side)
            self.selected = ("route", edge.id)
            self._connect_source = None
            self._changed()
        except TopologyError as exc:
            messagebox.showwarning("Connection not allowed", str(exc))

    @live_edit_only
    def _on_double_click(self, event):
        # Tk dispatches a rapid second press here instead of the single-click handler.
        if self.tool == 'Select' and self._extend_selection(event):
            self._on_press(event)
            return 'break'
        if self.tool == "Polyline" and self._gesture and self._gesture[0] == "polyline":
            self._finish_polyline()
            return "break"
        picked = self._parse_selectable(self._tags_at_event(event))
        if not picked:
            return
        kind, ref = picked
        if kind in {"route", "label"}:
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
        elif kind == "element":
            element = self.controller.document.elements.get(ref)
            if element and element.kind == "location":
                self.rename_location(ref)
            elif element and element.kind == 'device':
                self.selected = ('element', ref)
                self.edit_device()

    @live_edit_only
    def rename_location(self, element_id):
        self.cancel(redraw=False)
        self.controller.sync_external()
        frame = self.controller.document.elements.get(element_id)
        if frame is None:
            return
        value = simpledialog.askstring(
            "Rename location globally", "New equipment location:", initialvalue=frame.ref,
            parent=self.winfo_toplevel())
        if value is None:
            return
        try:
            updates, merging = plan_location_rename(
                self.design, self.controller.document, element_id, value)
            if not updates:
                return
            message = (f'Rename to “{value.strip()}” for {len(updates)} equipment items?\n\n'
                       + ', '.join(updates) + '\n\n'
                       'This updates the shared project and future exports. '
                       'Zone descriptions and electrical connections stay unchanged. '
                       'You can Undo this change.')
            if merging:
                message += '\n\nThis location already exists. Its location groups will be combined; equipment will not move.'
            if messagebox.askyesno("Rename location globally?", message,
                                   parent=self.winfo_toplevel()):
                self.controller.rename_location(element_id, value, allow_merge=merging)
                self._changed()
        except ValueError as exc:
            messagebox.showwarning("Location not changed", str(exc), parent=self.winfo_toplevel())

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

    @live_edit_only
    def undo(self):
        self.cancel(redraw=False)
        if self.controller.undo():
            self._sync_title_fields()
            self._changed()
        else:
            self.redraw()

    @live_edit_only
    def redo(self):
        self.cancel(redraw=False)
        if self.controller.redo():
            self._sync_title_fields()
            self._changed()
        else:
            self.redraw()

    def _sync_title_fields(self):
        for name, variable in self._title_vars.items():
            value = getattr(self.controller.document.title_block, name)
            variable.set(" | ".join(value) if name == "revisions" else value)

    def cancel(self, redraw=True):
        if self._layout_preview is not None:
            self._layout_preview = None
            self._preview_bar.grid_remove()
            for entry in self._title_entries.values():
                entry.configure(state='normal')
        self._cancel_drag_frame()
        self._restore_move_preview()
        self.canvas.delete('marquee')
        if self._gesture:
            if self._gesture[0] == 'move-group':
                self._restore_group_preview(self._gesture[2])
            elif self._gesture[0] == 'move-label':
                _, key, _start, offset = self._gesture
                if key in self.controller.document.routes:
                    self.controller.document.routes[key].label_offset = offset
            elif self._gesture[0] == "route":
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

    @live_edit_only
    def add_device(self):
        self.cancel(redraw=False)
        if self.on_add_hardware:
            self.on_add_hardware()

    @live_edit_only
    def edit_device(self):
        self.cancel(redraw=False)
        if self.selected and self.selected[0] == 'element':
            element = self.controller.document.elements.get(self.selected[1])
            if element and element.kind == 'device' and self.on_edit_hardware:
                self.on_edit_hardware(element.ref)

    @live_edit_only
    def delete_selected(self):
        self.cancel(redraw=False)
        self._reconcile_selection()
        if len(self.selection) > 1:
            messagebox.showinfo("Select one item", "Select one item to delete or disconnect it.",
                                parent=self.winfo_toplevel())
            return
        if not self.selected:
            messagebox.showinfo("Nothing selected", "Select a device, cable, callout, or markup object.",
                                parent=self.winfo_toplevel())
            return
        kind, ref = self.selected
        if kind == "annotation":
            self.controller.delete_annotation(ref)
        elif kind == "label":
            self.controller.update_label(ref, hidden=True)
        elif kind == "route" and messagebox.askyesno(
                "Disconnect cable?", "Remove this electrical connection?",
                parent=self.winfo_toplevel()):
            self.controller.disconnect(ref)
        elif kind == "element":
            element = self.controller.document.elements[ref]
            if element.kind == "location":
                messagebox.showinfo("Location module", "This box groups project hardware by location. "
                    "Change device locations or remove hardware in SPLITTERS, KEYPADS, or POWER; "
                    "the location box cannot be deleted independently.", parent=self.winfo_toplevel())
            elif element.ref == "MSP":
                messagebox.showinfo("Control panel required", "The MSP is the project's control panel "
                                    "and cannot be deleted from the riser.", parent=self.winfo_toplevel())
            elif self.on_remove_hardware:
                self.on_remove_hardware(element.ref)
            else:
                messagebox.showinfo("Remove project hardware", "Remove this device in SPLITTERS, "
                                    "KEYPADS, or POWER so zone and wiring changes are confirmed together.",
                                    parent=self.winfo_toplevel())
            return
        else:
            return
        self.selected = None
        self._changed()

    @live_edit_only
    def duplicate_selected(self):
        self.cancel(redraw=False)
        self._reconcile_selection()
        if self.selected and self.selected[0] == "annotation":
            duplicate = self.controller.duplicate_annotation(self.selected[1])
            self.selected = ("annotation", duplicate.id)
            self._changed()
        else:
            messagebox.showinfo("Duplicate markup", "Select text or a markup shape to duplicate. "
                                "Add hardware through SPLITTERS, KEYPADS, or POWER so capacity "
                                "and wiring rules are checked.", parent=self.winfo_toplevel())

    @live_edit_only
    def nudge(self, dx, dy):
        self.cancel(redraw=False)
        self._reconcile_selection()
        if len(self.selection) > 1:
            if self.controller.move_selection(self.selection, dx, dy):
                self._changed()
            return
        if not self.selected:
            return
        kind, ref = self.selected
        if kind == "element":
            self.controller.move_element(ref, dx, dy)
        elif kind == 'label':
            offset = self.controller.document.routes[ref].label_offset
            self.controller.update_label(ref, offset=(offset[0] + dx, offset[1] + dy))
        elif kind == "annotation":
            annotation = next(a for a in self.controller.document.annotations if a.id == ref)
            self.controller.update_annotation(
                ref, points=[(x + dx, y + dy) for x, y in annotation.points])
        else:
            return
        self._changed()

    def relayout(self):
        self.cancel(redraw=False)
        self._layout_preview = self.controller.preview_layout()
        self.selected = None
        self._preview_bar.grid()
        for entry in self._title_entries.values():
            entry.configure(state='disabled')
        self._frame_initial_view()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def apply_preview(self):
        if self._layout_preview is None:
            return
        if not messagebox.askyesno('Apply layout?',
                'Replace device positions and cable routes with this preview?\n\n'
                'Manual geometry will be replaced. Title data and markup remain. You can Undo.',
                parent=self.winfo_toplevel()):
            return
        try:
            self.controller.apply_layout(self._layout_preview)
        except ValueError as exc:
            messagebox.showwarning('Preview out of date', str(exc), parent=self.winfo_toplevel())
            self.cancel()
            return
        self.cancel(redraw=False)
        self._changed()

    @live_edit_only
    def auto_layout(self):
        self.controller.auto_layout()
        self._changed()

    def set_zoom(self, value):
        self._fit_mode = False
        self._set_zoom(value)

    def _set_zoom(self, value, *, inspector=True):
        self.zoom = min(1.75, max(0.18, value))
        self._zoom_label.configure(text=f"{round(self.zoom * 100)}%")
        self.redraw(inspector=inspector)

    def fit_to_view(self, *, inspector=True):
        """Fit the canonical sheet inside the current canvas without cropping."""
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 100 or height < 100:
            return
        document = self.controller.document
        fitted = min((width - 48) / document.page_width,
                     (height - 48) / document.page_height)
        self._set_zoom(fitted, inspector=inspector)
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
        document = self._layout_preview or self.controller.document
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
            callback = (lambda: self.fit_to_view(inspector=False)) if self._fit_mode else (
                lambda: self.redraw(inspector=False))
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
        if name == 'Locations':
            if self._layout_preview is not None:
                self.redraw()
                return
            self.controller._mutate(lambda: setattr(self.controller.document,
                'show_location_frames',not self.controller.document.show_location_frames))
            self.on_edit()
            self.redraw()
            return
        self.layer_visibility[name] = not self.layer_visibility[name]
        self.redraw()

    @live_edit_only
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

    def _refresh_selection_properties(self):
        self._clear_frame(self.properties)
        if self._layout_preview is not None:
            ctk.CTkLabel(self.properties, text='Preview only. Apply or cancel to resume editing.',
                         wraplength=250, justify='left').grid(row=0, column=0, padx=10, pady=10)
            return
        if len(self.selection) > 1:
            ctk.CTkLabel(self.properties, text=f"{len(self.selection)} items selected",
                         wraplength=250, justify='left').grid(row=0, column=0, padx=10, pady=10)
            ctk.CTkLabel(self.properties,
                         text="Drag or use arrow keys to move the group. Command-click (Mac) or Ctrl-click (Windows) to change selection. Escape clears it.",
                         wraplength=250, justify='left').grid(row=1, column=0, padx=10, pady=4)
            return
        if not self.selected:
            ctk.CTkLabel(self.properties, text="Select a device, cable, or markup object.",
                         wraplength=250, justify="left", text_color=theme.TEXT_SECOND,
                         font=theme.ui_font(theme.SIZE["meta"])).grid(
                row=0, column=0, sticky="w", padx=10, pady=10)
        else:
            kind, ref = self.selected
            element = self.controller.document.elements.get(ref) if kind == "element" else None
            label = element.ref if element and element.kind == "location" else ref
            ctk.CTkLabel(self.properties, text=label, anchor="w", wraplength=260, justify="left",
                         text_color=theme.TEXT,
                         font=theme.mono_font(theme.SIZE["meta"], "bold")).grid(
                row=0, column=0, sticky="ew", padx=10, pady=(9, 3))
            if kind in {"route", "label"}:
                self._build_cable_properties(ref)
            elif kind == "annotation":
                self._build_markup_properties(ref)
            elif element and element.kind == "location":
                ctk.CTkButton(
                    self.properties, text="Rename location globally…", height=28,
                    command=lambda: self.rename_location(ref)).grid(
                        row=1, column=0, sticky="ew", padx=10, pady=(6, 10))
                identities = {self.design.device_location_ids.get(e.ref)
                              for e in self.controller.document.elements.values()
                              if e.kind == 'device' and e.location_id == element.id and not e.stale}
                identities.discard(None)
                identity = element.physical_location_id or (next(iter(identities)) if len(identities) == 1 else None)
                if identity in self.design.equipment_locations:
                    self._build_location_fields(identity)
                else:
                    ctk.CTkLabel(self.properties,
                        text='This legacy box includes different location records. Preview layout to review them separately.',
                        wraplength=250, justify='left').grid(row=2, column=0, padx=10, pady=6)
            elif element and element.kind == 'device':
                if element.ref.startswith(('RSP-', 'KEYPAD-')):
                    ctk.CTkLabel(self.properties, text='Input side', anchor='w').grid(
                        row=6, column=0, sticky='w', padx=10, pady=(8, 0))
                    side_value = element.input_side.title() if element.input_side_locked else 'Auto'
                    variable = tk.StringVar(value=side_value)
                    ctk.CTkOptionMenu(self.properties,
                        values=['Auto', 'Top', 'Right', 'Bottom', 'Left'], variable=variable,
                        command=lambda value: self._set_input_side(element.id, value.lower())).grid(
                            row=7, column=0, sticky='ew', padx=10, pady=6)
                self._build_location_assignment(element.ref)
                ctk.CTkButton(self.properties, text='Edit Device',
                             command=self.edit_device).grid(
                    row=4, column=0, sticky='ew', padx=10, pady=6)
                if element.ref != 'MSP':
                    ctk.CTkButton(self.properties, text='Remove from Project',
                                 command=self.delete_selected).grid(
                        row=5, column=0, sticky='ew', padx=10, pady=6)

    @live_edit_only
    def _set_input_side(self, element_id, side):
        self.cancel(redraw=False)
        self.controller.set_input_side(element_id, side)
        self._changed()

    def _build_location_fields(self, identity):
        record = self.design.equipment_locations[identity]
        variables = {}
        for row, name in enumerate(('building', 'floor', 'room')):
            ctk.CTkLabel(self.properties, text=name.title(), anchor='w').grid(
                row=2 + row * 2, column=0, sticky='w', padx=10)
            variables[name] = tk.StringVar(value=getattr(record, name))
            ctk.CTkEntry(self.properties, textvariable=variables[name]).grid(
                row=3 + row * 2, column=0, sticky='ew', padx=10, pady=(0, 6))
        self._structured_location_vars = variables
        ctk.CTkButton(self.properties, text='Apply location fields',
                     command=lambda: self._apply_location_fields(identity, variables)).grid(
                         row=8, column=0, sticky='ew', padx=10, pady=(6, 10))

    @live_edit_only
    def _apply_location_fields(self, identity, variables):
        values = {name: variable.get() for name, variable in variables.items()}
        members = [ref for ref, key in self.design.device_location_ids.items() if key == identity]
        from location_model import location_key
        label = ' '.join(value.strip() for value in values.values() if value.strip())
        merging = any(key != identity and location_key(record.full_label) == location_key(label)
                      for key, record in self.design.equipment_locations.items())
        message = ('Update the shared location for:\n\n' + ', '.join(members)
                   + '\n\nThis changes future exports, not wiring or device positions.')
        if merging:
            message += '\n\nThis location exists; combine these location groups?'
        if not messagebox.askyesno('Apply location fields?', message, parent=self.winfo_toplevel()):
            return
        try:
            self.controller.edit_location(identity, **values, allow_merge=merging)
            self._changed()
        except ValueError as exc:
            messagebox.showwarning('Location not changed', str(exc), parent=self.winfo_toplevel())

    def _build_location_assignment(self, device_id):
        choices = equipment_location_choices(self.design)
        choice_ids = {location_key(label): identity for label, identity in choices.items()}
        current = self.design.device_location_ids.get(device_id)
        current_record = self.design.equipment_locations.get(current)
        ctk.CTkLabel(self.properties, text='Assign equipment location', anchor='w').grid(
            row=1, column=0, sticky='w', padx=10)
        variable = tk.StringVar(master=self.properties)
        picker = SearchableComboBox(
            self.properties, values=list(choices), max_visible=8,
            allow_custom=True, variable=variable)
        picker.set(next((label for label, identity in choices.items() if identity == current),
                        'Location needs review'))
        picker.grid(row=2, column=0, sticky='ew', padx=10, pady=4)
        apply_button = ctk.CTkButton(
            self.properties, text='Assign location', state='disabled',
            command=lambda: self._assign_equipment_location(
                device_id, choice_ids.get(location_key(variable.get())), name=variable.get()))
        apply_button.grid(row=3, column=0, sticky='ew', padx=10, pady=6)

        def update_ready(*_args):
            value = variable.get()
            identity = choice_ids.get(location_key(value))
            if identity is not None:
                ready = identity != current
            else:
                ready = is_equipment_location_name(value) and (
                    current_record is None or location_key(value) != location_key(current_record.full_label))
            if apply_button.winfo_exists():
                apply_button.configure(state='normal' if ready else 'disabled')

        # Observe only the draft text. Creation and assignment happen together
        # inside the controller's undoable command after the Assign click.
        trace_id = variable.trace_add('write', update_ready)
        picker.bind('<Destroy>', lambda _event: variable.trace_remove('write', trace_id), add='+')

    @live_edit_only
    def _assign_equipment_location(self, device_id, identity, *, name=None):
        if identity is None and not is_equipment_location_name(name):
            return
        if identity is not None and self.design.device_location_ids.get(device_id) == identity:
            return
        label = (self.design.equipment_locations[identity].full_label if identity is not None
                 else ' '.join(name.split()))
        if messagebox.askyesno('Assign location?',
                f'Assign {device_id} to "{label}"? Paired RSP power supplies follow. '
                'Other occupants of its previous room stay there; drawing geometry does not move.',
                parent=self.winfo_toplevel()):
            try:
                if identity is None:
                    self.controller.assign_location_name(device_id, label)
                else:
                    self.controller.assign_location(device_id, identity)
                self._changed()
            except ValueError as exc:
                messagebox.showwarning('Location not changed', str(exc), parent=self.winfo_toplevel())

    def _refresh_inspector(self):
        self._refresh_selection_properties()
        self._clear_frame(self.validation_frame)
        issues = validate_riser(self.design, self._layout_preview or self.controller.document)
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

        route = self.controller.document.routes.get(edge_id)
        if route:
            ctk.CTkButton(self.properties,
                text='Restore Label' if route.label_hidden else 'Hide Label',
                command=lambda: self._set_label(edge_id, hidden=not route.label_hidden)).grid(
                    row=row+1, column=0, sticky='ew', padx=10, pady=4)
            ctk.CTkButton(self.properties, text='Reset Label Position',
                command=lambda: self._set_label(edge_id, reset=True)).grid(
                    row=row+2, column=0, sticky='ew', padx=10, pady=4)
            ctk.CTkLabel(self.properties,
                text='Drag the callout to move it. Hiding a label keeps the wire and its metadata.',
                wraplength=250, justify='left').grid(row=row+3, column=0, padx=10, pady=6)

    @live_edit_only
    def _set_label(self, edge_id, **changes):
        self.cancel(redraw=False)
        self.controller.update_label(edge_id, **changes)
        self._reconcile_selection()
        self._changed()

    @live_edit_only
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

    @live_edit_only
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

    @live_edit_only
    def _arrange(self, annotation_id, where):
        self.controller.arrange_annotation(annotation_id, where)
        self._changed()

    @live_edit_only
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
        doc = self._layout_preview or self.controller.document
        element = doc.elements.get(ref)
        if element:
            points = [(element.x, element.y),
                      (element.x + element.width, element.y + element.height)]
        route = doc.routes.get(ref)
        if route:
            points = route.points
        annotation = next((item for item in doc.annotations
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
        self.controller.sync_external()
        self._reconcile_selection()
        for name, variable in self._title_vars.items():
            value = getattr(self.controller.document.title_block, name)
            variable.set(" | ".join(value) if name == "revisions" else str(value))
        self.redraw()
