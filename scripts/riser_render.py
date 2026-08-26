"""SVG/PDF renderers and revision-matched riser output bundles."""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import fitz

from paths import resource_path
from riser_scene import TITLE_BLOCK_WIDTH, find_bridges


MASTER_WIDTH = 36 * 72
MASTER_HEIGHT = 24 * 72
SMALL_WIDTH = 17 * 72
SMALL_HEIGHT = 11 * 72


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_") or "UNTITLED"


def _logo_data() -> str:
    path = resource_path("logos/c1_logo.png")
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""


def _connection_map(design):
    return {edge.id: edge for edge in design.connections}


def _route_path(connection_id: str, points, bridges) -> str:
    if not points:
        return ""
    commands = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    route_bridges = [b for b in bridges if b.connection_id == connection_id]
    for start, end in zip(points, points[1:]):
        if start[1] == end[1]:
            forward = end[0] >= start[0]
            crossings = [b for b in route_bridges
                         if min(start[0], end[0]) < b.x < max(start[0], end[0])
                         and abs(b.y - start[1]) < 0.01]
            crossings.sort(key=lambda b: b.x, reverse=not forward)
            for bridge in crossings:
                before = bridge.x - 9 if forward else bridge.x + 9
                after = bridge.x + 9 if forward else bridge.x - 9
                commands.append(f"L {before:.2f} {start[1]:.2f}")
                commands.append(
                    f"Q {bridge.x:.2f} {start[1] - 10:.2f} {after:.2f} {start[1]:.2f}")
            commands.append(f"L {end[0]:.2f} {end[1]:.2f}")
        else:
            commands.append(f"L {end[0]:.2f} {end[1]:.2f}")
    return " ".join(commands)


def _label_point(points) -> tuple[float, float]:
    segments = list(zip(points, points[1:]))
    if not segments:
        return points[0] if points else (0.0, 0.0)
    horizontal = [(abs(b[0] - a[0]), a, b) for a, b in segments if a[1] == b[1]]
    _length, a, b = max(horizontal or [(0.0, *segments[0])], key=lambda item: item[0])
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 9)


def _device_detail(design, ref: str) -> str:
    if ref.startswith("RSP-"):
        number = int(ref.split("-", 1)[1])
        rsp = next((r for r in design.rsps if r.number == number), None)
        if rsp:
            zone = f"Z{min(rsp.zones)}–Z{max(rsp.zones)}" if rsp.zones else ""
            return " · ".join(x for x in (rsp.model, zone, rsp.location or "") if x)
    if ref.startswith("KEYPAD-"):
        number = int(ref.split("-", 1)[1])
        keypad = next((k for k in design.keypads if k.number == number), None)
        return keypad.location or "" if keypad else ""
    splitter = next((s for s in design.splitters if s.id == ref), None)
    return splitter.location or "" if splitter else ""


