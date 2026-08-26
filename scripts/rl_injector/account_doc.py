"""Byte-faithful model of a decrypted RemoteLink account document.

RemoteLink's import format is deliberately narrow: one ``Panels/Panel``
document, no inter-tag whitespace, and leaf fields shaped exactly as
``<NAME DataType="N">value</NAME>``.  The parser accepts only that verified
shape.  Rejecting unfamiliar input is safer than silently normalizing an
account that will later be sent to a panel.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from .errors import AccountSafetyError, InjectorError


_ROOT_OPEN = "<Panels><Panel>"
_ROOT_CLOSE = "</Panel></Panels>"
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_OPEN_RE = re.compile(rf"<({_NAME})>")
_LEAF_RE = re.compile(
    rf'<({_NAME}) DataType="(\d+)">([^<]*)</\1>'
)


def _b64(text: str) -> str:
    """Encode a DataType=1 value using RemoteLink's padding convention."""
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise InjectorError(
            "RemoteLink DataType 1 text must be latin-1 encodable"
        ) from exc
    raw += b"\x00" * ((3 - len(raw) % 3) % 3)
    return base64.b64encode(raw).decode("ascii")


def _unb64(value: str) -> str:
    """Decode a RemoteLink DataType=1 value and remove its NUL fill."""
    try:
        return base64.b64decode(value, validate=True).decode("latin-1").rstrip("\x00")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InjectorError("invalid base64 in RemoteLink DataType 1 field") from exc


_INTEGER_RE = re.compile(r"-?\d+")
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}")


def _validate_raw(data_type: str, value: str) -> None:
    """Validate the exact scalar spellings accepted by the account format."""
    if data_type == "1":
        _unb64(value)
        return
    if any(char in value for char in "<>&"):
        raise InjectorError(
            f"RemoteLink DataType {data_type} value contains XML metacharacters"
        )
    valid = (
        data_type in {"3", "14"} and _INTEGER_RE.fullmatch(value)
        or data_type == "5" and value in {"True", "False"}
        or data_type == "11" and _DATETIME_RE.fullmatch(value)
    )
    if not valid:
        raise InjectorError(
            f"invalid RemoteLink DataType {data_type} value: {value!r}"
        )


@dataclass
class Field:
    name: str
    data_type: str
    raw: str

    @property
    def text(self) -> str:
        return _unb64(self.raw) if self.data_type == "1" else self.raw

    def set_text(self, value: str) -> None:
        value = str(value)
        if self.data_type == "1":
            self.raw = _b64(value)
            return
        _validate_raw(self.data_type, value)
        self.raw = value

    def serialize(self) -> str:
        _validate_raw(self.data_type, self.raw)
        return (
            f'<{self.name} DataType="{self.data_type}">'
            f"{self.raw}</{self.name}>"
        )


@dataclass
class Row:
    name: str
    fields: list[Field]

    def find(self, tag: str) -> Field | None:
        return next((field for field in self.fields if field.name == tag), None)

    def require(self, tag: str) -> Field:
        field = self.find(tag)
        if field is None:
            raise InjectorError(f"<{self.name}> has no <{tag}> field")
        return field

    def text(self, tag: str, default: str | None = None) -> str | None:
        field = self.find(tag)
        return field.text if field is not None else default

    def set(self, tag: str, value: str) -> bool:
        field = self.find(tag)
        if field is None:
            return False
        field.set_text(value)
        return True

    def clone(self) -> "Row":
        return Row(
            self.name,
            [Field(field.name, field.data_type, field.raw) for field in self.fields],
        )

    def serialize(self) -> str:
        return (
            f"<{self.name}>"
            + "".join(field.serialize() for field in self.fields)
            + f"</{self.name}>"
        )


@dataclass
class Table:
    name: str
    rows: list[Row]

    def serialize(self) -> str:
        return (
            f"<{self.name}>"
            + "".join(row.serialize() for row in self.rows)
            + f"</{self.name}>"
        )


