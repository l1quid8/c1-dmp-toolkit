"""Pure riser layout, routing, synchronization, and validation."""

from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from parse_dmp_worksheet import DMPDesign, Keypad, RSP, SiteInfo, Splitter  # noqa: E402
from riser_model import (  # noqa: E402
    DevicePortRef,
    RiserAnnotation,
    RiserElement,
    TopologyConnection,
)
from riser_scene import (  # noqa: E402
    TITLE_BLOCK_WIDTH,
    _collinear_overlap_length,
    _segment_hits_rect,
    find_bridges,
    layout_riser,
    port_point,
    route_connection,
    route_topology_connection,
    sync_riser_document,
    validate_riser,
)
from topology_service import connect  # noqa: E402


def branched_design() -> DMPDesign:
    d = DMPDesign(
        site_info=SiteInfo(
            school_name="BRANCH SCHOOL", school_code="4321",
            address_line1="123 SCHOOL ST", address_line2="LOS ANGELES, CA",
            xr550_location="MDF",
        ),
        splitters=[
            Splitter("710-KP-1", "KP", "MDF", outputs=["Spare"] * 3),
            Splitter("710-LX500-1", "LX", "MDF", outputs=["Spare"] * 3),
            Splitter("710-LX500-2", "LX", "CLASSROOM 27", outputs=["Spare"] * 3),
        ],
        rsps=[RSP(1, "MDF", [501]), RSP(2, "CLASSROOM 27", [517])],
        keypads=[Keypad(1, "MSP", "MDF"), Keypad(2, "710-KP-1", "OFFICE")],
    )
    connect(d, DevicePortRef("MSP", "KP BUS"), DevicePortRef("KEYPAD-1", "IN"))
    connect(d, DevicePortRef("MSP", "PROG"), DevicePortRef("710-KP-1", "IN"))
    connect(d, DevicePortRef("710-KP-1", "OUT1"), DevicePortRef("KEYPAD-2", "IN"))
    connect(d, DevicePortRef("MSP", "LX500"), DevicePortRef("710-LX500-1", "IN"))
    connect(d, DevicePortRef("710-LX500-1", "OUT1"), DevicePortRef("RSP-1", "IN"))
    connect(d, DevicePortRef("710-LX500-1", "OUT2"), DevicePortRef("710-LX500-2", "IN"))
    connect(d, DevicePortRef("710-LX500-2", "OUT1"), DevicePortRef("RSP-2", "IN"))
    return d


def three_rsp_chain_design() -> DMPDesign:
    """Small chained topology that must fit comfortably on one 24x36 sheet."""
    d = DMPDesign(
        site_info=SiteInfo(
            school_name="THREE RSP SCHOOL", school_code="1234",
            address_line1="1 TEST ST", xr550_location="MDF",
        ),
        splitters=[
            Splitter(f"710-LX500-{number}", "LX", f"ROOM {100 + number}",
                     outputs=["Spare"] * 3)
            for number in range(1, 4)
        ],
        rsps=[
            RSP(number, f"ROOM {100 + number}", [500 + number * 16])
            for number in range(1, 4)
        ],
        keypads=[Keypad(1, "MSP", "MDF")],
    )
    connect(d, DevicePortRef("MSP", "KP BUS"), DevicePortRef("KEYPAD-1", "IN"))
    connect(d, DevicePortRef("MSP", "LX500"), DevicePortRef("710-LX500-1", "IN"))
    for number in range(1, 4):
        connect(
            d, DevicePortRef(f"710-LX500-{number}", "OUT1"),
            DevicePortRef(f"RSP-{number}", "IN"),
        )
        if number < 3:
            connect(
                d, DevicePortRef(f"710-LX500-{number}", "OUT2"),
                DevicePortRef(f"710-LX500-{number + 1}", "IN"),
            )
    return d


