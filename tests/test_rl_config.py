"""Typed configuration tests for configurable RemoteLink generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_dmp_worksheet import DMPDesign, Keypad, SiteInfo  # noqa: E402
from rl_injector.rl_config import (  # noqa: E402
    ARM_MODES,
    COMM_PATH_TYPES,
    CONNECT_TYPES,
    KEYPAD_COMM_TYPES,
    KEYPAD_DEFAULT_DISPLAY_AREAS,
    KEYPAD_DEVICE_TYPES,
    SCHEDULE_FIELD_MAP,
    RLAdvanced,
    RLArming,
    RLComm,
    RLKeypad,
    RLScheduleDay,
    RLUser,
    RemoteLinkConfig,
    config_from_dict,
    config_to_dict,
    effective_keypad,
    reconcile_keypads,
    resolve_config,
    validate_config,
)
from editor_zones import rl_type_display, rl_type_from_label  # noqa: E402


FIELD_MAPS = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "rl_field_maps.json").read_text()
)


def _design(code: str = "2250") -> DMPDesign:
    return DMPDesign(site_info=SiteInfo(
        school_name="TEST ELEMENTARY SCHOOL",
        school_code=code,
    ))


def test_verified_connect_type_mappings_match_fake_account_exports():
    assert dict(CONNECT_TYPES) == FIELD_MAPS["connect_types"]


def test_verified_arm_modes_include_their_required_system_area_layouts():
    assert {
        key: {
            "arm_mode": value.raw,
            "inst_arm": value.inst_arm,
            "areas": list(value.areas),
        }
        for key, value in ARM_MODES.items()
    } == FIELD_MAPS["arm_modes"]


def test_verified_keypad_type_and_bus_mappings_match_fake_exports():
    assert dict(KEYPAD_DEVICE_TYPES) == FIELD_MAPS["keypad_device_types"]
    assert dict(KEYPAD_COMM_TYPES) == FIELD_MAPS["keypad_comm_types"]
    assert dict(KEYPAD_DEFAULT_DISPLAY_AREAS) == \
        FIELD_MAPS["keypad_default_display_areas"]


def test_extra_communication_path_exports_are_preserved_as_evidence():
    assert dict(COMM_PATH_TYPES) == FIELD_MAPS["communication_path_types"]


def test_schedule_field_map_matches_calibrated_monday_and_known_weekday_dates():
    expected = FIELD_MAPS["schedule"]
    assert {
        day: list(fields)
        for day, fields in SCHEDULE_FIELD_MAP["day_fields"].items()
    } == expected["day_fields"]
    assert [list(field) for field in SCHEDULE_FIELD_MAP["static_fields"]] == \
        expected["static_fields"]
    assert SCHEDULE_FIELD_MAP["area_link_field"] == expected["area_link_field"]
    assert SCHEDULE_FIELD_MAP["schedule_number"] == expected["schedule_number"]


def test_zone_type_display_distinguishes_auto_explicit_and_spare():
    assert rl_type_display("", automatic="NT") == "Auto → Night"
    assert rl_type_display("EX", automatic="NT") == "Exit"
    assert rl_type_display("SV", automatic="NT") == "Supervisory"
    assert rl_type_display("NT", automatic="EX", spare=True) == "Spare"


def test_zone_type_label_parser_stores_auto_as_blank_code():
    assert rl_type_from_label("Auto") == ""
    assert rl_type_from_label("Auto → Night") == ""
    assert rl_type_from_label("Night") == "NT"
    assert rl_type_from_label("Exit") == "EX"
    assert rl_type_from_label("Supervisory") == "SV"


def test_effective_keypad_derives_safe_defaults_without_persisting_them():
    config = RemoteLinkConfig()
    keypad = Keypad(number=7, location="LIBRARY")

    effective = effective_keypad(config, keypad)

    assert effective == RLKeypad(
        "KEYPAD 7", "keypad", "keypad_bus", "FFFFFFFF"
    )
    assert config.keypads == {}


def test_effective_keypad_uses_saved_values_but_fills_blank_name():
    config = RemoteLinkConfig(keypads={
        2: RLKeypad("", "door", "keypad_bus", "00000001"),
    })

    assert effective_keypad(config, Keypad(number=2)) == RLKeypad(
        "KEYPAD 2", "door", "keypad_bus", "00000001"
    )


def test_reconcile_keypads_removes_stale_numbers_and_keeps_survivors():
    config = RemoteLinkConfig(keypads={
        1: RLKeypad("LOBBY"),
        2: RLKeypad("OFFICE"),
        9: RLKeypad("REMOVED"),
    })
    design = _design()
    design.keypads = [Keypad(1), Keypad(2), Keypad(3)]

    changed = reconcile_keypads(config, design)

    assert changed is True
    assert config.keypads == {
        1: RLKeypad("LOBBY"),
        2: RLKeypad("OFFICE"),
    }


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


def test_valid_configuration_has_no_validation_warnings():
    """Safe defaults resolved from a numeric school code are immediately usable."""
    assert validate_config(RemoteLinkConfig(), _design()) == []


def test_duplicate_user_numbers_and_codes_are_reported_separately():
    """Both collisions matter because RemoteLink keys users by number and code."""
    config = RemoteLinkConfig(
        users=[
            RLUser(7, "ONE", "7777", "1"),
            RLUser(7, "TWO", "7777", "2"),
        ],
        users_customized=True,
    )

    issues = validate_config(config, _design())

    assert {issue.code for issue in issues} == {
        "remotelink.user_number_duplicate",
        "remotelink.user_code_duplicate",
    }


def test_nonnumeric_and_short_user_codes_are_reported():
    """Invalid panel codes should be visible before encrypted generation."""
    config = RemoteLinkConfig(
        users=[
            RLUser(1, "ALPHA", "A123", "1"),
            RLUser(2, "SHORT", "12", "1"),
        ],
        users_customized=True,
    )

    issues = validate_config(config, _design())

    assert [issue.code for issue in issues].count("remotelink.user_code_invalid") == 2


def test_port_outside_tcp_range_is_reported():
    """A numeric but unusable port cannot silently reach the account."""
    config = RemoteLinkConfig(comm=RLComm(port="70000"))

    assert {issue.code for issue in validate_config(config, _design())} == {
        "remotelink.port_invalid"
    }


def test_incomplete_enabled_schedule_day_is_reported():
    """An open time without its close partner is ambiguous and unsafe."""
    config = RemoteLinkConfig(arming=RLArming(advanced=RLAdvanced(
        schedule_enabled=True,
        schedule={"mon": RLScheduleDay(open_time="08:00", close_time="")},
    )))

    assert {issue.code for issue in validate_config(config, _design())} == {
        "remotelink.schedule_incomplete"
    }


def test_invalid_keypad_display_area_mask_is_reported():
    """Displayed areas must use RemoteLink's eight-digit hexadecimal mask."""
    config = RemoteLinkConfig(keypads={
        1: RLKeypad("LOBBY", "keypad", "keypad_bus", "AREA 1"),
    })

    assert {issue.code for issue in validate_config(config, _design())} == {
        "remotelink.display_areas_invalid"
    }


def test_non_display_device_uses_verified_two_digit_area_value():
    config = RemoteLinkConfig(keypads={
        1: RLKeypad("EXPANDER", "zone_expander", "keypad_bus", "00"),
    })

    assert validate_config(config, _design()) == []


def test_invalid_schedule_time_is_reported_before_generation():
    config = RemoteLinkConfig(arming=RLArming(advanced=RLAdvanced(
        schedule_enabled=True,
        schedule={"mon": RLScheduleDay("25:99", "22:00")},
    )))

    assert {issue.code for issue in validate_config(config, _design())} == {
        "remotelink.schedule_time_invalid"
    }


def test_unverified_enum_values_are_never_accepted_as_raw_programming():
    config = RemoteLinkConfig(
        comm=RLComm(connect_type="mystery"),
        arming=RLArming(arm_mode="mystery"),
        keypads={1: RLKeypad("X", "mystery", "mystery", "FFFFFFFF")},
    )

    assert {issue.code for issue in validate_config(config, _design())} == {
        "remotelink.connect_type_invalid",
        "remotelink.arm_mode_invalid",
        "remotelink.keypad_device_type_invalid",
        "remotelink.keypad_comm_type_invalid",
    }
