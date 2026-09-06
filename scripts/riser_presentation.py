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
                             route_topology_connection, _route_segments, _place_route_labels)
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
    # Pack each electrical depth without shrinking symbols. Dense ranks wrap
    # onto additional rows; excessive depth remains a legibility warning.
    rows=[]
    for rank in sorted(set(depth.values())):
        members=sorted((r for r in ids if depth[r]==rank and r!=service),
                       key=lambda r:(preferred[r],natural_key(r)))
        row=[]; used=0
        for ref in members:
            w=packing_width[ref]
            if row and used+90+w>available:
                rows.append(row); row=[]; used=0
            row.append(ref); used+=w+(90 if len(row)>1 else 0)
        if row: rows.append(row)
    heights=[max(sizes[r][1]+(60 if r.startswith('KEYPAD-') else 0) for r in row) for row in rows]
    y=90. if len(rows)>4 else 180.
    gap=min(190.,max(36.,(doc.page_height-y-90-sum(heights))/max(1,len(rows)-1)))
    for row,height in zip(rows,heights):
        centers=[]; cursor=left
        for ref in row:
            w=packing_width[ref]
            cx=max(preferred[ref],cursor+w/2)
            centers.append(cx); cursor=cx+w/2+90
        # Backward packing compresses only overflowing gaps, never translates
        # the whole rank off the left edge to accommodate its final device.
        limit=right
        for i in range(len(row)-1,-1,-1):
            w=packing_width[row[i]]
            centers[i]=min(centers[i],limit-w/2)
            limit=centers[i]-w/2-90
        for ref,cx in zip(row,centers):
            w,h=sizes[ref]
            identity=isolated.device_location_ids[ref]
            doc.elements['device:'+ref]=RiserElement('device:'+ref,'device',ref,cx-w/2,y,w,h,
                                                    location_id='location:'+identity,symbol_style='detailed')
        y+=height+gap
    if service:
        panel=doc.elements['device:MSP']; w,h=sizes[service]
        panel.x=max(panel.x,left+w+150)
        identity=isolated.device_location_ids[service]
        # Keep the service keypad to the left of the panel's midpoint feed.
        doc.elements['device:'+service]=RiserElement('device:'+service,'device',service,
            panel.x-w-150,panel.y+(panel.height-h)/2,w,h,
            location_id='location:'+identity,symbol_style='detailed',input_side='right')
    panel=doc.elements['device:MSP']
    active=sorted({e.source.port_id for e in isolated.connections if e.source.device_id=='MSP'}-{'KP BUS'},key=natural_key)
    active.sort(key=lambda name:(name!='PROG',natural_key(name)))
    start,span=(.08,.84) if len(active)>3 else (.25,.5)
    panel.port_x={name:(.5 if len(active)==1 else start+span*i/(len(active)-1)) for i,name in enumerate(active)}
    unused=[name for name in ('PROG','LX500','LX600','LX700','LX800','LX900') if name not in panel.port_x]
    slots=[.08,.92,.16,.84,.4,.6,.5]
    for name in unused:
        slot=max(slots,key=lambda s:min(abs(s-p) for p in panel.port_x.values()) if panel.port_x else 1)
        panel.port_x[name]=slot; slots.remove(slot)
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
        points=route_topology_connection(edge,source,target,obstacles,reserved_segments=reserved)
        previous=title_source.routes.get(edge.id) if title_source else None
        doc.routes[edge.id]=RiserRoute(edge.id,points,label_hidden=previous.label_hidden if previous else False)
        reserved.extend(_route_segments(points))
    _place_route_labels(isolated,doc)
    return doc
