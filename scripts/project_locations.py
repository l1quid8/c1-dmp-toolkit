"""Shared equipment-location edits; never rename arbitrary zone descriptions."""
from __future__ import annotations

import re
import uuid
from collections import Counter

from location_model import EquipmentLocation, location_key, is_unresolved
from location_model import legacy_normal_location as _normal_location


# A direction such as HALLWAY (E) is valid; a cable model following (E)/(N)
# or a bare West Penn/AQC model is not an equipment location.
_CABLE_LOCATION_RE = re.compile(
    r'\([NE]\)\s*(?:\(\d+\)\s*)?[A-Z]+\d+|\b(?:WP|AQC)\s*\d+', re.I)


def _retained_location_ids(design):
    identities = set(design.device_location_ids.values())
    identities.update(key for key, record in design.equipment_locations.items() if record.confirmed)
    if design.riser_document:
        identities.update(element.physical_location_id
                          for element in design.riser_document.elements.values()
                          if element.kind == 'location' and element.manual)
    return identities


def is_equipment_location_name(value):
    """Keep manual entry and reusable choices under the same location rules."""
    return bool(not is_unresolved(value) and re.search(r'\w', value or '')
                and location_key(value) not in {'(E)', '(N)', 'LOCATION NEEDS REVIEW'}
                and not _CABLE_LOCATION_RE.search(value))


def equipment_location_choices(design):
    """Offer actual equipment rooms, not edit history or imported cable labels."""
    retained = _retained_location_ids(design)
    candidates = [(identity, ' '.join(record.full_label.split()))
                  for identity, record in design.equipment_locations.items()
                  if identity in retained and is_equipment_location_name(record.full_label)]
    counts = Counter(location_key(label) for _, label in candidates)
    choices = {}
    for identity, label in sorted(candidates, key=lambda item: (location_key(item[1]), item[0])):
        if counts[location_key(label)] > 1:
            members = sorted(ref for ref, key in design.device_location_ids.items() if key == identity)
            label += ' · ' + (', '.join(members) or 'Unassigned')
        base = label
        suffix = 2
        while label in choices:
            label = f'{base} ({suffix})'
            suffix += 1
        choices[label] = identity
    return choices


def _location(design, ref):
    fields = location_fields(design)
    if ref not in fields:
        return 'UNSPECIFIED'
    obj, attr = fields[ref]
    value = getattr(obj, attr)
    if ref.startswith('RSP-') and location_key(value) in {'MSP', 'AT MSP', 'SAME AS MSP'}:
        return design.site_info.xr550_location or 'MSP'
    return value or ('MSP' if ref == 'MSP' else 'UNSPECIFIED')


def location_fields(design):
    """Stable hardware references to the authoritative writable location fields."""
    result = {'MSP': (design.site_info, 'xr550_location')}
    result.update({s.id: (s, 'location') for s in design.splitters})
    for prefix, items in [('RSP', design.rsps), ('KEYPAD', design.keypads),
                          ('PS', design.power_supplies)]:
        result.update({f'{prefix}-{item.number}': (item, 'location') for item in items})
    return result


def location_values(design):
    return {ref: getattr(obj, attr) for ref, (obj, attr) in location_fields(design).items()}


def restore_locations(design, values, refs):
    fields = location_fields(design)
    for ref in refs:
        if ref in fields and ref in values:
            obj, attr = fields[ref]
            setattr(obj, attr, values[ref])


def sync_project_locations(design):
    """Reconcile legacy single-device edits without rewriting imported text."""
    values = location_values(design)
    records = design.equipment_locations
    bindings = design.device_location_ids
    previous = design.location_sync_values
    for ref in sorted(values, key=lambda r: (r != 'MSP', r.startswith('PS-'), r)):
        value = values[ref]
        if ref.startswith('RSP-') and location_key(value) in {'MSP', 'AT MSP', 'SAME AS MSP'}:
            bindings[ref] = bindings['MSP']
            continue
        if ref.startswith('PS-') and f'RSP-{ref[3:]}' in bindings:
            bindings[ref] = bindings[f'RSP-{ref[3:]}']
            continue
        if bindings.get(ref) in records and ref in previous and previous[ref] == value:
            continue
        label = re.sub(r'\s*\(\s*SERVICE\s+KEYPAD\s*\)', '', value or '', flags=re.I).strip()
        unresolved = is_unresolved(label)
        matches = sorted(key for key, record in records.items()
                         if not unresolved and location_key(record.full_label) == location_key(label))
        if matches:
            identity = matches[0]
        else:
            seed = f'unknown:{ref}' if unresolved else f'location:{location_key(label)}'
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'c1-dmp:' + seed))
            # A renamed record retains its ID. Never overwrite it when the
            # original spelling is later assigned to another physical room.
            suffix = 0
            while identity in records:
                suffix += 1
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f'c1-dmp:{seed}:{suffix}'))
            records[identity] = EquipmentLocation(identity, label)
        bindings[ref] = identity
    design.device_location_ids = {ref: identity for ref, identity in bindings.items() if ref in values}
    design.location_sync_values = dict(values)
    # Location entry fields synchronize on each keystroke. Their previous text
    # is not a reusable room once nothing refers to it. Undo keeps its own full
    # registry snapshot; confirmed rooms and manual drawing frames remain valid.
    retained = _retained_location_ids(design)
    for identity in set(records) - retained:
        del records[identity]


def _write_assignments(design, refs, identity):
    label = design.equipment_locations[identity].full_label
    values = location_values(design)
    updates = {}
    for ref in refs:
        if ref not in values:
            continue
        suffix = ' (Service Keypad)' if re.search(
            r'\(\s*SERVICE\s+KEYPAD\s*\)', values[ref] or '', re.I) else ''
        updates[ref] = label + suffix
        design.device_location_ids[ref] = identity
    restore_locations(design, updates, updates)
    design.location_sync_values = location_values(design)