def _svg_bytes(design, document, *, width, height, physical_width: str,
               physical_height: str) -> bytes:
    bridges = find_bridges({key: route.points for key, route in document.routes.items()})
    connections = _connection_map(design)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (f'<svg xmlns="http://www.w3.org/2000/svg" '
         f'xmlns:xlink="http://www.w3.org/1999/xlink" width="{physical_width}" '
         f'height="{physical_height}" viewBox="0 0 {document.page_width:.0f} {document.page_height:.0f}">'),
        '<rect width="100%" height="100%" fill="white"/>',
        '<g id="drawing" font-family="Arial, Helvetica, sans-serif" fill="#111111">',
    ]

    for element_id in document.z_order:
        element = document.elements.get(element_id)
        if element is None:
            continue
        if element.kind == "location":
            lines.append(
                f'<g id="{_esc(element.id)}" class="location"><rect x="{element.x:.2f}" '
                f'y="{element.y:.2f}" width="{element.width:.2f}" height="{element.height:.2f}" '
                'rx="8" fill="none" stroke="#8b949e" stroke-width="1" '
                'stroke-dasharray="8 5"/><text x="{:.2f}" y="{:.2f}" '
                'font-size="13" font-weight="bold">{}</text></g>'.format(
                    element.x + 12, element.y + 18, _esc(element.ref)))
            continue
        if element.kind != "device":
            continue
        x, y, w, h = element.x, element.y, element.width, element.height
        lines.append(f'<g id="{_esc(element.id)}" class="device {_esc(element.ref)}">')
        if element.ref.startswith("KEYPAD-"):
            lines.append(f'<ellipse cx="{x + w/2:.2f}" cy="{y + h/2:.2f}" rx="{w/2:.2f}" '
                         f'ry="{h/2:.2f}" fill="white" stroke="#111" stroke-width="2"/>')
        else:
            lines.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
                         'rx="4" fill="white" stroke="#111" stroke-width="2"/>')
        lines.append(f'<text x="{x + w/2:.2f}" y="{y + h/2 - 3:.2f}" text-anchor="middle" '
                     f'font-size="18" font-weight="bold">{_esc(element.ref)}</text>')
        detail = _device_detail(design, element.ref)
        if detail:
            lines.append(f'<text x="{x + w/2:.2f}" y="{y + h/2 + 17:.2f}" text-anchor="middle" '
                         f'font-size="13">{_esc(detail)}</text>')
        if element.ref.startswith("710-"):
            lines.append(f'<text x="{x + w/2:.2f}" y="{y + 12:.2f}" text-anchor="middle" font-size="11">IN</text>')
            for index in range(1, 4):
                px = x + w * index / 4
                lines.append(f'<text x="{px:.2f}" y="{y + h - 7:.2f}" text-anchor="middle" '
                             f'font-size="11">OUT {index}</text>')
        lines.append('</g>')

    lines.append('<g id="topology" fill="none" stroke="#111" stroke-width="1.2">')
    for connection_id, route in document.routes.items():
        path_d = _route_path(connection_id, route.points, bridges)
        lines.append(f'<path id="route-{_esc(connection_id)}" d="{path_d}"/>')
    lines.append('</g>')
    lines.append('<g id="cable-labels" font-size="14" font-weight="bold">')
    for connection_id, route in document.routes.items():
        edge = connections.get(connection_id)
        if edge is None or not route.points:
            continue
        x, y = _label_point(route.points)
        x += route.label_offset[0]
        y += route.label_offset[1]
        lines.append(f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="middle">{_esc(edge.label)}</text>')
    lines.append('</g>')

    lines.append('<g id="markup">')
    for annotation in document.annotations:
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in annotation.points)
        common = (f'stroke="{_esc(annotation.stroke)}" stroke-width="{annotation.stroke_width:.2f}" '
                  f'fill="{_esc(annotation.fill or "none")}"')
        if annotation.kind == "text" and annotation.points:
            x, y = annotation.points[0]
            lines.append(f'<text id="{_esc(annotation.id)}" x="{x:.2f}" y="{y:.2f}" '
                         f'font-size="{annotation.font_size:.2f}" font-weight="{_esc(annotation.font_weight)}" '
                         f'fill="{_esc(annotation.stroke)}">{_esc(annotation.text)}</text>')
        elif annotation.kind in {"rectangle", "ellipse"} and len(annotation.points) >= 2:
            (x1, y1), (x2, y2) = annotation.points[:2]
            if annotation.kind == "rectangle":
                lines.append(f'<rect id="{_esc(annotation.id)}" x="{min(x1,x2):.2f}" y="{min(y1,y2):.2f}" '
                             f'width="{abs(x2-x1):.2f}" height="{abs(y2-y1):.2f}" {common}/>')
            else:
                lines.append(f'<ellipse id="{_esc(annotation.id)}" cx="{(x1+x2)/2:.2f}" cy="{(y1+y2)/2:.2f}" '
                             f'rx="{abs(x2-x1)/2:.2f}" ry="{abs(y2-y1)/2:.2f}" {common}/>')
        elif len(annotation.points) >= 2:
            lines.append(f'<polyline id="{_esc(annotation.id)}" points="{points}" {common}/>')
    lines.append('</g>')

    tb = document.title_block
    tx = document.page_width - TITLE_BLOCK_WIDTH
    lines.append('<g id="title-block" stroke="#111" fill="white">')
    lines.append(f'<rect x="{tx:.2f}" y="36" width="{TITLE_BLOCK_WIDTH - 36:.2f}" '
                 f'height="{document.page_height - 72:.2f}" stroke-width="2"/>')
    logo = _logo_data()
    if logo:
        lines.append(f'<image x="{tx + 22:.2f}" y="64" width="{TITLE_BLOCK_WIDTH - 80:.2f}" height="80" '
                     f'preserveAspectRatio="xMidYMid meet" xlink:href="data:image/png;base64,{logo}"/>')
    text_rows = [
        (170, tb.school_name, 18, "bold"),
        (205, tb.address.replace("\n", " · "), 12, "normal"),
        (245, f"LOCAL CODE: {tb.local_code}", 12, "bold"),
        (310, tb.project_title, 14, "bold"),
        (370, tb.drawing_title, 18, "bold"),
        (410, f"SYSTEM: {tb.system}", 12, "bold"),
        (470, f"DRAWN BY: {tb.drawn_by}", 11, "normal"),
        (500, f"CHECKED BY: {tb.checked_by}", 11, "normal"),
        (530, f"DATE: {tb.issue_date}", 11, "normal"),
        (document.page_height - 115, f"SHEET {tb.sheet_number}", 20, "bold"),
    ]
    for y, text, size, weight in text_rows:
        lines.append(f'<text x="{tx + (TITLE_BLOCK_WIDTH - 36)/2:.2f}" y="{y:.2f}" '
                     f'text-anchor="middle" stroke="none" fill="#111" font-size="{size}" '
                     f'font-weight="{weight}">{_esc(text)}</text>')
    lines.append('</g></g></svg>')
    return "\n".join(lines).encode("utf-8")


