"""Splitter feed rows accept the capitalization used on field worksheets."""

from pathlib import Path
import sys

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from parse_dmp_worksheet import _parse_kp_splitters, _parse_lx_splitters


def test_uppercase_bus_in_is_an_input_not_an_output():
    sheet = openpyxl.Workbook().active
    sheet.append(["710-LX600-1", "LX-BUS IN", "LX-600 BUS XR-550", "ELECT RM"])
    sheet.append([None, "LX-OUT-1", "RSP-7", None])

    splitter, = _parse_lx_splitters(sheet)
    assert splitter.inputs == {"LX-BUS IN": "LX-600 BUS XR-550"}
    assert splitter.outputs == ["RSP-7"]


def test_keypad_bus_in_case_is_also_accepted():
    sheet = openpyxl.Workbook().active
    sheet.append(["KP-710-1", "KP-BUS IN", "XR550", "MDF"])
    sheet.append([None, "KP-OUT-1", "KEYPAD #1", None])

    splitter, = _parse_kp_splitters(sheet)
    assert splitter.inputs == {"KP-BUS IN": "XR550"}
    assert splitter.outputs == ["KEYPAD #1"]
