"""Encode a DMP design as a RemoteLink encrypted `.xml` account export.

Format (reverse-engineered from real exports; see the injector repo's
tools/XML_FORMAT.md):

    file = HEX_UPPER( AES-128-ECB( xml_document, key = MD5(passphrase_ascii) ) )

The plaintext is a <Panels><Panel>...</Panel></Panels> document. This module
produces one the way the SQL path does — clone-and-rebadge from a golden
DECRYPTED template account (the operator supplies it once, the XML analogue of
templates/academy_full.sql), swapping in the design's account identity, zones,
and keypads while keeping the template's valid comm/options config.

Field values carry a DataType attribute: "3"/"14" = integer text, "1" =
Base64(text + trailing NUL), "11" = datetime. Zones live in <ZoneInfoList> as
<ZoneInfo> blocks (NUMBER int, NAME base64 "Z501 ROOM", TYPE base64 NT/SV/--);
keypads in <DeviceInfoList> as <DeviceInfo> blocks.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from Crypto.Cipher import AES

from .account_doc import (
    AccountDoc,
    SafetyIntent,
    _b64,
    _unb64,
    parse_account_xml,
    verify_account_doc,
)
from .errors import InjectorError
from .schema import build_staging_account

BLOCK = 16
SENTINEL_BASE = 90000   # internal <ID> = SENTINEL_BASE + account number (matches clone.py)


# ------------------------------------------------------------------ crypto ---

def _key(passphrase: str) -> bytes:
    return hashlib.md5(passphrase.encode("ascii")).digest()


def decode_account(data: bytes | str, passphrase: str) -> str:
    """Decrypt a RemoteLink `.xml` export (hex text or raw bytes) to its XML."""
    if isinstance(data, str):
        ct = bytes.fromhex(data.strip())
    else:
        stripped = bytes(b for b in data if b not in b" \t\r\n")
        ct = bytes.fromhex(stripped.decode("ascii")) if stripped and \
            all(c in b"0123456789abcdefABCDEF" for c in stripped) else bytes(data)
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
    if not account_num.isdigit():
        raise InjectorError(
            f"Account number '{account_num}' must be numeric (the school LOC "
            "CODE, e.g. 2250) — it is assigned as the panel user code.")
    if not passphrase:
        raise InjectorError("An export passphrase is required for the .xml.")
    tmpl = Path(template_path)
    if not tmpl.is_file():
        raise InjectorError(
            f"RemoteLink XML template not found: {tmpl}\n"
            "Decode one real export once (tools/rl_xml.py decode) and point the "
            "app at that decrypted .xml.")
    template_xml = tmpl.read_text(encoding="latin-1")
    if "<Panels>" not in template_xml:
        raise InjectorError(
            f"{tmpl} is not a decrypted RemoteLink account XML (no <Panels>). "
            "It must be the DECRYPTED template, not an encrypted export.")

    acct = build_staging_account(design, account_num, receiver_num=(receiver_num or "").strip())
    if not acct.zones:
        raise InjectorError("No zones to stage — the design has no zones on an installed RSP.")
    xml = build_account_xml(acct, template_xml, sentinel=sentinel)
    hexed = encode_account(xml, passphrase)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{account_num}_remotelink.xml"
    out_path.write_text(hexed, encoding="ascii")
    return out_path