def mixed_location_order_design() -> DMPDesign:
    """A shallow source whose target shares a room with a deeper branch."""
    d = DMPDesign(
        site_info=SiteInfo(
            school_name="ORDER SCHOOL", school_code="1234",
            address_line1="1 TEST ST", xr550_location="MDF",
        ),
        splitters=[
            Splitter("710-LX500-1", "LX", "MDF", outputs=["Spare"] * 3),
            Splitter("710-LX500-2", "LX", "SOURCE", outputs=["Spare"] * 3),
            Splitter("710-LX500-3", "LX", "TARGET", outputs=["Spare"] * 3),
            Splitter("710-LX500-4", "LX", "TARGET", outputs=["Spare"] * 3),
        ],
        rsps=[RSP(1, "TARGET", [501]), RSP(2, "TARGET", [517])],
        keypads=[Keypad(1, "MSP", "MDF")],
    )
    edges = [
        (("MSP", "KP BUS"), ("KEYPAD-1", "IN")),
        (("MSP", "LX500"), ("710-LX500-1", "IN")),
        (("710-LX500-1", "OUT1"), ("710-LX500-2", "IN")),
        (("710-LX500-1", "OUT2"), ("RSP-1", "IN")),
        (("710-LX500-2", "OUT1"), ("710-LX500-3", "IN")),
        (("710-LX500-3", "OUT1"), ("710-LX500-4", "IN")),
        (("710-LX500-4", "OUT1"), ("RSP-2", "IN")),
    ]
    for source, target in edges:
        connect(d, DevicePortRef(*source), DevicePortRef(*target))
    return d


def legacy_named_splitter_design() -> DMPDesign:
    d = DMPDesign(
        site_info=SiteInfo(
            school_name="LEGACY SCHOOL", school_code="1234",
            address_line1="1 TEST ST", xr550_location="MDF",
        ),
        splitters=[Splitter("KP-710-1", "KP", "MDF", outputs=["Spare"] * 3)],
        keypads=[Keypad(1, "MSP", "MDF"), Keypad(2, "KP-710-1", "OFFICE")],
    )
    connect(d, DevicePortRef("MSP", "KP BUS"), DevicePortRef("KEYPAD-1", "IN"))
    connect(d, DevicePortRef("MSP", "PROG"), DevicePortRef("KP-710-1", "IN"))
    connect(d, DevicePortRef("KP-710-1", "OUT1"), DevicePortRef("KEYPAD-2", "IN"))
    return d


def test_layout_is_deterministic_balanced_and_groups_shared_locations():
    first = layout_riser(branched_design())
    second = layout_riser(branched_design())

    assert first == second
    msp = first.elements["device:MSP"]
    kp = first.elements["device:710-KP-1"]
    lx = first.elements["device:710-LX500-1"]
    assert kp.x < msp.x < lx.x
    assert first.elements["location:MDF"].ref == "MDF"
    assert first.elements["device:RSP-1"].x >= first.elements["location:MDF"].x
    assert first.routes and all(route.points for route in first.routes.values())


def test_layout_keeps_location_modules_compact_and_non_overlapping():
    document = layout_riser(branched_design())
    locations = [element for element in document.elements.values()
                 if element.kind == "location"]

    assert document.elements["location:MDF"].width < 1000
    for index, first in enumerate(locations):
        for second in locations[index + 1:]:
            assert not (first.x < second.x + second.width and
                        first.x + first.width > second.x and
                        first.y < second.y + second.height and
                        first.y + first.height > second.y)


def test_sparse_layout_balances_vertical_whitespace_on_the_sheet():
    document = layout_riser(branched_design())
    locations = [element for element in document.elements.values()
                 if element.kind == "location"]
    top = min(element.y for element in locations)
    bottom = max(element.y + element.height for element in locations)

    assert abs(top - (document.page_height - bottom)) <= 2 * 18


def test_modest_three_rsp_chain_fits_printable_page():
    document = layout_riser(three_rsp_chain_design())
    drawing_right = document.page_width - TITLE_BLOCK_WIDTH

    assert all(
        0 <= element.x
        and 0 <= element.y
        and element.x + element.width <= drawing_right
        and element.y + element.height <= document.page_height
        for element in document.elements.values()
    )


def test_layout_never_places_a_downstream_target_above_its_source():
    design = mixed_location_order_design()
    document = layout_riser(design)

    violations = [
        (edge.source.device_id, edge.target.device_id)
        for edge in design.connections
        if document.elements[f"device:{edge.target.device_id}"].y
        < document.elements[f"device:{edge.source.device_id}"].y
    ]

    assert not violations


def test_legacy_named_splitter_uses_splitter_geometry():
    document = layout_riser(legacy_named_splitter_design())
    splitter = document.elements["device:KP-710-1"]

    assert (splitter.width, splitter.height) == (184.0, 80.0)


