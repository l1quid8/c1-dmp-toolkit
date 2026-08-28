"""Canonical riser-topology mutation and legacy-field projection."""

from __future__ import annotations

import re
import uuid

from riser_model import (
    DevicePortRef,
    TopologyConnection,
    default_riser_document,
    derive_legacy_connections,
    stable_connection_id,
)


class TopologyError(ValueError):
    pass


def ensure_explicit_topology(design) -> None:
    """Initialize graph/presentation state for a newly imported design."""
    # A presentation document is the initialization marker for schema 2+. An
    # explicitly empty graph is valid state (all wiring may have been removed)
    # and must never be re-derived from compatibility strings during save.
    if getattr(design, "riser_document", None) is None:
        # A caller can supply a canonical graph before a drawing exists.
        # Create only the missing drawing; legacy projection cannot recover
        # custom edges or their cable metadata.
        if not design.connections:
            design.connections = derive_legacy_connections(design)
        design.riser_document = default_riser_document(design)


_LX_ID_RE = re.compile(r"^710-LX(\d{3})-", re.I)
_BUS_INPUT_RE = re.compile(r"(\d{3})\s*BUS", re.I)
_OUT_RE = re.compile(r"^OUT([123])$")
_COLLISION_NAMESPACE = uuid.UUID("0d10efdd-4f98-4f29-a431-63db198919fc")


def _splitter(design, device_id: str):
    return next((s for s in design.splitters if s.id == device_id), None)


def _lx_bus(design, device_id: str, seen: set[str] | None = None) -> str | None:
    """Resolve modern and legacy LX splitter IDs to their electrical bus."""
    splitter = _splitter(design, device_id)
    if splitter is None or splitter.splitter_type.upper() != "LX":
        return None
    match = _LX_ID_RE.match(splitter.id or "")
    if match:
        return match.group(1)
    for value in (splitter.inputs or {}).values():
        match = _BUS_INPUT_RE.search(value or "")
        if match:
            return match.group(1)
    seen = set() if seen is None else seen
    if device_id in seen:
        return None
    seen.add(device_id)
    for value in (splitter.inputs or {}).values():
        text = (value or "").strip()
        if text.lower().startswith("from "):
            inherited = _lx_bus(design, text[5:].strip(), seen)
            if inherited:
                return inherited
    # Parser-supported LX-710-N files predate bus-bearing IDs and historically
    # mean LX500 when neither their own input nor a parent declares a bus.
    return "500"


def _device_kind(design, device_id: str) -> str:
    if device_id == "MSP":
        return "msp"
    splitter = _splitter(design, device_id)
    if splitter:
        return "kp_splitter" if splitter.splitter_type.upper() == "KP" else "lx_splitter"
    # Riser imports may contain field devices that are intentionally absent
    # from the worksheet inventory (the UI labels these "not in design").
    # Their canonical IDs still define valid electrical endpoints.
    if re.fullmatch(r"RSP-\d+", device_id):
        return "rsp"
    if re.fullmatch(r"KEYPAD-\d+", device_id):
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
            bus = _lx_bus(design, target.device_id)
            if bus and source.port_id == f"LX{bus}":
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
        src_bus = _lx_bus(design, source.device_id)
        dst_bus = _lx_bus(design, target.device_id)
        if src_bus and dst_bus and src_bus != dst_bus:
            raise TopologyError("LX splitters on different buses cannot be chained")


