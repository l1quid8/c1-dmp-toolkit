"""Pure scene construction, routing, bridge detection, and validation."""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass

from riser_model import (
    RiserDocument,
    RiserElement,
    RiserRoute,
    default_riser_document,
)
from topology_service import TopologyError, validate_connection_endpoints


TITLE_BLOCK_WIDTH = 270.0
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
        return (rsp.location if rsp else None) or "UNSPECIFIED"
    if device_id.startswith("KEYPAD-"):
        number = int(device_id.split("-", 1)[1])
        keypad = next((k for k in design.keypads if k.number == number), None)
        return (keypad.location if keypad else None) or "UNSPECIFIED"
    return "UNSPECIFIED"


def _normal_location(value: str) -> str:
    text = (value or "UNSPECIFIED").upper()
    text = re.sub(r"\(\s*SERVICE\s+KEYPAD\s*\)", "", text)
    text = re.sub(r"\bBLDG\b", "BUILDING", text)
    text = re.sub(r"\bFLR\b", "FLOOR", text)
    text = re.sub(r"[()_,./-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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


def layout_riser(design, *, title_source: RiserDocument | None = None) -> RiserDocument:
    """Create a deterministic location-first 24x36 electrical scene."""
    document = default_riser_document(design)
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
    location_devices: dict[str, list[str]] = {}
    for device_id in device_ids:
        location_devices.setdefault(locations[device_id], []).append(device_id)

    H_GAP = 36.0
    # Three independent splitter outputs need distinct orthogonal lanes
    # between vertically stacked devices. A 36-point gap left only four
    # points after symbol clearances and forced cables through the next 710.
    V_GAP = 54.0
    FRAME_X = 24.0
    FRAME_TOP = 50.0
    FRAME_BOTTOM = 18.0

    def module_geometry(location: str, members: list[str]):
        rows: dict[int, list[str]] = {}
        for device_id in members:
            rows.setdefault(depth[device_id], []).append(device_id)
        ordered_rows: list[list[str]] = []
        maximum_inner_width = drawing_width - FRAME_X * 2
        for level in sorted(rows):
            ordered = sorted(rows[level], key=lambda item: (
                {"kp": 0, "root": 1, "lx": 2}.get(branch[item], 1), item))
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
        height = FRAME_TOP + sum(row_heights) + V_GAP * max(0, len(ordered_rows) - 1) + FRAME_BOTTOM
        local_positions = {}
        y = FRAME_TOP
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
    for edge in design.connections:
        source_location = locations.get(edge.source.device_id)
        target_location = locations.get(edge.target.device_id)
        if (source_location == target_location or target_location not in remaining
                or source_location == root_location or source_location not in remaining):
            continue
        if target_location not in adjacency[source_location]:
            adjacency[source_location].add(target_location)
            indegree[target_location] += 1

    def location_sort_key(location: str):
        return (min(depth[item] for item in location_devices[location]),
                lane(location), location)

    ready = sorted((location for location in remaining if indegree[location] == 0),
                   key=location_sort_key)
    ordered_locations: list[str] = []
    while ready:
        location = ready.pop(0)
        ordered_locations.append(location)
        for target_location in sorted(adjacency[location], key=location_sort_key):
            indegree[target_location] -= 1
            if indegree[target_location] == 0:
                ready.append(target_location)
                ready.sort(key=location_sort_key)
    ordered_locations.extend(sorted(remaining - set(ordered_locations),
                                    key=location_sort_key))

    MODULE_GAP = 36.0
    ROW_GAP = 54.0
    packed_rows: list[list[str]] = []
    current_row: list[str] = []
    current_width = 0.0
    for location in ordered_locations:
        width = modules[location][0]
        added = width if not current_row else MODULE_GAP + width
        if current_row and current_width + added > drawing_width:
            packed_rows.append(current_row)
            current_row, current_width = [], 0.0
            added = width
        current_row.append(location)
        current_width += added
    if current_row:
        packed_rows.append(current_row)

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
    if content_height < printable_height:
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
    obstacles = [e for e in document.elements.values() if e.kind == "device"]
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
        routed_connections[edge.id] = RiserRoute(edge.id, points)
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


def port_point(element: RiserElement, port_id: str, *, output: bool) -> tuple[float, float]:
    if element.ref == "MSP":
        # These seven targets must remain distinct at editor fit zoom. Do not
        # grid-snap them: snapping previously collapsed LX800 and LX900 onto
        # the same coordinate and made reconnecting to the intended bus
        # impossible.
        order = {"KP BUS": 0.06, "PROG": 0.207, "LX500": 0.353,
                 "LX600": 0.5, "LX700": 0.647, "LX800": 0.793,
                 "LX900": 0.94}
        return (element.x + element.width * order.get(port_id, 0.5),
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
    face down and input symbols face up, so reserve a short vertical lead at
    both ends before asking the obstacle router to connect them.
    """
    reserved_segments = tuple(reserved_segments)
    start = port_point(source, edge.source.port_id, output=True)
    end = port_point(target, edge.target.port_id, output=False)

    output_match = re.fullmatch(r"OUT([123])", edge.source.port_id)
    if output_match:
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

    target_lead = (end[0], end[1] - GRID)

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

    prefixes = [([start, start_lead], start_lead), *side_leads(
        source, start, downward=True)]
    suffixes = [([target_lead, end], target_lead), *side_leads(
        target, end, downward=False)]
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


def sync_riser_document(design, document: RiserDocument) -> None:
    live = set(_device_ids(design))
    existing = {e.ref for e in document.elements.values() if e.kind == "device"}
    for element in document.elements.values():
        if element.kind == "device":
            element.stale = element.ref not in live
    for device_id in sorted(live - existing):
        if device_id not in document.unplaced:
            document.unplaced.append(device_id)
    document.unplaced = [d for d in document.unplaced if d in live and d not in existing]
    obstacles = [e for e in document.elements.values()
                 if e.kind == "device" and not e.stale]
    live_connection_ids = {edge.id for edge in design.connections}
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
            route.points = reattach_route_endpoint(
                route.points, start, at_start=True)
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
    point = route_label_point(route.points)
    if point is None:
        return None
    x = point[0] + route.label_offset[0]
    y = point[1] + route.label_offset[1]
    width = max(42.0, len(edge.label) * 7.5)
    return (x - width / 2, y - 14, x + width / 2, y + 3)


def _location_heading_box(location: RiserElement):
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
        for element in document.elements.values() if element.kind == "location"
    ]
    used_boxes = []
    edges = {edge.id: edge for edge in design.connections}
    offsets = [(0.0, 0.0)]
    offsets.extend(
        (dx, dy)
        for dy in (-28.0, 28.0, -56.0, 56.0, -84.0, 84.0, -112.0, 112.0)
        for dx in (0.0, -72.0, 72.0, -144.0, 144.0, -216.0, 216.0)
    )
    for route_id, route in document.routes.items():
        edge = edges.get(route_id)
        if edge is None:
            continue
        chosen = None
        for offset in offsets:
            route.label_offset = offset
            box = _route_label_box(edge, route)
            if box is None:
                continue
            inside = (box[0] >= PAGE_MARGIN / 2 and box[1] >= PAGE_MARGIN / 2 and
                      box[2] <= document.page_width - TITLE_BLOCK_WIDTH - PAGE_MARGIN / 2 and
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
        if (element.x < 0 or element.y < 0 or
                element.x + element.width > document.page_width - TITLE_BLOCK_WIDTH or
                element.y + element.height > document.page_height):
            issues.append(RiserIssue("scene.off_page", "Drawing object is outside the printable area", element.id))
    devices = [e for e in document.elements.values() if e.kind == "device"]
    locations = [e for e in document.elements.values() if e.kind == "location"]
    for device in devices:
        expected = _normal_location(_location(design, device.ref))
        center = (device.x + device.width / 2, device.y + device.height / 2)
        containers = [frame for frame in locations
                      if frame.x <= center[0] <= frame.x + frame.width
                      and frame.y <= center[1] <= frame.y + frame.height]

        def equivalent(frame):
            actual = _normal_location(frame.ref)
            without_floor = re.sub(
                r"\b\d+(?:ST|ND|RD|TH)\s+FLOOR\b", "", expected)
            without_floor = re.sub(r"\s+", " ", without_floor).strip()
            return actual in {expected, without_floor}

        if not any(equivalent(frame) for frame in containers):
            issues.append(RiserIssue(
                "scene.location_mismatch",
                f"{device.ref} is not placed in its current location: {expected}",
                device.id))
    for index, first in enumerate(devices):
        for second in devices[index + 1:]:
            if _overlap(first, second):
                issues.append(RiserIssue("scene.overlap", "Device symbols overlap", first.id))
    edges = {edge.id: edge for edge in design.connections}
    label_boxes = []
    location_heading_boxes = [
        (location.ref, _location_heading_box(location)) for location in locations
    ]
    for route_id, route in document.routes.items():
        if any(x < 0 or y < 0 or x > document.page_width - TITLE_BLOCK_WIDTH
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
        if any(x < 0 or y < 0 or x > document.page_width - TITLE_BLOCK_WIDTH
               or y > document.page_height for x, y in annotation.points):
            issues.append(RiserIssue(
                "scene.off_page", "Markup is outside the printable drawing area",
                annotation.id))
        if annotation.kind == "text" and annotation.font_size * 11 / 24 < 6:
            issues.append(RiserIssue("print.legibility", "Annotation is below the 11x17 minimum text size", annotation.id))
    return issues
