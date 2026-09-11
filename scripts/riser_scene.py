"""Pure scene construction, routing, bridge detection, and validation."""

from __future__ import annotations

import heapq
import re
import fitz
from dataclasses import dataclass, fields, replace

from riser_model import (
    RiserDocument,
    RiserElement,
    RiserRoute,
    default_riser_document,
)
from topology_service import TopologyError, validate_connection_endpoints
from riser_drawing import TITLE_BLOCK_WIDTH, location_text_runs, title_bounds, device_text, title_text
from location_model import legacy_normal_location


PAGE_MARGIN = 54.0
GRID = 18.0
BRIDGE_HALF_WIDTH = 9.0
BRIDGE_HEIGHT = 10.0


@dataclass(frozen=True)
class Bridge:
    connection_id: str
    x: float
    y: float
    orientation: str


@dataclass(frozen=True)
class _RouteContact:
    first_connection_id: str
    second_connection_id: str
    x: float
    y: float
    bridge: Bridge | None


@dataclass(frozen=True)
class RiserIssue:
    code: str
    message: str
    ref: str = ""
    severity: str = "warning"


def _device_ids(design) -> list[str]:
    return [
        "MSP",
        *(s.id for s in design.splitters),
        *(f"RSP-{r.number}" for r in design.rsps),
        *(f"KEYPAD-{k.number}" for k in design.keypads),
    ]


def _location(design, device_id: str) -> str:
    if device_id == "MSP":
        return design.site_info.xr550_location or "MSP"
    for splitter in design.splitters:
        if splitter.id == device_id:
            return splitter.location or "UNSPECIFIED"
    if device_id.startswith("RSP-"):
        number = int(device_id.split("-", 1)[1])
        rsp = next((r for r in design.rsps if r.number == number), None)
        location = (rsp.location if rsp else None) or "UNSPECIFIED"
        if _normal_location(location) in {"MSP", "AT MSP", "SAME AS MSP"}:
            return design.site_info.xr550_location or "MSP"
        return location
    if device_id.startswith("KEYPAD-"):
        number = int(device_id.split("-", 1)[1])
        keypad = next((k for k in design.keypads if k.number == number), None)
        return (keypad.location if keypad else None) or "UNSPECIFIED"
    return "UNSPECIFIED"


def _normal_location(value: str) -> str:
    return legacy_normal_location(value)


def _kind(design, device_id: str) -> str:
    if device_id == "MSP":
        return "msp"
    if any(splitter.id == device_id for splitter in design.splitters):
        return "splitter"
    if device_id.startswith("RSP-"):
        return "rsp"
    return "keypad"


def _size(design, device_id: str) -> tuple[float, float]:
    return {
        "msp": (220.0, 104.0),
        "splitter": (184.0, 80.0),
        "rsp": (220.0, 110.0),
        "keypad": (148.0, 72.0),
    }[_kind(design, device_id)]


def _branch_and_depth(design) -> tuple[dict[str, str], dict[str, int]]:
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in design.connections:
        adjacency.setdefault(edge.source.device_id, []).append(
            (edge.target.device_id, edge.source.port_id))
    branch = {"MSP": "root"}
    depth = {"MSP": 0}
    pending: list[str] = ["MSP"]
    while pending:
        source = pending.pop(0)
        for target, port in sorted(adjacency.get(source, [])):
            side = branch.get(source, "")
            if source == "MSP":
                side = "kp" if port in {"KP BUS", "PROG"} else "lx"
            candidate_depth = depth[source] + 1
            if target not in depth or candidate_depth < depth[target]:
                depth[target] = candidate_depth
                branch[target] = side
                pending.append(target)
    for device_id in _device_ids(design):
        branch.setdefault(device_id, "lx" if device_id.startswith(("RSP-", "710-LX")) else "kp")
        depth.setdefault(device_id, 99)
    return branch, depth


def _connection_sort_key(edge):
    match = re.fullmatch(r"OUT(\d+)", edge.source.port_id)
    port_order = int(match.group(1)) if match else 0
    return (
        edge.source.device_id,
        port_order,
        edge.source.port_id,
        edge.target.device_id,
        edge.target.port_id,
        edge.id,
    )


def _snap(value: float) -> float:
    return round(value / GRID) * GRID


def layout_clusters(design, *, title_source=None):
    from riser_cluster_layout import layout_clusters as build
    return build(design, title_source=title_source)


