"""Behavioral tests for the structured RemoteLink account document model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rl_injector.account_doc import (  # noqa: E402
    AccountDoc,
    Field,
    Row,
    Table,
    parse_account_xml,
)
from rl_injector.errors import InjectorError  # noqa: E402


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
