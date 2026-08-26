"""Canonical riser-topology mutation and legacy-field projection."""

from __future__ import annotations

import re
import uuid

from riser_model import (
    DevicePortRef,
    TopologyConnection,
    default_riser_document,
    derive_legacy_connections,
)


class TopologyError(ValueError):
    pass


def ensure_explicit_topology(design) -> None:
    """Initialize graph/presentation state for a newly imported design."""
    if not getattr(design, "connections", None):
        design.connections = derive_legacy_connections(design)
    if getattr(design, "riser_document", None) is None:
        design.riser_document = default_riser_document(design)


_LX_ID_RE = re.compile(r"^710-LX(\d{3})-", re.I)
_OUT_RE = re.compile(r"^OUT([123])$")


def _splitter(design, device_id: str):
    return next((s for s in design.splitters if s.id == device_id), None)


def _device_kind(design, device_id: str) -> str:
    if device_id == "MSP":
        return "msp"
    splitter = _splitter(design, device_id)
    if splitter:
        return "kp_splitter" if splitter.splitter_type.upper() == "KP" else "lx_splitter"
    if re.fullmatch(r"RSP-\d+", device_id) and any(
            f"RSP-{r.number}" == device_id for r in design.rsps):
        return "rsp"
    if re.fullmatch(r"KEYPAD-\d+", device_id) and any(
            f"KEYPAD-{k.number}" == device_id for k in design.keypads):
        return "keypad"
    raise TopologyError(f"Unknown device: {device_id}")


def _validate_ports(design, source: DevicePortRef, target: DevicePortRef) -> None:
    source_kind = _device_kind(design, source.device_id)
    target_kind = _device_kind(design, target.device_id)
    if source.device_id == target.device_id:
        raise TopologyError("A device cannot connect to itself")
    if target.port_id != "IN":
        raise TopologyError("Connections must land on an IN port")

    if source_kind == "msp":
        if source.port_id == "KP BUS" and target_kind == "keypad":
            return
        if source.port_id == "PROG" and target_kind == "kp_splitter":
            return
        if re.fullmatch(r"LX\d{3}", source.port_id) and target_kind == "lx_splitter":
            bus = _LX_ID_RE.match(target.device_id)
            if bus and source.port_id == f"LX{bus.group(1)}":
                return
            raise TopologyError(f"{source.port_id} cannot feed {target.device_id}")
        if target_kind == "kp_splitter":
            raise TopologyError("A KP splitter must be fed from the MSP PROG port")
        if target_kind == "lx_splitter":
            raise TopologyError("An LX splitter must be fed from its matching MSP LX bus")
        raise TopologyError(f"MSP {source.port_id} cannot feed {target.device_id}")

    if source_kind not in {"kp_splitter", "lx_splitter"} or not _OUT_RE.match(source.port_id):
        raise TopologyError("Only MSP buses and splitter OUT ports can source a connection")
    if source_kind == "kp_splitter" and target_kind not in {"keypad", "kp_splitter"}:
        raise TopologyError("A KP splitter output can feed only a keypad or KP splitter")
    if source_kind == "lx_splitter" and target_kind not in {"rsp", "lx_splitter"}:
        raise TopologyError("An LX splitter output can feed only an RSP or LX splitter")
    if target_kind == "lx_splitter":
        src_bus = _LX_ID_RE.match(source.device_id)
        dst_bus = _LX_ID_RE.match(target.device_id)
        if src_bus and dst_bus and src_bus.group(1) != dst_bus.group(1):
            raise TopologyError("LX splitters on different buses cannot be chained")


def _has_path(design, start: str, goal: str, *, excluding: str | None = None) -> bool:
    adjacency: dict[str, list[str]] = {}
    for edge in design.connections:
        if edge.id == excluding:
            continue
        adjacency.setdefault(edge.source.device_id, []).append(edge.target.device_id)
    pending = [start]
    seen: set[str] = set()
    while pending:
        device = pending.pop()
        if device == goal:
            return True
        if device in seen:
            continue
        seen.add(device)
        pending.extend(adjacency.get(device, []))
    return False