def render_svg(design, document, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_svg_bytes(
        design, document, width=MASTER_WIDTH, height=MASTER_HEIGHT,
        physical_width="36in", physical_height="24in"))
    return output_path


def render_pdf(design, document, output_path: str | Path, *, profile: str = "24x36") -> Path:
    if profile not in {"24x36", "11x17"}:
        raise ValueError("profile must be 24x36 or 11x17")
    width, height = ((MASTER_WIDTH, MASTER_HEIGHT) if profile == "24x36"
                     else (SMALL_WIDTH, SMALL_HEIGHT))
    svg = _svg_bytes(design, document, width=width, height=height,
                     physical_width=str(width), physical_height=str(height))
    source = fitz.open(stream=svg, filetype="svg")
    pdf_bytes = source.convert_to_pdf()
    source.close()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)
    return output_path


def _next_revision(output_dir: Path, slug: str) -> int:
    pattern = re.compile(rf"^{re.escape(slug)}_riser_rev(\d+)(?:_|\.)")
    revisions = []
    if output_dir.exists():
        for path in output_dir.iterdir():
            match = pattern.match(path.name)
            if match:
                revisions.append(int(match.group(1)))
    return max(revisions, default=0) + 1


def generate_riser_bundle(design, document, output_dir: str | Path) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(design.site_info.school_name or "UNTITLED")
    revision = _next_revision(output_dir, slug)
    base = output_dir / f"{slug}_riser_rev{revision}"
    master = render_pdf(design, document, Path(f"{base}_24x36.pdf"), profile="24x36")
    small = render_pdf(design, document, Path(f"{base}_11x17.pdf"), profile="11x17")
    svg = render_svg(design, document, Path(f"{base}.svg"))
    return [master, small, svg]

