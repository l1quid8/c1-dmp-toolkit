"""One-sheet location groups arranged around readable electrical bus spines.

This is presentation geometry only. The project graph and canonical location
bindings remain the source of truth, including cycles and disconnected devices.
"""
import copy

from riser_cluster_layout import cluster_heading, natural_key
from riser_drawing import location_text_runs
from riser_model import RiserDocument, RiserElement, RiserRoute, default_riser_document
from riser_symbols import grouped_size


# Two equipment shelves flank each central bus. The wider right inner shelf
# accommodates the usual co-located 710 + RSP pair without separating a room.
_LANES = {
    'KP outer': (68., 350.),
    'KP inner': (444., 410.),
    'LX inner': (1316., 470.),
    'LX outer': (1812., 375.),
}
_FIT_CODES = {'scene.off_page', 'scene.overlap', 'scene.heading_overlap',
              'scene.location_overlap', 'scene.cable_through_device',
              'scene.cable_through_heading',
              'scene.caption_overlap', 'scene.label_overlap',
              'scene.uncovered_intersection', 'print.legibility', 'title.overflow'}


def _electrical_order(design, ids):
    """A spanning forest gives placement order, never a replacement topology."""
    from riser_scene import _connection_sort_key
    outgoing = {ref: [] for ref in ids}
    for edge in sorted(design.connections, key=_connection_sort_key):
        if edge.source.device_id in outgoing and edge.target.device_id in ids:
            outgoing[edge.source.device_id].append(edge)
    splitter_types = {item.id: item.splitter_type.upper() for item in design.splitters}
    depth, branch, order = {}, {}, {}
    pending = [('MSP', 0, 'HEAD')] if 'MSP' in ids else []
    while pending or len(order) < len(ids):
        if not pending:
            root = min(ids - order.keys(), key=natural_key)
            pending.append((root, 1, 'KP' if root.startswith('KEYPAD-')
                            or splitter_types.get(root) == 'KP' else 'LX'))
        ref, level, side = pending.pop(0)
        if ref in order:
            continue
        depth[ref], branch[ref], order[ref] = level, side, len(order)
        for edge in outgoing[ref]:
            target = edge.target.device_id
            if target in order:
                continue
            if ref == 'MSP':
                child_side = 'KP' if edge.source.port_id in {'KP BUS', 'PROG'} else 'LX'
            else:
                child_side = side
            pending.append((target, level + 1, child_side))
    return depth, branch, order


def _reference_branch_slots(design, ids):
    """Identify the approved two-bus, paired-RSP topology by connections.

    Slots describe electrical roles, not device numbers or room names. Other
    topologies retain the general packing algorithm below.
    """
    links = {(e.source.device_id, e.source.port_id): e.target.device_id
             for e in design.connections}
    def child(ref, port):
        return links.get((ref, port))
    service = child('MSP', 'KP BUS')
    kp1 = child('MSP', 'PROG')
    lx1 = child('MSP', 'LX500')
    if not all((service, kp1, lx1)):
        return None
    kp2 = child(kp1, 'OUT3')
    lx2 = child(lx1, 'OUT3')
    lx3 = child(lx2, 'OUT1')
    lx4 = child(lx2, 'OUT2')
    lx5 = child(lx3, 'OUT2')
    lx7 = child(lx3, 'OUT3')
    lx6 = child(lx5, 'OUT2') if lx5 else None
    slots = {
        'MSP': (920., 90.), service: (590., 128.),
        kp1: (389., 478.), child(kp1, 'OUT1'): (104., 816.),
        child(kp1, 'OUT2'): (650., 816.), kp2: (441., 1137.),
        child(kp2, 'OUT1') if kp2 else None: (226., 1381.),
        lx1: (1609., 206.), lx2: (1943., 209.),
        child(lx1, 'OUT1'): (1071., 421.),
        child(lx1, 'OUT2'): (1513., 421.),
        lx3: (1521., 762.), lx4: (1942., 758.),
        child(lx3, 'OUT1') if lx3 else None: (1087., 829.),
        child(lx4, 'OUT1') if lx4 else None: (1795., 947.),
        lx5: (863., 1218.), lx6: (1311., 1215.),
        lx7: (1793., 1215.),
        child(lx5, 'OUT1') if lx5 else None: (712., 1456.),
        child(lx6, 'OUT1') if lx6 else None: (1162., 1456.),
        child(lx7, 'OUT1') if lx7 else None: (1644., 1458.),
    }
    if None in slots or set(slots) != ids or len(slots) != 21:
        return None
    splitters = {item.id for item in design.splitters}
    rsps = {f'RSP-{item.number}' for item in design.rsps}
    keypads = {f'KEYPAD-{item.number}' for item in design.keypads}
    if (not {kp1, kp2, lx1, lx2, lx3, lx4, lx5, lx6, lx7} <= splitters
            or not {service, child(kp1, 'OUT1'), child(kp1, 'OUT2'),
                    child(kp2, 'OUT1')} <= keypads
            or not {child(lx1, 'OUT1'), child(lx1, 'OUT2'),
                    child(lx3, 'OUT1'), child(lx4, 'OUT1'),
                    child(lx5, 'OUT1'), child(lx6, 'OUT1'),
                    child(lx7, 'OUT1')} <= rsps):
        return None
    return slots