@dataclass
class AccountDoc:
    nodes: list[Row | Table]

    def find(self, name: str) -> Row | Table | None:
        return next((node for node in self.nodes if node.name == name), None)

    def row(self, name: str) -> Row:
        node = self.find(name)
        if not isinstance(node, Row):
            kind = "missing" if node is None else "a table"
            raise InjectorError(f"<{name}> is {kind}, not a field block")
        return node

    def table(self, name: str) -> Table:
        node = self.find(name)
        if not isinstance(node, Table):
            kind = "missing" if node is None else "a field block"
            raise InjectorError(f"<{name}> is {kind}, not a table")
        return node

    def find_row(self, name: str) -> Row | None:
        node = self.find(name)
        return node if isinstance(node, Row) else None

    def find_table(self, name: str) -> Table | None:
        node = self.find(name)
        return node if isinstance(node, Table) else None

    def iter_rows(self) -> Iterator[Row]:
        for node in self.nodes:
            if isinstance(node, Row):
                yield node
            else:
                yield from node.rows

    def serialize(self) -> str:
        return (
            _ROOT_OPEN
            + "".join(node.serialize() for node in self.nodes)
            + _ROOT_CLOSE
        )


class SafetyKind(str, Enum):
    """Classes of programming that can make a panel act or signal on its own."""

    SCHEDULE = "schedule"
    AREA_SCHEDULE_LINK = "area_schedule_link"
    AMBUSH_REPORTS = "ambush_reports"
    AMBUSH_OUTPUT = "ambush_output"
    MORNING_AMBUSH = "morning_ambush"
    AUTO_ARM_DISARM = "auto_arm_disarm"


@dataclass(frozen=True)
class SafetyFinding:
    kind: SafetyKind
    message: str


@dataclass(frozen=True)
class SafetyIntent:
    """Dangerous classes explicitly requested by the operator."""

    allowed: frozenset[SafetyKind] = frozenset()


@dataclass(frozen=True)
class ScheduleSummary:
    number: str
    name: str


@dataclass(frozen=True)
class AreaScheduleLink:
    area_number: str
    field: str
    value: str


@dataclass(frozen=True)
class AreaSummary:
    number: str
    name: str
    auto_arm: str
    auto_disarm: str


@dataclass(frozen=True)
class UserSummary:
    number: str
    name: str
    code: str
    profile: str


@dataclass(frozen=True)
class AccountSummary:
    account_num: str
    internal_id: str
    name: str
    receiver_num: str
    address: str
    city: str
    state: str
    zip_code: str
    phone: str
    schedules: list[ScheduleSummary]
    area_links: list[AreaScheduleLink]
    sysrpts_ambush: str | None
    outopts_ambush: str | None
    morn_ambsh: list[str]
    areas: list[AreaSummary]
    users: list[UserSummary]
    zone_total: int
    zone_real: int
    zone_spare: int
    keypads: list[tuple[str, str]]
    warnings: list[SafetyFinding]


