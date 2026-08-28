"""Regression coverage for schema-2 topology ownership and legacy migration."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from parse_dmp_worksheet import DMPDesign, Keypad, RSP, SiteInfo, Splitter  # noqa: E402
from riser_model import (  # noqa: E402
    DevicePortRef,
    RiserDocument,
    RiserTitleBlock,
    derive_legacy_connections,
)
from session import Session, load_session, save_session  # noqa: E402


def _legacy_populated_design_dict(*, connections: list[dict]) -> dict:
    """A schema-2 payload whose compatibility fields disagree with its graph."""
    return {
        "site_info": {"school_name": "EMPTY GRAPH SCHOOL"},
        "splitters": [{
            "id": "710-LX500-1",
            "splitter_type": "LX",
            "location": "MDF",
            "inputs": {"LX-Bus In": "500 BUS IN FROM XR/550"},
            "outputs": ["RSP-1", "Spare", "Spare"],
        }],
        "rsps": [{
            "number": 1,
            "location": "MDF",
            "zones": [501],
            "model": "714-16",
        }],
        "keypads": [{
            "number": 1,
            "source": "MSP",
            "location": "MDF",
            "global_keypad": True,
        }],
        "connections": connections,
        "riser_document": {
            "title_block": {"school_name": "EMPTY GRAPH SCHOOL"},
            "elements": {},
            "routes": {},
            "annotations": [],
            "z_order": [],
            "unplaced": [],
        },
    }


def test_schema_two_explicit_empty_graph_is_not_rederived_from_legacy_fields(tmp_path):
    """In schema 2, an empty ``connections`` list is authoritative, not missing."""
    path = tmp_path / "empty-schema-two.dmps"
    path.write_text(json.dumps({
        "schema_version": 2,
        "source": {"kind": "", "name": ""},
        "topology_confirmed": False,
        "design": _legacy_populated_design_dict(connections=[]),
    }))

    restored = load_session(path)

    assert restored.design.connections == []


def test_saving_explicit_empty_graph_does_not_mutate_or_resurrect_connections(tmp_path):
    """Save may project graph -> legacy, but must never migrate schema-2 state again."""
    splitter = Splitter(
        "710-LX500-1", "LX", "MDF",
        inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
        outputs=["RSP-1", "Spare", "Spare"],
    )
    design = DMPDesign(
        site_info=SiteInfo(school_name="EMPTY GRAPH SCHOOL"),
        splitters=[splitter],
        rsps=[RSP(1, "MDF", [501])],
        keypads=[Keypad(1, "MSP", "MDF")],
        connections=[],
        riser_document=RiserDocument(
            title_block=RiserTitleBlock(school_name="EMPTY GRAPH SCHOOL")),
    )
    path = tmp_path / "empty-save.dmps"

    save_session(Session(design=design, path=path))

    raw = json.loads(path.read_text())
    assert design.connections == []
    assert raw["design"]["connections"] == []
    assert splitter.inputs == {}
    assert splitter.outputs == ["Spare", "Spare", "Spare"]
    assert design.keypads[0].source is None
    assert load_session(path).design.connections == []


def test_schema_one_keypad_source_falls_back_to_first_free_kp_output(tmp_path):
    """A legacy keypad source remains topology even if an output row omitted it."""
    legacy = {
        "schema_version": 1,
        "source": {"kind": "xlsx", "name": "legacy.xlsx"},
        "design": {
            "site_info": {"school_name": "LEGACY KEYPAD SCHOOL"},
            "splitters": [{
                "id": "710-KP-1",
                "splitter_type": "KP",
                "location": "MDF",
                "inputs": {"KP-Bus In": "KEYPAD BUS IN FROM XR/550"},
                "outputs": ["Spare", "Spare", "Spare"],
            }],
            "keypads": [{
                "number": 2,
                "source": "710-KP-1",
                "location": "OFFICE",
                "global_keypad": True,
            }],
        },
    }
    path = tmp_path / "legacy-keypad.dmps"
    path.write_text(json.dumps(legacy))

    first = load_session(path)
    second = load_session(path)
    keypad_edge = next(
        edge for edge in first.design.connections
        if edge.target.device_id == "KEYPAD-2")

    assert keypad_edge.source.device_id == "710-KP-1"
    assert keypad_edge.source.port_id == "OUT1"
    assert keypad_edge.id == next(
        edge.id for edge in second.design.connections
        if edge.target.device_id == "KEYPAD-2")


def test_legacy_prefixed_parent_input_does_not_fabricate_direct_msp_feed():
    """Legacy IDs are KP-710-N/LX-710-N, not only modern 710-KP/LX IDs."""
    parent = Splitter(
        "KP-710-1", "KP", "MDF",
        inputs={"KP-Bus In": "KEYPAD BUS IN FROM XR/550"},
        outputs=["To KP-710-2", "Spare", "Spare"],
    )
    child = Splitter(
        "KP-710-2", "KP", "OFFICE",
        inputs={"KP-Bus In": "From KP-710-1"},
        outputs=["Spare", "Spare", "Spare"],
    )
    design = DMPDesign(splitters=[parent, child])

    edges = derive_legacy_connections(design)
    incoming = [edge for edge in edges if edge.target == DevicePortRef("KP-710-2", "IN")]

    assert len(incoming) == 1
    assert incoming[0].source == DevicePortRef("KP-710-1", "OUT1")