def _group_members(design, order):
    splitters = {item.id for item in design.splitters}
    groups = {}
    for ref in order:
        identity = design.device_location_ids[ref]
        groups.setdefault(identity, []).append(ref)
    for members in groups.values():
        # Keep the through-bus device on the same spine when a room also has
        # a terminal RSP or keypad. Siblings retain their electrical order.
        members.sort(key=lambda ref: (ref != 'MSP', ref not in splitters,
                                      order[ref], natural_key(ref)))
    return groups


def _lane_for(members, branch, splitters):
    first = min(members, key=lambda ref: branch['order'][ref])
    side = branch['side'][first]
    if side == 'HEAD':
        side = 'KP' if any(ref.startswith(('KEYPAD-', '710-KP-')) for ref in members) else 'LX'
    splitter = any(ref in splitters for ref in members)
    return side + (' inner' if splitter else ' outer')


def _place_group(doc, design, identity, members, x, y, width, sizes):
    """Wrap measured cards inside one location; return its actual height."""
    record = design.equipment_locations[identity]
    key = 'location:' + identity
    frame = RiserElement(key, 'location', record.full_label or 'LOCATION UNCONFIRMED',
                         x, y, width, 1.,
                         physical_location_id=identity,
                         heading_lines=cluster_heading(record, members))
    runs = location_text_runs(frame, small=True)
    frame.heading_height = max((run.y - y + run.size * .3 for run in runs), default=30.) + 12.
    rows, row, used = [], [], 0.
    usable = width - 32.
    for ref in members:
        w, _h = sizes[ref]
        need = w + (16. if row else 0.)
        if row and used + need > usable:
            rows.append(row)
            row, used = [], 0.
            need = w
        row.append(ref)
        used += need
    if row:
        rows.append(row)
    cursor_y = y + frame.heading_height + 10.
    for row_index, row in enumerate(rows):
        row_width = sum(sizes[ref][0] for ref in row) + 16. * (len(row) - 1)
        cursor_x = x + max(16., (width - row_width) / 2)
        height = max(sizes[ref][1] for ref in row)
        for ref in row:
            w, h = sizes[ref]
            doc.elements['device:' + ref] = RiserElement(
                'device:' + ref, 'device', ref, cursor_x, cursor_y, w, h,
                location_id=key, symbol_style='grouped')
            cursor_x += w + 16.
        # A crowded head-end needs an actual wiring/label band below the MSP
        # outputs; a tiny row gap drives cables back through the panel body.
        row_gap = 64. if 'MSP' in members and row_index < len(rows) - 1 else 12.
        cursor_y += height + row_gap
    frame.height = cursor_y - y
    doc.elements[key] = frame
    return frame.height