def layout_riser(design, *, title_source: RiserDocument | None = None,
                 _cluster_records=None, _cluster_bindings=None,
                 _cluster_spacing=(72.0, 90.0)) -> RiserDocument:
    """Create a deterministic location-first 24x36 electrical scene."""
    document = default_riser_document(design)
    clustered = _cluster_records is not None
    document.layout_version = 2 if clustered else 1
    from riser_cluster_layout import natural_key, cluster_heading
    source_z_order: list[str] = []
    if title_source is not None:
        document.title_block = title_source.title_block
        document.annotations = list(title_source.annotations)
        source_z_order = list(title_source.z_order)
    drawing_left = PAGE_MARGIN
    drawing_right = document.page_width - TITLE_BLOCK_WIDTH - PAGE_MARGIN
    drawing_width = drawing_right - drawing_left
    branch, depth = _branch_and_depth(design)

    device_ids = _device_ids(design)
    raw_locations = {device_id: _normal_location(_location(design, device_id))
                     for device_id in device_ids}
    known_locations = set(raw_locations.values())

    def physical_location(key: str) -> str:
        # If the only difference is an explicit floor qualifier, prefer the
        # already-known room name. This joins e.g. "MAIN BUILDING 1ST FLOOR
        # SUPPLY ROOM" to "MAIN BUILDING SUPPLY ROOM" without ever merging
        # two distinct floor-qualified locations.
        without_floor = re.sub(r"\b\d+(?:ST|ND|RD|TH)\s+FLOOR\b", "", key)
        without_floor = re.sub(r"\s+", " ", without_floor).strip()
        return without_floor if without_floor in known_locations else key

    locations = {device_id: physical_location(key)
                 for device_id, key in raw_locations.items()}
    if clustered:
        locations = {device_id: _cluster_bindings[device_id] for device_id in device_ids}
    location_devices: dict[str, list[str]] = {}
    for device_id in device_ids:
        location_devices.setdefault(locations[device_id], []).append(device_id)

    H_GAP = 54.0
    # Three independent splitter outputs need distinct orthogonal lanes
    # between vertically stacked devices. A 36-point gap left only four
    # points after symbol clearances and forced cables through the next 710.
    V_GAP = _cluster_spacing[0] if clustered else 72.0
    FRAME_X = 24.0
    FRAME_TOP = 50.0
    FRAME_BOTTOM = 18.0
    heading_data = {}

    def module_geometry(location: str, members: list[str]):
        rows: dict[int, list[str]] = {}
        for device_id in members:
            rows.setdefault(depth[device_id], []).append(device_id)
        ordered_rows: list[list[str]] = []
        maximum_inner_width = drawing_width - FRAME_X * 2
        for level in sorted(rows):
            ordered = sorted(rows[level], key=lambda item: (
                {"kp": 0, "root": 1, "lx": 2}.get(branch[item], 1),
                natural_key(item) if clustered else item))
            current: list[str] = []
            current_width = 0.0
            for item in ordered:
                item_width = _size(design, item)[0]
                added = item_width if not current else H_GAP + item_width
                if current and current_width + added > maximum_inner_width:
                    ordered_rows.append(current)
                    current, current_width = [], 0.0
                    added = item_width
                current.append(item)
                current_width += added
            if current:
                ordered_rows.append(current)
        row_widths = [sum(_size(design, item)[0] for item in row) +
                      H_GAP * max(0, len(row) - 1) for row in ordered_rows]
        row_heights = [max(_size(design, item)[1] for item in row)
                       for row in ordered_rows]
        label_width = min(drawing_width, len(location) * 9.5 + FRAME_X * 2)
        width = max(max(row_widths, default=220.0) + FRAME_X * 2,
                    label_width)
        frame_top = FRAME_TOP
        if clustered:
            width = max(max(row_widths, default=220.0) + FRAME_X * 2, 360.0)
            lines = cluster_heading(_cluster_records[location], members)
            probe = RiserElement('measure', 'location', '', 0, 0, width, 0, heading_lines=lines)
            runs = location_text_runs(probe, small=True)
            heading_height = max((run.y + run.size * .3 for run in runs), default=30) + 12
            frame_top = heading_height + 24
            heading_data[location] = (lines, heading_height)
        height = frame_top + sum(row_heights) + V_GAP * max(0, len(ordered_rows) - 1) + FRAME_BOTTOM
        local_positions = {}
        y = frame_top
        for row, row_width, row_height in zip(ordered_rows, row_widths, row_heights):
            x = (width - row_width) / 2
            for device_id in row:
                item_width, item_height = _size(design, device_id)
                local_positions[device_id] = (x, y + (row_height - item_height) / 2)
                x += item_width + H_GAP
            y += row_height + V_GAP
        return width, height, local_positions

    modules = {location: module_geometry(location, members)
               for location, members in location_devices.items()}
    root_location = locations["MSP"]
    module_origins: dict[str, tuple[float, float]] = {}
    root_width, root_height, _root_positions = modules[root_location]
    root_x = drawing_left + (drawing_width - root_width) / 2
    root_y = 72.0
    module_origins[root_location] = (_snap(root_x), _snap(root_y))

    def lane(location: str) -> int:
        sides = {branch[item] for item in location_devices[location]} - {"root"}
        if sides == {"kp"}:
            return 0
        if sides == {"lx"}:
            return 2
        return 1

    remaining = {location for location in location_devices if location != root_location}
    adjacency: dict[str, set[str]] = {location: set() for location in remaining}
    indegree = {location: 0 for location in remaining}
    incoming_order = {}
    ranks = {location: 0 for location in remaining}
    for edge in design.connections:
        source_location = locations.get(edge.source.device_id)
        target_location = locations.get(edge.target.device_id)
        key = _connection_sort_key(edge)
        if clustered:
            key = (natural_key(edge.source.device_id), key[1], edge.source.port_id,
                   natural_key(edge.target.device_id), edge.target.port_id, edge.id)
        if target_location != source_location:
            incoming_order[target_location] = min(incoming_order.get(target_location, key), key)
        if (source_location == target_location or target_location not in remaining
                or source_location == root_location or source_location not in remaining):
            continue
        if target_location not in adjacency[source_location]:
            adjacency[source_location].add(target_location)
            indegree[target_location] += 1

    def location_sort_key(location: str):
        return (min(depth[item] for item in location_devices[location]),
                lane(location), incoming_order.get(location, ()), location)

    ready = sorted((location for location in remaining if indegree[location] == 0),
                   key=location_sort_key)
    ordered_locations: list[str] = []
    while ready:
        location = ready.pop(0)
        ordered_locations.append(location)
        for target_location in sorted(adjacency[location], key=location_sort_key):
            ranks[target_location] = max(ranks[target_location], ranks[location] + 1)
            indegree[target_location] -= 1
            if indegree[target_location] == 0:
                ready.append(target_location)
                ready.sort(key=location_sort_key)
    ordered_locations.extend(sorted(remaining - set(ordered_locations),
                                    key=location_sort_key))
    if clustered:
        ranks = {location: min(depth[item] for item in location_devices[location])
                 for location in remaining}
        ordered_locations = sorted(remaining, key=lambda loc: (
            ranks[loc], lane(loc), incoming_order.get(loc, ()),
            natural_key(_cluster_records[loc].full_label), loc))

    MODULE_GAP = 72.0
    ROW_GAP = _cluster_spacing[1] if clustered else 90.0

    def balanced_rows(items):
        # Minimum row count, then minimum squared unused width. Unlike a
        # greedy shelf, a nearly empty last row is balanced with its siblings.
        best = [(0, 0.0, [])] + [None] * len(items)
        for end in range(1, len(items) + 1):
            width = 0.0
            for start in range(end - 1, -1, -1):
                width += modules[items[start]][0] + (MODULE_GAP if start < end - 1 else 0)
                if width > drawing_width and start < end - 1:
                    break
                previous = best[start]
                candidate = (previous[0] + 1, previous[1] + (drawing_width - width)**2,
                             previous[2] + [items[start:end]])
                if best[end] is None or candidate[:2] < best[end][:2]:
                    best[end] = candidate
        return best[-1][2]

    # Reserve a separate tier for downstream modules whenever the sheet has
    # room. Within a tier, KP/LX and source output order remain deterministic.
    tiers = {}
    for location in ordered_locations:
        tiers.setdefault(ranks[location], []).append(location)
    packed_rows = [row for rank in sorted(tiers)
                   for row in balanced_rows(tiers[rank])]
    height = root_height + sum(max(modules[loc][1] for loc in row) + ROW_GAP
                               for row in packed_rows)
    if height > document.page_height - 2 * PAGE_MARGIN:
        # Dense chains retain left-to-right topological order on compact rows,
        # rather than shrinking symbols or spilling a long single column.
        packed_rows = balanced_rows(ordered_locations)

    cursor_y = root_y + root_height + ROW_GAP
    for row in packed_rows:
        row_width = sum(modules[location][0] for location in row) + \
            MODULE_GAP * max(0, len(row) - 1)
        x = drawing_left + max(0.0, (drawing_width - row_width) / 2)
        row_height = max(modules[location][1] for location in row)
        for location in row:
            width, _height, _positions = modules[location]
            module_origins[location] = (_snap(x), _snap(cursor_y))
            x += width + MODULE_GAP
        cursor_y += row_height + ROW_GAP

    # Sparse risers previously clung to the top edge of the 24x36 sheet,
    # leaving most of the page blank below the last location module.  Keep
    # the topology-driven packing intact and translate the completed block
    # as one unit so the vertical whitespace is balanced.  Dense drawings
    # that consume the printable height are deliberately left untouched.
    content_top = min(y for _x, y in module_origins.values())
    content_bottom = max(
        module_origins[location][1] + modules[location][1]
        for location in module_origins
    )
    printable_top = PAGE_MARGIN
    printable_bottom = document.page_height - PAGE_MARGIN
    content_height = content_bottom - content_top
    printable_height = printable_bottom - printable_top
    if content_height < printable_height and not clustered:
        balanced_top = printable_top + (printable_height - content_height) / 2
        shift_y = _snap(balanced_top - content_top)
        module_origins = {
            location: (x, y + shift_y)
            for location, (x, y) in module_origins.items()
        }

    positions: dict[str, tuple[float, float]] = {}
    for location, members in location_devices.items():
        origin_x, origin_y = module_origins[location]
        _width, _height, local_positions = modules[location]
        for device_id in members:
            local_x, local_y = local_positions[device_id]
            positions[device_id] = (_snap(origin_x + local_x),
                                    _snap(origin_y + local_y))

    for device_id in _device_ids(design):
        x, y = positions[device_id]
        width, height = _size(design, device_id)
        element = RiserElement(
            id=f"device:{device_id}", kind="device", ref=device_id,
            x=x, y=y, width=width, height=height,
            location_id=f"location:{locations[device_id]}",
        )
        document.elements[element.id] = element

    # Location frames are compact modules assigned before device placement,
    # so one room can never become a giant dashed box spanning both branches.
    for location, members in sorted(location_devices.items()):
        x1, y1 = module_origins[location]
        module_width, module_height, _positions = modules[location]
        key = f"location:{location}"
        document.elements[key] = RiserElement(
            id=key, kind="location", ref=location,
            x=x1, y=y1, width=module_width, height=module_height,
        )
        if clustered:
            frame = document.elements[key]
            frame.ref = _cluster_records[location].full_label or 'LOCATION UNCONFIRMED'
            frame.physical_location_id = location
            frame.heading_lines, frame.heading_height = heading_data[location]

    location_ids = sorted(k for k, e in document.elements.items() if e.kind == "location")
    device_ids = [f"device:{d}" for d in _device_ids(design)]
    scene_ids = location_ids + device_ids
    annotation_ids = [annotation.id for annotation in document.annotations]
    if title_source is None:
        document.z_order = scene_ids + annotation_ids
    else:
        # Re-layout replaces electrical geometry, but markup remains the
        # user's presentation state. Anchor each annotation to the nearest
        # preceding live scene object so back/front/interleaved stacking
        # survives while new or removed hardware is merged safely.
        live_scene_ids = set(scene_ids)
        live_annotation_ids = set(annotation_ids)
        leading_annotations: list[str] = []
        annotations_after: dict[str, list[str]] = {}
        seen_annotations: set[str] = set()
        previous_scene_id: str | None = None
        for item_id in source_z_order:
            if item_id in live_scene_ids:
                previous_scene_id = item_id
            elif item_id in live_annotation_ids and item_id not in seen_annotations:
                seen_annotations.add(item_id)
                if previous_scene_id is None:
                    leading_annotations.append(item_id)
                else:
                    annotations_after.setdefault(previous_scene_id, []).append(item_id)
        merged_order = list(leading_annotations)
        for scene_id in scene_ids:
            merged_order.append(scene_id)
            merged_order.extend(annotations_after.get(scene_id, ()))
        merged_order.extend(
            annotation_id for annotation_id in annotation_ids
            if annotation_id not in seen_annotations
        )
        document.z_order = merged_order
    obstacles = routing_obstacles(document)
    reserved_segments = []
    routed_connections: dict[str, RiserRoute] = {}
    for edge in sorted(design.connections, key=_connection_sort_key):
        source = document.elements.get(f"device:{edge.source.device_id}")
        target = document.elements.get(f"device:{edge.target.device_id}")
        if not source or not target:
            continue
        points = route_topology_connection(
            edge, source, target, obstacles,
            reserved_segments=reserved_segments,
        )
        previous = title_source.routes.get(edge.id) if title_source else None
        routed_connections[edge.id] = RiserRoute(
            edge.id, points, label_hidden=previous.label_hidden if previous else False)
        reserved_segments.extend(_route_segments(points))
    # Routing order is deliberately canonical, while serialized/display
    # order continues to mirror the domain graph for compatibility.
    document.routes = {
        edge.id: routed_connections[edge.id]
        for edge in design.connections
        if edge.id in routed_connections
    }
    _place_route_labels(design, document)
    return document