def assign_location(design, device_id, location_id):
    sync_project_locations(design)
    if device_id not in location_fields(design) or location_id not in design.equipment_locations:
        raise ValueError('Choose current equipment and an existing location.')
    refs = [device_id]
    if device_id.startswith('RSP-'):
        refs.append('PS-' + device_id[4:])
    if device_id.startswith('PS-'):
        refs.append('RSP-' + device_id[3:])
    _write_assignments(design, refs, location_id)
    sync_project_locations(design)


def assign_location_name(design, device_id, name):
    """Create or reuse a room only when a complete assignment is committed."""
    label = ' '.join((name or '').split())
    if not is_equipment_location_name(label):
        raise ValueError('Enter a location name, such as a building or room.')
    if device_id not in location_fields(design):
        raise ValueError('Choose current equipment to assign a location.')
    sync_project_locations(design)
    if device_id.startswith(('RSP-', 'PS-')) and location_key(label) in {'MSP', 'AT MSP', 'SAME AS MSP'}:
        identity = design.device_location_ids['MSP']
        assign_location(design, device_id, identity)
        return identity
    matches = [identity for identity, record in design.equipment_locations.items()
               if location_key(record.full_label) == location_key(label)]
    current = design.device_location_ids.get(device_id)
    if current in matches:
        identity = current
    elif len(matches) > 1:
        raise ValueError('Several locations use that name. Choose one from the list.')
    elif matches:
        identity = matches[0]
    else:
        identity = str(uuid.uuid4())
        design.equipment_locations[identity] = EquipmentLocation(
            identity, label, room=label, confirmed=True)
    assign_location(design, device_id, identity)
    return identity


def rename_equipment_location(design, identity, label, *, allow_merge=False):
    label = ' '.join(label.split())
    if not label or not re.search(r'\w', label):
        raise ValueError('Enter a location name.')
    record = design.equipment_locations[identity]
    target = next((key for key, other in sorted(design.equipment_locations.items())
                   if key != identity and not is_unresolved(label)
                   and location_key(other.full_label) == location_key(label)), None)
    if target and not allow_merge:
        raise ValueError('That location already exists; confirm combining the groups.')
    refs = [ref for ref, key in design.device_location_ids.items() if key == identity]
    if target:
        _write_assignments(design, refs, target)
        # Keep the old record available for undo, but not as a duplicate choice.
        del design.equipment_locations[identity]
        return target
    if record.full_label != label:
        record.full_label = label
        record.building = record.floor = record.room = ''
        record.confirmed = False
    _write_assignments(design, refs, identity)
    return identity


def edit_location(design, identity, *, building, floor, room, allow_merge=False):
    components = [' '.join(value.split()) for value in (building, floor, room)]
    label = ' '.join(value for value in components if value)
    identity = rename_equipment_location(design, identity, label, allow_merge=allow_merge)
    record = design.equipment_locations[identity]
    record.building, record.floor, record.room = components
    record.confirmed = True
    return identity


def plan_location_rename(design, document, element_id, new_name):
    """Return an exact, previewable update set and whether it joins another room.

    Current frame membership includes already-recognized spelling variants.
    Matching unplaced hardware is included, as are the paired RSP power supplies.
    Building substrings alone never count as a matching location.
    """
    new_name = ' '.join(new_name.split())
    if not new_name or not re.search(r'\w', new_name):
        raise ValueError('Enter a location name.')
    frame = document.elements.get(element_id)
    if frame is None or frame.kind != 'location' or frame.stale:
        raise ValueError('Select a current equipment location first.')
    fields = location_fields(design)
    values = location_values(design)
    members = {e.ref for e in document.elements.values()
               if e.kind == 'device' and not e.stale and e.location_id == element_id
               and e.ref in fields}
    if not members:
        raise ValueError('This location has no current equipment to rename.')
    if frame.physical_location_id:
        members = {ref for ref, identity in design.device_location_ids.items()
                   if identity == frame.physical_location_id and ref in fields}
    effective = {ref: (_location(design, ref) if not ref.startswith('PS-') else value)
                 for ref, value in values.items()}
    source_keys = {_normal_location(frame.ref)} | {
        _normal_location(effective[ref]) for ref in members}
    affected = members | {ref for ref, value in effective.items()
                          if _normal_location(value) in source_keys}
    if frame.physical_location_id:
        affected = members
    affected |= {f'PS-{ref[4:]}' for ref in tuple(affected)
                 if ref.startswith('RSP-') and f'PS-{ref[4:]}' in fields}
    destination = _normal_location(new_name)
    merging = any(ref not in affected and _normal_location(value) == destination
                  for ref, value in effective.items())
    if frame.physical_location_id:
        merging = any(key != frame.physical_location_id and location_key(record.full_label) == location_key(new_name)
                      for key, record in design.equipment_locations.items())
    updates = {}
    for ref in sorted(affected):
        # Preserve the role suffix, which isn't part of the physical room name.
        suffix = ' (Service Keypad)' if re.search(
            r'\(\s*SERVICE\s+KEYPAD\s*\)', values[ref] or '', re.I) else ''
        value = new_name + suffix if suffix and not re.search(
            r'\(\s*SERVICE\s+KEYPAD\s*\)', new_name, re.I) else new_name
        if value != values[ref]:
            updates[ref] = value
    if updates:
        primary = 'MSP' if 'MSP' in updates else next(iter(updates))
        identity = frame.physical_location_id or design.device_location_ids.get(primary)
        merging |= any(key != identity and not is_unresolved(new_name)
                       and location_key(record.full_label) == location_key(new_name)
                       for key, record in design.equipment_locations.items())
    return updates, merging