def test_splitter_output_ports_stay_on_the_symbol_boundary():
    splitter = RiserElement(
        "device:710-LX500-1", "device", "710-LX500-1",
        1083.0, 451.0, 184.0, 80.0,
    )

    output = port_point(splitter, "OUT3", output=True)
    input_ = port_point(splitter, "IN", output=False)

    assert output == (
        splitter.x + splitter.width * 3 / 4,
        splitter.y + splitter.height,
    )
    assert input_ == (splitter.x + splitter.width / 2, splitter.y)


def test_auto_layout_places_cable_labels_clear_of_device_symbols():
    design = branched_design()
    document = layout_riser(design)

    assert not [issue for issue in validate_riser(design, document)
                if issue.code == "scene.label_overlap"]


def test_sync_preserves_manual_geometry_and_places_new_devices_in_tray():
    d = branched_design()
    document = layout_riser(d)
    document.elements["device:RSP-1"].x = 777.0
    document.elements["device:RSP-1"].manual = True
    d.rsps.append(RSP(3, "GYM", [533]))
    d.splitters.append(Splitter("710-LX500-3", "LX", "GYM", outputs=["Spare"] * 3))

    sync_riser_document(d, document)

    assert document.elements["device:RSP-1"].x == 777.0
    assert "RSP-3" in document.unplaced
    assert "710-LX500-3" in document.unplaced


def test_sync_adds_routes_for_new_topology_without_touching_manual_geometry():
    d = branched_design()
    document = layout_riser(d)
    document.elements["device:RSP-1"].x = 777
    document.elements["device:RSP-1"].manual = True
    edge = next(edge for edge in d.connections if edge.target.device_id == "RSP-2")
    document.routes.pop(edge.id)

    sync_riser_document(d, document)

    assert edge.id in document.routes
    assert document.elements["device:RSP-1"].x == 777


def test_generated_splitter_fanout_uses_distinct_drop_lanes():
    """Separate outputs must not look like one shared electrical trunk."""
    design = DMPDesign(
        site_info=SiteInfo(
            school_name="FANOUT SCHOOL", school_code="1234",
            address_line1="1 TEST ST", xr550_location="MDF",
        ),
        splitters=[
            Splitter("710-LX500-1", "LX", "SOURCE", outputs=["Spare"] * 3),
        ],
        rsps=[
            RSP(1, "LEFT", [501]),
            RSP(2, "CENTER", [517]),
            RSP(3, "RIGHT", [533]),
        ],
    )
    for number in range(1, 4):
        connect(
            design,
            DevicePortRef("710-LX500-1", f"OUT{number}"),
            DevicePortRef(f"RSP-{number}", "IN"),
        )

    document = layout_riser(design)
    positions = {
        "710-LX500-1": (1200.0, 200.0),
        "RSP-1": (100.0, 700.0),
        "RSP-2": (350.0, 700.0),
        "RSP-3": (600.0, 700.0),
    }
    for device_id, (x, y) in positions.items():
        element = document.elements[f"device:{device_id}"]
        element.x, element.y = x, y
    document.routes.clear()

    sync_riser_document(design, document)

    fanout_routes = [document.routes[edge.id] for edge in design.connections]
    assert all(
        route.points[0][0] == route.points[1][0]
        and route.points[1][1] > route.points[0][1]
        for route in fanout_routes
    )
    assert not [
        issue for issue in validate_riser(design, document)
        if issue.code == "scene.uncovered_intersection"
    ]


def test_auto_layout_never_overlays_sibling_splitter_runs():
    design = DMPDesign(
        site_info=SiteInfo(
            school_name="FANOUT SCHOOL", school_code="1234",
            address_line1="1 TEST ST", xr550_location="MDF",
        ),
        splitters=[
            Splitter("710-LX500-1", "LX", "SOURCE", outputs=["Spare"] * 3),
            Splitter("710-LX500-2", "LX", "A", outputs=["Spare"] * 3),
            Splitter("710-LX500-3", "LX", "B", outputs=["Spare"] * 3),
            Splitter("710-LX500-4", "LX", "C", outputs=["Spare"] * 3),
        ],
    )
    connect(
        design,
        DevicePortRef("MSP", "LX500"),
        DevicePortRef("710-LX500-1", "IN"),
    )
    for number in range(1, 4):
        connect(
            design,
            DevicePortRef("710-LX500-1", f"OUT{number}"),
            DevicePortRef(f"710-LX500-{number + 1}", "IN"),
        )

    document = layout_riser(design)
    sibling_routes = [
        document.routes[edge.id].points
        for edge in design.connections
        if edge.source.device_id == "710-LX500-1"
    ]

    for index, first in enumerate(sibling_routes):
        for second in sibling_routes[index + 1:]:
            assert not any(
                _collinear_overlap_length(first_segment, second_segment) > 0
                for first_segment in zip(first, first[1:])
                for second_segment in zip(second, second[1:])
            )


