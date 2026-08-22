"""Behavioral tests for the structured RemoteLink account document model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rl_injector.account_doc import (  # noqa: E402
    AccountDoc,
    AccountSummary,
    Field,
    Row,
    SafetyIntent,
    SafetyKind,
    Table,
    collect_safety_findings,
    parse_account_xml,
    render_text,
    summarize_account,
    verify_account_doc,
)
from rl_injector.errors import AccountSafetyError, InjectorError  # noqa: E402


BUNDLED_TEMPLATE = REPO_ROOT / "remotelink_account_template.xml"


def test_bundled_template_round_trips_byte_identically():
    """Any parser/serializer normalization would risk a rejected import."""
    text = BUNDLED_TEMPLATE.read_text(encoding="latin-1")

    assert parse_account_xml(text).serialize() == text


def test_mini_document_round_trips_and_exposes_typed_values():
    """The parser must preserve each supported leaf datatype and row order."""
    text = (
        "<Panels><Panel>"
        '<Account><ID DataType="14">90000</ID>'
        '<NAME DataType="1">VEVTVCBBQ0NPVU5U</NAME>'
        '<ENABLED DataType="5">False</ENABLED>'
        '<OFFSET DataType="3">-1</OFFSET>'
        '<CHANGED DataType="11">2026-08-22 09:30:00.000</CHANGED>'
        "</Account>"
        "<UsersList><Users>"
        '<USER_NUM DataType="3">1</USER_NUM>'
        '<NAME DataType="1">VVNFUgAA</NAME>'
        "</Users></UsersList>"
        "<TimeSchedsList></TimeSchedsList>"
        "</Panel></Panels>"
    )

    doc = parse_account_xml(text)

    assert isinstance(doc, AccountDoc)
    assert doc.row("Account").text("OFFSET") == "-1"
    assert doc.row("Account").text("NAME") == "TEST ACCOUNT"
    assert doc.table("UsersList").rows[0].text("NAME") == "USER"
    assert doc.serialize() == text


def test_empty_table_keeps_explicit_open_and_close_tags():
    """RemoteLink's empty-list spelling must not become a self-closing tag."""
    text = (
        "<Panels><Panel><TimeSchedsList></TimeSchedsList>"
        "</Panel></Panels>"
    )

    doc = parse_account_xml(text)

    assert doc.table("TimeSchedsList").rows == []
    assert doc.serialize() == text
    assert "<TimeSchedsList/>" not in doc.serialize()


def test_empty_datatype_one_field_decodes_and_stays_empty():
    """An empty Wi-Fi key is valid and must survive decoding and re-encoding."""
    text = (
        '<Panels><Panel><NetOpts><WIFI_KEY DataType="1"></WIFI_KEY>'
        "</NetOpts></Panel></Panels>"
    )

    doc = parse_account_xml(text)
    field = doc.row("NetOpts").require("WIFI_KEY")

    assert field.text == ""
    field.set_text("")
    assert field.raw == ""
    assert doc.serialize() == text


def test_statlist_is_a_row_and_empty_schedule_list_is_a_table():
    """Classification must use structure because StatList is not a list."""
    doc = parse_account_xml(BUNDLED_TEMPLATE.read_text(encoding="latin-1"))

    assert isinstance(doc.find("StatList"), Row)
    assert len(doc.row("StatList").fields) == 13
    assert isinstance(doc.find("TimeSchedsList"), Table)
    assert doc.table("TimeSchedsList").rows == []
    assert [node.name for node in doc.nodes] == [
        "Account", "RmtOpts", "SysRpts", "SysOpts", "OutOpts",
        "OutGrpsList", "MenuDisp", "StatList", "AreaInfoList",
        "ZoneInfoList", "ProfilesList", "UsersList", "AccessCode",
        "PartInfoList", "DeviceInfoList", "HostLogRpts", "NetOpts",
        "BellOpts", "TimeSchedsList", "AreaTimeSchedsList",
    ]


