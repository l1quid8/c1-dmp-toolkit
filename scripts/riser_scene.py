"""Pure scene construction, routing, bridge detection, and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from riser_model import (
    RiserDocument,
    RiserElement,
    RiserRoute,
    default_riser_document,
)


TITLE_BLOCK_WIDTH = 270.0
PAGE_MARGIN = 54.0
GRID = 18.0


@dataclass(frozen=True)
class Bridge:
    connection_id: str
    x: float
    y: float
    orientation: str


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
    return re.sub(r"\s+", " ", (value or "UNSPECIFIED").strip()).upper()


def _kind(device_id: str) -> str:
    if device_id == "MSP":
        return "msp"
    if device_id.startswith("710-"):
        return "splitter"
    if device_id.startswith("RSP-"):
        return "rsp"
    return "keypad"


def _size(device_id: str) -> tuple[float, float]:
    return {
        "msp": (220.0, 104.0),
        "splitter": (184.0, 80.0),
        "rsp": (220.0, 110.0),
        "keypad": (148.0, 72.0),
    }[_kind(device_id)]


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


def _snap(value: float) -> float:
    return round(value / GRID) * GRID


def layout_riser(design, *, title_source: RiserDocument | None = None) -> RiserDocument:
    """Create a deterministic balanced 24x36 scene from the electrical graph."""
    document = default_riser_document(design)
    if title_source is not None:
        document.title_block = title_source.title_block
        document.annotations = list(title_source.annotations)
    usable_right = document.page_width - TITLE_BLOCK_WIDTH - PAGE_MARGIN
    center = usable_right / 2
    branch, depth = _branch_and_depth(design)

    positions: dict[str, tuple[float, float]] = {"MSP": (center - 110.0, 90.0)}
    for side in ("kp", "lx"):
        devices = [d for d in _device_ids(design) if d != "MSP" and branch[d] == side]
        by_depth: dict[int, list[str]] = {}
        for device_id in devices:
            by_depth.setdefault(depth[device_id], []).append(device_id)
        left, right = ((PAGE_MARGIN + 90, center - 170) if side == "kp"
                       else (center + 170, usable_right - 90))
        for level, level_devices in sorted(by_depth.items()):
            ordered = sorted(level_devices, key=lambda d: (_normal_location(_location(design, d)), d))
            y = 270.0 + min(level, 6) * 190.0
            span = max(0.0, right - left)
            for index, device_id in enumerate(ordered):
                width, _height = _size(device_id)
                anchor = left + span * (index + 1) / (len(ordered) + 1)
                positions[device_id] = (_snap(anchor - width / 2), _snap(y))

    for device_id in _device_ids(design):
        x, y = positions[device_id]
        width, height = _size(device_id)
        element = RiserElement(
            id=f"device:{device_id}", kind="device", ref=device_id,
            x=x, y=y, width=width, height=height,
        )
        document.elements[element.id] = element

    # Location frames are a presentation grouping only. Their bounds follow
    # the device geometry and never affect electrical connectivity.
    location_members: dict[str, list[RiserElement]] = {}
    for device_id in _device_ids(design):
        location_members.setdefault(_normal_location(_location(design, device_id)), []).append(
            document.elements[f"device:{device_id}"])
    for location, members in sorted(location_members.items()):
        pad_x, pad_top, pad_bottom = 24.0, 34.0, 22.0
        x1 = min(m.x for m in members) - pad_x
        y1 = min(m.y for m in members) - pad_top
        x2 = max(m.x + m.width for m in members) + pad_x
        y2 = max(m.y + m.height for m in members) + pad_bottom
        key = f"location:{location}"
        document.elements[key] = RiserElement(
            id=key, kind="location", ref=location,
            x=x1, y=y1, width=x2 - x1, height=y2 - y1,
        )

    location_ids = sorted(k for k, e in document.elements.items() if e.kind == "location")
    device_ids = [f"device:{d}" for d in _device_ids(design)]
    document.z_order = location_ids + device_ids + [a.id for a in document.annotations]
    obstacles = [e for e in document.elements.values() if e.kind == "device"]
    for edge in design.connections:
        source = document.elements.get(f"device:{edge.source.device_id}")
        target = document.elements.get(f"device:{edge.target.device_id}")
        if not source or not target:
            continue
        start = port_point(source, edge.source.port_id, output=True)
        end = port_point(target, edge.target.port_id, output=False)
        points = route_connection(start, end, obstacles, source_ref=source.ref, target_ref=target.ref)
        document.routes[edge.id] = RiserRoute(edge.id, points)
    return document


def port_point(element: RiserElement, port_id: str, *, output: bool) -> tuple[float, float]:
    if element.ref == "MSP":
        order = {"KP BUS": 0.18, "PROG": 0.34, "LX500": 0.55,
                 "LX600": 0.68, "LX700": 0.81, "LX800": 0.88, "LX900": 0.94}
        return (_snap(element.x + element.width * order.get(port_id, 0.5)),
                _snap(element.y + element.height))
    match = re.fullmatch(r"OUT([123])", port_id)
    if output and match:
        return (_snap(element.x + element.width * int(match.group(1)) / 4),
                _snap(element.y + element.height))
    return (_snap(element.x + element.width / 2), _snap(element.y))


def _segment_hits_rect(a, b, rect: RiserElement, clearance: float = 12.0) -> bool:
    left, top = rect.x - clearance, rect.y - clearance
    right, bottom = rect.x + rect.width + clearance, rect.y + rect.height + clearance
    if a[0] == b[0]:
        return left < a[0] < right and max(min(a[1], b[1]), top) < min(max(a[1], b[1]), bottom)
    if a[1] == b[1]:
        return top < a[1] < bottom and max(min(a[0], b[0]), left) < min(max(a[0], b[0]), right)
    return False


def route_connection(start: tuple[float, float], end: tuple[float, float],
                     obstacles: list[RiserElement], *, source_ref: str = "",
                     target_ref: str = "") -> list[tuple[float, float]]:
    """Route an orthogonal connection, moving its trunk below blockers."""
    sx, sy = start
    tx, ty = end
    mid_y = _snap(sy + max(54.0, (ty - sy) / 2))
    blockers = [o for o in obstacles if o.ref not in {source_ref, target_ref}]
    for obstacle in blockers:
        if _segment_hits_rect((sx, mid_y), (tx, mid_y), obstacle):
            mid_y = _snap(max(mid_y, obstacle.y + obstacle.height + 30.0))
    points = [(sx, sy), (sx, mid_y), (tx, mid_y), (tx, ty)]
    compact: list[tuple[float, float]] = []
    for point in points:
        if not compact or point != compact[-1]:
            compact.append(point)
    return compact


def _segments(points):
    return list(zip(points, points[1:]))


def find_bridges(routes: dict[str, list[tuple[float, float]]]) -> list[Bridge]:
    bridges: list[Bridge] = []
    ids = sorted(routes)
    for i, first_id in enumerate(ids):
        for second_id in ids[i + 1:]:
            for a1, a2 in _segments(routes[first_id]):
                for b1, b2 in _segments(routes[second_id]):
                    a_h = a1[1] == a2[1] and a1[0] != a2[0]
                    b_h = b1[1] == b2[1] and b1[0] != b2[0]
                    if a_h == b_h:
                        continue
                    h_id, h1, h2, v1, v2 = ((first_id, a1, a2, b1, b2)
                                             if a_h else (second_id, b1, b2, a1, a2))
                    x, y = v1[0], h1[1]
                    if (min(h1[0], h2[0]) < x < max(h1[0], h2[0]) and
                            min(v1[1], v2[1]) < y < max(v1[1], v2[1])):
                        bridge = Bridge(h_id, x, y, "horizontal")
                        if bridge not in bridges:
                            bridges.append(bridge)
    return sorted(bridges, key=lambda b: (b.connection_id, b.y, b.x))


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


def _overlap(a: RiserElement, b: RiserElement) -> bool:
    return (a.x < b.x + b.width and a.x + a.width > b.x and
            a.y < b.y + b.height and a.y + a.height > b.y)


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
    if any(count > 1 for count in incoming.values()) or any(count > 1 for count in outgoing.values()):
        issues.append(RiserIssue("topology.occupied", "A topology port has multiple connections"))
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
    for index, first in enumerate(devices):
        for second in devices[index + 1:]:
            if _overlap(first, second):
                issues.append(RiserIssue("scene.overlap", "Device symbols overlap", first.id))
    for name in ("school_name", "drawing_title", "system", "sheet_number"):
        if not getattr(document.title_block, name).strip():
            issues.append(RiserIssue("title.required", f"Title block field is required: {name}", name))
    for annotation in document.annotations:
        if annotation.kind == "text" and annotation.font_size * 17 / 36 < 6:
            issues.append(RiserIssue("print.legibility", "Annotation is below the 11x17 minimum text size", annotation.id))
    return issues