def collect_safety_findings(doc: AccountDoc) -> list[SafetyFinding]:
    """Read every dangerous setting without deciding whether it was intended."""
    findings: list[SafetyFinding] = []

    schedules = doc.find_table("TimeSchedsList")
    if schedules is not None:
        for row in schedules.rows:
            number = row.text("NUMBER", "?") or "?"
            name = row.text("NAME", "") or ""
            findings.append(SafetyFinding(
                SafetyKind.SCHEDULE,
                f'arming schedule defined: #{number} "{name}"',
            ))

    area_schedules = doc.find_table("AreaTimeSchedsList")
    if area_schedules is not None:
        for row in area_schedules.rows:
            area = row.text("NUMBER", "?") or "?"
            for field in row.fields:
                if field.name.startswith("SCHED_"):
                    findings.append(SafetyFinding(
                        SafetyKind.AREA_SCHEDULE_LINK,
                        f"Area {area} linked to schedule via "
                        f"{field.name}={field.raw}",
                    ))

    sysrpts = doc.find_row("SysRpts")
    sys_ambush = sysrpts.find("AMBUSH") if sysrpts is not None else None
    if sys_ambush is not None and sys_ambush.raw != "False":
        findings.append(SafetyFinding(
            SafetyKind.AMBUSH_REPORTS,
            f"SysRpts.AMBUSH={sys_ambush.raw}",
        ))

    outopts = doc.find_row("OutOpts")
    out_ambush = outopts.find("AMBUSH") if outopts is not None else None
    if out_ambush is not None and out_ambush.raw != "0":
        findings.append(SafetyFinding(
            SafetyKind.AMBUSH_OUTPUT,
            f"OutOpts.AMBUSH={out_ambush.raw}",
        ))

    parts = doc.find_table("PartInfoList")
    if parts is not None:
        for row in parts.rows:
            value = row.find("MORN_AMBSH")
            if value is not None and value.raw != "0":
                part = row.text("PART_NUM", "?") or "?"
                findings.append(SafetyFinding(
                    SafetyKind.MORNING_AMBUSH,
                    f"Partition {part} MORN_AMBSH={value.raw}",
                ))

    areas = doc.find_table("AreaInfoList")
    if areas is not None:
        for row in areas.rows:
            area = row.text("AREA_NUM", "?") or "?"
            for tag in ("AUTO_ARM", "AUTO_DISRM"):
                value = row.find(tag)
                if value is not None and value.raw != "False":
                    findings.append(SafetyFinding(
                        SafetyKind.AUTO_ARM_DISARM,
                        f"Area {area} {tag}={value.raw}",
                    ))

    return findings


def verify_account_doc(doc: AccountDoc, intent: SafetyIntent | None = None) -> None:
    """Reject dangerous programming that was not explicitly requested."""
    intent = intent or SafetyIntent()
    unexpected = [
        finding for finding in collect_safety_findings(doc)
        if finding.kind not in intent.allowed
    ]
    if unexpected:
        detail = "\n  - ".join(finding.message for finding in unexpected)
        raise AccountSafetyError(
            "Account failed safety verification:\n  - " + detail
        )


def _table_rows(doc: AccountDoc, name: str) -> list[Row]:
    table = doc.find_table(name)
    return table.rows if table is not None else []


def summarize_account(doc: AccountDoc) -> AccountSummary:
    """Build the one read-back summary shared by every visibility surface."""
    account = doc.find_row("Account")

    def account_text(tag: str) -> str:
        return account.text(tag, "") or "" if account is not None else ""

    schedules = [
        ScheduleSummary(
            number=row.text("NUMBER", "") or "",
            name=row.text("NAME", "") or "",
        )
        for row in _table_rows(doc, "TimeSchedsList")
    ]

    area_links = [
        AreaScheduleLink(
            area_number=row.text("NUMBER", "") or "",
            field=field.name,
            value=field.raw,
        )
        for row in _table_rows(doc, "AreaTimeSchedsList")
        for field in row.fields
        if field.name.startswith("SCHED_")
    ]

    sysrpts = doc.find_row("SysRpts")
    outopts = doc.find_row("OutOpts")
    sys_ambush = sysrpts.text("AMBUSH") if sysrpts is not None else None
    out_ambush = outopts.text("AMBUSH") if outopts is not None else None

    morn_ambsh = [
        row.text("MORN_AMBSH", "") or ""
        for row in _table_rows(doc, "PartInfoList")
        if row.find("MORN_AMBSH") is not None
    ]
    areas = [
        AreaSummary(
            number=row.text("AREA_NUM", "") or "",
            name=row.text("NAME", "") or "",
            auto_arm=row.text("AUTO_ARM", "") or "",
            auto_disarm=row.text("AUTO_DISRM", "") or "",
        )
        for row in _table_rows(doc, "AreaInfoList")
    ]
    users = [
        UserSummary(
            number=row.text("USER_NUM", "") or "",
            name=row.text("NAME", "") or "",
            code=row.text("CODE", "") or "",
            profile=row.text("PROFILE1", "") or "",
        )
        for row in _table_rows(doc, "UsersList")
    ]
    zones = _table_rows(doc, "ZoneInfoList")
    spare_count = sum(row.text("TYPE") == "--" for row in zones)
    keypads = [
        (row.text("NUMBER", "") or "", row.text("NAME", "") or "")
        for row in _table_rows(doc, "DeviceInfoList")
    ]

    return AccountSummary(
        account_num=account_text("ACCOUNT_NUM"),
        internal_id=account_text("ID"),
        name=account_text("NAME"),
        receiver_num=account_text("RECVR_NUM"),
        address=account_text("ADDRESS"),
        city=account_text("CITY"),
        state=account_text("STATE"),
        zip_code=account_text("ZIP"),
        phone=account_text("PHONE"),
        schedules=schedules,
        area_links=area_links,
        sysrpts_ambush=sys_ambush,
        outopts_ambush=out_ambush,
        morn_ambsh=morn_ambsh,
        areas=areas,
        users=users,
        zone_total=len(zones),
        zone_real=len(zones) - spare_count,
        zone_spare=spare_count,
        keypads=keypads,
        warnings=collect_safety_findings(doc),
    )