def test_stacked_splitters_leave_room_for_independent_output_lanes():
    design = DMPDesign(
        site_info=SiteInfo(
            school_name="STACK SCHOOL", school_code="1234",
            address_line1="1 TEST ST", xr550_location="MDF",
        ),
        splitters=[
            Splitter(
                f"710-LX500-{number}", "LX",
                "MDF" if number <= 3 else f"REMOTE {number}",
                outputs=["Spare"] * 3,
            )
            for number in range(1, 7)
        ],
    )
    edges = [
        (("MSP", "LX500"), ("710-LX500-1", "IN")),
        (("710-LX500-1", "OUT1"), ("710-LX500-2", "IN")),
        (("710-LX500-1", "OUT2"), ("710-LX500-4", "IN")),
        (("710-LX500-2", "OUT1"), ("710-LX500-3", "IN")),
        (("710-LX500-2", "OUT2"), ("710-LX500-5", "IN")),
        (("710-LX500-2", "OUT3"), ("710-LX500-6", "IN")),
    ]
    for source, target in edges:
        connect(design, DevicePortRef(*source), DevicePortRef(*target))

    document = layout_riser(design)
    route_issues = [
        issue for issue in validate_riser(design, document)
        if issue.code in {
            "scene.cable_through_device",
            "scene.uncovered_intersection",
        }
    ]

    assert not route_issues


def test_route_uses_orthogonal_segments_and_avoids_obstacle():
    source = RiserElement("device:A", "device", "A", 100, 100, 100, 60)
    target = RiserElement("device:B", "device", "B", 500, 100, 100, 60)
    obstacle = RiserElement("device:C", "device", "C", 280, 150, 120, 90)

    points = route_connection((150, 160), (550, 100), [source, target, obstacle])

    assert points[0] == (150, 160)
    assert points[-1] == (550, 100)
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:]))
    assert not any(280 < x < 400 and 150 < y < 240 for x, y in points[1:-1])


def test_topology_route_leads_do_not_overlap_reserved_runs():
    edge = TopologyConnection(
        "edge",
        DevicePortRef("S", "OUT1"),
        DevicePortRef("T", "IN"),
    )
    source = RiserElement("device:S", "device", "S", 100, 100, 184, 80)
    target = RiserElement("device:T", "device", "T", 500, 500, 184, 80)
    reserved = [((146.0, 192.0), (146.0, 300.0))]

    points = route_topology_connection(
        edge, source, target, [source, target],
        reserved_segments=reserved,
    )

    assert not any(
        _collinear_overlap_length(segment, reserved[0]) > 0
        for segment in zip(points, points[1:])
    )


def test_topology_route_escapes_sideways_when_output_drop_is_blocked():
    edge = TopologyConnection(
        "edge",
        DevicePortRef("S", "OUT1"),
        DevicePortRef("T", "IN"),
    )
    source = RiserElement("device:S", "device", "S", 100, 100, 184, 80)
    target = RiserElement("device:T", "device", "T", 500, 500, 184, 80)
    blocker = RiserElement("device:B", "device", "B", 130, 188, 40, 100)

    points = route_topology_connection(
        edge, source, target, [source, target, blocker])

    assert not any(
        _segment_hits_rect(first, second, blocker, clearance=0)
        for first, second in zip(points, points[1:])
    )


def test_route_detours_around_a_blocker_on_its_vertical_drop():
    source = RiserElement("device:A", "device", "A", 100, 100, 100, 60)
    target = RiserElement("device:B", "device", "B", 100, 500, 100, 60)
    obstacle = RiserElement("device:C", "device", "C", 90, 250, 120, 100)

    points = route_connection(
        (150, 160), (150, 500), [source, target, obstacle],
        source_ref="A", target_ref="B")

    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:]))
    assert any(x < 78 or x > 222 for x, _y in points)


