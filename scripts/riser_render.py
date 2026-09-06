"""SVG/PDF renderers and revision-matched riser output bundles."""

from __future__ import annotations

import base64
import html
import os
import re
import tempfile
from pathlib import Path

import fitz

from paths import resource_path
from riser_drawing import device_shape, device_text, location_text_runs, title_bounds, logo_bounds, title_text
from riser_symbols import symbol_parts, modern_title, terminal_parts
from riser_scene import (
    find_bridges,
    route_label_point,
    wire_segments,
)


MASTER_WIDTH = 36 * 72
MASTER_HEIGHT = 24 * 72
SMALL_WIDTH = 17 * 72
SMALL_HEIGHT = 11 * 72


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _svg_text(run):
    return (f'<text x="{run.x:.2f}" y="{run.y:.2f}" text-anchor="{run.anchor}" '
            'font-family="Helvetica" '
            f'font-size="{run.size}" font-weight="{"bold" if run.bold else "normal"}" '
            f'stroke="none" fill="#111">{_esc(run.text)}</text>')


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


def _svg_symbol(part):
    c = part.coords
    common = f'fill="{"white" if part.fill else "none"}" stroke="#111" stroke-width="{part.width}"'
    if part.kind == 'polygon':
        closed=(*c,*c[:2])
        points=' '.join(f'{x:.2f},{y:.2f}' for x,y in zip(closed[::2],closed[1::2]))
        return f'<polygon points="{points}" {common}/>'
    x,y,right,bottom=c
    if part.kind == 'ellipse':
        return f'<ellipse cx="{(x+right)/2:.2f}" cy="{(y+bottom)/2:.2f}" rx="{(right-x)/2:.2f}" ry="{(bottom-y)/2:.2f}" {common}/>'
    if part.kind == 'line':
        return f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{right:.2f}" y2="{bottom:.2f}" {common}/>'
    return f'<rect x="{x:.2f}" y="{y:.2f}" width="{right-x:.2f}" height="{bottom-y:.2f}" {common}/>'


def _route_path(connection_id: str, points, bridges) -> str:
    if not points:
        return ""
    commands = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    for segment in wire_segments(connection_id, points, bridges):
        coordinates = " ".join(f"{x:.2f} {y:.2f}" for x, y in segment[2:])
        commands.append(f"{segment[0]} {coordinates}")
    return " ".join(commands)




