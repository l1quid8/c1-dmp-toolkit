"""Add/remove hardware on a DMPDesign — post-CAD field changes.

Pure mutations with capacity guards; no UI. The editor calls these, then
re-syncs master zones and refreshes its tabs.

Conventions encoded here:
- New expanders take an available consecutive 8- or 16-zone range on one
  LX bus. Module IDs do not determine addresses; imported ranges stay intact.
- The expander's last two physical points supervise its paired power supply
  (exact phrases 'PS-N: A/C LOSS' / 'PS-N: BATT. TRBL' — door-chart
  conditional formatting keys on them).
- Zone addresses are physical: removal leaves a numbering gap, never
  renumbers. Adding reuses the lowest free module number and a fitting free
  address range, which need not be the range of the removed module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from parse_dmp_worksheet import DMPDesign, Keypad, PowerSupply, RSP, Splitter, ZoneInfo

# Template capacities (DMP Installation Worksheet_template_blank.xlsx)
MAX_EXPANDERS = 15        # Point Info sheets shipped in the template
MAX_SPLITTERS_PER_TYPE = 12   # 4 rows per splitter in rows 2-50
MAX_KEYPADS = 28          # Keypad sheet rows 3-30

EXPANDER_MODELS = {"714-16": 16, "714-8": 8}

ZONE_BLOCK = 16
ZONE_BASE = 501
MODULES_PER_BUS = 6   # legacy template layout: six 16-point sheets per bus
LX_BUS_BASES = (500, 600, 700, 800, 900)
LX_BUS_ZONE_COUNT = 100


class HardwareError(Exception):
    """A hardware change the template (or physics) can't accommodate."""


# -------- expanders (RSP + paired PS + zone block) --------

def zone_block_for(number: int) -> range:
    """Return a module's nominal block in the original worksheet template.

    Legacy formula relocation and PDF recovery use this template mapping.
    It is not an allocation rule: live module ownership is always rsp.zones.
    """
    bus, slot = divmod(number - 1, MODULES_PER_BUS)
    start = ZONE_BASE + 100 * bus + ZONE_BLOCK * slot
    return range(start, start + ZONE_BLOCK)


def next_expander_number(design: DMPDesign) -> int:
    used = {r.number for r in design.rsps}
    n = 1
    while n in used:
        n += 1
    return n


def block_orphans(design: DMPDesign, number: int) -> list[ZoneInfo]:
    """Legacy inspection of unowned rows in a nominal template block.

    Additions preserve these rows and allocate around them.
    """
    block = set(zone_block_for(number))
    owned = {zone for rsp in design.rsps for zone in rsp.zones}
    return [z for z in design.zones if z.number in block and z.number not in owned]


def _next_expander_zones(design: DMPDesign, points: int) -> list[int]:
    """Find a whole free module range without moving or overwriting any zones."""
    occupied = {zone for rsp in design.rsps for zone in rsp.zones}
    occupied.update(z.number for z in design.zones)
    occupied.update(z.number for z in design.master_zones)
    for bus in LX_BUS_BASES:
        # Keep the worksheet's usual first address x01. The x00 address is
        # valid too: try a block starting there before leaving this bus.
        starts = [*range(bus + 1, bus + LX_BUS_ZONE_COUNT - points + 1), bus]
        for start in starts:
            block = range(start, start + points)
            if occupied.isdisjoint(block):
                return list(block)
    raise HardwareError(
        f"No consecutive {points}-zone space is available on LX buses 500-900. "
        "Existing zone addresses have been preserved."
    )