def _safety_value(summary: AccountSummary, kind: SafetyKind, clean: str) -> str:
    messages = [f.message for f in summary.warnings if f.kind == kind]
    return "; ".join(messages) if messages else clean


def render_text(summary: AccountSummary) -> str:
    """Render a stable operator-facing receipt suitable for UI and sidecars."""
    lines = ["RemoteLink Account Summary", "=========================="]
    if summary.warnings:
        lines.extend([
            f"⚠ SAFETY: {len(summary.warnings)} active setting(s) require review",
            *[f"⚠ {finding.message}" for finding in summary.warnings],
            "",
        ])

    account_bits = []
    if summary.receiver_num:
        account_bits.append(f"receiver {summary.receiver_num}")
    if summary.internal_id:
        account_bits.append(f"internal id {summary.internal_id}")
    suffix = f"   ({', '.join(account_bits)})" if account_bits else ""
    lines.extend([
        f"Account   {summary.account_num or 'not present'}{suffix}",
        f"Name      {summary.name or 'not present'}",
    ])
    address = ", ".join(
        value for value in (summary.address, summary.city, summary.state,
                            summary.zip_code) if value
    )
    if address or summary.phone:
        lines.append(
            f"Address   {address or 'not present'}"
            + (f" · {summary.phone}" if summary.phone else "")
        )

    schedule_text = (
        ", ".join(f'#{s.number} "{s.name}"' for s in summary.schedules)
        if summary.schedules else "none"
    )
    link_text = (
        ", ".join(
            f"Area {link.area_number} {link.field}={link.value}"
            for link in summary.area_links
        ) if summary.area_links else "none"
    )
    auto_text = "; ".join(
        f"Area {area.number}: AUTO_ARM={area.auto_arm}, "
        f"AUTO_DISRM={area.auto_disarm}"
        for area in summary.areas
        if area.auto_arm not in ("", "False")
        or area.auto_disarm not in ("", "False")
    ) or "off"
    lines.extend([
        "",
        "Safety checks",
        "-------------",
        f"  Arming schedules      {schedule_text}",
        f"  Area schedule links   {link_text}",
        "  Ambush/duress report  "
        + _safety_value(summary, SafetyKind.AMBUSH_REPORTS,
                        f"off ({summary.sysrpts_ambush or 'not present'})"),
        "  Ambush output         "
        + _safety_value(summary, SafetyKind.AMBUSH_OUTPUT,
                        f"none ({summary.outopts_ambush or 'not present'})"),
        "  Morning ambush        "
        + _safety_value(summary, SafetyKind.MORNING_AMBUSH,
                        "off" if summary.morn_ambsh else "not present"),
        f"  Auto arm / disarm     {auto_text}",
        "",
        "Users",
        "-----",
    ])
    if summary.users:
        lines.extend(
            f"  {user.number:>4}  {user.name:<16} code {user.code}  "
            f"profile {user.profile}"
            for user in summary.users
        )
    else:
        lines.append("  none")

    keypad_text = ", ".join(
        f"{number} ({name})" for number, name in summary.keypads
    ) or "none"
    area_text = ", ".join(
        f'Area {area.number} "{area.name}"' for area in summary.areas
    ) or "none"
    lines.extend([
        "",
        "Contents",
        "--------",
        f"  Zones    {summary.zone_total} total — "
        f"{summary.zone_real} real, {summary.zone_spare} spare",
        f"  Keypads  {keypad_text}",
        f"  Areas    {area_text}",
    ])
    return "\n".join(lines) + "\n"


