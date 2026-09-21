"""Blank-project initialization and persistence contract."""

import copy
import json
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_dmp_worksheet import SiteInfo  # noqa: E402
from riser_model import RiserTitleBlock  # noqa: E402
from riser_editor import RiserEditorController  # noqa: E402
from rl_injector.rl_config import RemoteLinkConfig  # noqa: E402
import session as session_mod  # noqa: E402
from session import (  # noqa: E402
    SCHEMA_VERSION,
    load_recovery,
    load_session,
    recovery_path,
    save_session,
    write_recovery,
)


def _create_blank_session(*args, **kwargs):
    factory = getattr(session_mod, "create_blank_session", None)
    assert factory is not None, "session.create_blank_session is missing"
    return factory(*args, **kwargs)


@pytest.fixture
def session_path(tmp_path):
    return tmp_path / "manual-site.dmps"


@pytest.mark.parametrize("project_title, confirm, prompt_count, copied", [
    ("", False, 0, True),
    ("UPDATED SITE", False, 0, True),
    ("Independent project", False, 1, False),
    ("Independent project", True, 1, True),
])
def test_explicit_site_copy_confirms_title_conflicts_and_preserves_drawing_fields(
    session_path, project_title, confirm, prompt_count, copied
):
    session = _create_blank_session(
        SiteInfo(school_name="Original site"),
        title_block_updates={
            "project_title": project_title, "drawing_title": "Custom drawing",
            "system": "Custom system", "sheet_number": "S-9",
            "drawn_by": "PREPARED", "checked_by": "CHECKED",
            "issue_date": "2025-01-02", "revisions": ["Revision A"],
        },
    )
    session.path = session_path
    save_session(session)
    loaded = load_session(session_path)
    controller = RiserEditorController(loaded.design, loaded.design.riser_document)
    before = copy.deepcopy(controller.document.title_block)
    site = loaded.design.site_info
    site.school_name = "UPDATED SITE"
    site.school_code = "LC-9"
    site.address_line1 = "1500 Sycamore Lane"
    site.address_line2 = "Riverton, CA 90000"
    controller.sync_external()
    assert controller.document.title_block == before
    prompts = []

    def confirm_overwrite(current, replacement):
        prompts.append((current, replacement))
        return confirm

    result = controller.copy_title_from_site(confirm_overwrite=confirm_overwrite)

    assert result is copied
    assert len(prompts) == prompt_count
    if prompt_count:
        assert prompts == [("Independent project", "UPDATED SITE")]
    if not copied:
        assert controller.document.title_block == before
        assert not controller.can_undo
        return
    expected = copy.deepcopy(before)
    expected.school_name = "UPDATED SITE"
    expected.local_code = "LC-9"
    expected.address = "1500 Sycamore Lane\nRiverton, CA 90000"
    expected.project_title = "UPDATED SITE"
    assert controller.document.title_block == expected
    assert controller.undo()
    assert controller.document.title_block == before
    assert site.school_name == "UPDATED SITE"
    assert controller.redo()
    assert controller.document.title_block == expected


def test_site_copy_without_confirmation_cannot_replace_an_independent_title():
    session = _create_blank_session(SiteInfo(school_name="Site"),
                                    title_block_updates={"project_title": "Independent"})
    controller = RiserEditorController(session.design, session.design.riser_document)
    before = copy.deepcopy(controller.document)

    assert controller.copy_title_from_site() is False

    assert controller.document == before
    assert not controller.can_undo


def test_site_copy_clears_missing_site_fields_without_copying_install_metadata():
    session = _create_blank_session(
        SiteInfo(install_tech="SITE TECH", install_date="2026-09-12"),
        title_block_updates={"school_name": "Old school", "local_code": "Old code",
                             "address": "Old address", "project_title": ""},
    )
    controller = RiserEditorController(session.design, session.design.riser_document)

    assert controller.copy_title_from_site() is True

    title = controller.document.title_block
    assert (title.school_name, title.local_code, title.address, title.project_title) == ("", "", "", "")
    assert title.drawn_by == RiserTitleBlock().drawn_by
    assert title.issue_date == RiserTitleBlock().issue_date
    assert controller.copy_title_from_site() is False