def test_route_avoids_multiple_staggered_obstacles():
    obstacles = [
        RiserElement("device:A", "device", "A", 1188, 612, 184, 80),
        RiserElement("device:B", "device", "B", 1782, 1026, 184, 80),
        RiserElement("device:C", "device", "C", 936, 1116, 220, 110),
        RiserElement("device:D", "device", "D", 1278, 1638, 148, 72),
    ]

    points = route_connection((1044, 684), (1872, 1638), obstacles)

    def segment_hits(a, b, obstacle):
        left, top = obstacle.x - 12, obstacle.y - 12
        right = obstacle.x + obstacle.width + 12
        bottom = obstacle.y + obstacle.height + 12
        if a[0] == b[0]:
            return (left < a[0] < right
                    and max(min(a[1], b[1]), top) < min(max(a[1], b[1]), bottom))
        if a[1] == b[1]:
            return (top < a[1] < bottom
                    and max(min(a[0], b[0]), left) < min(max(a[0], b[0]), right))
        return True

    assert all(a[0] == b[0] or a[1] == b[1]
               for a, b in zip(points, points[1:]))
    assert not any(
        segment_hits(a, b, obstacle)
        for a, b in zip(points, points[1:])
        for obstacle in obstacles
    )


def test_bridge_detection_reports_crossing_without_masking_routes():
    horizontal = [(100.0, 200.0), (500.0, 200.0)]
    vertical = [(300.0, 100.0), (300.0, 400.0)]

    bridges = find_bridges({"horizontal": horizontal, "vertical": vertical})

    assert [(b.connection_id, b.x, b.y, b.orientation) for b in bridges] == [
        ("horizontal", 300.0, 200.0, "horizontal")
    ]
    assert horizontal == [(100.0, 200.0), (500.0, 200.0)]
    assert vertical == [(300.0, 100.0), (300.0, 400.0)]


def test_bridge_detection_hops_over_an_independent_route_endpoint():
    """A cable endpoint on another cable's run must not render as a tee."""
    routes = {
        "msp-prog": [(1107.54, 626.0), (954.0, 626.0), (954.0, 666.0)],
        "msp-kp-bus": [(1075.2, 626.0), (1075.2, 666.0), (1170.0, 666.0)],
    }

    bridges = find_bridges(routes)

    assert [(b.connection_id, b.x, b.y, b.orientation) for b in bridges] == [
        ("msp-prog", 1075.2, 626.0, "horizontal")
    ]


def test_bridge_detection_uses_vertical_run_when_horizontal_ends_at_contact():
    routes = {
        "horizontal": [(100.0, 200.0), (300.0, 200.0)],
        "vertical": [(300.0, 100.0), (300.0, 400.0)],
    }

    bridges = find_bridges(routes)

    assert [(b.connection_id, b.x, b.y, b.orientation) for b in bridges] == [
        ("vertical", 300.0, 200.0, "vertical")
    ]


def test_bridge_detection_does_not_treat_one_routes_own_bend_as_a_crossing():
    assert find_bridges({
        "one-cable": [(100.0, 200.0), (300.0, 200.0), (300.0, 400.0)],
    }) == []


def test_validation_warns_when_independent_endpoint_contact_cannot_take_a_hop():
    design = branched_design()
    document = layout_riser(design)
    first, second = list(document.routes)[:2]
    document.routes[first].points = [(100.0, 200.0), (300.0, 200.0)]
    document.routes[second].points = [(300.0, 200.0), (300.0, 400.0)]

    issues = validate_riser(design, document)

    assert any(issue.code == "scene.uncovered_intersection"
               and issue.ref == first for issue in issues)


def test_validation_warns_for_metadata_unplaced_overlap_and_title_fields():
    d = branched_design()
    document = layout_riser(d)
    d.connections[0].cable_type = ""
    document.unplaced.append("RSP-99")
    document.title_block.school_name = ""
    a = document.elements["device:710-KP-1"]
    b = document.elements["device:710-LX500-1"]
    b.x, b.y = a.x, a.y

    issues = validate_riser(d, document)
    codes = {issue.code for issue in issues}

    assert {"cable.metadata", "scene.unplaced", "scene.overlap", "title.required"} <= codes
    assert all(issue.severity == "warning" for issue in issues)


