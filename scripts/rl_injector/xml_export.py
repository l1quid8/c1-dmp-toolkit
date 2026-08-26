"""Encode a DMP design as a RemoteLink encrypted `.xml` account export.

Format (reverse-engineered from real exports; see the injector repo's
tools/XML_FORMAT.md):

    file = HEX_UPPER( AES-128-ECB( xml_document, key = MD5(passphrase_ascii) ) )

The plaintext is a <Panels><Panel>...</Panel></Panels> document. This module
produces one by cloning and rebadging the bundled, scrubbed demo account,
swapping in the design's identity, zones, and keypads while keeping the
template's valid comm/options structure.

Field values carry a DataType attribute: "3"/"14" = integer text, "1" =
Base64(text + trailing NUL), "11" = datetime. Zones live in <ZoneInfoList> as
<ZoneInfo> blocks (NUMBER int, NAME base64 "Z501 ROOM", TYPE base64 NT/SV/--);
keypads in <DeviceInfoList> as <DeviceInfo> blocks.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from Crypto.Cipher import AES

from .account_doc import (
    AccountDoc,
    Field,
    Row,
    SafetyKind,
    SafetyIntent,
    _b64,
    _unb64,
    parse_account_xml,
    render_text,
    summarize_account,
    verify_account_doc,
)
from .errors import InjectorError
from .rl_config import (
    ARM_MODES,
    CONNECT_TYPES,
    KEYPAD_COMM_TYPES,
    KEYPAD_DEVICE_TYPES,
    SCHEDULE_DAYS,
    SCHEDULE_FIELD_MAP,
    RLKeypad,
    RemoteLinkConfig,
    ResolvedRemoteLinkConfig,
    effective_keypad as resolve_keypad,
    resolve_config,
)
from .schema import build_staging_account

BLOCK = 16
SENTINEL_BASE = 90000   # internal <ID> = SENTINEL_BASE + account number (matches clone.py)


# ------------------------------------------------------------------ crypto ---

def _key(passphrase: str) -> bytes:
    try:
        encoded = passphrase.encode("ascii")
    except UnicodeEncodeError as exc:
        raise InjectorError(
            "RemoteLink export passphrase must contain ASCII characters only"
        ) from exc
    return hashlib.md5(encoded).digest()


def decode_account(data: bytes | str, passphrase: str) -> str:
    """Decrypt a RemoteLink `.xml` export (hex text or raw bytes) to its XML."""
    try:
        if isinstance(data, str):
            ct = bytes.fromhex(data.strip())
        else:
            stripped = bytes(b for b in data if b not in b" \t\r\n")
            ct = bytes.fromhex(stripped.decode("ascii")) if stripped and \
                all(c in b"0123456789abcdefABCDEF" for c in stripped) else bytes(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InjectorError("RemoteLink ciphertext is not valid hexadecimal") from exc
    if len(ct) % BLOCK:
        raise InjectorError(f"ciphertext length {len(ct)} is not a multiple of {BLOCK}")
    text = AES.new(_key(passphrase), AES.MODE_ECB).decrypt(ct).decode("latin-1")
    end = text.rfind("</Panels>")
    if end == -1:
        raise InjectorError("decrypted payload has no </Panels> — wrong passphrase?")
    return text[:end + len("</Panels>")]


def encode_account(xml_text: str, passphrase: str) -> str:
    """Encrypt an XML account document to the on-disk UPPERCASE hex form."""
    raw = xml_text.encode("latin-1")
    if len(raw) % BLOCK:
        raw += b"\x00" * (BLOCK - len(raw) % BLOCK)
    return AES.new(_key(passphrase), AES.MODE_ECB).encrypt(raw).hex().upper()


def _apply_account_edits(doc: AccountDoc, acct, sentinel: int) -> None:
    """Apply the legacy account edits precisely through the document model."""
    account = doc.row("Account")
    account.set("ID", str(sentinel))

    # Identity fields occur throughout the document. Match by tag rather than
    # replacing every numeric value equal to the template's old internal ID.
    for row in doc.iter_rows():
        row.set("ACCOUNT_ID", str(sentinel))
        row.set("ACCOUNT_NUM", acct.account_num)
        row.set("ACCT_NUM", acct.account_num)
    account.set("NAME", acct.name)
    if acct.receiver_num:
        account.set("RECVR_NUM", acct.receiver_num)

    for tag, value in (
        ("ADDRESS", acct.address),
        ("CITY", acct.city),
        ("STATE", acct.state),
        ("ZIP", acct.zip_code),
        ("PHONE", acct.phone),
    ):
        if value:
            account.set(tag, value)

    areas = doc.find_table("AreaInfoList")
    if areas is not None:
        for area in areas.rows:
            area.set("NAME", acct.name)

    users = doc.find_table("UsersList")
    if users is not None:
        for user in users.rows:
            code = {
                "1": acct.account_num,
                "9999": "1" + acct.account_num,
            }.get(user.text("USER_NUM"))
            if code is not None:
                user.set("CODE", code)

    zones = doc.find_table("ZoneInfoList")
    if zones is None or not zones.rows:
        raise InjectorError("template has no <ZoneInfo> block to clone from")
    prototypes = {}
    for row in zones.rows:
        prototypes.setdefault(row.text("TYPE"), row)
    default_prototype = prototypes.get("NT") or zones.rows[0]
    staged_zones = []
    for zone in sorted(acct.zones, key=lambda item: item.number):
        row = prototypes.get(zone.zone_type, default_prototype).clone()
        row.set("NUMBER", str(zone.number))
        row.set("NAME", f"Z{zone.number} {zone.name}")
        staged_zones.append(row)
    zones.rows = staged_zones

    devices = doc.find_table("DeviceInfoList")
    if devices is not None and devices.rows and acct.keypads:
        prototype = devices.rows[0]
        staged_devices = []
        for number in acct.keypads:
            row = prototype.clone()
            row.set("NUMBER", str(number))
            row.set("NAME", f"KEYPAD {number}")
            staged_devices.append(row)
        devices.rows = staged_devices


def build_account_doc(acct, template_xml: str, *, sentinel: Optional[int] = None,
                      safety_intent: SafetyIntent | None = None) -> AccountDoc:
    """Build a structured account while preserving the legacy output bytes."""
    if sentinel is None:
        sentinel = SENTINEL_BASE + int(acct.account_num)
    doc = parse_account_xml(template_xml)
    _apply_account_edits(doc, acct, sentinel)
    verify_account_doc(doc, safety_intent or SafetyIntent())
    return doc


def build_account_xml(acct, template_xml: str, *, sentinel: Optional[int] = None) -> str:
    """Render a staged account through the structured document model."""
    return build_account_doc(acct, template_xml, sentinel=sentinel).serialize()


# ---------------------------------------------------------- configured path ---

def _field(name: str, data_type: str, value: str) -> Field:
    field = Field(name, data_type, "")
    field.set_text(str(value))
    return field


def _set_required(row: Row, tag: str, value: str) -> None:
    if not row.set(tag, str(value)):
        raise InjectorError(f"<{row.name}> has no required <{tag}> field")


def _intent_for(config: ResolvedRemoteLinkConfig) -> SafetyIntent:
    advanced = config.arming.advanced
    allowed: set[SafetyKind] = set()
    if advanced.schedule_enabled:
        allowed.update((SafetyKind.SCHEDULE, SafetyKind.AREA_SCHEDULE_LINK))
    if advanced.ambush_reports:
        allowed.add(SafetyKind.AMBUSH_REPORTS)
    if advanced.ambush_output != "0":
        allowed.add(SafetyKind.AMBUSH_OUTPUT)
    if advanced.morn_ambush_min != "0":
        allowed.add(SafetyKind.MORNING_AMBUSH)
    if advanced.auto_arm or advanced.auto_disarm:
        allowed.add(SafetyKind.AUTO_ARM_DISARM)
    return SafetyIntent(frozenset(allowed))


def _rebuild_areas(doc: AccountDoc, resolved: ResolvedRemoteLinkConfig) -> None:
    areas = doc.table("AreaInfoList")
    if not areas.rows:
        raise InjectorError("template has no <AreaInfo> block to clone from")
    mode = ARM_MODES.get(resolved.arming.arm_mode)
    if mode is None:
        raise InjectorError(f"Unverified RemoteLink arm mode: {resolved.arming.arm_mode}")
    names = mode.areas or (resolved.account_num,)
    # Area mode is the site's ordinary single area, so use the school name.
    if not mode.areas:
        names = (doc.row("Account").text("NAME", "AREA 1") or "AREA 1",)
    prototype = areas.rows[0]
    rebuilt = []
    for number, name in enumerate(names, 1):
        row = prototype.clone()
        _set_required(row, "AREA_NUM", str(number))
        _set_required(row, "NAME", name)
        row.set("PART_NUM", "1")
        row.set("ACCT_NUM", resolved.account_num)
        _set_required(row, "AUTO_ARM", str(resolved.arming.advanced.auto_arm))
        _set_required(row, "AUTO_DISRM", str(resolved.arming.advanced.auto_disarm))
        rebuilt.append(row)
    areas.rows = rebuilt


def _rebuild_users(doc: AccountDoc, resolved: ResolvedRemoteLinkConfig) -> None:
    users = doc.table("UsersList")
    if not users.rows:
        raise InjectorError("template has no <Users> block to clone from")
    prototypes = list(users.rows)
    rebuilt = []
    for index, configured in enumerate(resolved.users):
        row = prototypes[index if index < len(prototypes) else 0].clone()
        _set_required(row, "USER_NUM", configured.number)
        _set_required(row, "NAME", configured.name)
        _set_required(row, "CODE", configured.code)
        _set_required(row, "PROFILE1", configured.profile)
        rebuilt.append(row)
    users.rows = rebuilt


def _effective_keypad(resolved: ResolvedRemoteLinkConfig, number: int) -> RLKeypad:
    return resolve_keypad(resolved, number)


def _rebuild_keypads(doc: AccountDoc, resolved: ResolvedRemoteLinkConfig,
                     keypad_numbers: list[int]) -> None:
    devices = doc.table("DeviceInfoList")
    if not devices.rows:
        raise InjectorError("template has no <DeviceInfo> block to clone from")
    prototype = devices.rows[0]
    rebuilt = []
    for number in keypad_numbers:
        configured = _effective_keypad(resolved, number)
        raw_type = KEYPAD_DEVICE_TYPES.get(configured.device_type, ...)
        if raw_type is ...:
            raise InjectorError(
                f"Unverified RemoteLink keypad device type: {configured.device_type}"
            )
        raw_comm = KEYPAD_COMM_TYPES.get(configured.comm_type)
        if raw_comm is None:
            raise InjectorError(
                f"Unverified RemoteLink keypad communication type: "
                f"{configured.comm_type}"
            )
        row = prototype.clone()
        _set_required(row, "NUMBER", number)
        _set_required(row, "NAME", configured.name)
        if raw_type is None:
            row.fields = [field for field in row.fields if field.name != "TYPE"]
        else:
            _set_required(row, "TYPE", raw_type)
        _set_required(row, "COMM_TYPE", raw_comm)
        _set_required(row, "DISP_AREAS", configured.disp_areas)
        rebuilt.append(row)
    devices.rows = rebuilt


def _schedule_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")


def _apply_schedule(doc: AccountDoc, resolved: ResolvedRemoteLinkConfig,
                    sentinel: int) -> None:
    schedules = doc.table("TimeSchedsList")
    area_links = doc.table("AreaTimeSchedsList")
    for row in area_links.rows:
        row.fields = [field for field in row.fields
                      if not field.name.startswith("SCHED_")]

    advanced = resolved.arming.advanced
    if not advanced.schedule_enabled:
        schedules.rows = []
        return

    fields = [
        _field("ACCOUNT_ID", "3", str(sentinel)),
        _field("LAST_CHANGE", "11", _schedule_timestamp()),
        _field("NUMBER", "3", SCHEDULE_FIELD_MAP["schedule_number"]),
        _field("NAME", "1", "ARMING SCHEDULE"),
    ]
    for day in SCHEDULE_DAYS:
        item = advanced.schedule.get(day)
        if item is None or not item.open_time.strip() or not item.close_time.strip():
            continue
        open_field, close_field, date = SCHEDULE_FIELD_MAP["day_fields"][day]
        fields.extend([
            _field(open_field, "11", f"{date} {item.open_time.strip()}:00.000"),
            _field(close_field, "11", f"{date} {item.close_time.strip()}:00.000"),
        ])
    fields.extend(_field(name, data_type, value)
                  for name, data_type, value
                  in SCHEDULE_FIELD_MAP["static_fields"])
    schedules.rows = [Row("TimeScheds", fields)]

    if not area_links.rows:
        area_links.rows = [Row("AreaTimeScheds", [
            _field("ACCOUNT_ID", "3", str(sentinel)),
            _field("LAST_CHANGE", "11", _schedule_timestamp()),
            _field("NUMBER", "3", "1"),
        ])]
    area_one = next(
        (row for row in area_links.rows if row.text("NUMBER") == "1"), None
    )
    if area_one is None:
        raise InjectorError("template has no Area 1 schedule-link row")
    area_one.fields.append(_field(
        SCHEDULE_FIELD_MAP["area_link_field"], "3",
        SCHEDULE_FIELD_MAP["schedule_number"],
    ))


def _apply_configured_edits(doc: AccountDoc, design,
                            resolved: ResolvedRemoteLinkConfig,
                            keypad_numbers: list[int], sentinel: int) -> None:
    account = doc.row("Account")
    raw_connect = CONNECT_TYPES.get(resolved.comm.connect_type)
    if raw_connect is None:
        raise InjectorError(
            f"Unverified RemoteLink connection type: {resolved.comm.connect_type}"
        )
    _set_required(account, "CONNECT_TYPE", raw_connect)
    _set_required(account, "PANEL_IP_PRT", resolved.comm.port)
    panel_ip = str(getattr(design.site_info, "ip_address", "") or "").strip()
    if panel_ip:
        _set_required(account, "PANEL_IP", panel_ip)
    if resolved.comm.serial.strip():
        _set_required(account, "SERIAL_NUMBER", resolved.comm.serial.strip())

    mode = ARM_MODES.get(resolved.arming.arm_mode)
    if mode is None:
        raise InjectorError(f"Unverified RemoteLink arm mode: {resolved.arming.arm_mode}")
    sysopts = doc.row("SysOpts")
    for number, delay in enumerate(resolved.arming.entry_delays, 1):
        _set_required(sysopts, f"ENT_DLY_{number}", delay)
    _set_required(sysopts, "ARM_MODE", mode.raw)
    _set_required(sysopts, "INST_ARM", str(mode.inst_arm))

    advanced = resolved.arming.advanced
    _set_required(doc.row("SysRpts"), "AMBUSH", str(advanced.ambush_reports))
    _set_required(doc.row("OutOpts"), "AMBUSH", advanced.ambush_output)
    for row in doc.table("PartInfoList").rows:
        _set_required(row, "EXIT_DELAY", resolved.arming.exit_delay)
        _set_required(row, "MORN_AMBSH", advanced.morn_ambush_min)

    _rebuild_areas(doc, resolved)
    _rebuild_users(doc, resolved)
    _rebuild_keypads(doc, resolved, keypad_numbers)
    _apply_schedule(doc, resolved, sentinel)


def _verify_configured_account(doc: AccountDoc,
                               resolved: ResolvedRemoteLinkConfig,
                               acct, design) -> None:
    """Read requested programming back before encryption and fail on drift."""
    mismatches: list[str] = []

    def expect(label: str, actual, expected) -> None:
        if actual != expected:
            mismatches.append(f"{label}: expected {expected!r}, got {actual!r}")

    account = doc.row("Account")
    expect("Account.ACCOUNT_NUM", account.text("ACCOUNT_NUM"), resolved.account_num)
    expect("Account.RECVR_NUM", account.text("RECVR_NUM"), resolved.receiver_num)
    expect("Account.CONNECT_TYPE", account.text("CONNECT_TYPE"),
           CONNECT_TYPES[resolved.comm.connect_type])
    expect("Account.PANEL_IP_PRT", account.text("PANEL_IP_PRT"), resolved.comm.port)
    panel_ip = str(getattr(design.site_info, "ip_address", "") or "").strip()
    if panel_ip:
        expect("Account.PANEL_IP", account.text("PANEL_IP"), panel_ip)
    if resolved.comm.serial.strip():
        expect("Account.SERIAL_NUMBER", account.text("SERIAL_NUMBER"),
               resolved.comm.serial.strip())

    mode = ARM_MODES[resolved.arming.arm_mode]
    sysopts = doc.row("SysOpts")
    expect("SysOpts.ARM_MODE", sysopts.text("ARM_MODE"), mode.raw)
    expect("SysOpts.INST_ARM", sysopts.text("INST_ARM"), str(mode.inst_arm))
    for number, delay in enumerate(resolved.arming.entry_delays, 1):
        expect(f"SysOpts.ENT_DLY_{number}",
               sysopts.text(f"ENT_DLY_{number}"), str(delay))

    advanced = resolved.arming.advanced
    expect("SysRpts.AMBUSH", doc.row("SysRpts").text("AMBUSH"),
           str(advanced.ambush_reports))
    expect("OutOpts.AMBUSH", doc.row("OutOpts").text("AMBUSH"),
           advanced.ambush_output)
    for index, row in enumerate(doc.table("PartInfoList").rows, 1):
        expect(f"PartInfo[{index}].EXIT_DELAY", row.text("EXIT_DELAY"),
               str(resolved.arming.exit_delay))
        expect(f"PartInfo[{index}].MORN_AMBSH", row.text("MORN_AMBSH"),
               advanced.morn_ambush_min)

    area_names = list(mode.areas or (acct.name,))
    actual_areas = [
        (row.text("AREA_NUM"), row.text("NAME"), row.text("AUTO_ARM"),
         row.text("AUTO_DISRM"))
        for row in doc.table("AreaInfoList").rows
    ]
    expected_areas = [
        (str(number), name, str(advanced.auto_arm), str(advanced.auto_disarm))
        for number, name in enumerate(area_names, 1)
    ]
    expect("AreaInfoList", actual_areas, expected_areas)

    expected_users = [
        (str(user.number), user.name, user.code, user.profile)
        for user in resolved.users
    ]
    actual_users = [
        (row.text("USER_NUM"), row.text("NAME"), row.text("CODE"),
         row.text("PROFILE1"))
        for row in doc.table("UsersList").rows
    ]
    expect("UsersList", actual_users, expected_users)

    expected_devices = []
    for number in acct.keypads:
        keypad = _effective_keypad(resolved, number)
        expected_devices.append((
            str(number), keypad.name, KEYPAD_DEVICE_TYPES[keypad.device_type],
            KEYPAD_COMM_TYPES[keypad.comm_type], keypad.disp_areas,
        ))
    actual_devices = [
        (row.text("NUMBER"), row.text("NAME"), row.text("TYPE"),
         row.text("COMM_TYPE"), row.text("DISP_AREAS"))
        for row in doc.table("DeviceInfoList").rows
    ]
    expect("DeviceInfoList", actual_devices, expected_devices)

    expected_zones = [
        (str(zone.number), f"Z{zone.number} {zone.name}", zone.zone_type)
        for zone in sorted(acct.zones, key=lambda item: item.number)
    ]
    actual_zones = [
        (row.text("NUMBER"), row.text("NAME"), row.text("TYPE"))
        for row in doc.table("ZoneInfoList").rows
    ]
    expect("ZoneInfoList", actual_zones, expected_zones)

    schedules = doc.table("TimeSchedsList").rows
    area_links = doc.table("AreaTimeSchedsList").rows
    schedule_links = [
        (row.text("NUMBER"), field.name, field.raw)
        for row in area_links
        for field in row.fields
        if field.name.startswith("SCHED_")
    ]
    if advanced.schedule_enabled:
        expect("TimeSchedsList row count", len(schedules), 1)
        if schedules:
            schedule = schedules[0]
            expect("TimeScheds.NUMBER", schedule.text("NUMBER"),
                   SCHEDULE_FIELD_MAP["schedule_number"])
            for day in SCHEDULE_DAYS:
                item = advanced.schedule.get(day)
                if item is None or not item.open_time.strip() \
                        or not item.close_time.strip():
                    continue
                open_field, close_field, date = SCHEDULE_FIELD_MAP["day_fields"][day]
                expect(f"TimeScheds.{open_field}", schedule.text(open_field),
                       f"{date} {item.open_time.strip()}:00.000")
                expect(f"TimeScheds.{close_field}", schedule.text(close_field),
                       f"{date} {item.close_time.strip()}:00.000")
        expect("AreaTimeScheds schedule link", schedule_links, [
            ("1", SCHEDULE_FIELD_MAP["area_link_field"],
             SCHEDULE_FIELD_MAP["schedule_number"]),
        ])
    else:
        expect("TimeSchedsList row count", len(schedules), 0)
        expect("AreaTimeScheds schedule links", schedule_links, [])

    if mismatches:
        raise InjectorError(
            "RemoteLink configuration failed read-back verification:\n  - "
            + "\n  - ".join(mismatches)
        )


def build_configured_account_doc(
    design, config: RemoteLinkConfig, template_xml: str, *,
    sentinel: Optional[int] = None,
) -> AccountDoc:
    """Build and read back the fully configured account before serialization."""
    resolved = resolve_config(config, design)
    if not resolved.account_num.isdigit():
        raise InjectorError(
            f"Account number '{resolved.account_num}' must be numeric"
        )
    if sentinel is None:
        sentinel = SENTINEL_BASE + int(resolved.account_num)
    acct = build_staging_account(
        design, resolved.account_num, receiver_num=resolved.receiver_num,
    )
    if not acct.zones:
        raise InjectorError("No zones to stage — the design has no zones on an installed RSP.")
    doc = parse_account_xml(template_xml)
    _apply_account_edits(doc, acct, sentinel)
    _apply_configured_edits(doc, design, resolved, acct.keypads, sentinel)
    _verify_configured_account(doc, resolved, acct, design)
    verify_account_doc(doc, _intent_for(resolved))
    return doc


def _verify_final_account(doc: AccountDoc, design, config: RemoteLinkConfig) -> None:
    """Verify a decrypted final artifact against the operator's configuration."""
    resolved = resolve_config(config, design)
    acct = build_staging_account(
        design, resolved.account_num, receiver_num=resolved.receiver_num,
    )
    _verify_configured_account(doc, resolved, acct, design)
    verify_account_doc(doc, _intent_for(resolved))


