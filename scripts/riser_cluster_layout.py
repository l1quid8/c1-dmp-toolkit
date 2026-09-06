"""Pure physical-room clustering; the existing router remains shared."""
import copy
import re

from location_model import is_unresolved
from project_locations import sync_project_locations


def natural_key(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part.upper())
                 for part in re.split(r'(\d+)', value))


def cluster_heading(record, members):
    if is_unresolved(record.full_label):
        return [', '.join(sorted(members, key=natural_key)), 'LOCATION UNCONFIRMED']
    if record.confirmed:
        return [part for part in (record.building, ' · '.join(
            part for part in (record.floor, record.room) if part)) if part]
    return [record.full_label]


def layout_clusters(design, *, title_source=None):
    from riser_scene import layout_riser, validate_riser, PAGE_MARGIN
    isolated = copy.deepcopy(design)
    isolated.splitters.sort(key=lambda item: natural_key(item.id))
    isolated.rsps.sort(key=lambda item: item.number)
    isolated.keypads.sort(key=lambda item: item.number)
    sync_project_locations(isolated)
    candidates = []
    for spacing in [(72.0, 90.0), (54.0, 72.0), (36.0, 54.0)]:
        scene = layout_riser(isolated, title_source=copy.deepcopy(title_source),
                             _cluster_records=isolated.equipment_locations,
                             _cluster_bindings=isolated.device_location_ids,
                             _cluster_spacing=spacing)
        issues = validate_riser(isolated, scene)
        # Keep branch order fixed. Score spacing candidates by hard clearance
        # first, independent contacts next, then prefer the roomier profile.
        overflow = sum(max(0, e.y + e.height - scene.page_height + PAGE_MARGIN)
                       for e in scene.elements.values())
        clearance = sum(i.code in {'scene.off_page', 'scene.overlap', 'scene.heading_overlap',
                                   'scene.cable_through_device', 'scene.location_overlap'} for i in issues)
        contacts = sum(i.code in {'scene.uncovered_intersection', 'scene.label_overlap'} for i in issues)
        candidates.append(((clearance, overflow, contacts, len(candidates)), scene))
        if overflow == 0 and clearance == 0:
            break  # No need to tighten an already legible, on-page scene.
    return min(candidates, key=lambda item: item[0])[1]