def test_same_field_name_keeps_its_per_occurrence_datatype():
    """A global tag-to-datatype map would corrupt one of the AMBUSH fields."""
    doc = parse_account_xml(BUNDLED_TEMPLATE.read_text(encoding="latin-1"))

    report = doc.row("SysRpts").require("AMBUSH")
    output = doc.row("OutOpts").require("AMBUSH")

    assert (report.data_type, report.raw) == ("5", "False")
    assert (output.data_type, output.raw) == ("3", "0")


def test_ragged_profile_rows_preserve_their_own_fields():
    """Rows cannot be forced into a fixed table schema without data loss."""
    doc = parse_account_xml(BUNDLED_TEMPLATE.read_text(encoding="latin-1"))

    assert {len(row.fields) for row in doc.table("ProfilesList").rows} == {41, 42}


def test_datatype_one_set_text_never_emits_base64_padding():
    """RemoteLink mis-decodes ordinary '='-padded base64 string values."""
    field = Field("NAME", "1", "")

    field.set_text("A")

    assert field.text == "A"
    assert "=" not in field.raw


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("<Panel></Panel>", "must start"),
        ("<Panels><Panel>oops</Panel></Panels>", "offset"),
        (
            '<Panels><Panel><Account><ID DataType="3">1</ID></Wrong>'
            "</Panel></Panels>",
            "Account",
        ),
        (
            '<Panels><Panel><Account><ID DataType="3" Extra="1">1</ID>'
            "</Account></Panel></Panels>",
            "offset",
        ),
        (
            "<Panels><Panel><ItemsList><Item>"
            '<ID DataType="3">1</ID><Nested><X DataType="3">2</X></Nested>'
            "</Item></ItemsList></Panel></Panels>",
            "unsupported nesting",
        ),
        (
            "<Panels><Panel><TimeSchedsList></TimeSchedsList>"
            "</Panel></Panels>trailing",
            "trailing",
        ),
    ],
)
def test_parser_rejects_shapes_it_cannot_preserve(text, message):
    """Unsupported XML must fail loudly instead of being normalized silently."""
    with pytest.raises(InjectorError, match=message):
        parse_account_xml(text)


def _bundled_doc() -> AccountDoc:
    return parse_account_xml(BUNDLED_TEMPLATE.read_text(encoding="latin-1"))


def _add_schedule(doc: AccountDoc) -> None:
    doc.table("TimeSchedsList").rows.append(Row("TimeScheds", [
        Field("ACCOUNT_ID", "3", "90000"),
        Field("NUMBER", "3", "1"),
        Field("NAME", "1", "QVJNSU5HIFNDSEVEVUxF"),
    ]))


def _add_area_schedule_link(doc: AccountDoc) -> None:
    doc.table("AreaTimeSchedsList").rows[0].fields.append(
        Field("SCHED_1", "3", "1")
    )


def test_clean_template_has_no_safety_findings():
    """Safe dealer defaults must not be reported as active panel behavior."""
    doc = _bundled_doc()

    assert collect_safety_findings(doc) == []
    verify_account_doc(doc, SafetyIntent())


def test_schedule_definition_is_a_safety_finding():
    """A template-carried schedule must be visible even without an area link."""
    doc = _bundled_doc()
    _add_schedule(doc)

    findings = collect_safety_findings(doc)

    assert [finding.kind for finding in findings] == [SafetyKind.SCHEDULE]
    assert "ARMING SCHEDULE" in findings[0].message


def test_area_schedule_link_is_a_safety_finding():
    """An area link can activate a schedule and must be checked independently."""
    doc = _bundled_doc()
    _add_area_schedule_link(doc)

    findings = collect_safety_findings(doc)

    assert [finding.kind for finding in findings] == [SafetyKind.AREA_SCHEDULE_LINK]
    assert "Area 1" in findings[0].message


def test_ambush_report_setting_is_a_safety_finding():
    """SysRpts AMBUSH=True allows user-code duress reports."""
    doc = _bundled_doc()
    doc.row("SysRpts").set("AMBUSH", "True")

    assert [f.kind for f in collect_safety_findings(doc)] == [
        SafetyKind.AMBUSH_REPORTS
    ]


def test_ambush_output_setting_is_a_safety_finding():
    """A nonzero ambush output can operate hardware and cannot be implicit."""
    doc = _bundled_doc()
    doc.row("OutOpts").set("AMBUSH", "7")

    assert [f.kind for f in collect_safety_findings(doc)] == [
        SafetyKind.AMBUSH_OUTPUT
    ]