def routing_obstacles(document: RiserDocument, design=None) -> list[RiserElement]:
    """Device footprints and the text band of each location are protected."""
    obstacles = [e for e in document.elements.values() if e.kind == "device"]
    for element in document.elements.values():
        if element.kind == "location" and document.show_location_frames:
            left, top, right, bottom = _location_heading_box(element)
            obstacles.append(RiserElement(
                f"heading:{element.id}", "heading", f"heading:{element.id}",
                left, top, right - left, bottom - top))
    if design is not None:
        from riser_symbols import caption_boxes
        for element in document.elements.values():
            if element.kind != 'device':
                continue
            for index,(left,top,right,bottom) in enumerate(caption_boxes(design,element)):
                obstacles.append(RiserElement(f'caption:{element.id}:{index}', 'caption',
                    f'caption:{element.id}:{index}',left,top,right-left,bottom-top))
    return obstacles


INPUT_SIDES = ('top', 'right', 'bottom', 'left')
INPUT_NORMALS = {'top': (0, -1), 'right': (1, 0), 'bottom': (0, 1), 'left': (-1, 0)}
MSP_OUTPUT_X = {"PROG": 0.207, "LX500": 0.353, "LX600": 0.5,
                "LX700": 0.647, "LX800": 0.793, "LX900": 0.94}


def input_side_point(element: RiserElement, side: str):
    return {'top': (element.x + element.width / 2, element.y),
            'right': (element.x + element.width, element.y + element.height / 2),
            'bottom': (element.x + element.width / 2, element.y + element.height),
            'left': (element.x, element.y + element.height / 2)}[side]


def port_point(element: RiserElement, port_id: str, *, output: bool) -> tuple[float, float]:
    if not output:
        return input_side_point(element, element.input_side)
    if element.ref == "MSP":
        if port_id == 'KP BUS':
            return (element.x, element.y + element.height / 2)
        # These seven targets must remain distinct at editor fit zoom. Do not
        # grid-snap them: snapping previously collapsed LX800 and LX900 onto
        # the same coordinate and made reconnecting to the intended bus
        # impossible.
        return (element.x + element.width * element.port_x.get(port_id,MSP_OUTPUT_X.get(port_id, 0.5)),
                element.y + element.height)
    match = re.fullmatch(r"OUT([123])", port_id)
    if output and match:
        return (element.x + element.width * int(match.group(1)) / 4,
                element.y + element.height)
    return (element.x + element.width / 2, element.y)


def _segment_hits_rect(a, b, rect: RiserElement, clearance: float = 12.0) -> bool:
    left, top = rect.x - clearance, rect.y - clearance
    right, bottom = rect.x + rect.width + clearance, rect.y + rect.height + clearance
    if a[0] == b[0]:
        return left < a[0] < right and max(min(a[1], b[1]), top) < min(max(a[1], b[1]), bottom)
    if a[1] == b[1]:
        return top < a[1] < bottom and max(min(a[0], b[0]), left) < min(max(a[0], b[0]), right)
    return False


