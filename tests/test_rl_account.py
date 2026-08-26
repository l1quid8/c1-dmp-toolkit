"""Tests for the RemoteLink account generation path (scripts/rl_injector).

The staging logic (DMPDesign -> StagingAccount) and the .xml encoder both run on
every OS with no external template — a small synthetic template is built inline.

Run: pytest tests/test_rl_account.py
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from parse_dmp_worksheet import (  # noqa: E402
    DMPDesign, SiteInfo, RSP, Keypad, Zone, ZoneInfo,
)
from rl_injector.schema import (  # noqa: E402
    ZONE_TYPE_NIGHT,
    ZONE_TYPE_SPARE,
    ZONE_TYPE_SUPERVISORY,
    build_staging_account,
)
from rl_injector.errors import InjectorError  # noqa: E402
from rl_injector.account_doc import (  # noqa: E402
    SafetyIntent,
    render_text,
    summarize_account,
    verify_account_doc,
)
from rl_injector.rl_config import (  # noqa: E402
    RLAdvanced,
    RLArming,
    RLComm,
    RLKeypad,
    RLScheduleDay,
    RLUser,
    RemoteLinkConfig,
)
from rl_injector.xml_export import (  # noqa: E402
    _b64,
    build_account_doc,
    build_account_xml,
    decode_account,
    encode_account,
    generate_configured_account_xml,
    generate_account_xml,
    inspect_account,
    preview_account_summary,
    build_configured_account_doc,
)


def _rl_design(school_code="2250") -> DMPDesign:
    """A small design: two 16-point RSPs, and master zones covering four motion
    zones, one spare, and two supervisory (A/C-loss + battery) points."""
    d = DMPDesign(site_info=SiteInfo(
        school_name="TEST ELEMENTARY SCHOOL", school_code=school_code,
        address_line1="1 MAIN ST", address_line2="ENCINO, CA 91316",
        phone="(818) 555-1212"))
    d.rsps = [RSP(number=1, zones=list(range(501, 517))),
              RSP(number=2, zones=list(range(517, 533)))]
    d.keypads = [Keypad(number=1), Keypad(number=2), Keypad(number=1)]  # dup collapses
    d.master_zones = [
        Zone(number=501, description="ROOM 101"),
        Zone(number=502, description="ROOM 102"),
        Zone(number=503, description="ROOM 103"),
        Zone(number=504, description="ROOM 104"),
        Zone(number=505, description="SPARE", is_spare=True),
        Zone(number=515, description="PS-1 A/C", is_ps_ac=True),
        Zone(number=516, description="PS-1 BATT", is_ps_batt=True),
    ]
    return d


def test_build_staging_account_counts():
    acct = build_staging_account(_rl_design(), "2250", receiver_num="")
    assert acct.account_num == "2250"
    assert acct.name == "TEST ELEMENTARY SCHOOL"
    assert len(acct.zones) == 7
    assert acct.real_zone_count == 6      # spare excluded
    assert acct.spare_zone_count == 1
    assert acct.keypads == [1, 2]         # de-duplicated + sorted bus numbers


def test_build_staging_account_zone_types():
    acct = build_staging_account(_rl_design(), "2250", receiver_num="")
    types = {z.number: z.zone_type for z in acct.zones}
    assert types[501] == ZONE_TYPE_NIGHT
    assert types[505] == ZONE_TYPE_SPARE
    assert types[515] == ZONE_TYPE_SUPERVISORY
    assert types[516] == ZONE_TYPE_SUPERVISORY


def test_explicit_zone_type_override_wins_over_automatic_derivation():
    """A field-tech Exit selection must replace the default Night type."""
    design = _rl_design()
    design.zones = [ZoneInfo(number=501, rl_type="EX")]

    acct = build_staging_account(design, "2250", receiver_num="")

    assert {z.number: z.zone_type for z in acct.zones}[501] == "EX"


def test_auto_zone_type_preserves_derivation_and_spare_cannot_be_overridden():
    """Auto remains today's behavior and an unused point always stays Spare."""
    design = _rl_design()
    design.zones = [
        ZoneInfo(number=501, rl_type=""),
        ZoneInfo(number=505, rl_type="NT"),
    ]

    acct = build_staging_account(design, "2250", receiver_num="")
    types = {z.number: z.zone_type for z in acct.zones}

    assert types[501] == "NT"
    assert types[505] == "--"


