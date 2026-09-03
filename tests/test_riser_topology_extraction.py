"""Regression tests for topology reconstructed from riser vector linework.

The fixtures are deliberately synthetic.  They model CAD conventions seen in
real school drawings without retaining any customer PDF or extracted page data:

* splitter/keypad symbols can be drawn inside a larger MSP/RSP rectangle;
* a cable can use a curved jump-over at an independent crossing; and
* KP and LX wiring can share a connected drawing component while remaining
  electrically separate.
"""
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import extract_topology as topology  # noqa: E402
from extract_topology import Device, TextSpan, reconstruct_edges  # noqa: E402
from generate_dmp_ws import (  # noqa: E402
    _apply_phase3_topology,
    _auto_derive_splitter_io,
    _phase3_topology_complete,
)
from parse_dmp_worksheet import DMPDesign, Keypad, RSP, Splitter  # noqa: E402


def _device(kind: str, device_id: str, x: float, y: float) -> Device:
    return Device(
        kind=kind,
        id=device_id,
        anchor=TextSpan(device_id, x - 2, y - 2, x + 2, y + 2),
    )


def test_embedded_symbols_keep_compact_connection_footprints(monkeypatch):
    """An enclosing location box must not make its devices indistinguishable."""
    enclosure = (0.0, 0.0, 200.0, 200.0)
    rsp = _device("RSP", "RSP1", 80, 80)
    splitter = _device("SPLITTER", "710-LX500-2", 165, 90)
    keypad = _device("KEYPAD", "KEYPAD 2", 145, 130)
    monkeypatch.setattr(topology, "_riser_rectangles",
                        lambda _path, _page: [enclosure])

    footprints, splitter_on_rsp = topology.compute_device_footprints(
        [rsp, splitter, keypad], [], Path("unused.pdf"), 0)

    assert footprints[rsp.id] == enclosure
    assert footprints[splitter.id] != enclosure
    assert footprints[keypad.id] != enclosure
    assert splitter_on_rsp == {splitter.id: rsp.id}


def test_jump_over_gap_reconnects_cable_without_joining_crossed_line():
    """A missing curve segment in a bridge hop is still one logical cable."""
    kp1 = _device("SPLITTER", "710-KP-1", 0, 0)
    kp2 = _device("SPLITTER", "710-KP-2", 100, 0)
    segments = [
        ((0.0, 0.0), (40.0, 0.0)),
        ((60.0, 0.0), (100.0, 0.0)),
        ((50.0, -40.0), (50.0, 40.0)),
    ]
    footprints = {
        kp1.id: (-5.0, -5.0, 5.0, 5.0),
        kp2.id: (95.0, -5.0, 105.0, 5.0),
    }

    edges = reconstruct_edges(
        segments, [kp1, kp2], [], footprints=footprints)

    assert [(edge.src.id, edge.dst.id) for edge in edges] == [
        (kp1.id, kp2.id),
    ]