def validate_connection_endpoints(design, source: DevicePortRef,
                                  target: DevicePortRef) -> None:
    """Validate one endpoint pair without mutating or checking occupancy."""
    _validate_ports(design, source, target)


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
    if _has_path(design, target.device_id, source.device_id):
        raise TopologyError("This connection would create a cycle")
    if status not in {"new", "existing"}:
        raise TopologyError("Cable status must be new or existing")
    if quantity < 1:
        raise TopologyError("Cable quantity must be at least 1")
    requested_id = connection_id or stable_connection_id(source, target)
    used_ids = {edge.id for edge in design.connections}
    if requested_id in used_ids and connection_id is not None:
        raise TopologyError(f"Connection ID already exists: {requested_id}")
    edge_id = requested_id
    ordinal = 2
    while edge_id in used_ids:
        edge_id = str(uuid.uuid5(
            _COLLISION_NAMESPACE, f"{requested_id}:{ordinal}"))
        ordinal += 1
    edge = TopologyConnection(
        id=edge_id, source=source, target=target,
        cable_type=(cable_type or "").strip(), status=status, quantity=quantity,
        custom_label=(custom_label or "").strip() or None,
    )
    design.connections.append(edge)
    # An explicit edit establishes graph ownership, even before opening RISER.
    # Keep this marker after disconnecting the last edge so save/recovery do
    # not resurrect stale compatibility wiring. Set it only after validation.
    if getattr(design, "riser_document", None) is None:
        design.riser_document = default_riser_document(design)
    return edge


def disconnect(design, connection_id: str) -> TopologyConnection:
    for index, edge in enumerate(design.connections):
        if edge.id == connection_id:
            # Imported graphs can be edited before any drawing exists too.
            # Mark ownership before deleting the last evidence of that graph.
            if getattr(design, "riser_document", None) is None:
                design.riser_document = default_riser_document(design)
            return design.connections.pop(index)
    raise TopologyError(f"Unknown connection: {connection_id}")


def reconnect(design, connection_id: str, *, source: DevicePortRef | None = None,
              target: DevicePortRef | None = None) -> TopologyConnection:
    index = next((offset for offset, edge in enumerate(design.connections)
                  if edge.id == connection_id), None)
    if index is None:
        raise TopologyError(f"Unknown connection: {connection_id}")
    old = design.connections[index]
    new_source, new_target = source or old.source, target or old.target
    if new_source == old.source and new_target == old.target:
        return old
    design.connections.pop(index)
    try:
        replacement = connect(
            design, new_source, new_target,
            cable_type=old.cable_type, status=old.status, quantity=old.quantity,
            custom_label=old.custom_label, connection_id=old.id,
        )
    except Exception:
        design.connections.insert(index, old)
        raise
    design.connections.remove(replacement)
    design.connections.insert(index, replacement)
    return replacement


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
    manual_inputs = {
        splitter.id: dict(splitter.inputs)
        for splitter in design.splitters
        if _manual_input_text(design, splitter)
    }
    manual_outputs = {
        splitter.id: {
            index: value for index, value in enumerate(splitter.outputs or [])
            if _manual_output_text(design, value)
        }
        for splitter in design.splitters
    }
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

    for splitter_id, inputs in manual_inputs.items():
        splitters[splitter_id].inputs = inputs
    for splitter_id, outputs in manual_outputs.items():
        for index, value in outputs.items():
            splitters[splitter_id].outputs[index] = value


def _manual_input_text(design, splitter) -> bool:
    """Whether a legacy IN field is a note the graph cannot represent."""
    value = next(iter((splitter.inputs or {}).values()), "").strip()
    if not value:
        return False
    if splitter.splitter_type.upper() == "KP":
        if value.casefold() == "keypad bus in from xr/550":
            return False
    elif re.fullmatch(r"\d{3}\s+BUS\s+IN\s+FROM\s+XR/550", value, re.I):
        return False
    if value.casefold().startswith("from "):
        return _splitter(design, value[5:].strip()) is None
    return True


def _manual_output_text(design, value: str) -> bool:
    """Whether an OUT value is legacy wording rather than a graph endpoint."""
    text = (value or "").strip()
    if not text or text.casefold() == "spare":
        return False
    if re.fullmatch(r"RSP[\s_-]*\d+", text, re.I):
        return False
    if re.fullmatch(r"(?:KEYPAD|KP)[\s#_-]*\d+", text, re.I):
        return False
    if text.casefold().startswith("to "):
        return _splitter(design, text[3:].strip()) is None
    return True