def test_morning_ambush_setting_is_a_safety_finding():
    """A nonzero countdown can silently trigger ambush after disarm."""
    doc = _bundled_doc()
    doc.table("PartInfoList").rows[0].set("MORN_AMBSH", "5")

    assert [f.kind for f in collect_safety_findings(doc)] == [
        SafetyKind.MORNING_AMBUSH
    ]


def test_auto_arm_and_disarm_settings_are_safety_findings():
    """Either automated area action must require explicit operator intent."""
    doc = _bundled_doc()
    area = doc.table("AreaInfoList").rows[0]
    area.set("AUTO_ARM", "True")
    area.set("AUTO_DISRM", "True")

    findings = collect_safety_findings(doc)

    assert {f.kind for f in findings} == {SafetyKind.AUTO_ARM_DISARM}
    message = " ".join(f.message for f in findings)
    assert "AUTO_ARM" in message and "AUTO_DISRM" in message


def test_gate_reports_every_unintended_dangerous_setting_at_once():
    """Generation errors must reveal all unsafe drift, not one item per run."""
    doc = _bundled_doc()
    _add_schedule(doc)
    _add_area_schedule_link(doc)
    doc.row("SysRpts").set("AMBUSH", "True")
    doc.row("OutOpts").set("AMBUSH", "7")
    doc.table("PartInfoList").rows[0].set("MORN_AMBSH", "5")
    doc.table("AreaInfoList").rows[0].set("AUTO_ARM", "True")

    with pytest.raises(AccountSafetyError) as caught:
        verify_account_doc(doc, SafetyIntent())

    message = str(caught.value)
    for marker in (
        "ARMING SCHEDULE", "SCHED_1", "SysRpts.AMBUSH",
        "OutOpts.AMBUSH", "MORN_AMBSH", "AUTO_ARM",
    ):
        assert marker in message


def test_explicit_intent_allows_generation_but_keeps_findings_visible():
    """Intent changes the hard gate, not the receipt's warning content."""
    doc = _bundled_doc()
    _add_schedule(doc)
    _add_area_schedule_link(doc)
    intent = SafetyIntent(frozenset({
        SafetyKind.SCHEDULE,
        SafetyKind.AREA_SCHEDULE_LINK,
    }))

    verify_account_doc(doc, intent)

    assert {f.kind for f in summarize_account(doc).warnings} == {
        SafetyKind.SCHEDULE,
        SafetyKind.AREA_SCHEDULE_LINK,
    }


def test_bundled_template_summary_reports_identity_users_and_contents():
    """All UI receipts must derive from one complete read-back summary."""
    summary = summarize_account(_bundled_doc())

    assert isinstance(summary, AccountSummary)
    assert (summary.account_num, summary.internal_id, summary.receiver_num) == (
        "1", "90000", "1"
    )
    assert summary.name == "DEMO TEMPLATE ACCOUNT"
    assert summary.zone_total == 61
    assert (summary.zone_real, summary.zone_spare) == (60, 1)
    assert [(u.number, u.name, u.profile) for u in summary.users] == [
        ("1", "USER", "1"),
        ("9999", "TECHNICIAN", "99"),
    ]
    assert summary.keypads == [
        ("1", "KEYPAD 1"), ("2", "KEYPAD 2"),
        ("3", "KEYPAD 3"), ("4", "KEYPAD 4"),
    ]
    assert summary.warnings == []


def test_render_text_distinguishes_clean_and_unsafe_accounts():
    """The operator receipt needs an unmistakable unsafe-state marker."""
    clean = render_text(summarize_account(_bundled_doc()))
    unsafe_doc = _bundled_doc()
    unsafe_doc.table("PartInfoList").rows[0].set("MORN_AMBSH", "5")
    unsafe = render_text(summarize_account(unsafe_doc))

    assert "RemoteLink Account Summary" in clean
    assert "Arming schedules" in clean and "none" in clean
    assert "⚠" not in clean
    assert "⚠ SAFETY" in unsafe
    assert "MORN_AMBSH=5" in unsafe
