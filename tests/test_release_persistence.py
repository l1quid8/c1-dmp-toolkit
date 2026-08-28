"""Cross-feature save contracts for the combined riser/RemoteLink release."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import session as session_module
from parse_dmp_worksheet import DMPDesign, Keypad, RSP, SiteInfo, Splitter, ZoneInfo
from riser_model import DevicePortRef, RiserAnnotation, RiserRoute
from rl_injector.rl_config import RLComm, RLKeypad, RLUser, RemoteLinkConfig
from session import Session, SessionLoadError, load_recovery, load_session, save_session, write_recovery
from topology_service import ensure_explicit_topology, reconnect


def _combined_session(path):
    design = DMPDesign(
        site_info=SiteInfo(school_name="MAPLEWOOD DEMO", school_code="9999"),
        splitters=[Splitter("710-LX500-1", "LX", "MDF",
                            {"LX-Bus In": "500 BUS IN FROM XR/550"},
                            ["RSP-1", "Spare", "Spare"])],
        rsps=[RSP(1, "MDF", list(range(501, 517)))],
        keypads=[Keypad(1, "MSP", "LOBBY")],
        zones=[ZoneInfo(501, "ENTRY", "Motion", 1, rl_type="EX")],
    )
    ensure_explicit_topology(design)
    edge = next(c for c in design.connections if c.target.device_id == "RSP-1")
    edge.custom_label = "FIELD CABLE"
    design.riser_document.routes[edge.id] = RiserRoute(
        edge.id, [(100.0, 120.0), (100.0, 240.0)], manual=True)
    design.riser_document.annotations.append(
        RiserAnnotation("note-demo", "text", [(12.0, 24.0)], text="DEMO NOTE"))
    return Session(
        design=design, path=path, source_name="fabricated.pdf",
        remotelink=RemoteLinkConfig(
            account_num="9999", receiver_num="7",
            users=[RLUser(7, "DEMO USER", "9876", "2")], users_customized=True,
            comm=RLComm("network", "2201", "DEMO0001"),
            keypads={1: RLKeypad("ENTRY KEYPAD")},
        ),
    )


@pytest.mark.parametrize("recovery", [False, True])
def test_combined_save_preserves_programming_and_manual_drawing(tmp_path, recovery):
    original = _combined_session(tmp_path / "combined.dmps")
    if recovery:
        write_recovery(original)
        restored = load_recovery(original.path)
    else:
        save_session(original)
        restored = load_session(original.path)

    assert restored.remotelink == original.remotelink
    assert restored.design.zones[0].rl_type == "EX"
    assert restored.design.connections == original.design.connections
    assert restored.design.riser_document == original.design.riser_document
    assert restored.design.riser_document.annotations[0].text == "DEMO NOTE"
    assert next(iter(restored.design.riser_document.routes.values())).manual


def test_schema_two_reader_refuses_combined_save_instead_of_discarding_fields(tmp_path, monkeypatch):
    original = _combined_session(tmp_path / "combined.dmps")
    save_session(original)
    # Both pre-integration apps used this same ceiling for incompatible fields.
    monkeypatch.setattr(session_module, "SCHEMA_VERSION", 2)
    with pytest.raises(SessionLoadError, match="newer version"):
        load_session(original.path)


@pytest.mark.parametrize("source", ["legacy", "remotelink", "riser"])
def test_each_prior_project_format_migrates_without_losing_its_feature(tmp_path, source):
    # Literal old-format data: no serializer under test supplies this fixture.
    design = {
        "site_info": {"school_name": "MIGRATION DEMO", "school_code": "9999"},
        "keypads": [{"number": 1, "source": "MSP", "location": "LOBBY"}],
        "zones": [{"number": 501, "location": "ENTRY", "device_type": "Motion",
                   "partition": 1, "rl_type": "EX" if source == "remotelink" else ""}],
    }
    raw = {"schema_version": 1 if source == "legacy" else 2, "design": design}
    if source == "remotelink":
        raw["remotelink"] = {"account_num": "9999", "receiver_num": "7",
                             "comm": {"connect_type": "direct", "port": "2301"}}
    if source == "riser":
        # Empty is authoritative, even if old compatibility strings still wire it.
        design["connections"] = []
        design["riser_document"] = {
            "title_block": {"school_name": "MIGRATION DEMO"},
            "annotations": [{"id": "old-note", "kind": "text",
                             "points": [[10.0, 20.0]], "text": "KEEP THIS NOTE"}],
        }
    path = tmp_path / f"{source}.dmps"
    path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = load_session(path)
    save_session(migrated)
    restored = load_session(path)

    assert restored.design.riser_document is not None
    if source == "riser":
        assert restored.design.connections == []
        assert restored.design.keypads[0].source is None
        assert restored.design.riser_document.annotations[0].text == "KEEP THIS NOTE"
    else:
        assert [(c.source.device_id, c.source.port_id, c.target.device_id)
                for c in restored.design.connections] == [("MSP", "KP BUS", "KEYPAD-1")]
    if source == "remotelink":
        assert restored.remotelink.receiver_num == "7"
        assert restored.remotelink.comm.connect_type == "direct"
        assert restored.remotelink.comm.port == "2301"
        assert restored.design.zones[0].rl_type == "EX"
    else:
        assert restored.remotelink == RemoteLinkConfig()


def test_riser_rewire_keeps_zone_override_and_account_settings_after_save(tmp_path):
    original = _combined_session(tmp_path / "rewired.dmps")
    edge = next(c for c in original.design.connections if c.target.device_id == "RSP-1")
    reconnect(original.design, edge.id, source=DevicePortRef("710-LX500-1", "OUT3"))
    save_session(original)
    restored = load_session(original.path)
    assert restored.design.splitters[0].outputs == ["Spare", "Spare", "RSP-1"]
    assert restored.design.zones[0].rl_type == "EX"
    assert restored.remotelink.receiver_num == "7"
    assert restored.remotelink.keypads[1].name == "ENTRY KEYPAD"


def test_one_reopened_project_generates_riser_worksheet_chart_and_verified_account(tmp_path):
    """Export paths must share inventory without resetting the other feature."""
    import copy
    import openpyxl
    from generate_dmp_ws import write_dmp_xlsx
    from inject_door_chart import inject
    from riser_render import generate_riser_bundle
    from riser_scene import layout_riser
    from rl_injector.account_doc import parse_account_xml, verify_account_doc
    from rl_injector.xml_export import decode_account, generate_configured_account_xml

    root = Path(__file__).resolve().parents[1]
    original = _combined_session(tmp_path / "export.dmps")
    original.design.zones.extend(
        ZoneInfo(n, "SPARE", "Spare", 1) for n in range(502, 515))
    original.design.zones.extend([
        ZoneInfo(515, "PS-1: A/C LOSS", "Supervisory", 1),
        ZoneInfo(516, "PS-1: BATT. TRBL", "Supervisory", 1),
    ])
    original.design.riser_document = layout_riser(original.design)
    save_session(original)
    restored = load_session(original.path)
    before = copy.deepcopy(restored)

    worksheet = tmp_path / "worksheet.xlsx"
    write_dmp_xlsx(restored.design, root / "DMP Installation Worksheet_template_blank.xlsx", worksheet)
    chart = tmp_path / "chart.xlsx"
    inject(root / "door_chart_template_blank.xlsx", restored.design, chart)
    riser_paths = generate_riser_bundle(restored.design, restored.design.riser_document, tmp_path)
    account = generate_configured_account_xml(
        restored.design, restored.remotelink,
        template_path=root / "remotelink_account_template.xml",
        passphrase="DEMO-TEST-ONLY", out_dir=tmp_path)
    doc = parse_account_xml(decode_account(account.read_bytes(), "DEMO-TEST-ONLY"))
    verify_account_doc(doc)
    zones = {row.text("NUMBER"): row for row in doc.table("ZoneInfoList").rows}

    assert zones["501"].text("TYPE") == "EX"
    assert zones["502"].text("TYPE") == "--"
    assert zones["515"].text("TYPE") == "SV"
    assert doc.row("Account").text("RECVR_NUM") == "7"
    assert doc.table("DeviceInfoList").rows[0].text("NAME") == "ENTRY KEYPAD"
    assert len(riser_paths) == 3 and all(p.stat().st_size > 0 for p in riser_paths)
    assert "710-LX500-1" in next(p for p in riser_paths if p.suffix == ".svg").read_text()
    wb = openpyxl.load_workbook(worksheet)
    try:
        assert wb["710 Splitter-Repeater LX500"]["A2"].value == "710-LX500-1"
        assert wb["710 Splitter-Repeater LX500"]["C3"].value == "RSP-1"
    finally:
        wb.close()
    wb = openpyxl.load_workbook(chart)
    try:
        assert wb["Master"]["A29"].value == "710-LX500-1"
        assert wb["Master"]["B67"].value == "ENTRY"
    finally:
        wb.close()
    assert restored.remotelink == before.remotelink
    assert restored.design.zones == before.design.zones
    assert restored.design.connections == before.design.connections
    assert restored.design.riser_document == before.design.riser_document