def _atomic_write_text(path: Path, text: str, encoding: str) -> None:
    """Replace one output only after its complete contents are on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=encoding, dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


# ------------------------------------------------------------------- public ---

def generate_account_xml(design, account_num, receiver_num: str = "", *,
                         template_path, passphrase: str,
                         out_dir, sentinel: Optional[int] = None) -> Path:
    """Build an encrypted RemoteLink `.xml` for a design and write it.

    `template_path` is a DECRYPTED golden account XML (create it once with the
    injector's tools/rl_xml.py: `decode <export> <passphrase> -o template.xml`).
    `passphrase` is the export pin the operator will type at import time.
    Returns the written `.xml` path. Raises InjectorError on bad input.
    """
    account_num = str(account_num).strip()
    config = RemoteLinkConfig(
        account_num=account_num,
        receiver_num=(receiver_num or "1").strip(),
    )
    return generate_configured_account_xml(
        design, config, template_path=template_path, passphrase=passphrase,
        out_dir=out_dir, sentinel=sentinel, write_summary=False,
    )


def _read_template(template_path) -> str:
    path = Path(template_path)
    if not path.is_file():
        raise InjectorError(f"RemoteLink XML template not found: {path}")
    text = path.read_text(encoding="latin-1")
    if "<Panels>" not in text:
        raise InjectorError(
            f"{path} is not a decrypted RemoteLink account XML (no <Panels>)"
        )
    return text


def generate_configured_account_xml(
    design, config: RemoteLinkConfig, *, template_path, passphrase: str,
    out_dir, sentinel: Optional[int] = None, write_summary: bool = True,
) -> Path:
    """Generate an encrypted account and human-readable receipt from config."""
    if not passphrase:
        raise InjectorError("An export passphrase is required for the .xml.")
    template_xml = _read_template(template_path)
    doc = build_configured_account_doc(
        design, config, template_xml, sentinel=sentinel,
    )
    encoded = encode_account(doc.serialize(), passphrase)
    readback = parse_account_xml(decode_account(encoded, passphrase))
    _verify_final_account(readback, design, config)
    summary = summarize_account(readback)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{summary.account_num}_remotelink.xml"
    summary_path = out_dir / f"{summary.account_num}_remotelink_summary.txt"
    summary_text = render_text(summary)
    _atomic_write_text(out_path, encoded, "ascii")
    if write_summary:
        _atomic_write_text(summary_path, summary_text, "utf-8")
    return out_path


def preview_account_summary(design, config: RemoteLinkConfig, template_path) -> str:
    """Render exactly what configured generation will produce, without writing."""
    doc = build_configured_account_doc(
        design, config, _read_template(template_path),
    )
    return render_text(summarize_account(doc))


def inspect_account(path, passphrase: str):
    """Decrypt and summarize an existing export without changing it."""
    path = Path(path)
    data = path.read_bytes()
    try:
        compact = b"".join(data.split())
        ascii_text = compact.decode("ascii")
    except UnicodeDecodeError as exc:
        raise InjectorError(
            f"{path.name} is not a RemoteLink export (not ASCII hex)"
        ) from exc
    if not ascii_text or any(char not in "0123456789abcdefABCDEF"
                             for char in ascii_text):
        raise InjectorError(f"{path.name} is not a RemoteLink export (not hex)")
    if len(ascii_text) % 2:
        raise InjectorError(
            f"{path.name} is not a RemoteLink export (odd-length hex)"
        )
    doc = parse_account_xml(decode_account(ascii_text, passphrase))
    return summarize_account(doc)