def add_expander(design: DMPDesign, model: str, location: str | None = None) -> RSP:
    if model not in EXPANDER_MODELS:
        raise HardwareError(f"Unknown expander model: {model}")
    if len(design.rsps) >= MAX_EXPANDERS:
        raise HardwareError(
            f"The worksheet template supports at most {MAX_EXPANDERS} expanders "
            f"(Point Info sheets 1-{MAX_EXPANDERS})."
        )
    number = next_expander_number(design)
    points = EXPANDER_MODELS[model]
    block = _next_expander_zones(design, points)

    # Check capacity before mutating either representation. Uncached worksheet
    # imports may have only Master records; materialize them before appending
    # so the next sync preserves every existing description.
    from session import ensure_editable_zones
    ensure_editable_zones(design, merge_missing=True)

    rsp = RSP(number=number, location=location, zones=block, model=model)
    design.rsps.append(rsp)
    design.rsps.sort(key=lambda r: r.number)

    design.power_supplies.append(PowerSupply(number=number, location=location))
    design.power_supplies.sort(key=lambda p: p.number)

    # Usable points arrive as SPARE for the tech to rename; the last two
    # physical points supervise the paired power supply.
    for i, zone_num in enumerate(block):
        if i == points - 2:
            zi = ZoneInfo(number=zone_num, location=f"PS-{number}: A/C LOSS",
                          device_type="Supervisory", partition=1)
        elif i == points - 1:
            zi = ZoneInfo(number=zone_num, location=f"PS-{number}: BATT. TRBL",
                          device_type="Supervisory", partition=1)
        else:
            zi = ZoneInfo(number=zone_num, location="SPARE",
                          device_type="Spare", partition=1)
        design.zones.append(zi)
    design.zones.sort(key=lambda z: z.number)
    return rsp


def renumber_expander(design: DMPDesign, number: int, new_number: int) -> RSP:
    """Rename an RSP/PS pair while preserving addresses and drawing geometry."""
    rsp = next((r for r in design.rsps if r.number == number), None)
    if rsp is None:
        raise HardwareError(f"No expander #{number} in this design.")
    if type(new_number) is not int or not 1 <= new_number <= MAX_EXPANDERS:
        raise HardwareError(f"RSP/PS number must be between 1 and {MAX_EXPANDERS}.")
    if number == new_number:
        return rsp
    if any(r.number == new_number for r in design.rsps) or any(
            p.number == new_number for p in design.power_supplies):
        raise HardwareError(f"RSP-{new_number} / PS-{new_number} already exists. Pick a free number.")

    return _rename_expander(design, number, new_number)


def renumber_expanders(design: DMPDesign, numbers: dict[int, int]) -> None:
    """Validate final pair numbers together, allowing swaps and cycles."""
    current = {r.number for r in design.rsps}
    if not set(numbers) <= current:
        raise HardwareError("An edited expander is no longer in the project.")
    final = [numbers.get(r.number, r.number) for r in design.rsps]
    if any(type(n) is not int or not 1 <= n <= MAX_EXPANDERS for n in final):
        raise HardwareError(f"RSP/PS numbers must be between 1 and {MAX_EXPANDERS}.")
    if len(set(final)) != len(final):
        raise HardwareError("Each RSP/PS pair needs a unique number. Finish renumbering the pairs, then Save again.")
    orphan_ps = {p.number for p in design.power_supplies} - current
    if orphan_ps & set(final):
        raise HardwareError("A power supply already uses one of these numbers.")
    changes = [(old, new) for old, new in numbers.items() if old != new]
    # Temporary identities are internal only; no callbacks or saves run between
    # phases, and validation has completed before any mutation.
    for index, (old, new) in enumerate(changes, 1):
        _rename_expander(design, old, -index)
    for index, (old, new) in enumerate(changes, 1):
        _rename_expander(design, -index, new)