def connect(design, source: DevicePortRef, target: DevicePortRef, *,
            cable_type: str = "WP240R", status: str = "new", quantity: int = 1,
            custom_label: str | None = None, connection_id: str | None = None,
            allow_occupied_target: bool = False) -> TopologyConnection:
    _validate_ports(design, source, target)
    if any(c.source == source for c in design.connections):
        raise TopologyError(f"{source.device_id} {source.port_id} is already connected")
    if not allow_occupied_target and any(c.target == target for c in design.connections):
        raise TopologyError(f"{target.device_id} {target.port_id} is already connected")
    if _has_path(design, target.device_id, source.device_id):
        raise TopologyError("This connection would create a cycle")
    if status not in {"new", "existing"}:
        raise TopologyError("Cable status must be new or existing")
    if quantity < 1:
        raise TopologyError("Cable quantity must be at least 1")
    edge = TopologyConnection(
        id=connection_id or str(uuid.uuid4()), source=source, target=target,
        cable_type=(cable_type or "").strip(), status=status, quantity=quantity,
        custom_label=(custom_label or "").strip() or None,
    )
    design.connections.append(edge)
    return edge


def disconnect(design, connection_id: str) -> TopologyConnection:
    for index, edge in enumerate(design.connections):
        if edge.id == connection_id:
            return design.connections.pop(index)
    raise TopologyError(f"Unknown connection: {connection_id}")


def reconnect(design, connection_id: str, *, source: DevicePortRef | None = None,
              target: DevicePortRef | None = None) -> TopologyConnection:
    old = disconnect(design, connection_id)
    try:
        return connect(
            design, source or old.source, target or old.target,
            cable_type=old.cable_type, status=old.status, quantity=old.quantity,
            custom_label=old.custom_label, connection_id=old.id,
        )
    except Exception:
        design.connections.append(old)
        raise


def _legacy_output(target_id: str) -> str:
    if target_id.startswith("RSP-"):
        return target_id
    if target_id.startswith("KEYPAD-"):
        return f"KEYPAD #{target_id.split('-', 1)[1]}"
    return f"To {target_id}"


def project_legacy_topology(design) -> None:
    """Materialize the explicit graph into fields consumed by legacy exports."""
    splitters = {s.id: s for s in design.splitters}
    keypads = {f"KEYPAD-{k.number}": k for k in design.keypads}
    for splitter in design.splitters:
        splitter.inputs = {}
        splitter.outputs = ["Spare", "Spare", "Spare"]
    for keypad in design.keypads:
        keypad.source = None

    for edge in design.connections:
        target_splitter = splitters.get(edge.target.device_id)
        if target_splitter:
            key = "KP-Bus In" if target_splitter.splitter_type.upper() == "KP" else "LX-Bus In"
            if edge.source.device_id == "MSP":
                if target_splitter.splitter_type.upper() == "KP":
                    value = "KEYPAD BUS IN FROM XR/550"
                else:
                    bus = edge.source.port_id.removeprefix("LX") or "500"
                    value = f"{bus} BUS IN FROM XR/550"
            else:
                value = f"From {edge.source.device_id}"
            target_splitter.inputs = {key: value}

        source_splitter = splitters.get(edge.source.device_id)
        match = _OUT_RE.match(edge.source.port_id)
        if source_splitter and match:
            source_splitter.outputs[int(match.group(1)) - 1] = _legacy_output(edge.target.device_id)

        keypad = keypads.get(edge.target.device_id)
        if keypad:
            keypad.source = "MSP" if edge.source.device_id == "MSP" else edge.source.device_id


def refresh_connections_from_legacy(design) -> None:
    """Mirror card/hardware edits into the graph without losing cable metadata."""
    prior = {
        (edge.source, edge.target): edge
        for edge in getattr(design, "connections", [])
    }
    refreshed = derive_legacy_connections(design)
    for index, edge in enumerate(refreshed):
        old = prior.get((edge.source, edge.target))
        if old is not None:
            refreshed[index] = old
    design.connections = refreshed
