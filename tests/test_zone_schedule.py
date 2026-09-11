"""Tests for the MOTION DETECTOR ZONE SCHEDULE parser (parse_zone_schedule.extract_zones).

PyMuPDF emits each table cell on its own line. The parser must find a zone-id even when
OCR prepends a floating callout annotation onto the cell line (the real-world failure where
zones 557/573 were silently dropped from the TOLUCA design).

Run: pytest tests/test_zone_schedule.py
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_zone_schedule import (  # noqa: E402
    extract_address_from_title_block,
    extract_combus_lines,
    extract_zones,
)


def _by_num(records):
    return {int(r.zone[1:]): r for r in records}


def test_mid_line_zone_id_is_recovered():
    """A zone-id merged behind an OCR callout annotation must still be parsed, and the
    preceding clean zone must keep its own room (its body just gets shortened)."""
    text = "\n".join([
        "Z556/RSP5",
        "BUILDING C",
        "1ST FLR",
        "CLASSROOM 27",
        "NEW",
        "(N)AQC240",
        "(MAIN OFFICE, BUILDING | 7557/RSP5",   # callout annotation merged onto the id cell
        "BUILDING C",
        "1ST FLR",
        "CLASSROOM 24",
        "NEW",
        "(N)AQC240",
        "Z558/RSP5",
        "BUILDING C",
        "1ST FLR",
        "CLASSROOM 25",
        "NEW",
        "(N)AQC240",
    ])
    zones = _by_num(extract_zones(text))
    assert set(zones) == {556, 557, 558}
    assert zones[557].rsp == 5
    assert zones[557].room == "CLASSROOM 24"
    assert zones[556].room == "CLASSROOM 27"   # unchanged despite shortened body
    assert zones[558].room == "CLASSROOM 25"


def test_leading_z_misread_still_parses():
    """Leading 'Z' misread as 7/2 (or present) all resolve to the 3-digit zone number."""
    text = "\n".join([
        "7501/RSP1", "BUILDING A", "1ST FLR", "ROOM A", "NEW", "(N)WP240",
        "2502/RSP1", "BUILDING A", "1ST FLR", "ROOM B", "NEW", "(N)WP240",
        "Z503/RSP1", "BUILDING A", "1ST FLR", "ROOM C", "NEW", "(N)WP240",
    ])
    zones = _by_num(extract_zones(text))
    assert set(zones) == {501, 502, 503}
    assert zones[501].rsp == 1


def test_no_false_zone_from_noise_lines():
    """Cable types and combus labels must not be mistaken for zone-ids."""
    text = "\n".join([
        "RSP 5",
        "(N)WP240",
        "BUILDING C",
        "Z510/RSP1",
        "BUILDING A",
        "1ST FLR",
        "ROOM",
    ])
    zones = _by_num(extract_zones(text))
    assert set(zones) == {510}


def test_ocr_missing_separator_and_rsp_digit_artifact_keep_spare_and_ac_zones():
    """4th St OCR read Z547/RSP4 as 2547/RSPA4 and Z574/RSP6 without its slash."""
    text = "\n".join([
        "2546/RSP4", "SPARE",
        "2547/RSPA4", "NORTH BLDG", "1ST FLR", "CLASSROOM 17A", "N/A", "AC POWER",
        "2548/RSP4", "NORTH BLDG", "1ST FLR", "CLASSROOM 17A", "N/A", "BATTERY TROUBLE",
        "7573/RSP6", "BLDG BB 219", "2ND FLR", "CLASSROOM 37", "EXISTING", "(E)WP240",
        "7574RSP6", "SPARE",
        "7575/RSP6", "SPARE",
    ])
    zones = _by_num(extract_zones(text))
    assert set(zones) == {546, 547, 548, 573, 574, 575}
    assert zones[546].is_spare and not zones[546].is_ps_ac
    assert zones[547].rsp == 4 and zones[547].is_ps_ac
    assert zones[547].room == "CLASSROOM 17A"
    assert not zones[547].is_ps_batt
    assert zones[548].is_ps_batt and not zones[548].is_ps_ac
    assert zones[573].room == "CLASSROOM 37"
    assert zones[574].rsp == 6 and zones[574].is_spare


def test_title_block_address_accepts_street_name_without_suffix():
    """Some C1 title blocks omit AVE/ST even though the site address is valid."""
    text = "\n".join([
        "9000 ROCHESTER AVE #150",
        "RANCHO CUCAMONGA, CA 91730",
        "2025 GRIFFIN, LOS ANGELES, CA 90031",
    ])

    assert extract_address_from_title_block(text) == {
        "address_line1": "2025 GRIFFIN",
        "address_line2": "LOS ANGELES, CA 90031",
    }


def test_combus_rows_survive_reordered_zone_schedule_text_before_table():
    """CAD/OCR ordering can emit a neighboring schedule before COMBUS rows."""
    text = "\n".join([
        "SCHOOL NAME: TARZANA ES",
        "COMBUS LINES (RSP & KEYPADS)",
        "GENERAL NOTES:",
        "MOTION DETECTOR ZONE SCHEDULE",
        "ZONE NO.",
        "Z549/RSP4",
        "KINDERGARTEN BLDG",
        "NO.",
        "BUILDING",
        "FLOOR NO.",
        "ROOM/AREA",
        "FED FROM",
        "CABLE TYPE",
        "KEYPAD 1",
        "MAIN BLDG (SERVICE KP)",
        "1ST FLR",
        "SUPPLY ROOM",
        "MSP",
        "(N)WP240",
        "KEYPAD 2",
        "MAIN BLDG",
        "1ST FLR",
        "MAIN OFFICE",
        "MSP",
        "(N)AQC240",
        "KEYPAD 3",
        "AUDITORIUM",
        "1ST FLR",
        "CAFE MANAGER'S OFFICE",
        "MSP",
        "(N)AQC240",
        "KEYPAD 4",
        "BLDG X1325M",
        "1ST FLR",
        "CLERK'S AREA",
        "MSP",
        "(N)AQC240",
        "SCHOOL NAME: TARZANA ES",
        "MOTION DETECTOR ZONE SCHEDULE",
    ])

    rows = extract_combus_lines(text)

    assert [(row.kind, row.n, row.room) for row in rows] == [
        ("KEYPAD", 1, "SUPPLY ROOM"),
        ("KEYPAD", 2, "MAIN OFFICE"),
        ("KEYPAD", 3, "CAFE MANAGER'S OFFICE"),
        ("KEYPAD", 4, "CLERK'S AREA"),
    ]


def test_combus_inline_building_does_not_turn_supply_reference_into_a_location():
    """OCR can join the ID and building cells while leaving the rest separate."""
    text = "\n".join([
        "COMBUS LINES (RSP & KEYPADS)",
        "RSP5", "BUILDING A", "1ST FLR", "DATA ROOM", "MSP", "(N)AQC240",
        "RSP7", "BUILDING B", "1ST FLR", "STORAGE", "MSP", "(N)AQC240",
        "KEYPAD 7 | BUILDING A", "1ST FLR", "WAITING AREA (NE)", "RSP5", "(E)WP240",
        "KEYPAD 8", "BUILDING B", "1ST FLR", "KITCHEN (SW)", "RSP7", "(E)WP240",
    ])

    rows = extract_combus_lines(text)

    assert [(row.kind, row.n, row.building, row.room, row.fed_from, row.cable_type)
            for row in rows] == [
        ("RSP", 5, "BUILDING A", "DATA ROOM", "MSP", "(N)AQC240"),
        ("RSP", 7, "BUILDING B", "STORAGE", "MSP", "(N)AQC240"),
        ("KEYPAD", 7, "BUILDING A", "WAITING AREA (NE)", "RSP5", "(E)WP240"),
        ("KEYPAD", 8, "BUILDING B", "KITCHEN (SW)", "RSP7", "(E)WP240"),
    ]


def test_combus_incomplete_row_cannot_consume_cable_or_next_equipment_row():
    text = "\n".join([
        "COMBUS LINES (RSP & KEYPADS)",
        "RSP5", "(E)WP240",
        "KEYPAD 8", "BUILDING B", "1ST FLR", "KITCHEN", "MSP", "(E)WP240",
        "RSP7", "BUILDING C",
        "KEYPAD 9", "BUILDING C", "1ST FLR", "OFFICE", "MSP", "(E)WP240",
    ])

    rows = extract_combus_lines(text)

    assert [(row.kind, row.n, row.building, row.room) for row in rows] == [
        ("KEYPAD", 8, "BUILDING B", "KITCHEN"),
        ("KEYPAD", 9, "BUILDING C", "OFFICE"),
    ]