def _candidate(design, groups, tree, *, compact, title_source):
    from riser_scene import (MSP_OUTPUT_X, _compact_route, _connection_sort_key,
                             _place_route_labels, _route_segments, _segment_hits_rect, port_point,
                             route_connection, route_topology_connection,
                             routing_obstacles, validate_riser)
    doc = default_riser_document(design)
    doc.layout_version = 3
    doc.show_location_frames = True
    if title_source:
        doc.title_block = copy.deepcopy(title_source.title_block)
        doc.annotations = copy.deepcopy(title_source.annotations)
    sizes = {ref: grouped_size(design, ref, compact=compact) for ref in tree['order']}
    head = design.device_location_ids.get('MSP')
    top = 42.
    if head in groups:
        # A co-located head-end may span several rows, but remains one room.
        head_width = 900. if len(groups[head]) > 1 else 560.
        height = _place_group(doc, design, head, groups[head],
                              1126. - head_width / 2, top, head_width, sizes)
        top += height + 16.
    else:
        top = 286.
    lanes = {name: [] for name in _LANES}
    splitter_ids = {item.id for item in design.splitters}
    for identity, members in groups.items():
        if identity == head:
            continue
        lane = _lane_for(members, {'order': tree['order'], 'side': tree['branch']},
                         splitter_ids)
        lanes[lane].append(identity)
    for lane, identities in lanes.items():
        x, width = _LANES[lane]
        y = top
        identities.sort(key=lambda identity: (
            min(tree['depth'][ref] for ref in groups[identity]),
            min(tree['order'][ref] for ref in groups[identity]),
            natural_key(design.equipment_locations[identity].full_label)))
        probe = RiserDocument()
        heights = [_place_group(probe, design, identity, groups[identity],
                                x, 0., width, sizes) for identity in identities]
        clearance = (34. if compact else 40.) if lane.endswith('inner') else (8. if compact else 14.)
        if len(identities) > 1:
            # Sparse sheets can use their height for traceable, balanced
            # branches. Dense sheets keep only the clearances they can afford.
            spare = 1692. - top - sum(heights)
            clearance = max(clearance, min(180., .85 * spare / (len(identities) - 1)))
        for identity in identities:
            height = _place_group(doc, design, identity, groups[identity],
                                  x, y, width, sizes)
            y += height + clearance
    panel = doc.elements.get('device:MSP')
    if panel:
        panel.port_x = dict(MSP_OUTPUT_X)
    for edge in design.connections:
        target = doc.elements.get('device:' + edge.target.device_id)
        source = doc.elements.get('device:' + edge.source.device_id)
        if not source or not target:
            continue
        if (target.ref.startswith('RSP-') and any(
                other.kind == 'device' and other.ref in splitter_ids
                and other.location_id == target.location_id
                and abs(other.y - target.y) < 1 and other.x < target.x
                for other in doc.elements.values())):
            target.input_side = 'left'
        elif source.location_id == target.location_id and abs(source.y - target.y) < 1:
            target.input_side = 'left' if target.x > source.x else 'right'
        elif target.ref.startswith('KEYPAD-') and target.x < source.x:
            target.input_side = 'right'
    scene_ids = sorted(doc.elements, key=lambda key: (
        doc.elements[key].kind != 'location', natural_key(key)))
    live = set(scene_ids) | {annotation.id for annotation in doc.annotations}
    order = [key for key in (title_source.z_order if title_source else []) if key in live]
    doc.z_order = order + [key for key in scene_ids + [a.id for a in doc.annotations]
                           if key not in order]
    obstacles = routing_obstacles(doc, design)
    reserved = []
    for edge in sorted(design.connections, key=_connection_sort_key):
        source = doc.elements.get('device:' + edge.source.device_id)
        target = doc.elements.get('device:' + edge.target.device_id)
        if not source or not target:
            continue
        # A remotely fed RSP can sit beside a splitter in its room. Its left
        # input is only a narrow gap from that splitter; enter the gap from
        # below instead of letting the general router cut through the 710.
        neighbor = next((item for item in doc.elements.values()
                         if item.kind == 'device' and item.ref in splitter_ids
                         and item.location_id == target.location_id
                         and abs(item.y - target.y) < 1 and item.x < target.x), None)
        if (panel and source.ref == 'MSP' and source.location_id == target.location_id
                and target.input_side == 'left' and target.x > source.x):
            start = port_point(source, edge.source.port_id, output=True)
            end = port_point(target, edge.target.port_id, output=False)
            head_members = [item for item in doc.elements.values()
                            if item.kind == 'device' and item.location_id == panel.location_id]
            entry_x = target.x - 8.
            points = None
            for below in (max(item.y + item.height for item in head_members) + 4.,
                          source.y + source.height + 24.):
                proposed = _compact_route([start, (start[0], below),
                                           (entry_x, below), (entry_x, end[1]), end])
                if not any(_segment_hits_rect(a, b, item, clearance=0.)
                           for a, b in _route_segments(proposed) for item in obstacles
                           if item.ref not in {source.ref, target.ref}):
                    points = proposed
                    break
            if points is None:
                points = route_topology_connection(edge, source, target, obstacles,
                                                   reserved_segments=reserved)
        elif (panel and source.location_id == panel.location_id
                and tree['branch'].get(target.ref) == 'LX' and target.x < 900
                and source.y < target.y):
            # A room can contain both KP and LX descendants. Keep its one
            # location group, but bring the LX feed across the clear central
            # corridor, not through the KP spine or its sibling routes.
            start = port_point(source, edge.source.port_id, output=True)
            end = port_point(target, edge.target.port_id, output=False)
            head_members = [item for item in doc.elements.values()
                            if item.kind == 'device' and item.location_id == panel.location_id]
            exit_x = max(item.x + item.width for item in head_members) + 18.
            exit_y = max(item.y + item.height for item in head_members) + 18.
            proposed = _compact_route([start, (start[0], start[1] + 18.),
                                       (exit_x, start[1] + 18.), (exit_x, exit_y),
                                       (1060., exit_y), (1060., end[1] - 5.),
                                       (end[0], end[1] - 5.), end])
            if not any(_segment_hits_rect(a, b, item, clearance=0.)
                       for a, b in _route_segments(proposed) for item in obstacles
                       if item.ref not in {source.ref, target.ref}):
                points = proposed
            else:
                points = route_topology_connection(edge, source, target, obstacles,
                                                   reserved_segments=reserved)
        elif (neighbor and target.ref.startswith('RSP-')
                and source.location_id != target.location_id
                and target.input_side == 'left'):
            start = port_point(source, edge.source.port_id, output=True)
            end = port_point(target, edge.target.port_id, output=False)
            start_lead = (start[0], start[1] + 24.)
            gap_x = (neighbor.x + neighbor.width + target.x) / 2
            bottom_y = max(neighbor.y + neighbor.height,
                           target.y + target.height) + 18.
            middle = route_connection(start_lead, (gap_x, bottom_y), obstacles,
                                      reserved_segments=reserved)
            points = _compact_route([start, start_lead, *middle,
                                     (gap_x, end[1]), end])
        else:
            points = route_topology_connection(edge, source, target, obstacles,
                                               reserved_segments=reserved)
        previous = title_source.routes.get(edge.id) if title_source else None
        doc.routes[edge.id] = RiserRoute(edge.id, points,
                                        label_hidden=previous.label_hidden if previous else False)
        reserved.extend(_route_segments(points))
    _place_route_labels(design, doc)
    issues = [issue for issue in validate_riser(design, doc) if issue.code in _FIT_CODES]
    hard = sum(issue.code in {'scene.off_page', 'scene.overlap', 'scene.heading_overlap',
                              'scene.location_overlap', 'scene.cable_through_device',
                              'scene.cable_through_heading',
                              'scene.caption_overlap', 'print.legibility', 'title.overflow'}
               for issue in issues)
    score = (hard, len(issues), int(compact))
    return score, doc, issues