def _svg_bytes(design, document, *, width, height, physical_width: str,
               physical_height: str, profile: str = "24x36") -> bytes:
    small = profile == "11x17"
    cable_size = 15.3 if small else 14
    bridges = find_bridges({key: route.points for key, route in document.routes.items()})
    connections = _connection_map(design)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (f'<svg xmlns="http://www.w3.org/2000/svg" '
         f'xmlns:xlink="http://www.w3.org/1999/xlink" width="{physical_width}" '
         f'height="{physical_height}" viewBox="0 0 {document.page_width:.0f} {document.page_height:.0f}">'),
        ('<defs><marker id="arrowhead" markerWidth="10" markerHeight="7" '
         'refX="9" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" '
         ' fill="context-stroke"/></marker></defs>'),
        '<rect width="100%" height="100%" fill="white"/>',
        '<g id="drawing" font-family="Helvetica" fill="#111111">',
    ]

    for element_id in document.z_order:
        element = document.elements.get(element_id)
        if element is None:
            continue
        if element.kind == "location":
            if not document.show_location_frames:
                continue
            lines.append(
                f'<g id="{_esc(element.id)}" class="location"><rect x="{element.x:.2f}" '
                f'y="{element.y:.2f}" width="{element.width:.2f}" height="{element.height:.2f}" '
                'fill="none" stroke="#8b949e" stroke-width="1" stroke-dasharray="8 5"/>'
                f'<line x1="{element.x:.2f}" y1="{element.y + element.heading_height:.2f}" '
                f'x2="{element.x + element.width:.2f}" y2="{element.y + element.heading_height:.2f}" '
                'stroke="#d7dde3" stroke-width="1"/>' +
                ''.join(_svg_text(run) for run in location_text_runs(element, small=small)) + '</g>')
            continue
        if element.kind != "device":
            continue
        x, y, w, h = element.x, element.y, element.width, element.height
        lines.append(f'<g id="{_esc(element.id)}" class="device {_esc(element.ref)}">')
        lines.extend(_svg_symbol(part) for part in symbol_parts(design,element))
        lines.extend(_svg_text(run) for run in device_text(design, element, small=small))
        lines.extend(_svg_symbol(part) for part in terminal_parts(design,element))
        lines.append('</g>')

    lines.append('<g id="topology" fill="none" stroke="#111" stroke-width="1.2">')
    for connection_id, route in document.routes.items():
        path_d = _route_path(connection_id, route.points, bridges)
        lines.append(f'<path id="route-{_esc(connection_id)}" d="{path_d}"/>')
    lines.append('</g>')
    lines.append(f'<g id="cable-labels" font-size="{cable_size}" font-weight="bold">')
    for connection_id, route in document.routes.items():
        edge = connections.get(connection_id)
        if edge is None or not route.points or route.label_hidden:
            continue
        x, y = route_label_point(route.points) or (0.0, 0.0)
        x += route.label_offset[0]
        y += route.label_offset[1]
        lines.append(f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="middle" '
                     f'font-family="Helvetica" font-size="{cable_size}" font-weight="bold">'
                     f'{_esc(edge.label)}</text>')
    lines.append('</g>')

    lines.append('<g id="markup">')
    annotations = sorted(
        document.annotations,
        key=lambda item: (document.z_order.index(item.id)
                          if item.id in document.z_order else len(document.z_order)))
    for annotation in annotations:
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in annotation.points)
        stroke_width = max(annotation.stroke_width, 0.77 if small else 0.0)
        common = (f'stroke="{_esc(annotation.stroke)}" stroke-width="{stroke_width:.2f}" '
                  f'fill="{_esc(annotation.fill or "none")}"')
        if annotation.kind == "text" and annotation.points:
            x, y = annotation.points[0]
            anchor = {"left": "start", "center": "middle", "right": "end"}.get(
                annotation.alignment, "start")
            font_size = max(annotation.font_size, 13.2 if small else 0.0)
            lines.append(f'<text id="{_esc(annotation.id)}" x="{x:.2f}" y="{y:.2f}" '
                         'font-family="Helvetica" '
                         f'font-size="{font_size:.2f}" font-weight="{_esc(annotation.font_weight)}" '
                         f'text-anchor="{anchor}" fill="{_esc(annotation.stroke)}">'
                         f'{_esc(annotation.text)}</text>')
        elif annotation.kind in {"rectangle", "ellipse"} and len(annotation.points) >= 2:
            (x1, y1), (x2, y2) = annotation.points[:2]
            if annotation.kind == "rectangle":
                lines.append(f'<rect id="{_esc(annotation.id)}" x="{min(x1,x2):.2f}" y="{min(y1,y2):.2f}" '
                             f'width="{abs(x2-x1):.2f}" height="{abs(y2-y1):.2f}" {common}/>')
            else:
                lines.append(f'<ellipse id="{_esc(annotation.id)}" cx="{(x1+x2)/2:.2f}" cy="{(y1+y2)/2:.2f}" '
                             f'rx="{abs(x2-x1)/2:.2f}" ry="{abs(y2-y1)/2:.2f}" {common}/>')
        elif len(annotation.points) >= 2:
            marker = ' marker-end="url(#arrowhead)"' if annotation.kind == "arrow" else ""
            lines.append(f'<polyline id="{_esc(annotation.id)}" points="{points}" '
                         f'{common}{marker}/>')
    lines.append('</g>')

    tx, ty, right, bottom = title_bounds(document)
    if document.layout_version >= 3:
        lines.append(f'<g id="sheet-frame"><rect x="36" y="36" width="{document.page_width-72}" height="{document.page_height-72}" fill="none" stroke="#111" stroke-width="2"/></g>')
    lines.append('<g id="title-block" stroke="#111" fill="white">')
    lines.append(f'<rect x="{tx:.2f}" y="{ty:.2f}" width="{right - tx:.2f}" '
                 f'height="{bottom - ty:.2f}" stroke-width="2"/>')
    logo = _logo_data()
    if logo:
        lx, ly, lr, lb = logo_bounds(document)
        lines.append(f'<image x="{lx:.2f}" y="{ly:.2f}" width="{lr-lx:.2f}" height="{lb-ly:.2f}" '
                     f'preserveAspectRatio="xMidYMid meet" xlink:href="data:image/png;base64,{logo}"/>')
    lines.extend(_svg_text(run) for run in title_text(document, small=small))
    if document.layout_version >= 3:
        for (x1,y1),(x2,y2) in modern_title(document)[1]:
            lines.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke-width="1.2"/>')
    lines.append('</g></g></svg>')
    return "\n".join(lines).encode("utf-8")


def render_svg(design, document, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_svg_bytes(
        design, document, width=MASTER_WIDTH, height=MASTER_HEIGHT,
        physical_width="36in", physical_height="24in", profile="24x36"))
    return output_path


def render_pdf(design, document, output_path: str | Path, *, profile: str = "24x36") -> Path:
    if profile not in {"24x36", "11x17"}:
        raise ValueError("profile must be 24x36 or 11x17")
    width, height = ((MASTER_WIDTH, MASTER_HEIGHT) if profile == "24x36"
                     else (SMALL_WIDTH, SMALL_HEIGHT))
    svg = _svg_bytes(design, document, width=width, height=height,
                     physical_width=str(width), physical_height=str(height),
                     profile=profile)
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


def generate_riser_bundle(design, document, output_dir: str | Path, *,
                          formats=("24x36", "11x17", "svg")) -> list[Path]:
    formats = tuple(dict.fromkeys(formats))
    if not formats or any(fmt not in {"24x36", "11x17", "svg"} for fmt in formats):
        raise ValueError("Choose at least one supported riser output.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(design.site_info.school_name or "UNTITLED")
    revision = _next_revision(output_dir, slug)
    names = [f"{slug}_riser_rev{revision}" +
             (".svg" if fmt == "svg" else f"_{fmt}.pdf") for fmt in formats]
    with tempfile.TemporaryDirectory(prefix=f".{slug}_riser_", dir=output_dir) as staging:
        staging_dir = Path(staging)
        staged = [staging_dir / name for name in names]
        for fmt, path in zip(formats, staged):
            if fmt == "svg":
                render_svg(design, document, path)
            else:
                render_pdf(design, document, path, profile=fmt)
        final = [output_dir / name for name in names]
        for source, target in zip(staged, final):
            os.replace(source, target)
    return final
