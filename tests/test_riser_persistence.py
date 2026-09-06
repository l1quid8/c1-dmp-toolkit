"""Riser persistence and legacy-project migration in the combined format."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from parse_dmp_worksheet import DMPDesign, Keypad, RSP, SiteInfo, Splitter  # noqa: E402
from riser_model import (  # noqa: E402
    DevicePortRef,
    RiserAnnotation,
    RiserDocument,
    RiserElement,
    RiserRoute,
    RiserTitleBlock,
    TopologyConnection,
)
from session import Session, load_session, save_session  # noqa: E402


def test_current_schema_round_trip_preserves_connections_and_drawing(tmp_path):
    connection = TopologyConnection(
        id="edge-1",
        source=DevicePortRef("MSP", "LX500"),
        target=DevicePortRef("710-LX500-1", "IN"),
        cable_type="WP240R",
        status="new",
        quantity=1,
        custom_label="TRUNK A",
    )
    document = RiserDocument(
        title_block=RiserTitleBlock(
            school_name="GRIFFIN ELEMENTARY SCHOOL",
            local_code="1234",
            address="2026 SCHOOL AVE",
            drawn_by="TC",
        ),
        elements={
            "device:MSP": RiserElement(
                id="device:MSP", kind="device", ref="MSP",
                x=144.0, y=108.0, width=180.0, height=96.0,
            )
        },
        routes={"edge-1": RiserRoute("edge-1", [(234.0, 204.0), (234.0, 300.0)])},
        annotations=[RiserAnnotation("note-1", "text", [(72.0, 72.0)], text="FIELD NOTE")],
        z_order=["device:MSP", "note-1"],
    )
    design = DMPDesign(
        site_info=SiteInfo(school_name="GRIFFIN ELEMENTARY SCHOOL"),
        connections=[connection],
        riser_document=document,
    )
    path = tmp_path / "griffin.dmps"

    save_session(Session(design=design, path=path))
    raw = json.loads(path.read_text())
    restored = load_session(path)

    assert raw["schema_version"] == 7
    assert restored.design.connections == [connection]
    assert restored.design.riser_document == document


def test_schema_one_project_derives_stable_connections_without_changing_legacy_fields(tmp_path):
    legacy = {
        "schema_version": 1,
        "source": {"kind": "pdf", "name": "legacy.pdf"},
        "topology_confirmed": True,
        "design": {
            "site_info": {"school_name": "LEGACY SCHOOL"},
            "splitters": [
                {
                    "id": "710-LX500-1",
                    "splitter_type": "LX",
                    "location": "MDF",
                    "inputs": {"LX-Bus In": "500 BUS IN FROM XR/550"},
                    "outputs": ["RSP-1", "To 710-LX500-2", "Spare"],
                },
                {
                    "id": "710-LX500-2",
                    "splitter_type": "LX",
                    "location": "CLASSROOM 27",
                    "inputs": {"LX-Bus In": "From 710-LX500-1"},
                    "outputs": ["RSP-2", "Spare", "Spare"],
                },
            ],
            "rsps": [
                {"number": 1, "location": "MDF", "zones": [501], "model": "714-16"},
                {"number": 2, "location": "CLASSROOM 27", "zones": [517], "model": "714-16"},
            ],
            "keypads": [{"number": 1, "source": "MSP", "location": "MDF", "global_keypad": True}],
        },
    }
    path = tmp_path / "legacy.dmps"
    path.write_text(json.dumps(legacy))

    first = load_session(path)
    second = load_session(path)

    assert [(c.source.device_id, c.source.port_id, c.target.device_id, c.target.port_id)
            for c in first.design.connections] == [
        ("MSP", "LX500", "710-LX500-1", "IN"),
        ("710-LX500-1", "OUT1", "RSP-1", "IN"),
        ("710-LX500-1", "OUT2", "710-LX500-2", "IN"),
        ("710-LX500-2", "OUT1", "RSP-2", "IN"),
        ("MSP", "KP BUS", "KEYPAD-1", "IN"),
    ]
    assert [c.id for c in first.design.connections] == [c.id for c in second.design.connections]
    assert first.design.splitters[0].outputs == ["RSP-1", "To 710-LX500-2", "Spare"]
    assert first.design.riser_document.title_block.school_name == "LEGACY SCHOOL"
