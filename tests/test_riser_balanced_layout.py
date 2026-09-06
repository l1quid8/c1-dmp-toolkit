"""Default layouts reserve readable tiers instead of packing a flat shelf."""
import copy
import pytest
from test_riser_scene import three_rsp_chain_design

from test_riser_scene import DMPDesign, SiteInfo, Splitter, RSP, DevicePortRef, connect
from riser_scene import layout_riser, validate_riser
from riser_scene import route_connection
import riser_scene
from riser_scene import _place_route_labels, _route_label_box, _segment_hits_rect, _location_heading_box
from riser_model import RiserElement


def fanout_school():
    design = DMPDesign(site_info=SiteInfo(school_name='BALANCED', school_code='1',
                                        xr550_location='MDF', address_line1='1 SCHOOL ST'),
        splitters=[Splitter('710-LX500-1', 'LX', 'HUB', outputs=['Spare']*3)],
        rsps=[RSP(n, name, [501+(n-1)*8]) for n, name in
              enumerate(('WEST', 'CENTER', 'EAST'), 1)])
    connect(design, DevicePortRef('MSP', 'LX500'), DevicePortRef('710-LX500-1', 'IN'))
    for n in range(1, 4):
        connect(design, DevicePortRef('710-LX500-1', f'OUT{n}'), DevicePortRef(f'RSP-{n}', 'IN'))
    return design


def test_fanout_siblings_share_a_clear_tier_below_their_parent():
    design = fanout_school()
    before = copy.deepcopy(design.connections)
    doc = layout_riser(design)
    parent = doc.elements['location:HUB']
    children = [doc.elements[f'location:{name}'] for name in ('WEST', 'CENTER', 'EAST')]
    assert len({child.y for child in children}) == 1
    assert children[0].y >= parent.y + parent.height + 72
    assert children[0].x < children[1].x < children[2].x  # OUT1, OUT2, OUT3
    middle = (children[0].x + children[-1].x + children[-1].width) / 2
    assert abs(parent.x + parent.width/2 - middle) <= 18
    assert design.connections == before
    assert not [i for i in validate_riser(design, doc)
                if i.code in {'scene.overlap', 'scene.off_page', 'scene.label_overlap'}]


def test_layout_does_not_depend_on_imported_connection_or_device_order():
    design = fanout_school()
    first = layout_riser(design)
    design.connections.reverse()
    design.rsps.reverse()
    second = layout_riser(design)
    assert first.elements == second.elements
    assert {k: v.points for k, v in first.routes.items()} == {k: v.points for k, v in second.routes.items()}


def test_generated_labels_do_not_cover_other_wiring():
    design = fanout_school()
    doc = layout_riser(design)
    first, second = list(doc.routes.values())[:2]
    first.points = [(100, 100), (400, 100)]
    second.points = [(250, 50), (250, 150)]
    _place_route_labels(design, doc)
    edge = next(e for e in design.connections if e.id == first.connection_id)
    x1,y1,x2,y2 = _route_label_box(edge, first)
    rect = RiserElement('test','location','test',x1,y1,x2-x1,y2-y1)
    assert not _segment_hits_rect(*second.points, rect, clearance=0)


@pytest.mark.parametrize('factory', [fanout_school, three_rsp_chain_design])
def test_generated_wires_do_not_strike_through_location_headings(factory):
    doc = layout_riser(factory())
    for module in (e for e in doc.elements.values() if e.kind == 'location'):
        x1,y1,x2,y2 = _location_heading_box(module)
        rect = RiserElement('test','location','test',x1,y1,x2-x1,y2-y1)
        assert not any(_segment_hits_rect(a,b,rect,clearance=0)
                       for route in doc.routes.values() for a,b in zip(route.points,route.points[1:]))


@pytest.mark.parametrize('blocked_corners', [False, True])
def test_clear_manhattan_route_does_not_search_a_whole_visibility_graph(monkeypatch, blocked_corners):
    expansions = []
    original = riser_scene.heapq.heappop
    def pop(queue):
        expansions.append(1)
        return original(queue)
    monkeypatch.setattr(riser_scene.heapq, 'heappop', pop)
    obstacles = [RiserElement('far','device','far',1000,1000,100,100)]
    if blocked_corners:
        obstacles += [RiserElement(str(x),'device',str(x),x,240,20,20) for x in (90,390)]
    points = route_connection((100,100), (400,400), obstacles)
    assert points[0] == (100,100) and points[-1] == (400,400)
    assert sum(abs(b[0]-a[0])+abs(b[1]-a[1]) for a,b in zip(points,points[1:])) == 600
    assert not expansions, 'An unobstructed one-bend path still built/searched the full graph'