def test_validation_finds_orphans_invalid_endpoints_stale_routes_and_cable_obstacles():
    d = branched_design()
    document = layout_riser(d)
    # Bypass the topology service to model a corrupted persisted graph.
    d.connections.append(TopologyConnection(
        "bad-edge", DevicePortRef("710-LX500-1", "OUT3"),
        DevicePortRef("KEYPAD-2", "IN")))
    document.routes["removed-edge"] = copy.deepcopy(next(iter(document.routes.values())))
    document.routes["removed-edge"].connection_id = "removed-edge"
    # Disconnect RSP-2 logically so it becomes an orphan.
    d.connections = [edge for edge in d.connections if edge.target.device_id != "RSP-2"]
    # Force a surviving route through RSP-2's footprint.
    route = next(iter(document.routes.values()))
    obstacle = document.elements["device:RSP-2"]
    route.points = [
        (obstacle.x - 20, obstacle.y + obstacle.height / 2),
        (obstacle.x + obstacle.width + 20, obstacle.y + obstacle.height / 2),
    ]

    codes = {issue.code for issue in validate_riser(d, document)}

    assert {"topology.orphan", "topology.incompatible", "scene.stale_route",
            "scene.cable_through_device"} <= codes


def test_validation_checks_markup_bounds_and_all_required_title_fields():
    d = branched_design()
    document = layout_riser(d)
    document.title_block.drawn_by = ""
    document.annotations.append(RiserAnnotation(
        "off-page-note", "text", [(document.page_width, 100)], text="NOTE"))

    issues = validate_riser(d, document)

    assert any(i.code == "scene.off_page" and i.ref == "off-page-note" for i in issues)
    assert any(i.code == "title.required" and i.ref == "drawn_by" for i in issues)


def test_validation_warns_when_cable_labels_overlap_devices_or_each_other():
    d = branched_design()
    document = layout_riser(d)
    route_ids = list(document.routes)[:2]
    for route_id in route_ids:
        document.routes[route_id].points = [(100, 100), (500, 100)]
    device = document.elements["device:RSP-1"]
    device.x, device.y, device.width, device.height = 250, 80, 120, 40

    issues = validate_riser(d, document)

    assert any(i.code == "scene.label_overlap" for i in issues)


def test_validation_warns_when_cable_label_covers_a_location_heading():
    d = branched_design()
    document = layout_riser(d)
    frame = document.elements["location:CLASSROOM 27"]
    route_id = next(iter(document.routes))
    # route_label_point places the baseline nine points above the run, which
    # puts this label directly across the location heading.
    document.routes[route_id].points = [
        (frame.x + 12, frame.y + 31),
        (frame.x + 120, frame.y + 31),
    ]
    document.routes[route_id].label_offset = (0, 0)

    issues = validate_riser(d, document)

    assert any(issue.code == "scene.label_overlap"
               and "location heading" in issue.message.lower()
               and issue.ref == route_id
               for issue in issues)


def test_validation_flags_route_outside_printable_page():
    design = branched_design()
    document = layout_riser(design)
    route_id = next(iter(document.routes))
    document.routes[route_id].points = [
        (100.0, 100.0), (document.page_width + 100.0, 100.0),
    ]

    issues = validate_riser(design, document)

    assert any(issue.code == "scene.off_page" and issue.ref == route_id
               for issue in issues)


def test_validation_flags_nonorthogonal_route_segments():
    design = branched_design()
    document = layout_riser(design)
    route_id = next(iter(document.routes))
    document.routes[route_id].points = [(100.0, 100.0), (300.0, 300.0)]

    issues = validate_riser(design, document)

    assert any(issue.code == "scene.nonorthogonal_route" and issue.ref == route_id
               for issue in issues)


def test_validation_flags_device_whose_domain_location_changed():
    design = branched_design()
    document = layout_riser(design)
    rsp = next(item for item in design.rsps if item.number == 2)
    rsp.location = "NEW WING ELECTRICAL ROOM"

    issues = validate_riser(design, document)

    assert any(issue.code == "scene.location_mismatch"
               and issue.ref == "device:RSP-2" for issue in issues)