def test_reconstruction_respects_bus_family_and_true_upstream_order():
    """Nearest geometry cannot attach a leaf to a downstream/cross-bus device."""
    msp = _device("MSP", "MSP", 0, 0)
    lx1 = _device("SPLITTER", "710-LX500-1", 20, 0)
    kp1 = _device("SPLITTER", "710-KP-1", 30, 0)
    rsp = _device("RSP", "RSP1", 70, 0)
    keypad = _device("KEYPAD", "KEYPAD 2", 90, 0)
    lx2 = _device("SPLITTER", "710-LX500-2", 100, 0)
    devices = [msp, lx1, kp1, rsp, keypad, lx2]
    segments = [
        ((20.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (70.0, 0.0)),
        ((70.0, 0.0), (90.0, 0.0)),
        ((90.0, 0.0), (100.0, 0.0)),
    ]
    footprints = {
        msp.id: (-5.0, -5.0, 25.0, 5.0),
        lx1.id: (15.0, -5.0, 25.0, 5.0),
        kp1.id: (25.0, -5.0, 35.0, 5.0),
        rsp.id: (65.0, -5.0, 75.0, 5.0),
        keypad.id: (85.0, -5.0, 95.0, 5.0),
        lx2.id: (95.0, -5.0, 105.0, 5.0),
    }

    pairs = {
        (edge.src.id, edge.dst.id)
        for edge in reconstruct_edges(segments, devices, [], footprints=footprints)
    }

    assert (lx1.id, rsp.id) in pairs
    assert (kp1.id, keypad.id) in pairs
    assert (lx1.id, lx2.id) in pairs
    assert not any(
        ("KP" in src and (dst.startswith("RSP") or "LX" in dst))
        or ("LX" in src and (dst.startswith("KEYPAD") or "-KP-" in dst))
        for src, dst in pairs
    )


def test_leaf_touched_by_two_components_keeps_only_closest_feed():
    """A large RSP footprint may touch two nets, but its input has one source."""
    distant = _device("SPLITTER", "710-LX500-1", 0, 0)
    local = _device("SPLITTER", "710-LX500-2", 100, 20)
    rsp = _device("RSP", "RSP1", 90, 10)
    footprints = {
        distant.id: (-5.0, -5.0, 5.0, 5.0),
        local.id: (95.0, 15.0, 105.0, 25.0),
        rsp.id: (75.0, -5.0, 105.0, 25.0),
    }
    segments = [
        ((0.0, 0.0), (75.0, 0.0)),
        ((100.0, 10.0), (100.0, 20.0)),
    ]

    edges = reconstruct_edges(
        segments, [distant, local, rsp], [], footprints=footprints)

    assert [(edge.src.id, edge.dst.id) for edge in edges] == [
        (local.id, rsp.id),
    ]


def test_phase3_does_not_invent_panel_feeds_for_unseen_inputs():
    """Missing extracted feeds must trigger fallback, not duplicate MSP roots."""
    design = DMPDesign(
        rsps=[RSP(number=1, location="ROOM")],
        splitters=[
            Splitter("710-LX500-1", "LX", outputs=[]),
            Splitter("710-LX500-2", "LX", outputs=[]),
        ],
    )
    lx1 = _device("SPLITTER", "710-LX500-1", 0, 0)
    rsp = _device("RSP", "RSP1", 100, 0)

    _apply_phase3_topology(
        design,
        [topology.Edge(src=lx1, dst=rsp)],
        [lx1],
    )

    assert design.splitters[0].inputs == {}
    assert design.splitters[1].inputs == {}
    assert _phase3_topology_complete(design) is False


def test_phase3_rejects_cross_bus_edges_defensively():
    design = DMPDesign(
        rsps=[RSP(number=1, location="ROOM")],
        keypads=[Keypad(number=2, source="", location="OFFICE")],
        splitters=[
            Splitter("710-LX500-1", "LX", outputs=[]),
            Splitter("710-KP-1", "KP", outputs=[]),
        ],
    )
    lx = _device("SPLITTER", "710-LX500-1", 0, 0)
    kp = _device("SPLITTER", "710-KP-1", 0, 10)
    rsp = _device("RSP", "RSP1", 100, 0)
    keypad = _device("KEYPAD", "KEYPAD 2", 100, 10)

    applied = _apply_phase3_topology(
        design,
        [
            topology.Edge(src=kp, dst=rsp),
            topology.Edge(src=lx, dst=keypad),
            topology.Edge(src=kp, dst=lx),
        ],
        [lx, kp],
    )

    assert applied == 0
    assert all(splitter.outputs == ["Spare"] * 3
               for splitter in design.splitters)
    assert all(splitter.inputs == {} for splitter in design.splitters)


def test_phase3_maps_preserved_splitter_ids_before_location_order_fallback():
    """Location-sorted worksheet rows must not renumber electrical identities."""
    design = DMPDesign(splitters=[
        # Deliberately opposite the electrical/ID order, as happens when the
        # remote room sorts before the MSP room.
        Splitter("710-LX500-2", "LX", location="A REMOTE", outputs=[]),
        Splitter("710-LX500-1", "LX", location="Z MSP", outputs=[]),
    ])
    msp = _device("MSP", "MSP", 0, 0)
    lx1 = _device("SPLITTER", "710-LX500-1", 10, 0)
    lx2 = _device("SPLITTER", "710-LX500-2", 20, 0)

    _apply_phase3_topology(
        design,
        [
            topology.Edge(src=msp, dst=lx1),
            topology.Edge(src=lx1, dst=lx2),
        ],
        [lx1, lx2],
    )

    by_id = {splitter.id: splitter for splitter in design.splitters}
    assert by_id[lx1.id].inputs == {
        "LX-Bus In": "500 BUS IN FROM XR/550",
    }
    assert by_id[lx1.id].outputs[0] == f"To {lx2.id}"
    assert by_id[lx2.id].inputs == {"LX-Bus In": f"From {lx1.id}"}


def test_auto_derive_uses_electrical_ids_not_location_storage_order():
    """Fallback roots and assignments must follow printed splitter numbers."""
    storage_order = [
        "710-LX500-3",
        "710-LX500-2",
        "710-LX500-1",
        "710-KP-2",
        "710-KP-1",
    ]
    design = DMPDesign(
        rsps=[RSP(number=number, location=f"RSP ROOM {number}")
              for number in range(1, 4)],
        keypads=[Keypad(number=number, source="", location=f"KP ROOM {number}")
                 for number in range(1, 5)],
        splitters=[
            Splitter("710-LX500-3", "LX", location="A CAFETERIA", outputs=[]),
            Splitter("710-LX500-2", "LX", location="B EAST", outputs=[]),
            Splitter("710-LX500-1", "LX", location="C MAIN", outputs=[]),
            Splitter("710-KP-2", "KP", location="A CAFETERIA", outputs=[]),
            Splitter("710-KP-1", "KP", location="C MAIN", outputs=[]),
        ],
    )

    _auto_derive_splitter_io(design)

    assert [splitter.id for splitter in design.splitters] == storage_order
    by_id = {splitter.id: splitter for splitter in design.splitters}
    assert by_id["710-LX500-1"].inputs == {
        "LX-Bus In": "500 BUS IN FROM XR/550",
    }
    assert by_id["710-LX500-1"].outputs == [
        "RSP 1", "To 710-LX500-2", "To 710-LX500-3",
    ]
    assert by_id["710-LX500-2"].inputs == {
        "LX-Bus In": "From 710-LX500-1",
    }
    assert by_id["710-LX500-2"].outputs == ["RSP 2", "Spare", "Spare"]
    assert by_id["710-LX500-3"].inputs == {
        "LX-Bus In": "From 710-LX500-1",
    }
    assert by_id["710-LX500-3"].outputs == ["RSP 3", "Spare", "Spare"]
    assert by_id["710-KP-1"].inputs == {
        "KP-Bus In": "KEYPAD BUS IN FROM XR/550",
    }
    assert by_id["710-KP-1"].outputs == [
        "KEYPAD #2", "KEYPAD #3", "To 710-KP-2",
    ]
    assert by_id["710-KP-2"].inputs == {
        "KP-Bus In": "From 710-KP-1",
    }
    assert by_id["710-KP-2"].outputs == ["KEYPAD #4", "Spare", "Spare"]


def test_phase3_completeness_rejects_two_roots_on_same_panel_port():
    kp_design = DMPDesign(splitters=[
        Splitter("710-KP-1", "KP",
                 inputs={"KP-Bus In": "KEYPAD BUS IN FROM XR/550"},
                 outputs=["Spare"] * 3),
        Splitter("710-KP-2", "KP",
                 inputs={"KP-Bus In": "KEYPAD BUS IN FROM XR/550"},
                 outputs=["Spare"] * 3),
    ])
    lx_design = DMPDesign(splitters=[
        Splitter("710-LX500-1", "LX",
                 inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
                 outputs=["Spare"] * 3),
        Splitter("710-LX500-2", "LX",
                 inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
                 outputs=["Spare"] * 3),
    ])

    assert _phase3_topology_complete(kp_design) is False
    assert _phase3_topology_complete(lx_design) is False


def test_phase3_completeness_allows_one_root_per_distinct_lx_bus():
    design = DMPDesign(splitters=[
        Splitter("710-LX500-1", "LX",
                 inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
                 outputs=["Spare"] * 3),
        Splitter("710-LX600-1", "LX",
                 inputs={"LX-Bus In": "600 BUS IN FROM XR/550"},
                 outputs=["Spare"] * 3),
    ])

    assert _phase3_topology_complete(design) is True