def test_blank_session_has_manual_identity_and_empty_canonical_graph():
    """The factory must create an ordinary manual session without imported data."""
    session = _create_blank_session(SiteInfo(school_name="MANUAL SITE"))

    assert session.source_kind == "manual"
    assert session.source_name == ""
    assert session.remotelink == RemoteLinkConfig()
    assert session.design.site_info.school_name == "MANUAL SITE"
    assert session.design.topology_source == "manual"
    assert session.design.splitters == []
    assert session.design.rsps == []
    assert session.design.keypads == []
    assert session.design.power_supplies == []
    assert session.design.zones == []
    assert session.design.master_zones == []
    assert session.design.connections == []
    assert session.design.equipment_locations == {}
    assert session.design.riser_document is not None
    assert session.design.riser_document.elements == {}
    assert session.design.riser_document.routes == {}
    assert session.design.riser_document.title_block.school_name == "MANUAL SITE"
    assert session.design.riser_document.title_block.project_title == "MANUAL SITE"


def test_blank_session_applies_persisted_title_fields_and_keeps_defaults():
    """Setup values map to real title fields; omitted values retain title defaults."""
    session = _create_blank_session(
        SiteInfo(school_name="MANUAL SITE"),
        title_block_updates={
            "school_name": "UPDATED SITE",
            "local_code": "A-123",
            "address": "1500 Sycamore Lane\nRiverton, CA 90000",
            "project_title": "UPDATED RISER",
            "sheet_number": "INT-7.0",
            "drawn_by": "FIELD TECH",
            "issue_date": "2026-09-12",
            "unknown_field": "must be ignored",
        },
    )

    title = session.design.riser_document.title_block
    assert title.school_name == "UPDATED SITE"
    assert title.local_code == "A-123"
    assert title.address == "1500 Sycamore Lane\nRiverton, CA 90000"
    assert title.project_title == "UPDATED RISER"
    assert title.sheet_number == "INT-7.0"
    assert title.drawn_by == "FIELD TECH"
    assert title.issue_date == "2026-09-12"
    assert title.drawing_title == RiserTitleBlock().drawing_title
    assert title.system == RiserTitleBlock().system
    assert title.checked_by == RiserTitleBlock().checked_by
    assert title.revisions == []
    assert not hasattr(title, "unknown_field")


