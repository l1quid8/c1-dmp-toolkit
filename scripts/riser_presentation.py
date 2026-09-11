"""Electrical-first layout for the approved detailed-symbol presentation.

Physical ownership remains shared with the project. Invisible location frames
are editing aids, not a constraint forcing unrelated electrical levels together.
"""
import copy

from riser_model import RiserElement, RiserRoute, default_riser_document
from riser_symbols import detailed_size, caption_boxes
from riser_cluster_layout import natural_key, cluster_heading


def layout_presentation(design, *, title_source=None):
    from project_locations import sync_project_locations
    from riser_scene import (_device_ids, _connection_sort_key, routing_obstacles,
                             route_topology_connection, _route_segments, _place_route_labels,
                             MSP_OUTPUT_X)
    isolated=copy.deepcopy(design)
    sync_project_locations(isolated)
    doc=default_riser_document(isolated)
    doc.layout_version=3
    doc.show_location_frames=(title_source.show_location_frames
                              if title_source and title_source.layout_version>=3 else False)
    if title_source:
        doc.title_block=copy.deepcopy(title_source.title_block)
        doc.annotations=copy.deepcopy(title_source.annotations)
    ids=set(_device_ids(isolated))
    sizes={ref:detailed_size(isolated,ref) for ref in ids}
    packing_width={ref:max(sizes[ref][0],300 if ref.startswith('KEYPAD-') else 0) for ref in ids}
    children={ref:[] for ref in ids}
    # A deterministic spanning forest is used only for geometry. All actual
    # edges (including cycles/invalid endpoints) remain in the project graph.
    assigned={'MSP'}; depth={'MSP':0}; pending=['MSP']
    edge_order=sorted(isolated.connections,key=lambda e:(
        0 if e.source.port_id in {'KP BUS','PROG'} else 1,
        natural_key(e.source.port_id),natural_key(e.target.device_id),e.id))
    while len(assigned)<len(ids) or pending:
        if not pending:
            root=min(ids-assigned,key=natural_key)
            assigned.add(root); depth[root]=1; pending.append(root)
        ref=pending.pop(0)
        for e in edge_order:
            target=e.target.device_id
            if e.source.device_id!=ref or target not in ids or target in assigned:
                continue
            children[ref].append(target); assigned.add(target)
            depth[target]=depth[ref]+1; pending.append(target)
    service=next((e.target.device_id for e in edge_order if e.source.device_id=='MSP'
                  and e.source.port_id=='KP BUS' and e.target.device_id.startswith('KEYPAD-')
                  and e.target.device_id in children['MSP'] and not children[e.target.device_id]),None)
    if service:
        children['MSP'].remove(service); depth[service]=0
    widths={}
    def measure(ref):
        widths[ref]=max(packing_width[ref],sum(measure(c) for c in children[ref])+
                        120*max(0,len(children[ref])-1))
        return widths[ref]
    roots=sorted(ids-{c for values in children.values() for c in values}-{service},
                 key=lambda ref:(ref!='MSP',natural_key(ref)))
    total=sum(measure(ref) for ref in roots)+120*max(0,len(roots)-1)
    left,right=90.,doc.page_width-348-54
    available=right-left
    preferred={}
    def position(ref,x):
        preferred[ref]=x+widths[ref]/2
        child_width=sum(widths[c] for c in children[ref])+120*max(0,len(children[ref])-1)
        cursor=x+(widths[ref]-child_width)/2
        for child in children[ref]:
            position(child,cursor); cursor+=widths[child]+120
    cursor=0
    for root in roots:
        position(root,cursor); cursor+=widths[root]+120
    preferred={ref:left+available/2+(x-total/2)*min(1,available/max(1,total))
               for ref,x in preferred.items()}
    # Measure captions as part of the footprint, then choose a packing that
    # fits the sheet before placing or routing any equipment.
    footprint_heights = {}
    for ref, (w, h) in sizes.items():
        probe = RiserElement(ref, 'device', ref, 0, 0, w, h, symbol_style='detailed')
        footprint_heights[ref] = max([h] + [box[3] for box in caption_boxes(isolated, probe)])

    def pack_rows(levels, spacing):
        packed = []
        for rank in sorted(set(levels.values())):
            members = sorted((r for r in ids if levels[r] == rank and r != service),
                             key=lambda r: (preferred[r], natural_key(r)))
            row, used = [], 0
            for ref in members:
                w = packing_width[ref]
                if row and used + spacing + w > available:
                    packed.append(row)
                    row, used = [], 0
                row.append(ref)
                used += w + (spacing if len(row) > 1 else 0)
            if row:
                packed.append(row)
        heights = [max(footprint_heights[r] for r in row) for row in packed]
        return packed, heights

    # Terminal equipment can sit beside its feeding splitter on dense sheets.
    # The splitter chain still flows down the page and wiring stays identical.
    compact_depth = dict(depth)
    for parent, leaves in children.items():
        if parent == 'MSP':
            continue
        for child in leaves:
            if not children[child] and child.startswith(('RSP-', 'KEYPAD-')):
                compact_depth[child] = depth[parent]
    for levels, spacing in ((depth, 90.), (depth, 54.), (compact_depth, 54.)):
        rows, heights = pack_rows(levels, spacing)
        y = 90. if len(rows) > 4 else 180.
        if y + sum(heights) + 36 * (len(rows)-1) <= doc.page_height - 54:
            break
    gap = min(190., max(36., (doc.page_height-y-54-sum(heights))/max(1,len(rows)-1)))
    columns = None
    if levels is compact_depth:
        columns = _branch_columns(children, roots, packing_width, footprint_heights,
                                  sizes, left, right, doc.page_height)
    for row,height in zip(rows,heights):
        centers=[]; cursor=left
        for ref in row:
            w=packing_width[ref]
            cx=max(preferred[ref],cursor+w/2)
            centers.append(cx); cursor=cx+w/2+spacing
        # Backward packing compresses only overflowing gaps, never translates
        # the whole rank off the left edge to accommodate its final device.
        limit=right
        for i in range(len(row)-1,-1,-1):
            w=packing_width[row[i]]
            centers[i]=min(centers[i],limit-w/2)
            limit=centers[i]-w/2-spacing
        for ref,cx in zip(row,centers):
            w,h=sizes[ref]
            identity=isolated.device_location_ids[ref]
            doc.elements['device:'+ref]=RiserElement('device:'+ref,'device',ref,cx-w/2,y,w,h,
                                                    location_id='location:'+identity,symbol_style='detailed')
        y+=height+gap
    if columns:
        for ref, (x, y) in columns.items():
            element = doc.elements['device:'+ref]
            element.x, element.y = x, y
    if service:
        panel=doc.elements['device:MSP']; w,h=sizes[service]
        panel.x=max(panel.x,left+w+150)
        identity=isolated.device_location_ids[service]
        # Keep the service keypad to the left of the panel's midpoint feed.
        doc.elements['device:'+service]=RiserElement('device:'+service,'device',service,
            panel.x-w-150,panel.y+(panel.height-h)/2,w,h,
            location_id='location:'+identity,symbol_style='detailed',input_side='right')
    panel=doc.elements['device:MSP']
    # Connected and unused outputs share one stable electrical order.
    panel.port_x=dict(MSP_OUTPUT_X)
    for identity,record in sorted(isolated.equipment_locations.items()):
        members=[e for e in doc.elements.values() if e.location_id=='location:'+identity]
        if not members: continue
        x=min(e.x for e in members)-24; y=min(e.y for e in members)-60
        right=max(e.x+e.width for e in members)+24
        bottom=max(max([e.y+e.height]+[b[3] for b in caption_boxes(isolated,e)]) for e in members)+24
        key='location:'+identity
        doc.elements[key]=RiserElement(key,'location',record.full_label,x,y,right-x,bottom-y,
                                      physical_location_id=identity,
                                      heading_lines=cluster_heading(record,[e.ref for e in members]),heading_height=36)
    from riser_scene import sync_cluster_ownership
    sync_cluster_ownership(isolated,doc)
    scene_ids=sorted(doc.elements,key=lambda k:(doc.elements[k].kind!='location',natural_key(k)))
    # Preserve markup's relative stacking with surviving logical objects.
    live=set(scene_ids)|{a.id for a in doc.annotations}
    order=[key for key in (title_source.z_order if title_source else []) if key in live]
    doc.z_order=order+[key for key in scene_ids+[a.id for a in doc.annotations] if key not in order]
    obstacles=routing_obstacles(doc,isolated)
    reserved=[]
    for edge in sorted(isolated.connections,key=_connection_sort_key):
        source=doc.elements.get('device:'+edge.source.device_id)
        target=doc.elements.get('device:'+edge.target.device_id)
        if not source or not target: continue
        if target.y == source.y and target.ref.startswith(('RSP-', 'KEYPAD-')):
            target.input_side = 'left' if target.x > source.x else 'right'
        points=route_topology_connection(edge,source,target,obstacles,reserved_segments=reserved)
        previous=title_source.routes.get(edge.id) if title_source else None
        doc.routes[edge.id]=RiserRoute(edge.id,points,label_hidden=previous.label_hidden if previous else False)
        reserved.extend(_route_segments(points))
        if columns:
            # A crossing needs straight cable on both sides for a bridge hop.
            # Keep later runs away from corners that would look like junctions.
            for index, (x, y) in enumerate(points[1:-1]):
                key = f'bend:{edge.id}:{index}'
                obstacles.append(RiserElement(key, 'cable-bend', key, x-1, y-1, 2, 2))
    _place_route_labels(isolated,doc)
    return doc