def _project_splitter_input(design, splitter_id: str) -> None:
    splitter = _splitter(design, splitter_id)
    if splitter is None:
        return
    edge = next((item for item in design.connections
                 if item.target == DevicePortRef(splitter_id, "IN")), None)
    if edge is None:
        splitter.inputs = {}
        return
    key = "KP-Bus In" if splitter.splitter_type.upper() == "KP" else "LX-Bus In"
    if edge.source.device_id == "MSP":
        if splitter.splitter_type.upper() == "KP":
            value = "KEYPAD BUS IN FROM XR/550"
        else:
            bus = edge.source.port_id.removeprefix("LX") or "500"
            value = f"{bus} BUS IN FROM XR/550"
    else:
        value = f"From {edge.source.device_id}"
    splitter.inputs = {key: value}


def _project_splitter_output(design, splitter_id: str, port_id: str) -> None:
    splitter = _splitter(design, splitter_id)
    match = _OUT_RE.match(port_id)
    if splitter is None or match is None:
        return
    while len(splitter.outputs) < 3:
        splitter.outputs.append("Spare")
    source = DevicePortRef(splitter_id, port_id)
    edge = next((item for item in design.connections if item.source == source), None)
    splitter.outputs[int(match.group(1)) - 1] = (
        _legacy_output(edge.target.device_id) if edge else "Spare")


def _project_keypad_source(design, keypad_id: str) -> None:
    match = re.fullmatch(r"KEYPAD-(\d+)", keypad_id)
    if match is None:
        return
    keypad = next((item for item in design.keypads
                   if item.number == int(match.group(1))), None)
    if keypad is None:
        return
    target = DevicePortRef(keypad_id, "IN")
    edge = next((item for item in design.connections if item.target == target), None)
    keypad.source = (None if edge is None else
                     "MSP" if edge.source.device_id == "MSP"
                     else edge.source.device_id)


def _project_affected_connection(design, edge: TopologyConnection | None) -> None:
    if edge is None:
        return
    _project_splitter_output(design, edge.source.device_id, edge.source.port_id)
    _project_splitter_input(design, edge.target.device_id)
    _project_keypad_source(design, edge.target.device_id)


