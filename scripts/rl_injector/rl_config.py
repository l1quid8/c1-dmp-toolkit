"""Typed, safely-defaulted configuration for RemoteLink account generation."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any


SCHEDULE_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


@dataclass
class RLUser:
    number: int
    name: str
    code: str
    profile: str


@dataclass
class RLComm:
    connect_type: str = "network"
    port: str = "2001"
    serial: str = ""


@dataclass
class RLScheduleDay:
    open_time: str = ""
    close_time: str = ""


@dataclass
class RLAdvanced:
    schedule_enabled: bool = False
    schedule: dict[str, RLScheduleDay] = field(default_factory=dict)
    ambush_reports: bool = False
    ambush_output: str = "0"
    morn_ambush_min: str = "0"
    auto_arm: bool = False
    auto_disarm: bool = False


@dataclass
class RLArming:
    entry_delays: tuple[int, int, int, int] = (30, 60, 90, 120)
    exit_delay: int = 120
    arm_mode: str = "area"
    advanced: RLAdvanced = field(default_factory=RLAdvanced)


@dataclass
class RLKeypad:
    name: str = ""
    device_type: str = "keypad"
    comm_type: str = "keypad_bus"
    disp_areas: str = "FFFFFFFF"


@dataclass
class RemoteLinkConfig:
    account_num: str = ""
    receiver_num: str = "1"
    users: list[RLUser] = field(default_factory=list)
    users_customized: bool = False
    comm: RLComm = field(default_factory=RLComm)
    arming: RLArming = field(default_factory=RLArming)
    keypads: dict[int, RLKeypad] = field(default_factory=dict)


@dataclass
class ResolvedRemoteLinkConfig:
    account_num: str
    receiver_num: str
    users: list[RLUser]
    comm: RLComm
    arming: RLArming
    keypads: dict[int, RLKeypad]


@dataclass(frozen=True)
class RLConfigIssue:
    code: str
    ref: str | None
    message: str


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def config_to_dict(config: RemoteLinkConfig) -> dict:
    """Return the explicit JSON shape stored under a session's remotelink key."""
    return dataclasses.asdict(config)


def config_from_dict(raw: Any) -> RemoteLinkConfig:
    """Load hand-edited/older JSON without allowing truthy junk to enable risk."""
    raw = _mapping(raw)

    users: list[RLUser] = []
    raw_users = raw.get("users")
    if isinstance(raw_users, list):
        for item in raw_users:
            if not isinstance(item, dict):
                continue
            users.append(RLUser(
                number=_int(item.get("number")),
                name=_text(item.get("name")),
                code=_text(item.get("code")),
                profile=_text(item.get("profile")),
            ))

    raw_comm = _mapping(raw.get("comm"))
    comm = RLComm(
        connect_type=_text(raw_comm.get("connect_type"), "network") or "network",
        port=_text(raw_comm.get("port"), "2001") or "2001",
        serial=_text(raw_comm.get("serial")),
    )

    raw_arming = _mapping(raw.get("arming"))
    raw_delays = raw_arming.get("entry_delays")
    default_delays = (30, 60, 90, 120)
    if isinstance(raw_delays, (list, tuple)) and len(raw_delays) == 4:
        parsed_delays = tuple(_int(value, -1) for value in raw_delays)
        entry_delays = parsed_delays if all(value >= 0 for value in parsed_delays) \
            else default_delays
    else:
        entry_delays = default_delays

    raw_advanced = _mapping(raw_arming.get("advanced"))
    schedule: dict[str, RLScheduleDay] = {}
    raw_schedule = _mapping(raw_advanced.get("schedule"))
    for day in SCHEDULE_DAYS:
        item = raw_schedule.get(day)
        if isinstance(item, dict):
            schedule[day] = RLScheduleDay(
                open_time=_text(item.get("open_time")),
                close_time=_text(item.get("close_time")),
            )
    advanced = RLAdvanced(
        schedule_enabled=_bool(raw_advanced.get("schedule_enabled")),
        schedule=schedule,
        ambush_reports=_bool(raw_advanced.get("ambush_reports")),
        ambush_output=_text(raw_advanced.get("ambush_output"), "0") or "0",
        morn_ambush_min=_text(raw_advanced.get("morn_ambush_min"), "0") or "0",
        auto_arm=_bool(raw_advanced.get("auto_arm")),
        auto_disarm=_bool(raw_advanced.get("auto_disarm")),
    )
    arming = RLArming(
        entry_delays=entry_delays,
        exit_delay=_int(raw_arming.get("exit_delay"), 120),
        arm_mode=_text(raw_arming.get("arm_mode"), "area") or "area",
        advanced=advanced,
    )

    keypads: dict[int, RLKeypad] = {}
    for raw_number, item in _mapping(raw.get("keypads")).items():
        number = _int(raw_number, -1)
        if number < 0 or not isinstance(item, dict):
            continue
        keypads[number] = RLKeypad(
            name=_text(item.get("name")),
            device_type=_text(item.get("device_type"), "keypad") or "keypad",
            comm_type=_text(item.get("comm_type"), "keypad_bus") or "keypad_bus",
            disp_areas=_text(item.get("disp_areas"), "FFFFFFFF") or "FFFFFFFF",
        )

    return RemoteLinkConfig(
        account_num=_text(raw.get("account_num")),
        receiver_num=_text(raw.get("receiver_num"), "1") or "1",
        users=users,
        users_customized=_bool(raw.get("users_customized")),
        comm=comm,
        arming=arming,
        keypads=keypads,
    )