def test_build_staging_account_filters_uninstalled_zones():
    """master_zones outside any installed RSP's point range are not staged."""
    d = _rl_design()
    d.master_zones.append(Zone(number=999, description="GHOST"))  # no RSP owns 999
    acct = build_staging_account(d, "2250", receiver_num="")
    assert 999 not in {z.number for z in acct.zones}


# --- .xml encoder ----------------------------------------------------------

def _zone_xml(num, typ, name):
    return (f'<ZoneInfo><ACCOUNT_ID DataType="3">90000</ACCOUNT_ID>'
            f'<NUMBER DataType="3">{num}</NUMBER>'
            f'<NAME DataType="1">{_b64(name)}</NAME>'
            f'<TYPE DataType="1">{_b64(typ)}</TYPE></ZoneInfo>')


def _mini_template() -> str:
    """A minimal DECRYPTED account XML with the fields the encoder touches:
    Account identity, one prototype zone per TYPE, and a keypad prototype."""
    account = (
        '<Account>'
        '<ID DataType="14">90000</ID>'
        '<RECVR_NUM DataType="3">1</RECVR_NUM>'
        '<ACCOUNT_NUM DataType="3">1</ACCOUNT_NUM>'
        f'<NAME DataType="1">{_b64("TEMPLATE")}</NAME>'
        f'<ADDRESS DataType="1">{_b64("OLD ADDR")}</ADDRESS>'
        f'<CITY DataType="1">{_b64("OLD CITY")}</CITY>'
        f'<STATE DataType="1">{_b64("XX")}</STATE>'
        f'<ZIP DataType="1">{_b64("00000")}</ZIP>'
        f'<PHONE DataType="1">{_b64("OLD PHONE")}</PHONE>'
        '<ACCT_NUM DataType="3">1</ACCT_NUM>'
        '</Account>')
    zones = ('<ZoneInfoList>'
             + _zone_xml(501, "NT", "Z501 A")
             + _zone_xml(515, "SV", "Z515 B")
             + _zone_xml(509, "--", "Z509 SPARE")
             + '</ZoneInfoList>')
    devices = ('<DeviceInfoList><DeviceInfo>'
               '<ACCOUNT_ID DataType="3">90000</ACCOUNT_ID>'
               '<NUMBER DataType="3">1</NUMBER>'
               f'<NAME DataType="1">{_b64("KEYPAD 1")}</NAME>'
               '</DeviceInfo></DeviceInfoList>')
    return '<Panels><Panel>' + account + zones + devices + '</Panel></Panels>'


def test_xml_encode_decode_round_trip():
    doc = "<Panels><Panel><Account><ID DataType=\"14\">90000</ID></Account></Panel></Panels>"
    assert decode_account(encode_account(doc, "6712"), "6712") == doc
    # wrong passphrase must not silently yield the document
    with pytest.raises(InjectorError):
        decode_account(encode_account(doc, "6712"), "0000")


def test_build_account_xml_from_design():
    xml = build_account_xml(
        build_staging_account(_rl_design(), "2250", receiver_num=""),
        _mini_template())
    s = summary_via_decode(xml)
    assert s["account_num"] == "2250" and s["id"] == "92250"
    assert s["name"] == "TEST ELEMENTARY SCHOOL"
    assert s["zone_count"] == 7                      # all 7 staged zones rendered
    assert s["keypads"] == 2                         # two keypad DeviceInfo blocks
    # old internal id fully re-pointed
    assert ">90000<" not in xml and xml.count(">92250<") >= 1


def test_receiver_number_is_written_when_staging_provides_one():
    """The receiver accepted by the public API must land in the account XML."""
    acct = build_staging_account(_rl_design(), "2250", receiver_num="7")

    doc = build_account_doc(acct, _mini_template())

    assert doc.row("Account").text("RECVR_NUM") == "7"


