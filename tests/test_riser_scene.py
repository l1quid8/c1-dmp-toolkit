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
    find_bridges,
    layout_riser,
    route_connection,
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


def test_route_uses_orthogonal_segments_and_avoids_obstacle():
    source = RiserElement("device:A", "device", "A", 100, 100, 100, 60)
    target = RiserElement("device:B", "device", "B", 500, 100, 100, 60)
    obstacle = RiserElement("device:C", "device", "C", 280, 150, 120, 90)

    points = route_connection((150, 160), (550, 100), [source, target, obstacle])

    assert points[0] == (150, 160)
    assert points[-1] == (550, 100)
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:]))
    assert not any(280 < x < 400 and 150 < y < 240 for x, y in points[1:-1])


def test_route_detours_around_a_blocker_on_its_vertical_drop():
    source = RiserElement("device:A", "device", "A", 100, 100, 100, 60)
    target = RiserElement("device:B", "device", "B", 100, 500, 100, 60)
    obstacle = RiserElement("device:C", "device", "C", 90, 250, 120, 100)

    points = route_connection(
        (150, 160), (150, 500), [source, target, obstacle],
        source_ref="A", target_ref="B")

    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:]))
    assert any(x < 78 or x > 222 for x, _y in points)


def test_bridge_detection_reports_crossing_without_masking_routes():
    horizontal = [(100.0, 200.0), (500.0, 200.0)]
    vertical = [(300.0, 100.0), (300.0, 400.0)]

    bridges = find_bridges({"horizontal": horizontal, "vertical": vertical})

    assert [(b.connection_id, b.x, b.y, b.orientation) for b in bridges] == [
        ("horizontal", 300.0, 200.0, "horizontal")
    ]
    assert horizontal == [(100.0, 200.0), (500.0, 200.0)]
    assert vertical == [(300.0, 100.0), (300.0, 400.0)]


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