def _compact_route(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    compact: list[tuple[float, float]] = []
    for point in points:
        if compact and point == compact[-1]:
            continue
        if (len(compact) >= 2
                and (compact[-2][0] == compact[-1][0] == point[0]
                     or compact[-2][1] == compact[-1][1] == point[1])):
            compact[-1] = point
        else:
            compact.append(point)
    return compact


def reattach_route_endpoint(points, new_endpoint: tuple[float, float], *,
                            at_start: bool) -> list[tuple[float, float]]:
    """Move one manual-route endpoint without moving the opposite endpoint."""
    if not at_start:
        return list(reversed(reattach_route_endpoint(
            list(reversed(points)), new_endpoint, at_start=True)))
    points = list(points)
    if len(points) < 2:
        return [new_endpoint]
    old_endpoint, adjacent = points[0], points[1]
    if new_endpoint == old_endpoint:
        return points
    if len(points) == 2:
        opposite = adjacent
        if (new_endpoint[0] == opposite[0]
                or new_endpoint[1] == opposite[1]):
            return [new_endpoint, opposite]
        if old_endpoint[0] == opposite[0]:
            bend = (opposite[0], new_endpoint[1])
        else:
            bend = (new_endpoint[0], opposite[1])
        return [new_endpoint, bend, opposite]
    points[0] = new_endpoint
    points[1] = (
        (new_endpoint[0], adjacent[1])
        if old_endpoint[0] == adjacent[0]
        else (adjacent[0], new_endpoint[1])
    )
    return points


def _route_segments(points: list[tuple[float, float]]):
    return list(zip(points, points[1:]))


def reattach_source_route(edge, source, points, obstacles):
    """Keep manual bends, adapting a legacy bottom feed to the left-side KP port."""
    start = port_point(source, edge.source.port_id, output=True)
    if source.ref != 'MSP' or edge.source.port_id != 'KP BUS' or len(points) < 2:
        return reattach_route_endpoint(points, start, at_start=True)
    if points[0] == start:
        return list(points)
    # Normal moves of an already left-facing lead retain their existing bends.
    adjusted = reattach_route_endpoint(points, start, at_start=True)
    if adjusted[1][0] < start[0] and adjusted[1][1] == start[1]:
        return adjusted
    lead = (start[0] - GRID, start[1])
    middle = route_connection(lead, points[1], obstacles)
    return _compact_route([start, *middle, *points[1:]])


def reserved_route_segments(document: RiserDocument, *, exclude_id: str = "",
                            live_ids: set[str] | None = None):
    segments = []
    for connection_id, route in document.routes.items():
        if connection_id == exclude_id:
            continue
        if live_ids is not None and connection_id not in live_ids:
            continue
        segments.extend(_route_segments(route.points))
    return segments


def _collinear_overlap_length(first, second) -> float:
    a, b = first
    c, d = second
    if a[1] == b[1] == c[1] == d[1]:
        return max(
            0.0,
            min(max(a[0], b[0]), max(c[0], d[0]))
            - max(min(a[0], b[0]), min(c[0], d[0])),
        )
    if a[0] == b[0] == c[0] == d[0]:
        return max(
            0.0,
            min(max(a[1], b[1]), max(c[1], d[1]))
            - max(min(a[1], b[1]), min(c[1], d[1])),
        )
    return 0.0


def route_connection(start: tuple[float, float], end: tuple[float, float],
                     obstacles: list[RiserElement], *, source_ref: str = "",
                     target_ref: str = "", reserved_segments=()) -> list[tuple[float, float]]:
    """Find a shortest obstacle-free Manhattan path on a visibility grid."""
    if start == end:
        return [start]
    reserved_segments = tuple(reserved_segments)
    reserved_horizontal: dict[float, list] = {}
    reserved_vertical: dict[float, list] = {}
    for segment in reserved_segments:
        first, second = segment
        if first[1] == second[1]:
            reserved_horizontal.setdefault(first[1], []).append(segment)
        elif first[0] == second[0]:
            reserved_vertical.setdefault(first[0], []).append(segment)

    def contains(point, obstacle):
        return (obstacle.x <= point[0] <= obstacle.x + obstacle.width
                and obstacle.y <= point[1] <= obstacle.y + obstacle.height)

    blockers = [
        obstacle for obstacle in obstacles
        if obstacle.ref not in {source_ref, target_ref}
        and not contains(start, obstacle) and not contains(end, obstacle)
    ]
    # An unobstructed L is already a minimum-length, minimum-bend route.
    # Check both orientations before allocating a visibility graph over every
    # device, heading, and reserved lane (especially costly after a drop).
    def direct_clear(points):
        segments = _route_segments(points)
        return (not any(_segment_hits_rect(a, b, obstacle, clearance=12.0)
                    for a, b in segments for obstacle in blockers)
                and not any(_collinear_overlap_length(segment, reserved) > 0
                            for segment in segments for reserved in reserved_segments))

    for bend in ((start[0], end[1]), (end[0], start[1])):
        direct = _compact_route([start, bend, end])
        if direct_clear(direct):
            return direct
    xs = {start[0], end[0]}
    ys = {start[1], end[1]}
    for obstacle in blockers:
        xs.update((obstacle.x - 18.0, obstacle.x + obstacle.width + 18.0))
        ys.update((obstacle.y - 18.0, obstacle.y + obstacle.height + 18.0))
    # Add an adjacent visibility lane beside every existing run. This gives
    # Dijkstra somewhere useful to move when a direct line is already taken.
    for first, second in reserved_segments:
        if first[1] == second[1]:
            ys.update((first[1] - GRID, first[1] + GRID))
        elif first[0] == second[0]:
            xs.update((first[0] - GRID, first[0] + GRID))
    xs.update((min(xs) - GRID, max(xs) + GRID))
    ys.update((min(ys) - GRID, max(ys) + GRID))

    # If both L orientations are blocked, a clear two-bend lane strictly
    # between the endpoints still has minimum Manhattan length. These cheap
    # checks must not take a detour outside that rectangle; complex detours
    # remain the visibility solver's responsibility.
    for axis, lanes in ((0, xs), (1, ys)):
        midpoint = (start[axis] + end[axis]) / 2
        for lane in sorted(lanes | {midpoint}, key=lambda value: (abs(value-midpoint), value)):
            if not min(start[axis], end[axis]) < lane < max(start[axis], end[axis]):
                continue
            bends = ([(lane, start[1]), (lane, end[1])] if axis == 0
                     else [(start[0], lane), (end[0], lane)])
            direct = _compact_route([start, *bends, end])
            if direct_clear(direct):
                return direct

    def inside_clearance(point):
        x, y = point
        return any(obstacle.x - 12.0 < x < obstacle.x + obstacle.width + 12.0
                   and obstacle.y - 12.0 < y < obstacle.y + obstacle.height + 12.0
                   for obstacle in blockers)

    nodes = {
        (x, y) for x in sorted(xs) for y in sorted(ys)
        if (x, y) in {start, end} or not inside_clearance((x, y))
    }
    neighbors: dict[tuple[float, float], list[tuple[tuple[float, float], str]]] = {
        node: [] for node in nodes
    }

    def clear_segment(first, second):
        return not any(_segment_hits_rect(first, second, obstacle, clearance=12.0)
                       for obstacle in blockers)

    columns: dict[float, list[tuple[float, float]]] = {}
    rows: dict[float, list[tuple[float, float]]] = {}
    for node in nodes:
        columns.setdefault(node[0], []).append(node)
        rows.setdefault(node[1], []).append(node)
    for x in sorted(columns):
        column = sorted(columns[x], key=lambda p: p[1])
        for first, second in zip(column, column[1:]):
            if clear_segment(first, second):
                neighbors[first].append((second, "v"))
                neighbors[second].append((first, "v"))
    for y in sorted(rows):
        row = sorted(rows[y], key=lambda p: p[0])
        for first, second in zip(row, row[1:]):
            if clear_segment(first, second):
                neighbors[first].append((second, "h"))
                neighbors[second].append((first, "h"))

    queue = [(0.0, 0, start, "")]
    distances = {(start, ""): (0.0, 0)}
    previous = {}
    final_state = None
    while queue:
        cost, bends, node, orientation = heapq.heappop(queue)
        if distances.get((node, orientation)) != (cost, bends):
            continue
        if node == end:
            final_state = (node, orientation)
            break
        for candidate, candidate_orientation in neighbors[node]:
            length = abs(candidate[0] - node[0]) + abs(candidate[1] - node[1])
            turn = bool(orientation and orientation != candidate_orientation)
            reservations = (
                reserved_horizontal.get(node[1], ())
                if candidate_orientation == "h"
                else reserved_vertical.get(node[0], ())
            )
            overlap = sum(
                _collinear_overlap_length((node, candidate), reserved)
                for reserved in reservations
            )
            # Positive overlap visually creates a false electrical junction.
            # Keep it possible as a last resort on an impossibly dense sheet,
            # but make any clear detour decisively cheaper. Perpendicular
            # crossings remain unpenalized and receive bridge hops later.
            congestion_cost = overlap * 1000.0
            next_cost = cost + length + (GRID if turn else 0.0) + congestion_cost
            next_bends = bends + int(turn)
            state = (candidate, candidate_orientation)
            if (next_cost, next_bends) < distances.get(state, (float("inf"), 10**9)):
                distances[state] = (next_cost, next_bends)
                previous[state] = (node, orientation)
                heapq.heappush(queue, (next_cost, next_bends, candidate,
                                       candidate_orientation))

    if final_state is None:
        # This should require malformed geometry (for example an endpoint
        # trapped inside an unrelated footprint); validation will flag it.
        return [start, (start[0], end[1]), end]

    path = []
    state = final_state
    while True:
        path.append(state[0])
        if state == (start, ""):
            break
        state = previous[state]
    path.reverse()

    return _compact_route(path)


def route_topology_connection(edge, source: RiserElement, target: RiserElement,
                              obstacles: list[RiserElement], *,
                              reserved_segments=()) -> list[tuple[float, float]]:
    """Route one logical edge with clear, port-specific device leads.

    The generic Manhattan router is free to leave an endpoint horizontally.
    Doing that for several outputs on one 710 places independent cables on top
    of each other and makes them look like a common trunk.  C1 output symbols
    face down and input symbols face up; MSP KP BUS faces left. Reserve a
    short outward lead before asking the obstacle router to connect them.
    """
    reserved_segments = tuple(reserved_segments)
    start = port_point(source, edge.source.port_id, output=True)
    end = port_point(target, edge.target.port_id, output=False)

    output_match = re.fullmatch(r"OUT([123])", edge.source.port_id)
    left_output = source.ref == 'MSP' and edge.source.port_id == 'KP BUS'
    if left_output:
        start_lead = (start[0] - GRID, start[1])
    elif output_match:
        port_number = int(output_match.group(1))
        source_bottom = source.y + source.height
        # Use full grid lanes where space permits.  If another device is
        # directly below the splitter, divide the clear gap among all three
        # stable output lanes so no lead enters its 12-point safety margin.
        maximum_drop = GRID * 3
        for obstacle in obstacles:
            if obstacle.ref == source.ref or obstacle.y < source_bottom:
                continue
            if (obstacle.x - 12.0 <= start[0]
                    <= obstacle.x + obstacle.width + 12.0):
                maximum_drop = min(
                    maximum_drop,
                    max(12.0, obstacle.y - source_bottom - 12.0),
                )
        if maximum_drop >= GRID * 3:
            drop_distance = GRID * port_number
        else:
            lane_spacing = max(3.0, (maximum_drop - 12.0) / 2.0)
            drop_distance = 12.0 + lane_spacing * (port_number - 1)
        start_lead = (start[0], start[1] + drop_distance)
    else:
        start_lead = (start[0], start[1] + GRID)

    normal = INPUT_NORMALS[target.input_side]
    target_lead = (end[0] + normal[0]*GRID, end[1] + normal[1]*GRID)

    def build(prefix, middle_start, middle_end, suffix):
        middle = route_connection(
            middle_start,
            middle_end,
            obstacles,
            # The explicit leads are already outside the endpoint footprints.
            # Keep source and target in the blocker set so the middle path
            # cannot double back through either symbol and erase the lead.
            reserved_segments=reserved_segments,
        )
        return _compact_route([*prefix, *middle, *suffix])

    endpoint_refs = {source.ref, target.ref}

    def score(points, preference):
        segments = _route_segments(points)
        crossings = sum(
            _segment_hits_rect(first, second, obstacle, clearance=0)
            for first, second in segments
            for obstacle in obstacles
            if obstacle.ref not in endpoint_refs
        )
        overlap = sum(
            _collinear_overlap_length(segment, reserved)
            for segment in segments
            for reserved in reserved_segments
        )
        length = sum(
            abs(second[0] - first[0]) + abs(second[1] - first[1])
            for first, second in segments
        )
        return crossings, overlap, length, len(segments), preference

    standard = build(
        [start, start_lead], start_lead,
        target_lead, [target_lead, end],
    )
    if score(standard, 0)[:2] == (0, 0):
        return standard

    def side_leads(element, endpoint, *, downward):
        direction = 1.0 if downward else -1.0
        relevant = []
        available = GRID * 2
        for obstacle in obstacles:
            if obstacle.ref in endpoint_refs:
                continue
            if not (obstacle.x <= endpoint[0] <= obstacle.x + obstacle.width):
                continue
            if downward:
                if obstacle.y >= endpoint[1]:
                    available = min(available, obstacle.y - endpoint[1])
                    relevant.append(obstacle)
                elif obstacle.y + obstacle.height > endpoint[1]:
                    available = 0.0
                    relevant.append(obstacle)
            else:
                bottom = obstacle.y + obstacle.height
                if bottom <= endpoint[1]:
                    available = min(available, endpoint[1] - bottom)
                    relevant.append(obstacle)
                elif obstacle.y < endpoint[1]:
                    available = 0.0
                    relevant.append(obstacle)
        for first, second in reserved_segments:
            if first[0] != second[0] or first[0] != endpoint[0]:
                continue
            low, high = sorted((first[1], second[1]))
            if downward:
                if low >= endpoint[1]:
                    available = min(available, low - endpoint[1])
                elif high > endpoint[1]:
                    available = 0.0
            else:
                if high <= endpoint[1]:
                    available = min(available, endpoint[1] - high)
                elif low < endpoint[1]:
                    available = 0.0
        turn_distance = max(1.0, min(GRID, available / 2.0))
        turn_y = endpoint[1] + direction * turn_distance
        entry_y = endpoint[1] + direction * GRID
        left = min([element.x, *(item.x - 12.0 for item in relevant)]) - GRID
        right = max([
            element.x + element.width,
            *(item.x + item.width + 12.0 for item in relevant),
        ]) + GRID
        if downward:
            return [
                ([endpoint, (endpoint[0], turn_y), (left, turn_y), (left, entry_y)],
                 (left, entry_y)),
                ([endpoint, (endpoint[0], turn_y), (right, turn_y), (right, entry_y)],
                 (right, entry_y)),
            ]
        return [
            ([(left, entry_y), (left, turn_y), (endpoint[0], turn_y), endpoint],
             (left, entry_y)),
            ([(right, entry_y), (right, turn_y), (endpoint[0], turn_y), endpoint],
             (right, entry_y)),
        ]

    prefixes = [([start, start_lead], start_lead)]
    if not left_output:
        prefixes.extend(side_leads(source, start, downward=True))
    suffixes = [([target_lead, end], target_lead)]
    if target.input_side == 'top':
        suffixes.extend(side_leads(target, end, downward=False))
    candidates = [(score(standard, 0), standard)]
    preference = 1
    for prefix, middle_start in prefixes:
        for suffix, middle_end in suffixes:
            if middle_start == start_lead and middle_end == target_lead:
                continue
            candidate = build(prefix, middle_start, middle_end, suffix)
            candidates.append((score(candidate, preference), candidate))
            preference += 1
    return min(candidates, key=lambda item: item[0])[1]


def route_rsp_input(edge, source, target, obstacles, *, reserved_segments=(), old_points=()):
    """Choose a drawing-side attachment without changing the logical IN port."""
    sides = (target.input_side,) if target.input_side_locked else INPUT_SIDES
    candidates = []
    for side in sides:
        candidate = replace(target, input_side=side)
        points = route_topology_connection(edge, source, candidate, obstacles,
                                           reserved_segments=reserved_segments)
        crossings = sum(_segment_hits_rect(a, b, obstacle, clearance=0)
                        for a, b in zip(points, points[1:]) for obstacle in obstacles)
        overlap = sum(_collinear_overlap_length(segment, reserved)
                      for segment in zip(points, points[1:]) for reserved in reserved_segments)
        length = sum(abs(a[0]-b[0]) + abs(a[1]-b[1]) for a, b in zip(points, points[1:]))
        candidates.append(((crossings, overlap, length, len(points), side != target.input_side), side, points))
    _, side, points = min(candidates, key=lambda candidate: candidate[0])
    if old_points and side == target.input_side:
        # Keep hand-placed bends when reattachment still approaches from outside.
        adapted = reattach_source_route(edge, source, old_points, obstacles)
        adapted = reattach_route_endpoint(adapted, input_side_point(target, side), at_start=False)
        normal = INPUT_NORMALS[side]
        if len(adapted) >= 2:
            previous, end = adapted[-2:]
            outward = ((previous[0]-end[0])*normal[0] + (previous[1]-end[1])*normal[1]) > 0
            if outward and not any(_segment_hits_rect(a, b, obstacle, clearance=0)
                                   for a, b in zip(adapted, adapted[1:]) for obstacle in obstacles):
                points = adapted
    return side, points


def _segments(points):
    return list(zip(points, points[1:]))


def _route_contacts(
        routes: dict[str, list[tuple[float, float]]]) -> list[_RouteContact]:
    """Find contacts between independent cable routes.

    A hop needs clear line on both sides of its center. Prefer the horizontal
    run (the established drafting convention), then use a vertical hop when a
    horizontal run ends at the contact. Contacts with no room for either hop
    remain explicit so validation can warn instead of drawing a false junction.
    Segments from one route are never compared, so its own bends and electrical
    endpoints retain their ordinary connected semantics.
    """
    contacts: dict[tuple[str, str, float, float], _RouteContact] = {}
    ids = sorted(routes)

    def record(first_id, second_id, x, y, bridge):
        key = (first_id, second_id, x, y)
        prior = contacts.get(key)
        # Multiple segment pairs can describe the same bend. Keep a bridgeable
        # description over an uncovered one and preserve horizontal preference.
        if (prior is None
                or (prior.bridge is None and bridge is not None)
                or (prior.bridge is not None and bridge is not None
                    and prior.bridge.orientation == "vertical"
                    and bridge.orientation == "horizontal")):
            contacts[key] = _RouteContact(
                first_id, second_id, x, y, bridge)

    for i, first_id in enumerate(ids):
        for second_id in ids[i + 1:]:
            for a1, a2 in _segments(routes[first_id]):
                for b1, b2 in _segments(routes[second_id]):
                    a_h = a1[1] == a2[1] and a1[0] != a2[0]
                    b_h = b1[1] == b2[1] and b1[0] != b2[0]
                    a_v = a1[0] == a2[0] and a1[1] != a2[1]
                    b_v = b1[0] == b2[0] and b1[1] != b2[1]
                    if a_h and b_v or a_v and b_h:
                        h_id, h1, h2, v_id, v1, v2 = (
                            (first_id, a1, a2, second_id, b1, b2)
                            if a_h else
                            (second_id, b1, b2, first_id, a1, a2)
                        )
                        x, y = v1[0], h1[1]
                        if not (min(h1[0], h2[0]) <= x <= max(h1[0], h2[0])
                                and min(v1[1], v2[1]) <= y <= max(v1[1], v2[1])):
                            continue
                        horizontal_clear = min(
                            abs(x - h1[0]), abs(x - h2[0])) >= BRIDGE_HALF_WIDTH
                        vertical_clear = min(
                            abs(y - v1[1]), abs(y - v2[1])) >= BRIDGE_HALF_WIDTH
                        bridge = None
                        if horizontal_clear:
                            bridge = Bridge(h_id, x, y, "horizontal")
                        elif vertical_clear:
                            bridge = Bridge(v_id, x, y, "vertical")
                        record(first_id, second_id, x, y, bridge)
                        continue

                    # Collinear overlap or endpoint contact cannot accept a
                    # transparent hop. Surface it through validation.
                    if a_h and b_h and a1[1] == b1[1]:
                        low = max(min(a1[0], a2[0]), min(b1[0], b2[0]))
                        high = min(max(a1[0], a2[0]), max(b1[0], b2[0]))
                        if low <= high:
                            record(first_id, second_id, (low + high) / 2,
                                   a1[1], None)
                    elif a_v and b_v and a1[0] == b1[0]:
                        low = max(min(a1[1], a2[1]), min(b1[1], b2[1]))
                        high = min(max(a1[1], a2[1]), max(b1[1], b2[1]))
                        if low <= high:
                            record(first_id, second_id, a1[0],
                                   (low + high) / 2, None)
    return sorted(contacts.values(), key=lambda item: (
        item.first_connection_id, item.second_connection_id, item.y, item.x))


def find_bridges(routes: dict[str, list[tuple[float, float]]]) -> list[Bridge]:
    bridges = {contact.bridge for contact in _route_contacts(routes)
               if contact.bridge is not None}
    return sorted(bridges, key=lambda b: (
        b.connection_id, b.y, b.x, b.orientation))


def wire_segments(connection_id, points, bridges):
    """Shared line/quadratic segments; hops never mask the underlying cable."""
    result = []
    for start, end in zip(points, points[1:]):
        axis = 0 if start[1] == end[1] else 1 if start[0] == end[0] else None
        cursor = start
        if axis is not None:
            orientation = "horizontal" if axis == 0 else "vertical"
            contacts = [b for b in bridges if b.connection_id == connection_id
                        and b.orientation == orientation
                        and min(start[axis], end[axis]) + BRIDGE_HALF_WIDTH <= (b.x, b.y)[axis]
                        <= max(start[axis], end[axis]) - BRIDGE_HALF_WIDTH
                        and abs((b.x, b.y)[1-axis] - start[1-axis]) < .01]
            direction = 1 if end[axis] >= start[axis] else -1
            for bridge in sorted(contacts, key=lambda b: (b.x, b.y)[axis], reverse=direction < 0):
                before, after, control = [bridge.x, bridge.y], [bridge.x, bridge.y], [bridge.x, bridge.y]
                before[axis] -= direction * BRIDGE_HALF_WIDTH
                after[axis] += direction * BRIDGE_HALF_WIDTH
                control[1-axis] += -BRIDGE_HEIGHT if axis == 0 else BRIDGE_HEIGHT
                result.append(("L", cursor, tuple(before)))
                result.append(("Q", tuple(before), tuple(control), tuple(after)))
                cursor = tuple(after)
        result.append(("L", cursor, end))
    return result


def repair_generated_scene(design, document: RiserDocument) -> bool:
    """Recover an outdated complete generated scene without moving manual work.

    Old saves can contain stale routes and frames with no logical ownership.
    A fresh layout is safe only when every device is present and no electrical
    drawing geometry was manually edited. Markup/title data survive via the
    normal re-layout path. Partially placed or manual scenes use conservative
    synchronization instead.
    """
    if document.layout_version >= 2:
        return False  # Version-2 geometry changes only through Preview/Apply.
    devices = [e for e in document.elements.values() if e.kind == "device"]
    if (document.unplaced or {e.ref for e in devices} != set(_device_ids(design))
            or any(e.manual for e in document.elements.values())
            or any(r.manual or r.label_manual for r in document.routes.values())):
        return False
    frames = {e.id: e for e in document.elements.values() if e.kind == "location"}
    owners = {e.location_id for e in devices}
    live_routes = {edge.id for edge in design.connections}
    invalid = bool(set(document.routes) - live_routes or set(frames) - owners)
    frame_list = list(frames.values())
    invalid = invalid or any(_overlap(a, b) for index, a in enumerate(frame_list)
                             for b in frame_list[index + 1:])
    for device in devices:
        expected = _normal_location(_location(design, device.ref))
        without_floor = re.sub(r"\b\d+(?:ST|ND|RD|TH)\s+FLOOR\b", "", expected)
        without_floor = re.sub(r"\s+", " ", without_floor).strip()
        owner = frames.get(device.location_id)
        if owner is None or _normal_location(owner.ref) not in {expected, without_floor}:
            invalid = True
        elif not (owner.x <= device.x and owner.y <= device.y
                  and device.x + device.width <= owner.x + owner.width
                  and device.y + device.height <= owner.y + owner.height):
            invalid = True
    if not invalid:
        return False
    replacement = layout_riser(design, title_source=document)
    for field in fields(RiserDocument):
        setattr(document, field.name, getattr(replacement, field.name))
    return True


def sync_location_ownership(design, document: RiserDocument) -> None:
    """Migrate and reconcile membership by domain location, never by containment.

    Existing coordinates and cable routes survive an assignment. A sole-owner
    frame can be renamed in place; joining an existing room changes ownership
    and lets validation highlight any placement that still needs adjustment.
    """
    if document.layout_version >= 2:
        sync_cluster_ownership(design, document)
        return
    frames = {e.id: e for e in document.elements.values() if e.kind == "location"}
    devices = [e for e in document.elements.values() if e.kind == "device" and not e.stale]
    desired = {e.id: _normal_location(_location(design, e.ref)) for e in devices}
    for device in devices:
        expected = desired[device.id]
        without_floor = re.sub(r"\b\d+(?:ST|ND|RD|TH)\s+FLOOR\b", "", expected)
        without_floor = re.sub(r"\s+", " ", without_floor).strip()
        owner = frames.get(device.location_id)
        matches = [f for f in frames.values()
                   if _normal_location(f.ref) in {expected, without_floor}]
        if owner in matches:
            continue
        if matches:
            device.location_id = sorted(matches, key=lambda f: (f.ref != expected, f.id))[0].id
            continue
        siblings = [e for e in devices if e.location_id == device.location_id] if owner else []
        if owner and all(desired[e.id] == expected for e in siblings):
            owner.ref = expected
            continue
        key = f"location:{expected}"
        suffix = 2
        while key in document.elements:
            key = f"location:{expected}:{suffix}"
            suffix += 1
        frame = RiserElement(key, "location", expected, device.x - 24, device.y - 50,
                             max(device.width + 48, len(expected) * 8 + 24), device.height + 68)
        document.elements[key] = frames[key] = frame
        document.z_order.insert(0, key)
        device.location_id = key

    # Generated frames are containers, not independent markup. Once their
    # last member leaves, retaining them creates ghost boxes on every reopen.
    occupied = {e.location_id for e in document.elements.values() if e.kind == "device"}
    unused = {key for key, frame in frames.items() if key not in occupied and not frame.manual}
    for key in unused:
        document.elements.pop(key, None)
    document.z_order = [key for key in document.z_order if key not in unused]


def sync_cluster_ownership(design, document):
    from project_locations import sync_project_locations
    from riser_cluster_layout import cluster_heading
    sync_project_locations(design)
    frames = {e.physical_location_id: e for e in document.elements.values()
              if e.kind == 'location' and e.physical_location_id}
    devices = [e for e in document.elements.values() if e.kind == 'device' and not e.stale]
    for device in devices:
        identity = design.device_location_ids.get(device.ref)
        if not identity:
            continue
        frame = frames.get(identity)
        if frame is None:
            key = 'location:' + identity
            suffix = 2
            while key in document.elements:
                key = f'location:{identity}:{suffix}'
                suffix += 1
            frame = RiserElement(key, 'location', '', device.x - 24, device.y - 90,
                                 max(360, device.width + 48), device.height + 114,
                                 physical_location_id=identity)
            frames[identity] = document.elements[key] = frame
            document.z_order.insert(0, key)
        device.location_id = frame.id
    occupied = {e.location_id for e in document.elements.values() if e.kind == 'device'}
    for frame in list(document.elements.values()):
        if frame.kind != 'location':
            continue
        if frame.id not in occupied and not frame.manual:
            document.elements.pop(frame.id)
            document.z_order = [key for key in document.z_order if key != frame.id]
            continue
        record = design.equipment_locations.get(frame.physical_location_id)
        if record:
            frame.ref = record.full_label or 'LOCATION UNCONFIRMED'
            frame.heading_lines = cluster_heading(record, [e.ref for e in devices if e.location_id == frame.id])
            runs = location_text_runs(frame, small=True)
            frame.heading_height = max((run.y - frame.y + run.size * .3 for run in runs), default=30) + 12


def sync_riser_document(design, document: RiserDocument) -> None:
    # Older generated layouts spread active outputs first and placed unused
    # ones in arbitrary gaps. Repair their order before attaching cables to
    # named ports; preserve already ordered custom spacing and device geometry.
    panel = document.elements.get('device:MSP')
    if panel is not None and panel.port_x:
        positions = [panel.port_x.get(name, x) for name, x in MSP_OUTPUT_X.items()]
        if not all(a < b for a, b in zip(positions, positions[1:])):
            panel.port_x.update(MSP_OUTPUT_X)
    live = set(_device_ids(design))
    existing = {e.ref for e in document.elements.values() if e.kind == "device"}
    for element in document.elements.values():
        if element.kind == "device":
            element.stale = element.ref not in live
            if element.symbol_style == 'detailed' and not element.stale:
                from riser_symbols import detailed_size
                minimum_width,minimum_height=detailed_size(design,element.ref)
                element.width=max(element.width,minimum_width)
                element.height=max(element.height,minimum_height)
    for device_id in sorted(live - existing):
        if device_id not in document.unplaced:
            document.unplaced.append(device_id)
    document.unplaced = [d for d in document.unplaced if d in live and d not in existing]
    sync_location_ownership(design, document)
    obstacles = [e for e in routing_obstacles(document, design) if not e.stale]
    live_connection_ids = {edge.id for edge in design.connections}
    for route_id in set(document.routes) - live_connection_ids:
        document.routes.pop(route_id)
    for edge in sorted(design.connections, key=_connection_sort_key):
        source = document.elements.get(f"device:{edge.source.device_id}")
        target = document.elements.get(f"device:{edge.target.device_id}")
        if source is None or target is None:
            continue
        start = port_point(source, edge.source.port_id, output=True)
        end = port_point(target, edge.target.port_id, output=False)
        route = document.routes.get(edge.id)
        reserved_segments = reserved_route_segments(
            document,
            exclude_id=edge.id,
            live_ids=live_connection_ids,
        )
        if route is None or len(route.points) < 2:
            document.routes[edge.id] = RiserRoute(
                edge.id,
                route_topology_connection(
                    edge, source, target, obstacles,
                    reserved_segments=reserved_segments,
                ),
            )
            continue
        if route.points[0] == start and route.points[-1] == end:
            continue
        if not route.manual:
            route.points = route_topology_connection(
                edge, source, target, obstacles,
                reserved_segments=reserved_segments,
            )
            continue
        if route.points[0] != start:
            route.points = reattach_source_route(edge, source, route.points, obstacles)
        if route.points[-1] != end:
            route.points = reattach_route_endpoint(
                route.points, end, at_start=False)


def _overlap(a: RiserElement, b: RiserElement) -> bool:
    return (a.x < b.x + b.width and a.x + a.width > b.x and
            a.y < b.y + b.height and a.y + a.height > b.y)


def _boxes_overlap(first, second) -> bool:
    return (first[0] < second[2] and first[2] > second[0] and
            first[1] < second[3] and first[3] > second[1])


def _route_label_box(edge, route):
    if route.label_hidden:
        return None
    point = route_label_point(route.points)
    if point is None:
        return None
    x = point[0] + route.label_offset[0]
    y = point[1] + route.label_offset[1]
    width = max(42.0, fitz.get_text_length(edge.label, fontname='hebo', fontsize=15.3) + 10)
    return (x - width / 2, y - 17, x + width / 2, y + 5)


def _location_heading_box(location: RiserElement):
    if location.heading_lines:
        return (location.x + 8, location.y + 4,
                location.x + location.width - 8, location.y + location.heading_height)
    return (
        location.x + 8,
        location.y + 4,
        min(location.x + location.width - 8,
            location.x + 12 + max(36.0, len(location.ref) * 8.0)),
        location.y + 30,
    )


def route_label_point(points):
    segments = list(_segments(points))
    if not segments:
        return None
    horizontals = [(abs(b[0] - a[0]), a, b) for a, b in segments
                   if a[1] == b[1]]
    _length, first, second = max(
        horizontals or [(0.0, *segments[0])], key=lambda item: item[0])
    return ((first[0] + second[0]) / 2,
            (first[1] + second[1]) / 2 - 9)


def _place_route_labels(design, document: RiserDocument) -> None:
    """Move generated cable labels into nearby clear drafting space."""
    device_boxes = [
        (element.x - 5, element.y - 5,
         element.x + element.width + 5, element.y + element.height + 5)
        for element in document.elements.values() if element.kind == "device"
    ]
    protected_boxes = device_boxes + [
        _location_heading_box(element)
        for element in document.elements.values() if element.kind == "location" and document.show_location_frames
    ]
    from riser_symbols import caption_boxes
    protected_boxes += [box for e in document.elements.values() if e.kind == 'device'
                        for box in caption_boxes(design,e)]
    protected_boxes += [
        (min(a[0], b[0]) - 3, min(a[1], b[1]) - 3,
         max(a[0], b[0]) + 3, max(a[1], b[1]) + 3)
        for route in document.routes.values() for a, b in _route_segments(route.points)
    ]
    used_boxes = []
    offsets = [(0.0, 0.0), (-72.0, 0.0), (72.0, 0.0)]
    offsets.extend(
        (dx, dy)
        for dy in (-28.0, 28.0, -56.0, 56.0, -84.0, 84.0, -112.0, 112.0)
        for dx in (0.0, -72.0, 72.0, -144.0, 144.0, -216.0, 216.0)
    )
    for edge in sorted(design.connections, key=_connection_sort_key):
        route = document.routes.get(edge.id)
        if route is None:
            continue
        if route.label_hidden:
            continue
        if route.label_manual:
            box = _route_label_box(edge, route)
            if box is not None:
                used_boxes.append(box)
            continue
        chosen = None
        for offset in offsets:
            route.label_offset = offset
            box = _route_label_box(edge, route)
            if box is None:
                continue
            inside = (box[0] >= PAGE_MARGIN / 2 and box[1] >= PAGE_MARGIN / 2 and
                      box[2] <= title_bounds(document)[0] - PAGE_MARGIN / 2 and
                      box[3] <= document.page_height - PAGE_MARGIN / 2)
            if (inside and not any(_boxes_overlap(box, item) for item in protected_boxes)
                    and not any(_boxes_overlap(box, item) for item in used_boxes)):
                chosen = box
                break
        if chosen is None:
            route.label_offset = (0.0, 0.0)
        else:
            used_boxes.append(chosen)


def _graph_cycle(design) -> bool:
    adjacency: dict[str, list[str]] = {}
    for edge in design.connections:
        adjacency.setdefault(edge.source.device_id, []).append(edge.target.device_id)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in adjacency.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in list(adjacency))


