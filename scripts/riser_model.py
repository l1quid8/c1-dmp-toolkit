"""Persistent logical and presentation models for the riser editor.

The electrical graph belongs to :class:`DMPDesign`; ``RiserDocument`` stores
only how that graph is arranged and annotated on the drawing sheet.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Literal


CableStatus = Literal["new", "existing"]


@dataclass(frozen=True)
class DevicePortRef:
    device_id: str
    port_id: str


@dataclass
class TopologyConnection:
    id: str
    source: DevicePortRef
    target: DevicePortRef
    cable_type: str = "WP240R"
    status: CableStatus = "new"
    quantity: int = 1
    custom_label: str | None = None

    @property
    def label(self) -> str:
        if self.custom_label:
            return self.custom_label
        marker = "N" if self.status == "new" else "E"
        return f"({marker})({max(1, self.quantity)}){self.cable_type}"


@dataclass
class RiserTitleBlock:
    school_name: str = ""
    local_code: str = ""
    address: str = ""
    project_title: str = ""
    drawing_title: str = "RISER DIAGRAM"
    system: str = "INTRUSION"
    sheet_number: str = "INT-5.0"
    drawn_by: str = ""
    checked_by: str = ""
    issue_date: str = field(default_factory=lambda: date.today().isoformat())
    revisions: list[str] = field(default_factory=list)


@dataclass
class RiserElement:
    id: str
    kind: str
    ref: str
    x: float
    y: float
    width: float
    height: float
    label_offset: tuple[float, float] = (0.0, 0.0)
    manual: bool = False
    stale: bool = False
    # Drawing ownership is independent of overlapping rectangles and labels.
    location_id: str | None = None
    physical_location_id: str | None = None
    heading_lines: list[str] = field(default_factory=list)
    heading_height: float = 34.0
    symbol_style: str = "classic"
    input_side: str = "top"
    input_side_locked: bool = False
    port_x: dict[str, float] = field(default_factory=dict)


@dataclass
class RiserRoute:
    connection_id: str
    points: list[tuple[float, float]] = field(default_factory=list)
    label_offset: tuple[float, float] = (0.0, 0.0)
    manual: bool = False
    label_hidden: bool = False
    label_manual: bool = False


@dataclass
class RiserAnnotation:
    id: str
    kind: str
    points: list[tuple[float, float]] = field(default_factory=list)
    text: str = ""
    stroke: str = "#111111"
    fill: str = ""
    stroke_width: float = 1.0
    font_size: float = 14.0
    font_weight: str = "normal"
    alignment: str = "left"


@dataclass
class RiserDocument:
    title_block: RiserTitleBlock = field(default_factory=RiserTitleBlock)
    elements: dict[str, RiserElement] = field(default_factory=dict)
    routes: dict[str, RiserRoute] = field(default_factory=dict)
    annotations: list[RiserAnnotation] = field(default_factory=list)
    z_order: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    page_width: float = 36 * 72
    page_height: float = 24 * 72
    layout_version: int = 1
    show_location_frames: bool = True
    fit_warnings: list[str] = field(default_factory=list)


_NAMESPACE = uuid.UUID("5de56b6e-4ae2-4b45-b2a6-31f8e1729898")
_RSP_RE = re.compile(r"^RSP[\s_-]*(\d+)$", re.I)
_KEYPAD_RE = re.compile(r"^(?:KEYPAD|KP)[\s#_-]*(\d+)$", re.I)
_BUS_RE = re.compile(r"(\d{3})\s*BUS", re.I)
_LX_ID_RE = re.compile(r"710-LX(\d{3})-", re.I)


def stable_connection_id(source: DevicePortRef, target: DevicePortRef) -> str:
    key = f"{source.device_id}:{source.port_id}>{target.device_id}:{target.port_id}"
    return str(uuid.uuid5(_NAMESPACE, key))


def _connection(source: DevicePortRef, target: DevicePortRef) -> TopologyConnection:
    return TopologyConnection(stable_connection_id(source, target), source, target)


def _panel_port(splitter) -> str:
    if splitter.splitter_type.upper() == "KP":
        return "PROG"
    text = next(iter((splitter.inputs or {}).values()), "")
    match = _BUS_RE.search(text) or _LX_ID_RE.search(splitter.id)
    return f"LX{match.group(1)}" if match else "LX500"


def derive_legacy_connections(
        design, *, assume_service_keypad: bool = True) -> list[TopologyConnection]:
    """Translate the schema-1 string topology into stable port-to-port edges."""
    result: list[TopologyConnection] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(source: DevicePortRef, target: DevicePortRef) -> None:
        key = (source.device_id, source.port_id, target.device_id, target.port_id)
        if key not in seen:
            seen.add(key)
            result.append(_connection(source, target))

    by_id = {s.id: s for s in getattr(design, "splitters", [])}
    by_normal_id = {s.id.upper(): s.id for s in getattr(design, "splitters", [])}
    for splitter_id in by_id:
        match = re.fullmatch(r"KP-710-(\d+)", splitter_id, re.I)
        if match:
            by_normal_id[f"710-KP-{match.group(1)}"] = splitter_id
        match = re.fullmatch(r"710-KP-(\d+)", splitter_id, re.I)
        if match:
            by_normal_id[f"KP-710-{match.group(1)}"] = splitter_id

    def named_parent(input_text: str) -> str | None:
        """Find a named upstream 710 in field worksheet input descriptions."""
        for token in re.findall(r"(?:KP-710|710-KP|710-LX\d{3}|LX-710)-\d+",
                                input_text.upper()):
            parent = by_normal_id.get(token)
            if parent:
                return parent
        return None

    # Explicit output rows take precedence over prose in the child IN field.
    # Field worksheets commonly write a bare splitter ID (without "To ").
    for splitter in getattr(design, "splitters", []):
        for index, raw in enumerate(splitter.outputs or [], 1):
            value = (raw or "").strip()
            if not value or value.lower() == "spare":
                continue
            target_id = ""
            if value.lower().startswith("to "):
                target_id = value[3:].strip()
            target_id = by_normal_id.get(target_id.upper() or value.upper(), target_id)
            if not target_id:
                target_id = by_normal_id.get(value.upper(), "")
            if not target_id:
                match = _RSP_RE.match(value)
                if match:
                    target_id = f"RSP-{int(match.group(1))}"
                else:
                    match = _KEYPAD_RE.match(value)
                    if match:
                        target_id = f"KEYPAD-{int(match.group(1))}"
            if target_id:
                add(DevicePortRef(splitter.id, f"OUT{index}"),
                    DevicePortRef(target_id, "IN"))

    # Preserve service keypads wired directly to the panel. Other keypad feeds
    # should already be represented by splitter output rows.
    for keypad in getattr(design, "keypads", []):
        source = (keypad.source or "").strip().upper()
        if (source in {"MSP", "XR550", "KP BUS"}
                or assume_service_keypad and keypad.number == 1 and not source):
            add(DevicePortRef("MSP", "KP BUS"),
                DevicePortRef(f"KEYPAD-{keypad.number}", "IN"))

    # A schema-1 keypad may name its upstream 710 only in ``keypad.source``.
    # Allocate the first free output deterministically when no output row
    # redundantly names that keypad.
    used = {(c.source.device_id, c.source.port_id) for c in result}
    incoming = {c.target.device_id for c in result}
    for keypad in getattr(design, "keypads", []):
        target_id = f"KEYPAD-{keypad.number}"
        parent = (keypad.source or "").strip()
        if target_id in incoming or parent not in by_id:
            continue
        for index in range(1, 4):
            port = (parent, f"OUT{index}")
            if port not in used:
                add(DevicePortRef(*port), DevicePortRef(target_id, "IN"))
                used.add(port)
                incoming.add(target_id)
                break
    # A service keypad may share the panel bus while all splitter outputs are
    # occupied by downstream devices.  Do not steal one of those outputs.
    if assume_service_keypad and any(k.number == 1 for k in getattr(design, "keypads", [])):
        if not any(edge.target.device_id == "KEYPAD-1" for edge in result):
            add(DevicePortRef("MSP", "KP BUS"), DevicePortRef("KEYPAD-1", "IN"))

    # Some legacy data names a parent only in the child's IN field. Attach it
    # to the first unused output so the edge is not silently lost.
    used = {(c.source.device_id, c.source.port_id) for c in result}
    for splitter in getattr(design, "splitters", []):
        input_text = next(iter((splitter.inputs or {}).values()), "").strip()
        parent = named_parent(input_text)
        if parent is None:
            continue
        if any(c.target.device_id == splitter.id for c in result):
            continue
        for index in range(1, 4):
            port = (parent, f"OUT{index}")
            if port not in used:
                add(DevicePortRef(*port), DevicePortRef(splitter.id, "IN"))
                used.add(port)
                break
    # Only true bus roots are direct panel children.  Previously every
    # populated input without a leading "From " became a direct MSP edge,
    # even when a different splitter explicitly listed it on an output.
    incoming = {c.target.device_id for c in result}
    for splitter in getattr(design, "splitters", []):
        input_text = next(iter((splitter.inputs or {}).values()), "").strip()
        if input_text and splitter.id not in incoming and not named_parent(input_text):
            add(DevicePortRef("MSP", _panel_port(splitter)),
                DevicePortRef(splitter.id, "IN"))
    panel_roots = [edge for edge in result
                   if edge.source.device_id == "MSP"
                   and edge.target.device_id in by_id]
    return panel_roots + [edge for edge in result if edge not in panel_roots]


def default_riser_document(design) -> RiserDocument:
    site = design.site_info
    address = "\n".join(x for x in (site.address_line1, site.address_line2) if x)
    return RiserDocument(title_block=RiserTitleBlock(
        school_name=site.school_name or "",
        local_code=site.school_code or "",
        address=address,
        project_title=site.school_name or "",
    ))