def _target_device_id(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.casefold() == "spare":
        return None
    if text.casefold().startswith("to "):
        return text[3:].strip()
    rsp = re.fullmatch(r"RSP[\s_-]*(\d+)", text, re.I)
    if rsp:
        return f"RSP-{int(rsp.group(1))}"
    keypad = re.fullmatch(r"(?:KEYPAD|KP)[\s#_-]*(\d+)", text, re.I)
    if keypad:
        return f"KEYPAD-{int(keypad.group(1))}"
    return text


def set_splitter_output(design, splitter_id: str, output_index: int,
                        value: str | None) -> TopologyConnection | None:
    """Apply one SPLITTERS card output edit through the canonical graph."""
    splitter = _splitter(design, splitter_id)
    if splitter is None:
        raise TopologyError(f"Unknown splitter: {splitter_id}")
    if output_index not in range(3):
        raise TopologyError("Splitter output index must be 0, 1, or 2")
    source = DevicePortRef(splitter_id, f"OUT{output_index + 1}")
    current = next((edge for edge in design.connections if edge.source == source), None)
    target_id = _target_device_id(value)
    if target_id is None:
        if current is not None:
            disconnect(design, current.id)
        _project_splitter_output(design, splitter_id, source.port_id)
        _project_affected_connection(design, current)
        return None
    target = DevicePortRef(target_id, "IN")
    if current is not None and current.target == target:
        _project_affected_connection(design, current)
        return current
    edge = (reconnect(design, current.id, target=target) if current is not None
            else connect(design, source, target))
    _project_affected_connection(design, current)
    _project_affected_connection(design, edge)
    return edge


def set_splitter_input(design, splitter_id: str,
                       source: DevicePortRef | None) -> TopologyConnection | None:
    """Reconnect a splitter IN card field to one exact canonical output."""
    if _splitter(design, splitter_id) is None:
        raise TopologyError(f"Unknown splitter: {splitter_id}")
    target = DevicePortRef(splitter_id, "IN")
    current = next((edge for edge in design.connections if edge.target == target), None)
    if source is None:
        if current is not None:
            disconnect(design, current.id)
        _project_splitter_input(design, splitter_id)
        _project_affected_connection(design, current)
        return None
    if current is not None and current.source == source:
        _project_affected_connection(design, current)
        return current
    edge = (reconnect(design, current.id, source=source) if current is not None
            else connect(design, source, target))
    _project_affected_connection(design, current)
    _project_affected_connection(design, edge)
    return edge


def set_keypad_source(design, keypad_number: int,
                      source_device_id: str | None) -> TopologyConnection | None:
    """Reconnect a keypad, allocating the first compatible free KP output."""
    keypad_id = f"KEYPAD-{keypad_number}"
    _device_kind(design, keypad_id)
    target = DevicePortRef(keypad_id, "IN")
    current = next((edge for edge in design.connections if edge.target == target), None)
    if not source_device_id:
        if current is not None:
            disconnect(design, current.id)
        _project_keypad_source(design, keypad_id)
        _project_affected_connection(design, current)
        return None

    if source_device_id == "MSP":
        source = DevicePortRef("MSP", "KP BUS")
    else:
        if _device_kind(design, source_device_id) != "kp_splitter":
            raise TopologyError("A keypad can be fed only by the MSP or a KP splitter")
        if current is not None and current.source.device_id == source_device_id:
            source = current.source
        else:
            occupied = {edge.source for edge in design.connections if edge.id != (
                current.id if current else None)}
            source = next((DevicePortRef(source_device_id, f"OUT{index}")
                           for index in range(1, 4)
                           if DevicePortRef(source_device_id, f"OUT{index}") not in occupied), None)
            if source is None:
                raise TopologyError(f"{source_device_id} has no free output ports")

    if current is not None and current.source == source:
        _project_affected_connection(design, current)
        return current
    edge = (reconnect(design, current.id, source=source) if current is not None
            else connect(design, source, target))
    _project_affected_connection(design, current)
    _project_affected_connection(design, edge)
    return edge


def prune_unknown_connections(design) -> list[TopologyConnection]:
    """Remove edges incident to hardware that no longer exists."""
    live = {
        "MSP",
        *(splitter.id for splitter in design.splitters),
        *(f"RSP-{rsp.number}" for rsp in design.rsps),
        *(f"KEYPAD-{keypad.number}" for keypad in design.keypads),
    }
    # A riser can legitimately show field devices that are not yet represented
    # by worksheet inventory rows. Legacy wiring text is the persistence marker
    # that distinguishes those endpoints from structured hardware just removed
    # by a user action (the remove_* helpers scrub those references first).
    for edge in derive_legacy_connections(design, assume_service_keypad=False):
        live.add(edge.source.device_id)
        live.add(edge.target.device_id)
    kept, removed = [], []
    for edge in design.connections:
        if edge.source.device_id in live and edge.target.device_id in live:
            kept.append(edge)
        else:
            removed.append(edge)
    design.connections = kept
    return removed


def refresh_connections_from_legacy(design) -> None:
    """Refresh an uninitialized legacy import, never an owned canonical graph.

    Runtime cards use set_splitter_input/output and set_keypad_source. Their
    legacy strings cannot express a draft with duplicate ports; rebuilding an
    initialized graph from those strings would drop cables and orphan routes.
    """
    if getattr(design, "riser_document", None) is not None:
        return
    prior = {
        (edge.source, edge.target): edge
        for edge in getattr(design, "connections", [])
    }
    # Runtime card edits must not apply schema-1's historical assumption that
    # a blank keypad #1 is wired to the MSP. Newly added blank hardware stays
    # disconnected until the user chooses a source.
    refreshed = derive_legacy_connections(design, assume_service_keypad=False)
    for index, edge in enumerate(refreshed):
        old = prior.get((edge.source, edge.target))
        if old is not None:
            refreshed[index] = old
    design.connections = refreshed