def _branch_columns(children, roots, packing_width, heights, sizes, left, right, page_height):
    """Pack dense bus trees into independent columns with aligned splitter spines."""
    branches = children['MSP'] + [ref for ref in roots if ref != 'MSP']
    if not branches:
        return None
    columns = []
    for branch in branches:
        groups = []
        def collect(ref):
            leaves = [child for child in children[ref]
                      if not children[child] and child.startswith(('RSP-', 'KEYPAD-'))]
            groups.append([ref, *leaves])
            for child in children[ref]:
                if child not in leaves:
                    collect(child)
        collect(branch)
        width = max(sum(packing_width[ref] for ref in group) + 54*(len(group)-1)
                    for group in groups)
        group_heights = [max(heights[ref] for ref in group) for group in groups]
        columns.append((groups, width, group_heights))
    gutter = 64.
    total_width = sum(width for _, width, _ in columns) + gutter*(len(columns)-1)
    panel_y = 54.
    top = panel_y + sizes['MSP'][1] + 54
    bottom = page_height - 72
    if total_width > right-left or any(
            sum(hs) + 30*(len(hs)-1) > bottom-top for _, _, hs in columns):
        return None
    result = {'MSP': ((left+right-sizes['MSP'][0])/2, panel_y)}
    x = left + (right-left-total_width)/2
    for groups, width, group_heights in columns:
        gap = min(100., (bottom-top-sum(group_heights))/max(1,len(groups)-1))
        y = top
        for group, height in zip(groups, group_heights):
            cursor = x
            for ref in group:
                result[ref] = (cursor+(packing_width[ref]-sizes[ref][0])/2, y)
                cursor += packing_width[ref]+54
            y += height+gap
        x += width+gutter
    return result