@pytest.mark.parametrize("recovery", [False, True])
def test_blank_session_save_and_recovery_round_trip_preserve_manual_state(
    session_path, recovery
):
    """Normal and crash-recovery persistence must retain manual blank-project state."""
    session = _create_blank_session(
        SiteInfo(
            school_name="MANUAL SITE",
            school_code="A-123",
            address_line1="1500 Sycamore Lane",
            address_line2="Riverton, CA 90000",
            xr550_location="MDF",
        ),
        title_block_updates={
            "local_code": "A-123",
            "address": "1500 Sycamore Lane\nRiverton, CA 90000",
            "sheet_number": "INT-7.0",
        },
    )
    session.path = session_path

    if recovery:
        saved_path = write_recovery(session)
        restored = load_recovery(session_path)
        assert saved_path == recovery_path(session_path)
    else:
        saved_path = save_session(session)
        restored = load_session(saved_path)
        raw = json.loads(saved_path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == SCHEMA_VERSION == 8

    assert restored.source_kind == "manual"
    assert restored.design.connections == []
    assert restored.design.topology_source == "manual"
    assert restored.design.site_info.xr550_location == "MDF"
    assert len(restored.design.equipment_locations) == 1
    location = next(iter(restored.design.equipment_locations.values()))
    assert location.full_label == "MDF"
    assert restored.design.device_location_ids == session.design.device_location_ids
    assert restored.design.equipment_locations == session.design.equipment_locations
    assert restored.design.riser_document.title_block.local_code == "A-123"
    assert restored.design.riser_document.title_block.address == (
        "1500 Sycamore Lane\nRiverton, CA 90000"
    )
    assert restored.design.riser_document.title_block.sheet_number == "INT-7.0"


def _readiness(name, *args):
    import validation
    helper = getattr(validation, name, None)
    assert helper is not None, f"validation.{name} is missing"
    return helper(*args)


def _manual_export_session():
    from hardware import add_expander, add_keypad, add_splitter
    from riser_model import DevicePortRef
    from topology_service import connect
    from riser_scene import layout_riser
    project = _create_blank_session(SiteInfo(
        school_name="MANUAL SITE", school_code="9999", xr550_location="MDF",
        address_line1="1500 Sycamore Lane", address_line2="Riverton, CA 90000",
        ip_address="192.0.2.10", default_gateway="192.0.2.1",
        install_tech="FIELD TECH", install_date="2026-09-12",
    ), title_block_updates={"drawn_by": "DRAWING TECH", "sheet_number": "S-9"})
    design = project.design
    rsp = add_expander(design, "714-16", "WING A ROOM 101")
    splitter = add_splitter(design, "LX", "MDF")
    keypad = add_keypad(design, "MDF (Service Keypad)", source="MSP")
    assert (rsp.number, splitter.id, keypad.number) == (1, "710-LX500-1", 1)
    design.zones[0].location, design.zones[0].device_type = "ENTRY", "Motion"
    design.zones[0].rl_type = "EX"
    for source, target, label in [
        (DevicePortRef("MSP", "LX500"), DevicePortRef(splitter.id, "IN"), "FEED CABLE"),
        (DevicePortRef(splitter.id, "OUT1"), DevicePortRef("RSP-1", "IN"), "FIELD CABLE"),
        (DevicePortRef("MSP", "KP BUS"), DevicePortRef("KEYPAD-1", "IN"), "SERVICE CABLE"),
    ]:
        connect(design, source, target, cable_type="WP240R", status="existing",
                quantity=2, custom_label=label)
    title = design.riser_document.title_block
    design.riser_document = layout_riser(design)
    design.riser_document.title_block = title
    project.topology_confirmed = True
    return project


@pytest.mark.parametrize("missing", ["rsps", "zones", "ip_address", "default_gateway",
                                      "install_date", "install_tech", "topology"])
def test_worksheet_readiness_explains_missing_export_data_without_mutating(missing):
    project = _manual_export_session()
    if missing in ("rsps", "zones"):
        setattr(project.design, missing, [])
    elif missing == "topology":
        project.topology_confirmed = False
    else:
        setattr(project.design.site_info, missing, "")
    before = copy.deepcopy(project)

    issues = _readiness("worksheet_readiness_issues", project.design, project)

    expected = {"rsps": "worksheet.no_rsps", "zones": "worksheet.no_zones",
                "topology": "topology.unconfirmed"}.get(missing, "site.required_missing")
    assert any(issue.code == expected for issue in issues)
    if missing not in ("rsps", "zones", "topology"):
        assert any(issue.ref == f"field:{missing}" for issue in issues)
    assert all(issue.severity == "warning" for issue in issues)
    assert project == before


def test_ready_manual_project_needs_no_worksheet_or_remotelink_conversion():
    project = _manual_export_session()
    before = copy.deepcopy(project)

    assert _readiness("worksheet_readiness_issues", project.design, project) == []
    assert _readiness("remotelink_readiness_issues", project.design, project.remotelink) == []
    assert project == before


@pytest.mark.parametrize("site_code, account, ready", [
    ("", "", False), ("LC-9", "", False), ("9999", "bad", False),
    ("9999", "", True), ("bad", " 1234 ", True),
])
def test_remotelink_readiness_uses_existing_account_fallback(site_code, account, ready):
    project = _manual_export_session()
    project.design.site_info.school_code = site_code
    project.remotelink.account_num = account
    before = copy.deepcopy(project)

    issues = _readiness("remotelink_readiness_issues", project.design, project.remotelink)

    assert (not any(i.code == "remotelink.account_numeric" for i in issues)) is ready
    assert project == before


@pytest.mark.parametrize("representation, installed, ready", [
    ("editable", True, True), ("master", True, True),
    ("master", False, False), ("empty_range", True, False),
    ("outside_range", True, False),
])
def test_remotelink_readiness_matches_normalized_installed_zone_staging(
    representation, installed, ready
):
    from parse_dmp_worksheet import Zone
    from session import sync_master_zones
    project = _manual_export_session()
    if representation != "editable":
        project.design.zones = []
        project.design.master_zones = [Zone(501, description="ENTRY")]
    if not installed:
        project.design.rsps = []
    if representation == "empty_range":
        project.design.rsps[0].zones = []
    if representation == "outside_range":
        project.design.master_zones = [Zone(999, description="UNINSTALLED")]
    before = copy.deepcopy(project)

    issues = _readiness("remotelink_readiness_issues", project.design, project.remotelink)

    assert (not any(i.code == "remotelink.no_installed_zones" for i in issues)) is ready
    assert project == before
    snapshot = copy.deepcopy(project.design)
    sync_master_zones(snapshot)
    from rl_injector.schema import build_staging_account
    assert bool(build_staging_account(snapshot, "9999", "1").zones) is ready


def test_remotelink_readiness_retains_existing_config_warnings():
    from rl_injector.rl_config import validate_config
    project = _manual_export_session()
    project.remotelink.comm.connect_type = "unverified"
    before = copy.deepcopy(project)

    issues = _readiness("remotelink_readiness_issues", project.design, project.remotelink)

    assert [(i.code, i.ref, i.message) for i in issues] == [
        (i.code, i.ref, i.message) for i in validate_config(project.remotelink, project.design)]
    assert all(i.severity == "warning" for i in issues)
    assert project == before


@pytest.fixture
def export_controller(tmp_path, monkeypatch):
    """Use real App orchestration and exports; replace only UI/async boundaries."""
    import io
    from types import SimpleNamespace
    import app as app_module
    controller = object.__new__(app_module.App)
    controller.session = _manual_export_session()
    controller.state, controller._generating = "editing", None
    controller.pdf_path = controller.dmp_path = controller.door_chart_path = None
    controller.output_dir, controller.root = tmp_path, None
    controller._ws_epoch, controller._redirector = None, io.StringIO()
    notes, messages, completed = [], [], []

    def show_issues(proceed, *, proceed_label, note=None):
        notes.append(note)
        proceed()

    controller.editor = SimpleNamespace(
        dirty=False, edit_epoch=0, generation_allowed=lambda: True,
        flush_design_refresh=lambda: None, show_issues_dialog=show_issues,
        set_generating=lambda which: None,
        tabs=SimpleNamespace(set=lambda name: None),
    )
    controller._choose_riser_outputs = lambda: ["11x17", "svg"]
    controller._show_toast = lambda *args, **kw: completed.append(kw["folder"])

    def run(work, on_done, on_error):
        # Let export failures fail the test instead of swallowing them in a fake.
        on_done(work())

    controller._run_async = run
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})
    monkeypatch.setattr(app_module, "save_prefs", lambda prefs: None)
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **kw: messages.append(a))
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **kw: (messages.append(a), True)[1])
    return controller, notes, messages, completed