def test_blank_receiver_keeps_the_template_default():
    """Legacy callers that omit receiver number retain the dealer default."""
    acct = build_staging_account(_rl_design(), "2250", receiver_num="")

    doc = build_account_doc(acct, _mini_template())

    assert doc.row("Account").text("RECVR_NUM") == "1"


def test_identity_rebadge_leaves_unrelated_matching_number_untouched():
    """A field whose value happens to equal the old ID is not an identity ref."""
    template = _mini_template().replace(
        "</Account>",
        '<PNL_BAUD DataType="3">90000</PNL_BAUD></Account>',
    )
    acct = build_staging_account(_rl_design(), "2250", receiver_num="")

    doc = build_account_doc(acct, template)

    assert doc.row("Account").text("ID") == "92250"
    assert doc.row("Account").text("PNL_BAUD") == "90000"
    assert {
        field.raw
        for row in doc.iter_rows()
        for field in row.fields
        if field.name == "ACCOUNT_ID"
    } == {"92250"}


def test_generate_account_xml_writes_file(tmp_path):
    tmpl = tmp_path / "template.xml"
    tmpl.write_text(_mini_template(), encoding="latin-1")
    out = generate_account_xml(_rl_design(), "2250", template_path=tmpl,
                               passphrase="secret", out_dir=tmp_path)
    assert out.is_file() and out.name == "2250_remotelink.xml"
    # the file is uppercase hex that decodes back under the passphrase
    text = out.read_text()
    assert text == text.upper() and all(c in "0123456789ABCDEF" for c in text)
    s = summary_via_decode(decode_account(text, "secret"))
    assert s["account_num"] == "2250" and s["zone_count"] == 7


def test_generate_account_xml_requires_passphrase(tmp_path):
    tmpl = tmp_path / "template.xml"
    tmpl.write_text(_mini_template(), encoding="latin-1")
    with pytest.raises(InjectorError):
        generate_account_xml(_rl_design(), "2250", template_path=tmpl,
                             passphrase="", out_dir=tmp_path)


def test_generate_account_xml_rejects_non_numeric_account(tmp_path):
    tmpl = tmp_path / "template.xml"
    tmpl.write_text(_mini_template(), encoding="latin-1")
    with pytest.raises(InjectorError):
        generate_account_xml(_rl_design(), "TEST", template_path=tmpl,
                             passphrase="p", out_dir=tmp_path)


def test_generate_account_xml_missing_template(tmp_path):
    with pytest.raises(InjectorError):
        generate_account_xml(_rl_design(), "2250", template_path=tmp_path / "nope.xml",
                             passphrase="p", out_dir=tmp_path)


def summary_via_decode(xml: str) -> dict:
    """Minimal structure read-back for assertions (avoids depending on rl_xml)."""
    import re
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    def _int(tag):
        el = root.find(f".//{tag}")
        return el.text if el is not None else None
    return {
        "id": _int("ID"),
        "account_num": _int("ACCOUNT_NUM"),
        "name": _decode_b64(root.find(".//Account/NAME").text),
        "zone_count": len(root.findall(".//ZoneInfo")),
        "keypads": len(root.findall(".//DeviceInfo")),
    }


def _decode_b64(v: str) -> str:
    import base64
    return base64.b64decode(v).decode("latin-1").rstrip("\x00")


# --- bundled demo template -------------------------------------------------

BUNDLED_TEMPLATE = REPO_ROOT / "remotelink_account_template.xml"