def _rename_expander(design: DMPDesign, number: int, new_number: int) -> RSP:
    rsp = next(r for r in design.rsps if r.number == number)
    from session import ensure_editable_zones, sync_master_zones
    from riser_model import DevicePortRef
    ensure_editable_zones(design, merge_missing=True)
    old_id, new_id = f"RSP-{number}", f"RSP-{new_number}"
    rsp.number = new_number
    for ps in design.power_supplies:
        if ps.number == number:
            ps.number = new_number
            ps.relays = {key: re.sub(rf"(\bExpander\s*#\s*){number}(?!\d)",
                                    lambda m: f"{m.group(1)}{new_number}", value, flags=re.I)
                         for key, value in ps.relays.items()}
    for zone in design.zones:
        if zone.number in rsp.zones and zone.location:
            zone.location = re.sub(rf"^PS-{number}(?=\s*:)",
                                   f"PS-{new_number}", zone.location, flags=re.I)
    for splitter in design.splitters:
        splitter.outputs = [new_id if re.fullmatch(
            rf"RSP[-\s]+{number}", (value or "").strip(), re.I) else value
            for value in (splitter.outputs or [])]
    for edge in design.connections:
        if edge.source.device_id == old_id:
            edge.source = DevicePortRef(new_id, edge.source.port_id)
        if edge.target.device_id == old_id:
            edge.target = DevicePortRef(new_id, edge.target.port_id)
    for mapping in (design.device_location_ids, design.location_sync_values):
        for prefix in ("RSP", "PS"):
            old_ref, new_ref = f"{prefix}-{number}", f"{prefix}-{new_number}"
            if old_ref in mapping:
                mapping[new_ref] = mapping.pop(old_ref)
    document = design.riser_document
    if document is not None:
        old_key, new_key = f"device:{old_id}", f"device:{new_id}"
        element = document.elements.pop(old_key, None)
        if element is not None:
            element.id, element.ref = new_key, new_id
            document.elements[new_key] = element
        document.z_order = [new_key if key == old_key else key for key in document.z_order]
        document.unplaced = [new_id if ref == old_id else ref for ref in document.unplaced]
    design.rsps.sort(key=lambda r: r.number)
    design.power_supplies.sort(key=lambda p: p.number)
    sync_master_zones(design)
    return rsp


def remove_expander(design: DMPDesign, number: int) -> None:
    rsp = next((r for r in design.rsps if r.number == number), None)
    if rsp is None:
        raise HardwareError(f"No expander #{number} in this design.")
    from session import ensure_editable_zones
    ensure_editable_zones(design, merge_missing=True)
    # A malformed import can claim a zone twice. Removing one module must not
    # erase a surviving module's point. Never infer ownership from module ID.
    surviving_zones = {zone for other in design.rsps if other is not rsp
                       for zone in other.zones}
    block = set(rsp.zones) - surviving_zones
    design.rsps.remove(rsp)
    design.power_supplies = [p for p in design.power_supplies if p.number != number]
    design.zones = [z for z in design.zones if z.number not in block]
    design.master_zones = [z for z in design.master_zones if z.number not in block]
    _scrub_splitter_outputs(design, f"RSP-{number}")
    _scrub_splitter_outputs(design, f"RSP {number}")


# -------- splitters --------

_SPLITTER_NUM_RE = re.compile(r"(\d+)\s*$")
_LX_BUS_ID_RE = re.compile(r"^710-LX(\d{3})-", re.I)
_BUS_INPUT_RE = re.compile(r"(\d{3})\s*BUS", re.I)


def _splitter_id(splitter_type: str, n: int, *, existing_id: str | None = None) -> str:
    if existing_id and _SPLITTER_NUM_RE.search(existing_id):
        return _SPLITTER_NUM_RE.sub(str(n), existing_id)
    return f"710-LX500-{n}" if splitter_type == "LX" else f"710-KP-{n}"


def _splitter_number(splitter: Splitter) -> int | None:
    m = _SPLITTER_NUM_RE.search(splitter.id or "")
    return int(m.group(1)) if m else None


def _splitter_bus(splitter: Splitter) -> str | None:
    """Return an LX splitter's electrical bus, including legacy IDs."""
    if splitter.splitter_type != "LX":
        return None
    match = _LX_BUS_ID_RE.match(splitter.id or "")
    if match:
        return match.group(1)
    for value in (splitter.inputs or {}).values():
        match = _BUS_INPUT_RE.search(value or "")
        if match:
            return match.group(1)
    # Legacy LX-710-N identifiers do not encode the bus.  Historical files
    # without a bus-bearing input were LX500 designs.
    return "500"


