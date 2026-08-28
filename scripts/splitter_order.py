"""Shared presentation order for splitter cards and worksheet sections.

Sorting returns a new list: storage order and electrical connections are never
changed to accommodate the order in which a technician reads the devices.
"""

import re


def lx_bus_number(splitter, by_id=None, seen=None) -> str:
    """Resolve modern or legacy LX naming, including downstream legacy IDs."""
    match = re.match(r"^710-LX(\d{3})-", splitter.id or "", re.I)
    if match:
        return match.group(1)
    value = next((v for v in (splitter.inputs or {}).values() if v and v.strip()), "")
    match = re.match(r"^\s*(\d+)\s*BUS\b", value, re.I)
    if match:
        return match.group(1)
    if value.lower().startswith("from ") and by_id:
        seen = set() if seen is None else seen
        if splitter.id not in seen:
            seen.add(splitter.id)
            parent = by_id.get(value[5:].strip())
            if parent is not None:
                return lx_bus_number(parent, by_id, seen)
    return "500"


def ordered_splitters(splitters):
    """LX by bus then device number, followed by KP by device number."""
    splitters = list(splitters)
    by_id = {item.id: item for item in splitters}

    def key(item):
        family = item.splitter_type.upper()
        bus = int(lx_bus_number(item, by_id)) if family == "LX" else 0
        suffix = item.id.rsplit("-", 1)[-1]
        number = int(suffix) if suffix.isdigit() else float("inf")
        return ({"LX": 0, "KP": 1}.get(family, 2), bus, number, item.id)

    return sorted(splitters, key=key)