def test_bundled_template_present_and_scrubbed():
    assert BUNDLED_TEMPLATE.is_file(), "bundled RemoteLink template is missing"
    xml = BUNDLED_TEMPLATE.read_text(encoding="latin-1")
    assert "<Panels>" in xml and xml.count("<ZoneInfo>") >= 3
    # No real customer data may be baked into the public repo. Fields are
    # base64, so decode each and assert none of the source account's identifiers
    # survive.
    import base64
    import re
    # LAUSD (the district) is an intentional dealer-standard Customer value, not
    # per-account identity — it's allowed. These are the source account's private
    # identifiers, which must not survive the scrub into the public repo.
    real = ("DARBY", "NORTHRIDGE", "10818", "360-1824", "0020D16D",
            "000B94289066", "D8D4X0VG", "FACP", "PRINCIPAL", "TEXTBOOK")
    # No private (10.x) IP may survive either.
    for m in re.finditer(r'DataType="1"[^>]*>([A-Za-z0-9+/=]*)<', xml):
        try:
            dec = base64.b64decode(m.group(1)).decode("latin-1").rstrip("\x00")
        except Exception:
            continue
        assert not re.fullmatch(r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}", dec), \
            f"private IP leaked in template: {dec}"
    for m in re.finditer(r'DataType="1"[^>]*>([A-Za-z0-9+/=]*)<', xml):
        try:
            dec = base64.b64decode(m.group(1)).decode("latin-1")
        except Exception:
            continue
        for tok in real:
            assert tok not in dec, f"real data leaked in template: {tok!r}"


def test_generate_account_xml_with_bundled_template(tmp_path):
    out = generate_account_xml(_rl_design(), "2250", template_path=BUNDLED_TEMPLATE,
                               passphrase="secret", out_dir=tmp_path)
    s = summary_via_decode(decode_account(out.read_text(), "secret"))
    assert s["account_num"] == "2250" and s["id"] == "92250"
    assert s["zone_count"] == 7 and s["keypads"] == 2


def _padded_dt1_fields(xml: str) -> list:
    import re
    return [v for v in re.findall(r'DataType="1"[^>]*>([A-Za-z0-9+/=]*)<', xml)
            if "=" in v]


def test_user_codes_default_from_account(tmp_path):
    import base64
    import re
    out = generate_account_xml(_rl_design(), "2250", template_path=BUNDLED_TEMPLATE,
                               passphrase="p", out_dir=tmp_path)
    xml = decode_account(out.read_text(), "p")
    codes = {}
    for m in re.finditer(r"<Users>.*?</Users>", xml, re.S):
        num = re.search(r'<USER_NUM DataType="3">(\d+)</USER_NUM>', m.group(0)).group(1)
        code = re.search(r'<CODE DataType="1">([^<]*)</CODE>', m.group(0)).group(1)
        codes[num] = base64.b64decode(code).decode("latin-1").rstrip("\x00")
    assert codes.get("1") == "2250"       # USER = site code
    assert codes.get("9999") == "12250"   # TECHNICIAN = 1 + site code


def test_generated_account_has_no_arming_schedule(tmp_path):
    """A generated account must not carry an auto-arming schedule.

    The template used to ship a TimeScheds entry named "ARMING SCHEDULE" with
    Area 1 bound to it (AreaTimeScheds SCHED_1=1). Imported as-is that self-arms
    the panel on a timer, which is a per-site decision the tech makes in Remote
    Link — not something a freshly-staged account should stamp in. Both were
    removed from the template; this asserts the property on the generated
    output, so it also catches a template regression.
    """
    out = generate_account_xml(_rl_design(), "2250", template_path=BUNDLED_TEMPLATE,
                               passphrase="p", out_dir=tmp_path)
    xml = decode_account(out.read_text(), "p")
    # No area is linked to a schedule number.
    assert "<SCHED_1" not in xml
    # No schedule definitions survive (the "ARMING SCHEDULE" block is gone).
    assert "<TimeScheds>" not in xml


def test_no_base64_padding_in_bundled_template():
    # Real DMP exports use '=' padding in zero string fields; RemoteLink
    # mis-decodes any '='-padded value into junk characters.
    xml = BUNDLED_TEMPLATE.read_text(encoding="latin-1")
    assert _padded_dt1_fields(xml) == []