def _move_rsp_zone_addresses(design: DMPDesign, moves: dict[int, int],
                             moving_rsps: list[RSP]) -> None:
    """Validate every destination before changing any zone representation."""
    if not moves:
        return
    occupied = ({zone for rsp in design.rsps for zone in rsp.zones}
                | {zone.number for zone in design.zones}
                | {zone.number for zone in design.master_zones}) - moves.keys()
    conflicts = occupied.intersection(moves.values())
    if conflicts or len(set(moves.values())) != len(moves):
        raise HardwareError(
            f"Destination zone addresses are occupied: "
            f"{', '.join(map(str, sorted(conflicts))) or 'overlapping RSP ranges'}. "
            "No changes were made.")
    for rsp in moving_rsps:
        rsp.zones = [moves[zone] for zone in rsp.zones]
    for zone in design.zones:
        zone.number = moves.get(zone.number, zone.number)
    design.zones.sort(key=lambda zone: zone.number)
    for zone in design.master_zones:
        zone.number = moves.get(zone.number, zone.number)
    design.master_zones.sort(key=lambda zone: zone.number)
    moving_numbers = {rsp.number for rsp in moving_rsps}
    for ps in design.power_supplies:
        if ps.number in moving_numbers:
            ps.relays = {relay: re.sub(
                r"(?i)(\bZone\s+)(\d+)",
                lambda match: match.group(1) + str(moves.get(int(match.group(2)), int(match.group(2)))),
                label) for relay, label in ps.relays.items()}