@pytest.mark.parametrize("reopen", [False, True])
def test_manual_chart_cannot_use_unrelated_same_named_worksheet(export_controller, reopen):
    controller, _, messages, completed = export_controller
    unrelated = controller.output_dir / "MANUAL_SITE_dmp_rev9.xlsx"
    unrelated.write_bytes(b"another project's worksheet")
    if reopen:
        save_session(controller.session, controller.output_dir / "MANUAL_SITE (2).dmps")
        controller.session = load_session(controller.session.path)

    assert controller._latest_worksheet_path() is None
    controller._generate_door_chart()

    assert messages[-1][0] == "No worksheet yet"
    assert completed == []
    assert unrelated.read_bytes() == b"another project's worksheet"


@pytest.mark.parametrize("source_kind", ["pdf", "xlsx", ""])
def test_imported_chart_disk_fallback_is_unchanged(export_controller, source_kind):
    controller, _, _, _ = export_controller
    controller.session.source_kind = source_kind
    worksheet = controller.output_dir / "MANUAL_SITE_dmp_rev9.xlsx"
    worksheet.write_bytes(b"existing worksheet")

    assert controller._latest_worksheet_path() == worksheet


def test_worksheet_controller_shows_readiness_warning_without_blocking(export_controller):
    controller, notes, _, completed = export_controller
    controller.session = _create_blank_session(SiteInfo(school_name="MANUAL SITE"))
    before = copy.deepcopy(controller.session)

    controller._generate_worksheet()

    assert notes and "RSP" in notes[0] and "zones" in notes[0]
    assert "Panel IP" in notes[0]
    assert controller.dmp_path.exists() and completed == [controller.dmp_path]
    assert controller.session == before