def test_no_base64_padding_in_generated_account():
    # Regression: names whose length was ≡1 mod 3 previously produced '=='-padded
    # base64 and rendered as garbage (e.g. "TEXTBOOK ROOM #2▯@") on import.
    d = _rl_design()
    d.master_zones.append(Zone(number=506, description="TEXTBOOK ROOM #2"))  # len ≡1 mod 3 case
    d.rsps[0].zones.append(506)
    xml = build_account_xml(build_staging_account(d, "2250", receiver_num=""),
                            BUNDLED_TEMPLATE.read_text(encoding="latin-1"))
    assert _padded_dt1_fields(xml) == []


# --- complete configurable account path -----------------------------------

def _configured_account() -> RemoteLinkConfig:
    return RemoteLinkConfig(
        account_num="3141",
        receiver_num="7",
        users=[
            RLUser(1, "ADMIN", "3141", "1"),
            RLUser(88, "CUSTODIAN", "8811", "2"),
        ],
        users_customized=True,
        comm=RLComm(connect_type="direct", port="2201", serial="FAKE0001"),
        arming=RLArming(
            entry_delays=(15, 30, 45, 60),
            exit_delay=90,
            arm_mode="all_perimeter",
        ),
        keypads={
            1: RLKeypad("LOBBY", "door", "keypad_bus", "00000001"),
            2: RLKeypad("OFFICE", "zone_expander", "keypad_bus", "00000002"),
        },
    )


def test_untouched_configuration_is_byte_identical_to_the_safe_legacy_path():
    design = _rl_design()
    template = BUNDLED_TEMPLATE.read_text(encoding="latin-1")
    legacy = build_account_doc(
        build_staging_account(design, "2250", receiver_num="1"), template,
    )

    configured = build_configured_account_doc(
        design, RemoteLinkConfig(), template,
    )

    assert configured.serialize() == legacy.serialize()


def test_configured_doc_applies_and_reads_back_every_ordinary_field():
    design = _rl_design("2250")
    design.site_info.ip_address = "192.0.2.10"
    design.zones = [ZoneInfo(number=501, rl_type="EX")]

    doc = build_configured_account_doc(
        design, _configured_account(),
        BUNDLED_TEMPLATE.read_text(encoding="latin-1"),
    )

    account = doc.row("Account")
    assert account.text("ACCOUNT_NUM") == "3141"
    assert account.text("RECVR_NUM") == "7"
    assert account.text("CONNECT_TYPE") == "3"
    assert account.text("PANEL_IP") == "192.0.2.10"
    assert account.text("PANEL_IP_PRT") == "2201"
    assert account.text("SERIAL_NUMBER") == "FAKE0001"

    sysopts = doc.row("SysOpts")
    assert tuple(sysopts.text(f"ENT_DLY_{i}") for i in range(1, 5)) == \
        ("15", "30", "45", "60")
    assert sysopts.text("ARM_MODE") == "A"
    assert sysopts.text("INST_ARM") == "True"
    assert doc.table("PartInfoList").rows[0].text("EXIT_DELAY") == "90"
    assert [row.text("NAME") for row in doc.table("AreaInfoList").rows] == [
        "PERIMETER", "INTERIOR",
    ]

    assert [
        (row.text("USER_NUM"), row.text("NAME"), row.text("CODE"),
         row.text("PROFILE1"))
        for row in doc.table("UsersList").rows
    ] == [
        ("1", "ADMIN", "3141", "1"),
        ("88", "CUSTODIAN", "8811", "2"),
    ]
    assert [
        (row.text("NUMBER"), row.text("NAME"), row.text("TYPE"),
         row.text("COMM_TYPE"), row.text("DISP_AREAS"))
        for row in doc.table("DeviceInfoList").rows
    ] == [
        ("1", "LOBBY", "1", "K", "00000001"),
        ("2", "OFFICE", "4", "K", "00000002"),
    ]
    assert {
        row.text("NUMBER"): row.text("TYPE")
        for row in doc.table("ZoneInfoList").rows
    }["501"] == "EX"
    assert summarize_account(doc).warnings == []