def _error(text: str, pos: int, message: str) -> InjectorError:
    snippet = text[pos:pos + 48]
    return InjectorError(f"{message} at offset {pos}: {snippet!r}")


def _leaf_at(text: str, pos: int) -> tuple[Field, int] | None:
    match = _LEAF_RE.match(text, pos)
    if match is None:
        return None
    field = Field(match.group(1), match.group(2), match.group(3))
    _validate_raw(field.data_type, field.raw)
    return field, match.end()


def _parse_row_body(text: str, pos: int, row_name: str) -> tuple[list[Field], int]:
    fields: list[Field] = []
    close = f"</{row_name}>"
    while not text.startswith(close, pos):
        parsed = _leaf_at(text, pos)
        if parsed is not None:
            field, pos = parsed
            fields.append(field)
            continue
        if _OPEN_RE.match(text, pos):
            raise _error(text, pos, f"unsupported nesting inside row <{row_name}>")
        raise _error(text, pos, f"expected a field or {close} for <{row_name}>")
    if not fields:
        raise _error(text, pos, f"row <{row_name}> has no fields")
    return fields, pos + len(close)


def parse_account_xml(text: str) -> AccountDoc:
    """Parse the exact RemoteLink account XML shape without normalizing it."""
    if not text.startswith(_ROOT_OPEN):
        raise InjectorError(
            "not a RemoteLink account document; must start with <Panels><Panel>"
        )

    pos = len(_ROOT_OPEN)
    panel_close = "</Panel>"
    nodes: list[Row | Table] = []

    while not text.startswith(panel_close, pos):
        outer = _OPEN_RE.match(text, pos)
        if outer is None:
            raise _error(text, pos, "expected a Panel child")
        name = outer.group(1)
        pos = outer.end()
        outer_close = f"</{name}>"

        if text.startswith(outer_close, pos):
            nodes.append(Table(name, []))
            pos += len(outer_close)
            continue

        first_leaf = _leaf_at(text, pos)
        if first_leaf is not None:
            fields, pos = _parse_row_body(text, pos, name)
            nodes.append(Row(name, fields))
            continue

        if _OPEN_RE.match(text, pos) is None:
            raise _error(text, pos, f"cannot classify <{name}>")

        rows: list[Row] = []
        while not text.startswith(outer_close, pos):
            row_open = _OPEN_RE.match(text, pos)
            if row_open is None:
                raise _error(text, pos, f"expected a row or {outer_close}")
            row_name = row_open.group(1)
            fields, pos = _parse_row_body(text, row_open.end(), row_name)
            rows.append(Row(row_name, fields))
        pos += len(outer_close)
        nodes.append(Table(name, rows))

    pos += len(panel_close)
    panels_close = "</Panels>"
    if not text.startswith(panels_close, pos):
        raise _error(text, pos, f"expected {panels_close}")
    pos += len(panels_close)
    if pos != len(text):
        raise _error(text, pos, "trailing content after </Panels>")
    return AccountDoc(nodes)