def resolve_config(config: RemoteLinkConfig, design) -> ResolvedRemoteLinkConfig:
    """Resolve school-dependent defaults without mutating persisted state."""
    copied = config_from_dict(config_to_dict(config))
    site_code = _text(getattr(getattr(design, "site_info", None), "school_code", ""))
    account_num = copied.account_num.strip() or site_code.strip()
    if copied.users_customized:
        users = [dataclasses.replace(user) for user in copied.users]
    else:
        users = [
            RLUser(1, "USER", account_num, "1"),
            RLUser(9999, "TECHNICIAN", "1" + account_num, "99"),
        ]
    return ResolvedRemoteLinkConfig(
        account_num=account_num,
        receiver_num=copied.receiver_num.strip(),
        users=users,
        comm=copied.comm,
        arming=copied.arming,
        keypads=copied.keypads,
    )


def _duplicates(values: list) -> set:
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def validate_config(config: RemoteLinkConfig, design) -> list[RLConfigIssue]:
    """Return non-blocking operator warnings for editable RemoteLink values."""
    resolved = resolve_config(config, design)
    issues: list[RLConfigIssue] = []

    for number in sorted(_duplicates([user.number for user in resolved.users])):
        issues.append(RLConfigIssue(
            code="remotelink.user_number_duplicate",
            ref=f"user:{number}",
            message=f"RemoteLink user number {number} is used more than once",
        ))

    duplicate_codes = _duplicates([
        user.code.strip() for user in resolved.users if user.code.strip()
    ])
    for code in sorted(duplicate_codes):
        issues.append(RLConfigIssue(
            code="remotelink.user_code_duplicate",
            ref="users",
            message=f"RemoteLink user code {code} is used more than once",
        ))

    for user in resolved.users:
        code = user.code.strip()
        if not code.isdigit() or len(code) < 3:
            issues.append(RLConfigIssue(
                code="remotelink.user_code_invalid",
                ref=f"user:{user.number}",
                message=f"RemoteLink user {user.number} needs a numeric code "
                        "with at least 3 digits",
            ))

    port = resolved.comm.port.strip()
    try:
        valid_port = 1 <= int(port) <= 65535
    except ValueError:
        valid_port = False
    if not valid_port:
        issues.append(RLConfigIssue(
            code="remotelink.port_invalid",
            ref="field:port",
            message="RemoteLink panel port must be a number from 1 to 65535",
        ))

    advanced = resolved.arming.advanced
    if advanced.schedule_enabled:
        for day, schedule in advanced.schedule.items():
            if bool(schedule.open_time.strip()) != bool(schedule.close_time.strip()):
                issues.append(RLConfigIssue(
                    code="remotelink.schedule_incomplete",
                    ref=f"schedule:{day}",
                    message=f"{day.title()} schedule needs both open and close times",
                ))

    for number, keypad in resolved.keypads.items():
        if not re.fullmatch(r"[0-9A-Fa-f]{8}", keypad.disp_areas.strip()):
            issues.append(RLConfigIssue(
                code="remotelink.display_areas_invalid",
                ref=f"keypad:{number}",
                message=f"Keypad {number} displayed areas must be 8 hexadecimal digits",
            ))

    return issues