@pytest.mark.parametrize("invalid", ["account", "zones"])
def test_remotelink_hard_readiness_precedes_passphrase_dialog(export_controller, invalid):
    controller, _, messages, _ = export_controller
    if invalid == "account":
        controller.session.design.site_info.school_code = "LC-9"
    else:
        controller.session.design.rsps = []
    opened = []
    controller._show_remotelink_dialog = lambda: opened.append("passphrase")
    before = copy.deepcopy(controller.session)

    controller._generate_remotelink()

    assert not opened
    assert messages and messages[0][0] == "RemoteLink not ready"
    assert controller.session == before


def test_remotelink_ready_controller_proceeds_with_nonblocking_config_warnings(export_controller):
    controller, _, messages, _ = export_controller
    controller.session.remotelink.comm.connect_type = "unverified"
    opened = []
    controller._show_remotelink_dialog = lambda: opened.append("passphrase")

    controller._generate_remotelink()

    assert opened == ["passphrase"]
    assert messages and "verified RemoteLink connection" in messages[0][1]


def test_msp_only_manual_riser_generates_after_existing_warning_confirmation(export_controller):
    import fitz
    controller, _, messages, completed = export_controller
    controller.session = _create_blank_session(SiteInfo(school_name="MANUAL SITE"))
    before = copy.deepcopy(controller.session)

    controller._generate_riser()

    assert any(m[0] == "Riser validation warnings" for m in messages)
    assert len(completed) == 1
    pdf = next(controller.output_dir.glob("*.pdf"))
    with fitz.open(pdf) as document:
        assert "MSP" in document[0].get_text()
    svg = next(controller.output_dir.glob("*.svg"))
    assert 'id="device:MSP"' in svg.read_text()
    assert controller.session == before


@pytest.mark.parametrize("which", ["worksheet", "riser"])
def test_cancelled_export_warnings_preserve_unnormalized_project(export_controller, monkeypatch, which):
    import app as app_module
    controller, _, _, completed = export_controller
    controller.session.design.riser_document.elements.clear()
    controller.session.design.master_zones = []
    controller.session.design.splitters[0].outputs = ["OLD FEED", "Spare", "Spare"]
    before = copy.deepcopy(controller.session)
    controller.editor.show_issues_dialog = lambda proceed, **kw: None
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **kw: False)

    getattr(controller, f"_generate_{which}")()

    assert controller.session == before
    assert not completed


@pytest.mark.parametrize("which", ["worksheet", "riser", "remotelink"])
def test_export_preparation_preserves_unnormalized_project(export_controller, which):
    controller, _, _, completed = export_controller
    controller.session.design.riser_document.elements.clear()
    controller.session.design.master_zones = []
    controller.session.design.splitters[0].outputs = ["OLD FEED", "Spare", "Spare"]
    before = copy.deepcopy(controller.session)

    if which == "remotelink":
        controller._run_generate_remotelink("MANUAL-TEST-ONLY")
    else:
        getattr(controller, f"_generate_{which}")()

    assert controller.session == before
    assert len(completed) == 1


def test_remotelink_dialog_preparation_cannot_mutate_live_master_before_ui(export_controller, monkeypatch):
    import app as app_module
    controller, _, _, _ = export_controller
    controller.session.design.master_zones = []
    before = copy.deepcopy(controller.session)

    def stop_before_any_ui(*args, **kw):
        raise RuntimeError("UI boundary deliberately not entered")

    monkeypatch.setattr(app_module.ctk, "CTkToplevel", stop_before_any_ui)

    with pytest.raises(RuntimeError, match="UI boundary deliberately not entered"):
        controller._show_remotelink_dialog()

    assert controller.session == before