def sync_rsp_zone_buses(design: DMPDesign) -> int:
    """Repair RSP ranges whose splitter feed already names a different LX bus."""
    by_id = {splitter.id: splitter for splitter in design.splitters}
    feeds = {edge.target.device_id: edge.source
             for edge in getattr(design, "connections", [])
             if edge.target.port_id == "IN" and edge.target.device_id in by_id}

    def fed_bus(splitter: Splitter, seen: set[str] | None = None) -> int:
        seen = set() if seen is None else seen
        if splitter.id in seen:
            return int(_splitter_bus(splitter))
        seen.add(splitter.id)
        source = feeds.get(splitter.id)
        if source is not None:
            match = (re.fullmatch(r"LX([5-9]00)", source.port_id, re.I)
                     if source.device_id == "MSP" else None)
            if match:
                return int(match.group(1))
            parent = by_id.get(source.device_id)
            if parent is not None:
                return fed_bus(parent, seen)
        for value in (splitter.inputs or {}).values():
            match = re.match(r"^\s*From\s+(.+?)\s*$", value or "", re.I)
            parent = by_id.get(match.group(1)) if match else None
            if parent is not None:
                return fed_bus(parent, seen)
        return int(_splitter_bus(splitter))

    targets: dict[int, int] = {}
    for splitter in design.splitters:
        if splitter.splitter_type != "LX":
            continue
        bus = fed_bus(splitter)
        for output in splitter.outputs or []:
            match = re.fullmatch(r"RSP[\s_-]*(\d+)", (output or "").strip(), re.I)
            if match:
                number = int(match.group(1))
                if number in targets and targets[number] != bus:
                    raise HardwareError(f"RSP-{number} is connected to more than one LX bus.")
                targets[number] = bus
    attached_rsps: dict[int, list[RSP]] = {}
    for rsp in design.rsps:
        target = targets.get(rsp.number)
        if target is not None and rsp.zones:
            attached_rsps.setdefault(target, []).append(rsp)

    groups: dict[int, list[RSP]] = {}
    for target, rsps in attached_rsps.items():
        ordered = sorted(rsps, key=lambda rsp: rsp.number)
        in_bus = all(target <= zone < target + 100
                     for rsp in ordered for zone in rsp.zones)
        in_order = all(max(previous.zones) < min(current.zones)
                       for previous, current in zip(ordered, ordered[1:]))
        if not in_bus or not in_order:
            groups[target] = ordered

    moving_rsps = [rsp for group in groups.values() for rsp in group]
    source_addresses = {zone for rsp in moving_rsps for zone in rsp.zones}
    occupied = ({zone for rsp in design.rsps for zone in rsp.zones}
                | {zone.number for zone in design.zones}
                | {zone.number for zone in design.master_zones}) - source_addresses
    moves: dict[int, int] = {}
    for target, group in sorted(groups.items()):
        offsets: dict[int, list[int]] = {}
        for rsp in group:
            first = min(rsp.zones)
            current = first if first % 100 == 0 else (first // 100) * 100
            offsets[rsp.number] = [zone + target - current for zone in rsp.zones]
        proposed = [zone for rsp in group for zone in offsets[rsp.number]]
        offsets_ordered = all(max(offsets[previous.number]) < min(offsets[current.number])
                              for previous, current in zip(group, group[1:]))
        if (offsets_ordered and len(set(proposed)) == len(proposed)
                and all(target <= zone < target + 100 and zone not in occupied
                        for zone in proposed)):
            placements = offsets
        else:
            # An imported module can straddle a boundary or the old ranges can
            # be out of RSP order. Pack whole modules by their RSP number.
            placements = {}
            reserved = set(occupied)
            for rsp in group:
                count = len(rsp.zones)
                starts = [*range(target + 1, target + 101 - count), target]
                block = next((list(range(start, start + count)) for start in starts
                              if reserved.isdisjoint(range(start, start + count))), None)
                if block is None:
                    raise HardwareError(
                        f"No room for all attached RSPs on LX{target}; "
                        "zone ranges were not changed.")
                placements[rsp.number] = block
                reserved.update(block)
        for rsp in group:
            moves.update(zip(sorted(rsp.zones), placements[rsp.number]))
            occupied.update(placements[rsp.number])
    _move_rsp_zone_addresses(design, moves, moving_rsps)
    return len(moving_rsps)


def _used_numbers(design: DMPDesign, splitter_type: str,
                  exclude: Splitter | None = None, *, bus: str | None = None) -> set[int]:
    """Trailing numbers already taken by splitters of this type."""
    if splitter_type == "LX":
        bus = bus or "500"
    used = set()
    for s in design.splitters:
        if s.splitter_type != splitter_type or s is exclude:
            continue
        if splitter_type == "LX" and _splitter_bus(s) != bus:
            continue
        n = _splitter_number(s)
        if n is not None:
            used.add(n)
    return used


def add_splitter(design: DMPDesign, splitter_type: str,
                 location: str | None = None, *, lx_bus: str = "500") -> Splitter:
    if splitter_type not in ("LX", "KP"):
        raise HardwareError(f"Unknown splitter type: {splitter_type}")
    if splitter_type == "LX" and lx_bus not in {"500", "600", "700", "800", "900"}:
        raise HardwareError("LX bus must be one of 500, 600, 700, 800, or 900.")
    same_type = [s for s in design.splitters if s.splitter_type == splitter_type]
    if len(same_type) >= MAX_SPLITTERS_PER_TYPE:
        raise HardwareError(
            f"The splitter sheet fits at most {MAX_SPLITTERS_PER_TYPE} "
            f"{splitter_type} splitters."
        )
    used = _used_numbers(design, splitter_type, bus=lx_bus)
    n = 1
    while n in used:
        n += 1
    splitter_id = (f"710-LX{lx_bus}-{n}" if splitter_type == "LX"
                   else _splitter_id(splitter_type, n))
    splitter = Splitter(id=splitter_id,
                        splitter_type=splitter_type, location=location,
                        outputs=["Spare", "Spare", "Spare"])
    design.splitters.append(splitter)
    return splitter


def remove_splitter(design: DMPDesign, splitter_id: str) -> None:
    splitter = next((s for s in design.splitters if s.id == splitter_id), None)
    if splitter is None:
        raise HardwareError(f"No splitter {splitter_id} in this design.")
    design.splitters.remove(splitter)
    _scrub_splitter_outputs(design, f"To {splitter_id}")
    # Keypads fed from this splitter need a new source — blank it so the
    # keypad.source_missing rule walks the tech back here before FINAL.
    for kp in design.keypads:
        if (kp.source or "").strip() == splitter_id:
            kp.source = None


def renumber_splitter(design: DMPDesign, splitter_id: str,
                      new_number: int, *, lx_bus: str | None = None) -> Splitter:
    """Change a splitter number and optionally its LX bus.

    Direct MSP feeds and the zone addresses of attached RSPs follow the
    selected bus. Existing destination addresses are never overwritten.

    The parser carries the diagram's numbering verbatim, so a missed splitter
    leaves a gap the tech can only patch with an out-of-order add. This lets
    them reassign the number directly. The number is a foreign key — keypad
    sources and other splitters' 'To …'/'From …' tokens reference the id by
    string — so every reference is rewritten atomically, mirroring how
    remove_splitter scrubs them.
    """
    splitter = next((s for s in design.splitters if s.id == splitter_id), None)
    if splitter is None:
        raise HardwareError(f"No splitter {splitter_id} in this design.")
    if lx_bus is not None and (
            splitter.splitter_type != "LX" or lx_bus not in {"500", "600", "700", "800", "900"}):
        raise HardwareError("LX bus must be one of 500, 600, 700, 800, or 900 for an LX splitter.")
    bus = lx_bus or _splitter_bus(splitter)
    if _splitter_number(splitter) == new_number and bus == _splitter_bus(splitter):
        return splitter  # no-op
    if not 1 <= new_number <= MAX_SPLITTERS_PER_TYPE:
        raise HardwareError(
            f"Splitter number must be between 1 and {MAX_SPLITTERS_PER_TYPE}."
        )
    new_id = (f"710-LX{bus}-{new_number}" if lx_bus is not None else _splitter_id(
        splitter.splitter_type, new_number, existing_id=splitter.id))
    # Legacy LX-710-N names do not encode their bus. A chained input may
    # therefore resolve to a different numbering pool even when the final ID
    # collides. IDs are graph/layout keys, so reject exact collisions first.
    if any(s is not splitter and s.id == new_id for s in design.splitters):
        raise HardwareError(f"{new_id} already exists. Pick a free number.")
    if new_number in _used_numbers(
            design, splitter.splitter_type, exclude=splitter,
            bus=bus):
        raise HardwareError(f"{new_id} already exists. Pick a free number.")

    zone_moves: dict[int, int] = {}
    moving_rsps: list[RSP] = []
    if lx_bus is not None and bus != _splitter_bus(splitter):
        target_bus = int(bus)
        attached = {int(match.group(1)) for output in splitter.outputs or []
                    if (match := re.fullmatch(r"RSP[\s_-]*(\d+)", (output or "").strip(), re.I))}
        for rsp in design.rsps:
            if rsp.number not in attached or not rsp.zones:
                continue
            first = min(rsp.zones)
            current_bus = first if first % 100 == 0 else (first // 100) * 100
            if any(not current_bus <= zone < current_bus + 100 for zone in rsp.zones):
                raise HardwareError(
                    f"RSP-{rsp.number} crosses a bus boundary; "
                    "review its physical addresses before changing the bus.")
            if current_bus != target_bus:
                moving_rsps.append(rsp)
                zone_moves.update({zone: zone + target_bus - current_bus for zone in rsp.zones})

    old_id = splitter.id
    _move_rsp_zone_addresses(design, zone_moves, moving_rsps)
    splitter.id = new_id
    _retoken_splitter_refs(design, old_id, new_id)
    if lx_bus is not None:
        splitter.inputs = {key: re.sub(
            r"\b[5-9]00(?=\s+BUS\s+IN\s+FROM\s+XR/550)", bus, value, flags=re.I)
            for key, value in (splitter.inputs or {}).items()}

    # Schema-2 connections are canonical. Renumber their endpoint references
    # in place so cable IDs, metadata, and manual routes remain stable.
    from riser_model import DevicePortRef
    for edge in getattr(design, "connections", []):
        if edge.source.device_id == old_id:
            edge.source = DevicePortRef(new_id, edge.source.port_id)
        if edge.target.device_id == old_id:
            if lx_bus is not None and edge.source.device_id == "MSP" and re.fullmatch(r"LX[5-9]00", edge.source.port_id):
                edge.source = DevicePortRef("MSP", f"LX{bus}")
            edge.target = DevicePortRef(new_id, edge.target.port_id)

    document = getattr(design, "riser_document", None)
    if document is not None:
        old_key, new_key = f"device:{old_id}", f"device:{new_id}"
        element = document.elements.pop(old_key, None)
        if element is not None:
            element.id = new_key
            element.ref = new_id
            document.elements[new_key] = element
            document.z_order = [new_key if item == old_key else item
                                for item in document.z_order]
        document.unplaced = [new_id if item == old_id else item
                             for item in document.unplaced]
    design.splitters.sort(key=lambda s: (s.splitter_type, _splitter_number(s) or 0))
    return splitter


# -------- keypads --------

def add_keypad(design: DMPDesign, location: str | None = None,
               source: str | None = None, global_keypad: bool = False) -> Keypad:
    if len(design.keypads) >= MAX_KEYPADS:
        raise HardwareError(f"The keypad sheet fits at most {MAX_KEYPADS} keypads.")
    used = {k.number for k in design.keypads}
    n = 1
    while n in used:
        n += 1
    keypad = Keypad(number=n, source=source, location=location,
                    global_keypad=global_keypad)
    design.keypads.append(keypad)
    design.keypads.sort(key=lambda k: k.number)
    return keypad


def remove_keypad(design: DMPDesign, number: int) -> None:
    keypad = next((k for k in design.keypads if k.number == number), None)
    if keypad is None:
        raise HardwareError(f"No keypad #{number} in this design.")
    design.keypads.remove(keypad)
    _scrub_splitter_outputs(design, f"KEYPAD #{number}")


# -------- shared --------

def existing_locations(design: DMPDesign) -> list[str]:
    """Distinct location strings already used anywhere in the design.

    Feeds the add-hardware dialogs' autocomplete so a tech reuses a room they
    already named instead of retyping it (a typo would split one room into two
    location strings). RSP and its paired power supply share a room, so the
    case-insensitive de-dupe collapses them to one suggestion.
    """
    seen: dict[str, str] = {}
    for obj in (*design.rsps, *design.power_supplies,
                *design.splitters, *design.keypads):
        loc = (getattr(obj, "location", None) or "").strip()
        if loc:
            seen.setdefault(loc.lower(), loc)   # keep first-seen casing
    return sorted(seen.values(), key=str.lower)


@dataclass
class CascadeChange:
    """A wiring reference auto-downgraded by a hardware removal, for the editor
    to surface so the tech reviews it instead of discovering it at finalize."""
    tab: str        # "SPLITTERS" | "KEYPADS" — which editor tab to route to
    message: str    # human description of what changed


def snapshot_refs(design: DMPDesign) -> dict:
    """Capture the wiring references a removal might silently rewrite.

    Pair with diff_refs() around a remove_* call: the scrubbers flip dependent
    splitter outputs to 'Spare' and blank keypad sources in place, destroying
    the evidence, so we snapshot before and compare after.
    """
    return {
        "outputs": {s.id: list(s.outputs or []) for s in design.splitters},
        "sources": {k.number: k.source for k in design.keypads},
    }


def diff_refs(before: dict, after: dict) -> list[CascadeChange]:
    """References that a removal downgraded, as routable CascadeChange items.

    Only reports surviving hardware whose wiring changed — a splitter/keypad
    that was itself removed (absent from `after`) is the intended deletion, not
    collateral, so it's skipped.
    """
    changes: list[CascadeChange] = []
    after_outputs = after["outputs"]
    for sid, outs in before["outputs"].items():
        new_outs = after_outputs.get(sid)
        if new_outs is None:
            continue                      # this splitter was the one removed
        for i, (old, new) in enumerate(zip(outs, new_outs)):
            if old != new and (new or "").strip() == "Spare":
                changes.append(CascadeChange(
                    "SPLITTERS", f"{sid} output {i + 1} is now Spare (was {old})"))
    sentinel = object()
    after_sources = after["sources"]
    for num, src in before["sources"].items():
        new_src = after_sources.get(num, sentinel)
        if new_src is sentinel:
            continue                      # this keypad was the one removed
        if src and not new_src:
            changes.append(CascadeChange(
                "KEYPADS", f"Keypad #{num} lost its source (was {src})"))
    return changes


def _scrub_splitter_outputs(design: DMPDesign, token: str) -> None:
    """Replace outputs that pointed at removed hardware with 'Spare'."""
    for s in design.splitters:
        s.outputs = ["Spare" if (o or "").strip() == token else o
                     for o in (s.outputs or [])]


def _retoken_splitter_refs(design: DMPDesign, old_id: str, new_id: str) -> None:
    """Repoint every reference to a renumbered splitter from old_id to new_id.

    Outputs/inputs hold whole-string tokens ('To 710-LX500-2', 'From …') and
    keypad sources hold the bare id, so exact-match replacement is correct and
    avoids partial-number collisions ('710-LX500-1' vs '710-LX500-10')."""
    for s in design.splitters:
        s.outputs = [f"To {new_id}" if (o or "").strip() == f"To {old_id}" else o
                     for o in (s.outputs or [])]
        s.inputs = {k: (f"From {new_id}" if (v or "").strip() == f"From {old_id}" else v)
                    for k, v in (s.inputs or {}).items()}
    for kp in design.keypads:
        if (kp.source or "").strip() == old_id:
            kp.source = new_id