def layout_presentation(design, *, title_source=None):
    """Present the actual circuit as two familiar, top-down schematic trees."""
    from project_locations import sync_project_locations
    from riser_scene import (MSP_OUTPUT_X, _connection_sort_key, _device_ids,
                             _compact_route, _place_route_labels, _route_contacts,
                             _route_segments,
                             _segment_hits_rect, port_point,
                             route_topology_connection, routing_obstacles,
                             validate_riser)
    from riser_symbols import detailed_size
    from riser_symbols import caption_boxes
    isolated = copy.deepcopy(design)
    sync_project_locations(isolated)
    ids = set(_device_ids(isolated))
    depth, branch, order = _electrical_order(isolated, ids)
    doc = default_riser_document(isolated)
    doc.layout_version = 3
    doc.show_location_frames = False
    if title_source:
        doc.title_block = copy.deepcopy(title_source.title_block)
        doc.annotations = copy.deepcopy(title_source.annotations)

    # Preserve the original symbol language and electrical reading direction.
    # Each depth is a tier; a target always starts below its source. The KP
    # branch lives to the left of the LX/RSP branch rather than being mixed
    # into location cards.
    sizes = {ref: detailed_size(isolated, ref) for ref in ids}
    reference_slots = _reference_branch_slots(isolated, ids)
    root = 'MSP'
    root_w, root_h = sizes[root]
    doc.elements['device:MSP'] = RiserElement(
        'device:MSP', 'device', root,
        reference_slots[root][0] if reference_slots else 1122. - root_w / 2,
        reference_slots[root][1] if reference_slots else 38.,
        root_w, root_h, symbol_style='detailed', port_x=dict(MSP_OUTPUT_X))
    if reference_slots:
        for ref, (x, y) in reference_slots.items():
            if ref == root:
                continue
            width, height = sizes[ref]
            doc.elements['device:' + ref] = RiserElement(
                'device:' + ref, 'device', ref, x, y, width, height,
                symbol_style='detailed')
    regions = {'KP': (100., 840.), 'LX': (875., 2220.)}
    for side in (() if reference_slots else ('KP', 'LX')):
        tier_y = 38. + root_h + 44.
        for level in sorted(set(depth.values()) - {0}):
            refs = sorted((ref for ref in ids if depth[ref] == level
                           and branch[ref] == side), key=lambda ref: order[ref])
            left, right = regions[side]
            rows, row, used = [], [], 0.
            for ref in refs:
                width = sizes[ref][0]
                required = width + (28. if row else 0.)
                if row and used + required > right - left:
                    rows.append(row)
                    row, used = [], 0.
                    required = width
                row.append(ref)
                used += required
            if row:
                rows.append(row)
            for row_index, refs in enumerate(rows):
                row_height = max(sizes[ref][1] for ref in refs)
                refs = rows[row_index]
                device_width = sum(sizes[ref][0] for ref in refs)
                free_width = right - left - device_width
                gap = (max(28., min(110., (free_width - 48.) / (len(refs) - 1)))
                       if len(refs) > 1 else 0.)
                total = device_width + gap * (len(refs) - 1)
                cursor = left + max(0., (right - left - total) / 2)
                placed = []
                for ref in refs:
                    width, height = sizes[ref]
                    item = RiserElement(
                        'device:' + ref, 'device', ref, cursor, tier_y,
                        width, height, symbol_style='detailed')
                    doc.elements[item.id] = item
                    placed.append(item)
                    cursor += width + gap
                # Keypad room captions sit below their physical symbol. Give
                # that visible text space before starting the next tier.
                caption_bottom = max((box[3] for item in placed
                                      for box in caption_boxes(isolated, item)),
                                     default=tier_y + row_height)
                base_gap = 18. if row_index + 1 < len(rows) else (50. if side == 'KP' else 24.)
                tier_y = max(tier_y + row_height + base_gap, caption_bottom + 12.)

    if not reference_slots:
        # Smaller trees otherwise occupy only the top half of the sheet. Use
        # the remaining vertical room for the wire bands between depth tiers.
        devices = [item for item in doc.elements.values() if item.kind == 'device']
        bottom = max((max([item.y + item.height] +
                          [box[3] for box in caption_boxes(isolated, item)])
                      for item in devices), default=0.)
        anchor = doc.elements['device:MSP'].y + root_h
        if anchor < bottom < doc.page_height - 260.:
            stretch = min(1.45, max(1., (doc.page_height - 200. - anchor) /
                                    (bottom - anchor)))
            for item in devices:
                if item.ref != 'MSP' and item.y >= anchor:
                    item.y = anchor + (item.y - anchor) * stretch

    # Keep canonical ownership available for editing, but leave the boxes
    # hidden: the original drawing conveys location through device captions.
    groups = _group_members(isolated, order)
    for identity, members in groups.items():
        devices = [doc.elements['device:' + ref] for ref in members]
        left = min(item.x for item in devices) - 12.
        top = min(item.y for item in devices) - 12.
        right = max(item.x + item.width for item in devices) + 12.
        bottom = max(item.y + item.height for item in devices) + 12.
        record = isolated.equipment_locations[identity]
        key = 'location:' + identity
        frame = RiserElement(
            key, 'location', record.full_label or 'LOCATION UNCONFIRMED',
            left, top, right - left, bottom - top,
            physical_location_id=identity,
            heading_lines=cluster_heading(record, members))
        runs = location_text_runs(frame, small=True)
        frame.heading_height = max((run.y - frame.y + run.size * .3
                                    for run in runs), default=30.) + 12.
        doc.elements[key] = frame
        for item in devices:
            item.location_id = key

    scene_ids = sorted(doc.elements, key=lambda key: (
        doc.elements[key].kind != 'location', natural_key(key)))
    live = set(scene_ids) | {annotation.id for annotation in doc.annotations}
    order_ids = [key for key in (title_source.z_order if title_source else []) if key in live]
    doc.z_order = order_ids + [key for key in scene_ids + [a.id for a in doc.annotations]
                              if key not in order_ids]
    obstacles = routing_obstacles(doc, isolated)
    reserved = []
    for edge in sorted(isolated.connections, key=_connection_sort_key):
        source = doc.elements.get('device:' + edge.source.device_id)
        target = doc.elements.get('device:' + edge.target.device_id)
        if source is None or target is None:
            continue
        points = route_topology_connection(edge, source, target, obstacles,
                                           reserved_segments=reserved)
        # The general router can choose a straight drop through a wide RSP
        # when a downstream splitter sits just below that RSP row. Search
        # actual free corridors before accepting such a route.
        blocking = [item for item in obstacles if item.id not in {source.id, target.id}]
        def crosses(items):
            return any(_segment_hits_rect(a, b, item, clearance=2.)
                       for a, b in _route_segments(items) for item in blocking)
        if crosses(points):
            start = port_point(source, edge.source.port_id, output=True)
            end = port_point(target, edge.target.port_id, output=False)
            candidates = sorted({item.x - 13. for item in blocking} |
                                {item.x + item.width + 13. for item in blocking},
                                key=lambda x: (abs(x - start[0]), x))
            for corridor in candidates:
                if not 32. < corridor < 2212.:
                    continue
                proposed = _compact_route([
                    start, (start[0], start[1] + 6.),
                    (corridor, start[1] + 6.),
                    (corridor, end[1] - 12.),
                    (end[0], end[1] - 12.), end])
                if not crosses(proposed):
                    points = proposed
                    break
        previous = title_source.routes.get(edge.id) if title_source else None
        doc.routes[edge.id] = RiserRoute(
            edge.id, points,
            label_hidden=previous.label_hidden if previous else False)
        reserved.extend(_route_segments(points))
    # Two sibling wires can leave their final drop on the same horizontal
    # channel. Separate those runs so the printed drawing does not imply a
    # false electrical junction where no bridge hop can fit.
    for _ in range(6):
        uncovered = [contact for contact in _route_contacts(
            {key: route.points for key, route in doc.routes.items()})
            if contact.bridge is None]
        if not uncovered:
            break
        changed = False
        for contact in uncovered:
            for route_id in (contact.first_connection_id,
                             contact.second_connection_id):
                route = doc.routes[route_id]
                for index in range(1, len(route.points) - 2):
                    a, b = route.points[index:index + 2]
                    if (a[1] != b[1] or a[1] != contact.y or
                            not min(a[0], b[0]) <= contact.x <= max(a[0], b[0])):
                        continue
                    for offset in (-4., 4., -6., 6., -12., 12.,
                                   -22., 22., -36., 36.):
                        proposed = list(route.points)
                        proposed[index] = (a[0], a[1] + offset)
                        proposed[index + 1] = (b[0], b[1] + offset)
                        if any(_segment_hits_rect(p, q, item, clearance=2.)
                               for p, q in _route_segments(proposed)
                               for item in obstacles
                               if item.ref not in {
                                   next(edge for edge in isolated.connections
                                        if edge.id == route_id).source.device_id,
                                   next(edge for edge in isolated.connections
                                        if edge.id == route_id).target.device_id}):
                            continue
                        contacts = _route_contacts({
                            **{key: current.points for key, current in doc.routes.items()
                               if key != route_id}, route_id: proposed})
                        if any(item.bridge is None for item in contacts):
                            continue
                        route.points = proposed
                        changed = True
                        break
                    if changed:
                        break
                if changed:
                    break
            if changed:
                break
        if not changed:
            break
    _place_route_labels(isolated, doc)
    issues = [issue for issue in validate_riser(isolated, doc) if issue.code in _FIT_CODES]
    if issues:
        doc.fit_warnings = list(dict.fromkeys(
            f'{issue.ref or "drawing"}: {issue.message}' for issue in issues))[:12]
    return doc