def test_configured_users_clone_unmodeled_template_fields():
    doc = build_configured_account_doc(
        _rl_design(), _configured_account(),
        BUNDLED_TEMPLATE.read_text(encoding="latin-1"),
    )

    rows = doc.table("UsersList").rows
    assert [row.text("ACTIVE") for row in rows] == ["True", "True"]
    assert [row.text("SND_TO_LKS") for row in rows] == ["False", "False"]


def test_enabled_schedule_uses_calibrated_fields_and_is_intentionally_allowed():
    config = _configured_account()
    config.arming.advanced = RLAdvanced(
        schedule_enabled=True,
        schedule={"mon": RLScheduleDay("08:11", "22:22")},
    )

    doc = build_configured_account_doc(
        _rl_design(), config,
        BUNDLED_TEMPLATE.read_text(encoding="latin-1"),
    )

    schedule = doc.table("TimeSchedsList").rows[0]
    assert schedule.text("MON_OPEN") == "1900-01-01 08:11:00.000"
    assert schedule.text("MON_CLOSE") == "1900-01-01 22:22:00.000"
    assert schedule.text("OP_SS_SS") == "1"
    assert schedule.text("CL_SS_SS") == "2"
    area_one = doc.table("AreaTimeSchedsList").rows[0]
    assert area_one.text("SCHED_1") == "1"
    assert {finding.kind.value for finding in summarize_account(doc).warnings} == {
        "schedule", "area_schedule_link",
    }
    with pytest.raises(InjectorError):
        verify_account_doc(doc, SafetyIntent())


def test_explicit_dangerous_settings_generate_but_stay_visible_as_warnings():
    config = _configured_account()
    config.arming.advanced = RLAdvanced(
        ambush_reports=True,
        ambush_output="7",
        morn_ambush_min="5",
        auto_arm=True,
        auto_disarm=True,
    )

    doc = build_configured_account_doc(
        _rl_design(), config,
        BUNDLED_TEMPLATE.read_text(encoding="latin-1"),
    )

    assert doc.row("SysRpts").text("AMBUSH") == "True"
    assert doc.row("OutOpts").text("AMBUSH") == "7"
    assert doc.table("PartInfoList").rows[0].text("MORN_AMBSH") == "5"
    assert all(row.text("AUTO_ARM") == "True"
               for row in doc.table("AreaInfoList").rows)
    text = render_text(summarize_account(doc))
    assert "⚠" in text and "MORN_AMBSH=5" in text and "AMBUSH=7" in text


def test_vplex_device_type_is_encoded_by_omitting_type_field():
    config = _configured_account()
    config.keypads = {
        1: RLKeypad("VPLEX", "vplex_pl500", "keypad_bus", "00000000"),
    }

    doc = build_configured_account_doc(
        _rl_design(), config,
        BUNDLED_TEMPLATE.read_text(encoding="latin-1"),
    )

    device = doc.table("DeviceInfoList").rows[0]
    assert device.find("TYPE") is None
    assert device.text("COMM_TYPE") == "K"


def test_configured_generation_writes_receipt_and_inspector_reads_same_account(tmp_path):
    config = _configured_account()

    out = generate_configured_account_xml(
        _rl_design(), config, template_path=BUNDLED_TEMPLATE,
        passphrase="secret", out_dir=tmp_path,
    )

    receipt = tmp_path / "3141_remotelink_summary.txt"
    assert out.is_file() and receipt.is_file()
    summary = inspect_account(out, "secret")
    assert summary.account_num == "3141"
    assert summary.receiver_num == "7"
    assert summary.zone_total == 7
    assert "Account   3141" in receipt.read_text(encoding="utf-8")


def test_preview_uses_same_configured_generation_path():
    text = preview_account_summary(
        _rl_design(), _configured_account(), BUNDLED_TEMPLATE,
    )

    assert "Account   3141" in text
    assert "TEST ELEMENTARY SCHOOL" in text
    assert "7 total" in text


def test_inspector_rejects_non_hex_file_cleanly(tmp_path):
    path = tmp_path / "not_export.xml"
    path.write_text("not a RemoteLink export")

    with pytest.raises(InjectorError, match="not a RemoteLink export"):
        inspect_account(path, "secret")
