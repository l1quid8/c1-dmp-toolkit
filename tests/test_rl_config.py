"""Typed configuration tests for configurable RemoteLink generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_dmp_worksheet import DMPDesign, SiteInfo  # noqa: E402
from rl_injector.rl_config import (  # noqa: E402
    RLAdvanced,
    RLArming,
    RLComm,
    RLKeypad,
    RLScheduleDay,
    RLUser,
    RemoteLinkConfig,
    config_from_dict,
    config_to_dict,
    resolve_config,
)


def _design(code: str = "2250") -> DMPDesign:
    return DMPDesign(site_info=SiteInfo(
        school_name="TEST ELEMENTARY SCHOOL",
        school_code=code,
    ))


def test_untouched_config_resolves_account_and_users_from_school_code():
    """Defaults must preserve today's site-code-derived generation behavior."""
    config = RemoteLinkConfig()

    resolved = resolve_config(config, _design())

    assert config.account_num == ""
    assert config.receiver_num == "1"
    assert config.users == [] and not config.users_customized
    assert resolved.account_num == "2250"
    assert [(u.number, u.name, u.code, u.profile) for u in resolved.users] == [
        (1, "USER", "2250", "1"),
        (9999, "TECHNICIAN", "12250", "99"),
    ]
    assert resolved.comm == RLComm(connect_type="network", port="2001", serial="")
    assert resolved.arming.entry_delays == (30, 60, 90, 120)
    assert resolved.arming.exit_delay == 120
    assert resolved.arming.advanced == RLAdvanced()


def test_custom_users_replace_dynamic_defaults_without_mutating_source():
    """Once edited, explicit users must survive later school-code changes."""
    config = RemoteLinkConfig(
        users=[RLUser(7, "CUSTODIAN", "7777", "2")],
        users_customized=True,
    )

    resolved = resolve_config(config, _design("9999"))

    assert resolved.users == [RLUser(7, "CUSTODIAN", "7777", "2")]
    assert resolved.users is not config.users


def test_config_json_round_trip_preserves_nested_values_and_int_keypads():
    """A saved project must restore every editable RemoteLink field exactly."""
    config = RemoteLinkConfig(
        account_num="3141",
        receiver_num="7",
        users=[RLUser(1, "ADMIN", "3141", "1")],
        users_customized=True,
        comm=RLComm(connect_type="network", port="2201", serial="FAKE0001"),
        arming=RLArming(
            entry_delays=(15, 30, 45, 60),
            exit_delay=90,
            arm_mode="area",
            advanced=RLAdvanced(
                schedule_enabled=True,
                schedule={
                    "sun": RLScheduleDay("08:00", "22:00"),
                    "mon": RLScheduleDay("07:30", "21:30"),
                },
                ambush_reports=True,
                ambush_output="7",
                morn_ambush_min="5",
                auto_arm=True,
                auto_disarm=True,
            ),
        ),
        keypads={
            1: RLKeypad("LOBBY", "keypad", "keypad_bus", "FFFFFFFF"),
        },
    )

    raw = json.loads(json.dumps(config_to_dict(config)))
    restored = config_from_dict(raw)

    assert restored == config
    assert list(restored.keypads) == [1]
    assert all(isinstance(number, int) for number in restored.keypads)


def test_malformed_config_values_fall_back_to_safe_defaults():
    """Hand-edited or partial session JSON cannot turn truthy strings dangerous."""
    restored = config_from_dict({
        "receiver_num": None,
        "users": [{"number": "bad", "name": 7}],
        "users_customized": "False",
        "comm": {"port": None},
        "arming": {
            "entry_delays": ["bad"],
            "advanced": {
                "schedule_enabled": "True",
                "ambush_reports": 1,
                "auto_arm": "yes",
            },
        },
        "keypads": {"bad": {}, "2": {"name": 17}},
    })

    assert restored.receiver_num == "1"
    assert restored.users == [RLUser(0, "7", "", "")]
    assert restored.users_customized is False
    assert restored.comm.port == "2001"
    assert restored.arming.entry_delays == (30, 60, 90, 120)
    assert restored.arming.advanced == RLAdvanced()
    assert restored.keypads == {
        2: RLKeypad("17", "keypad", "keypad_bus", "FFFFFFFF")
    }
