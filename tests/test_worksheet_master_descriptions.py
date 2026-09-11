"""Master descriptions come from project data, never template example labels."""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_dmp_ws import write_dmp_xlsx
from parse_dmp_worksheet import DMPDesign, RSP, Zone, ZoneInfo, parse_dmp_worksheet

TEMPLATE = ROOT / "DMP Installation Worksheet_template_blank.xlsx"
pytestmark = pytest.mark.skipif(not TEMPLATE.exists(), reason="Worksheet template unavailable")


def test_compact_school_export_has_only_its_96_real_master_descriptions(tmp_path):
    design = DMPDesign()
    for number, (start, end) in enumerate(
        [(501, 516), (517, 524), (525, 540), (541, 548),
         (549, 564), (565, 580), (581, 596)], 1
    ):
        zones = list(range(start, end + 1))
        design.rsps.append(RSP(number, f"ROOM {number}", zones))
        design.zones.extend(
            ZoneInfo(zone, f"ROOM POINT {zone}", "Supervisory" if zone >= end - 1 else "Motion")
            for zone in zones
        )
    output = tmp_path / "school-master.xlsx"
    write_dmp_xlsx(design, TEMPLATE, output)
    reread = parse_dmp_worksheet(output)
    descriptions = {zone.number: zone.description for zone in reread.master_zones}
    assert set(descriptions) == set(range(501, 597)), "Template supervisory placeholders became phantom project zones"
    assert descriptions[595] == "PS-7: A/C LOSS"
    assert descriptions[596] == "PS-7: BATT. TRBL"


@pytest.mark.parametrize("editable,expected", [
    ([], {500: "SPARE", 599: "DRAWING ROOM", 601: "OTHER DRAWING ROOM"}),
    ([ZoneInfo(599, "EDITED ROOM", "Motion")],
     {500: "SPARE", 599: "EDITED ROOM", 601: "OTHER DRAWING ROOM"}),
    ([ZoneInfo(599, None, "Motion")], {500: "SPARE", 601: "OTHER DRAWING ROOM"}),
])
def test_master_only_descriptions_survive_with_editable_values_taking_priority(tmp_path, editable, expected):
    design = DMPDesign(
        master_zones=[Zone(500, "SPARE", is_spare=True), Zone(599, "DRAWING ROOM"),
                      Zone(601, "OTHER DRAWING ROOM")],
        zones=editable,
    )
    output = tmp_path / "master-source-priority.xlsx"
    write_dmp_xlsx(design, TEMPLATE, output)
    reread = parse_dmp_worksheet(output)
    assert {zone.number: zone.description for zone in reread.master_zones} == expected