def test_remotelink_worker_uses_matching_design_and_config_snapshot(export_controller):
    from rl_injector.account_doc import parse_account_xml, verify_account_doc
    from rl_injector.xml_export import decode_account
    controller, _, _, completed = export_controller
    queued = []
    controller._run_async = lambda work, done, error: queued.append((work, done))

    controller._run_generate_remotelink("MANUAL-TEST-ONLY")
    controller.session.remotelink.account_num = "1234"
    controller.session.design.zones[0].rl_type = "NT"
    before_work = copy.deepcopy(controller.session)
    work, done = queued.pop()
    done(work())

    account = controller.output_dir / "9999_remotelink.xml"
    assert completed == [account]
    doc = parse_account_xml(decode_account(account.read_bytes(), "MANUAL-TEST-ONLY"))
    verify_account_doc(doc)
    zones = {row.text("NUMBER"): row for row in doc.table("ZoneInfoList").rows}
    assert doc.row("Account").text("ACCOUNT_NUM") == "9999"
    assert zones["501"].text("TYPE") == "EX"
    assert controller.session == before_work


def test_worksheet_snapshot_keeps_mid_generation_edits_stale_for_chart(export_controller):
    import openpyxl
    controller, _, messages, _ = export_controller
    queued = []
    controller._run_async = lambda work, done, error: queued.append((work, done))
    controller.editor.edit_epoch = 4

    controller._generate_worksheet()
    controller.session.design.zones[0].location = "EDITED AFTER GENERATION STARTED"
    controller.editor.edit_epoch = 5
    before_work = copy.deepcopy(controller.session)
    work, done = queued.pop()
    done(work())

    workbook = openpyxl.load_workbook(controller.dmp_path)
    try:
        assert workbook["Master"]["B2"].value == "ENTRY"
    finally:
        workbook.close()
    assert controller.session == before_work
    assert controller._ws_epoch == 4
    controller._generate_door_chart()
    assert messages and messages[-1][0] == "Worksheet may be stale"


def test_source_free_manual_session_saves_reopens_and_exports_all_deliverables(export_controller):
    import fitz
    import openpyxl
    import xml.etree.ElementTree as ET
    from rl_injector.account_doc import parse_account_xml, verify_account_doc
    from rl_injector.xml_export import decode_account
    controller, _, _, completed = export_controller
    save_session(controller.session, controller.output_dir / "manual.dmps")
    controller.session = load_session(controller.session.path)
    before = copy.deepcopy(controller.session)

    controller._generate_worksheet()
    assert controller._latest_worksheet_path() == controller.dmp_path
    assert controller.session == before
    controller._generate_door_chart()
    assert controller.session == before
    controller._generate_riser()
    assert controller.session == before
    controller._run_generate_remotelink("MANUAL-TEST-ONLY")
    assert controller.session == before

    assert len(completed) == 4
    for path, sheet, cell, expected in [
        (controller.dmp_path, "710 Splitter-Repeater LX500", "C3", "RSP-1"),
        (controller.door_chart_path, "Master", "B67", "ENTRY"),
        (controller.door_chart_path, "Master", "A29", "710-LX500-1"),
    ]:
        workbook = openpyxl.load_workbook(path)
        try:
            assert workbook[sheet][cell].value == expected
        finally:
            workbook.close()
    with fitz.open(next(controller.output_dir.glob("*.pdf"))) as pdf:
        text = pdf[0].get_text()
        assert "FIELD CABLE" in text and "DRAWING TECH" in text and "S-9" in text
    svg = ET.parse(next(controller.output_dir.glob("*.svg"))).getroot()
    texts = {node.text for node in svg.iter("{http://www.w3.org/2000/svg}text")}
    assert {"FIELD CABLE", "SERVICE CABLE"} <= texts
    account = controller.output_dir / "9999_remotelink.xml"
    doc = parse_account_xml(decode_account(account.read_bytes(), "MANUAL-TEST-ONLY"))
    verify_account_doc(doc)
    zones = {row.text("NUMBER"): row for row in doc.table("ZoneInfoList").rows}
    assert set(zones) == {str(n) for n in range(501, 517)}
    assert zones["501"].text("TYPE") == "EX"
    assert zones["515"].text("TYPE") == "SV"
    assert doc.row("Account").text("ACCOUNT_NUM") == "9999"
    assert doc.table("DeviceInfoList").rows[0].text("NUMBER") == "1"
    assert before.source_kind == "manual" and before.source_name == ""
    assert len(before.design.equipment_locations) == 2
    assert all(edge.quantity == 2 and edge.status == "existing" for edge in before.design.connections)