def validate_riser(design, document: RiserDocument) -> list[RiserIssue]:
    issues: list[RiserIssue] = []
    drawing_right = title_bounds(document)[0]
    if _graph_cycle(design):
        issues.append(RiserIssue("topology.cycle", "Topology contains a cycle"))
    incoming: dict[tuple[str, str], int] = {}
    outgoing: dict[tuple[str, str], int] = {}
    for edge in design.connections:
        incoming[(edge.target.device_id, edge.target.port_id)] = incoming.get(
            (edge.target.device_id, edge.target.port_id), 0) + 1
        outgoing[(edge.source.device_id, edge.source.port_id)] = outgoing.get(
            (edge.source.device_id, edge.source.port_id), 0) + 1
        if not edge.cable_type.strip() or edge.quantity < 1 or edge.status not in {"new", "existing"}:
            issues.append(RiserIssue("cable.metadata", "Cable metadata is incomplete", edge.id))
        try:
            validate_connection_endpoints(design, edge.source, edge.target)
        except TopologyError as exc:
            issues.append(RiserIssue("topology.incompatible", str(exc), edge.id))
    if any(count > 1 for count in incoming.values()) or any(count > 1 for count in outgoing.values()):
        issues.append(RiserIssue("topology.occupied", "A topology port has multiple connections"))
    incoming_devices = {device_id for device_id, _port in incoming}
    for device_id in _device_ids(design):
        if device_id != "MSP" and device_id not in incoming_devices:
            issues.append(RiserIssue(
                "topology.orphan", f"{device_id} is not connected to the topology",
                f"device:{device_id}"))
    connection_ids = {edge.id for edge in design.connections}
    for route_id in document.routes:
        if route_id not in connection_ids:
            issues.append(RiserIssue(
                "scene.stale_route", "Drawing contains a route for a removed cable", route_id))
    for edge in design.connections:
        if edge.id not in document.routes:
            issues.append(RiserIssue(
                "scene.missing_route", "Connected cable has no drawing route", edge.id))
    if document.unplaced:
        issues.append(RiserIssue("scene.unplaced", "Drawing has unplaced devices", document.unplaced[0]))
    for element in document.elements.values():
        if element.stale:
            issues.append(RiserIssue("scene.stale", "Drawing references removed hardware", element.ref))
        if element.kind == 'location' and not document.show_location_frames:
            continue
        if (element.x < 0 or element.y < 0 or
                element.x + element.width > drawing_right or
                element.y + element.height > document.page_height):
            issues.append(RiserIssue("scene.off_page", "Drawing object is outside the printable area", element.id))
    devices = [e for e in document.elements.values() if e.kind == "device"]
    locations = [e for e in document.elements.values() if e.kind == "location"]
    visible_locations = locations if document.show_location_frames else []
    owners = {e.location_id for e in devices}
    for index, location in enumerate(locations):
        if document.layout_version < 2:
            identities = {design.device_location_ids.get(e.ref) for e in devices
                          if e.location_id == location.id}
            identities.discard(None)
            if len(identities) > 1:
                issues.append(RiserIssue('location.review',
                    'Legacy box combines different location records; Preview layout to review', location.id))
        if location.id not in owners:
            issues.append(RiserIssue(
                "scene.unused_location", "Location box has no devices; Re-layout All can remove it",
                location.id))
        for other in (locations[index + 1:] if document.show_location_frames else []):
            if _overlap(location, other):
                issues.append(RiserIssue(
                    "scene.location_overlap", "Location boxes overlap", location.id))
    for device in devices:
        expected = _normal_location(_location(design, device.ref))
        center = (device.x + device.width / 2, device.y + device.height / 2)
        containers = [frame for frame in locations
                      if frame.id == device.location_id
                      and frame.x <= center[0] <= frame.x + frame.width
                      and frame.y <= center[1] <= frame.y + frame.height]

        def equivalent(frame):
            if document.layout_version >= 2:
                return frame.physical_location_id == design.device_location_ids.get(device.ref)
            actual = _normal_location(frame.ref)
            without_floor = re.sub(
                r"\b\d+(?:ST|ND|RD|TH)\s+FLOOR\b", "", expected)
            without_floor = re.sub(r"\s+", " ", without_floor).strip()
            return actual in {expected, without_floor}

        if document.layout_version >= 3:
            containers = [frame for frame in locations if frame.id == device.location_id]
        if not any(equivalent(frame) for frame in containers):
            issues.append(RiserIssue(
                "scene.location_mismatch",
                f"{device.ref} is not placed in its current location: {expected}",
                device.id))
    for index, first in enumerate(devices):
        for second in devices[index + 1:]:
            if _overlap(first, second):
                issues.append(RiserIssue("scene.overlap", "Device symbols overlap", first.id))
    if document.layout_version >= 2:
        from location_model import is_unresolved
        for location in locations:
            record = design.equipment_locations.get(location.physical_location_id)
            if record is None or is_unresolved(record.full_label):
                issues.append(RiserIssue('location.unconfirmed',
                                        'Confirm the physical location of this equipment', location.id))
            if not document.show_location_frames:
                continue
            heading = _location_heading_box(location)
            for device in devices:
                box = (device.x, device.y, device.x + device.width, device.y + device.height)
                if _boxes_overlap(heading, box):
                    issues.append(RiserIssue('scene.heading_overlap',
                                            'Location heading overlaps equipment', location.id))
            if any(run.size * 11 / 24 < 7 for run in location_text_runs(location, small=True)):
                issues.append(RiserIssue('print.legibility',
                                        'Location heading is below the 11x17 minimum', location.id))
    edges = {edge.id: edge for edge in design.connections}
    label_boxes = []
    location_heading_boxes = [
        (location.ref, _location_heading_box(location)) for location in visible_locations
    ]
    for route_id, route in document.routes.items():
        if any(x < 0 or y < 0 or x > drawing_right
               or y > document.page_height for x, y in route.points):
            issues.append(RiserIssue(
                "scene.off_page", "Cable route is outside the printable drawing area",
                route_id))
        if any(first[0] != second[0] and first[1] != second[1]
               for first, second in _segments(route.points)):
            issues.append(RiserIssue(
                "scene.nonorthogonal_route", "Cable route contains a diagonal segment",
                route_id))
        edge = edges.get(route_id)
        if edge is None:
            continue
        label_box = _route_label_box(edge, route)
        if label_box:
            if (label_box[0] < PAGE_MARGIN / 2 or label_box[1] < PAGE_MARGIN / 2
                    or label_box[2] > drawing_right - PAGE_MARGIN / 2
                    or label_box[3] > document.page_height - PAGE_MARGIN / 2):
                issues.append(RiserIssue('scene.off_page',
                                        'Cable callout is outside the printable drawing area', route_id))
            label_boxes.append((route_id, label_box))
            for device in devices:
                device_box = (device.x, device.y,
                              device.x + device.width, device.y + device.height)
                if _boxes_overlap(label_box, device_box):
                    issues.append(RiserIssue(
                        "scene.label_overlap",
                        f"Cable label overlaps {device.ref}", route_id))
                    break
            for location_ref, heading_box in location_heading_boxes:
                if _boxes_overlap(label_box, heading_box):
                    issues.append(RiserIssue(
                        "scene.label_overlap",
                        f"Cable label overlaps the {location_ref} location heading",
                        route_id))
                    break
        for obstacle in devices:
            if obstacle.ref in {edge.source.device_id, edge.target.device_id}:
                continue
            if any(_segment_hits_rect(a, b, obstacle, clearance=0.0)
                   for a, b in _segments(route.points)):
                issues.append(RiserIssue(
                    "scene.cable_through_device",
                    f"Cable crosses the {obstacle.ref} footprint", route_id))
                break
    for index, (first_id, first_box) in enumerate(label_boxes):
        for _second_id, second_box in label_boxes[index + 1:]:
            if _boxes_overlap(first_box, second_box):
                issues.append(RiserIssue(
                    "scene.label_overlap", "Cable labels overlap", first_id))
                break
    route_points = {route_id: route.points
                    for route_id, route in document.routes.items()}
    for contact in _route_contacts(route_points):
        if contact.bridge is None:
            issues.append(RiserIssue(
                "scene.uncovered_intersection",
                "Independent cables meet without room for a bridge hop",
                contact.first_connection_id,
            ))
    for name in ("school_name", "local_code", "address", "project_title",
                 "drawing_title", "system", "sheet_number", "drawn_by",
                 "checked_by", "issue_date"):
        if not getattr(document.title_block, name).strip():
            issues.append(RiserIssue("title.required", f"Title block field is required: {name}", name))
    for annotation in document.annotations:
        if any(x < 0 or y < 0 or x > drawing_right
               or y > document.page_height for x, y in annotation.points):
            issues.append(RiserIssue(
                "scene.off_page", "Markup is outside the printable drawing area",
                annotation.id))
        if annotation.kind == "text" and annotation.font_size * 11 / 24 < 6:
            issues.append(RiserIssue("print.legibility", "Annotation is below the 11x17 minimum text size", annotation.id))
    if document.layout_version >= 3:
        from riser_symbols import text_bounds, caption_boxes
        for element in devices:
            runs=device_text(design,element,small=True)
            for i,run in enumerate(runs):
                box=text_bounds(run)
                if run.size*11/24 < 7:
                    issues.append(RiserIssue('print.legibility','Equipment text is below the 11x17 minimum',element.id))
                if box[0]<36 or box[2]>drawing_right or box[1]<36 or box[3]>document.page_height-36:
                    issues.append(RiserIssue('scene.off_page','Equipment text is outside the printable area',element.id))
                if any(_boxes_overlap(box,text_bounds(other)) for other in runs[i+1:]):
                    issues.append(RiserIssue('scene.label_overlap','Equipment text overlaps; resize or edit its location',element.id))
            for box in caption_boxes(design,element):
                for route_id,route in document.routes.items():
                    probe=RiserElement('caption','caption','caption',box[0],box[1],box[2]-box[0],box[3]-box[1])
                    if any(_segment_hits_rect(a,b,probe,clearance=0) for a,b in _segments(route.points)):
                        issues.append(RiserIssue('scene.caption_overlap','Cable crosses a location caption',route_id))
                if any(_boxes_overlap(box,(e.x,e.y,e.x+e.width,e.y+e.height)) for e in devices if e.id!=element.id):
                    issues.append(RiserIssue('scene.caption_overlap','Location caption overlaps equipment',element.id))
        left,top,right,bottom=title_bounds(document)
        title_runs=title_text(document,small=True)
        if any((lambda b:b[0]<left or b[2]>right or b[1]<top or b[3]>bottom)(text_bounds(r)) for r in title_runs):
            issues.append(RiserIssue('title.overflow','Title-block text does not fit the sheet; shorten the metadata','titleblock'))
    return issues
