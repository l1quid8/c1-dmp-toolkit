"""Large LX inventories and bare 710 IDs survive a worksheet round trip."""

from collections import Counter
from pathlib import Path
import sys
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_dmp_ws import write_dmp_xlsx
from parse_dmp_worksheet import DMPDesign, Splitter, parse_dmp_worksheet
from riser_model import DevicePortRef, derive_legacy_connections
from session import Session, load_session, save_session
from validation import validate_design


ROOT = Path(__file__).resolve().parents[1]


def test_fourteen_lx_splitters_round_trip_without_capacity_error(tmp_path):
    design = DMPDesign(splitters=[
        Splitter(f"710-LX500-{n}", "LX", "ELECT RM",
                 {"LX-Bus In": "500 BUS IN FROM XR/550"}, ["Spare"] * 3)
        for n in range(1, 15)
    ])
    path = tmp_path / "fourteen.xlsx"
    write_dmp_xlsx(design, ROOT / "DMP Installation Worksheet_template_blank.xlsx", path)

    parsed = parse_dmp_worksheet(path)
    assert [s.id for s in parsed.splitters] == [s.id for s in design.splitters]
    assert all(s.inputs for s in parsed.splitters)
    assert not any(issue.code == "capacity.exceeded"
                   for issue in validate_design(parsed, topology_confirmed=True))


def test_bare_splitter_output_ids_form_chain_without_duplicate_panel_feeds():
    design = DMPDesign(splitters=[
        Splitter("KP-710-1", "KP", inputs={"KP-Bus In": "XR/550"},
                 outputs=["710-KP-2", "710-KP-4", "Spare"]),
        Splitter("KP-710-2", "KP", inputs={"KP-Bus In": "OUTPUT 1 KP-710-1"},
                 outputs=["710-KP-3", "Spare", "Spare"]),
        Splitter("KP-710-3", "KP", inputs={"KP-Bus In": "710-KP-2"},
                 outputs=["KEYPAD #3", "Spare", "Spare"]),
        Splitter("KP-710-4", "KP", inputs={"KP-Bus In": "710-KP-1"},
                 outputs=["KEYPAD #6", "Spare", "Spare"]),
    ])
    edges = derive_legacy_connections(design)
    sources = Counter(edge.source for edge in edges)

    assert not any(count > 1 for count in sources.values())
    assert [edge.target.device_id for edge in edges
            if edge.source == DevicePortRef("MSP", "PROG")] == ["KP-710-1"]
    assert any(edge.source == DevicePortRef("KP-710-1", "OUT2")
               and edge.target == DevicePortRef("KP-710-4", "IN")
               for edge in edges)
    assert any(edge.source == DevicePortRef("KP-710-3", "OUT1")
               and edge.target == DevicePortRef("KEYPAD-3", "IN")
               for edge in edges)


def test_splitter_output_locations_survive_session_and_worksheet(tmp_path):
    # The source sheet records the destination location on each output row.
    # One destination may disagree with the target's own device row, so the
    # original output-row text must take precedence over inferred locations.
    design = DMPDesign(splitters=[
        Splitter("KP-710-1", "KP", "STUDENT STORE",
                 {"KP-Bus In": "KEYPAD BUS IN FROM XR/550"},
                 ["710-KP-2", "Spare", "KEYPAD #2"],
                 ["MUSIC TELECOM", "SPARE", "ADMIN OFFICE"]),
        Splitter("KP-710-2", "KP", "STUDENT STORE",
                 {"KP-Bus In": "KP-710-1"}, ["Spare"] * 3,
                 ["SPARE"] * 3),
        Splitter("710-LX500-1", "LX", "STUDENT STORE",
                 {"LX-Bus In": "LX-500 BUS XR-550"},
                 ["710-LX500-6", "Spare", "Spare"],
                 ["2-STORY ELECT RM", "SPARE", "SPARE"]),
    ])
    session_path = tmp_path / "locations.dmps"
    save_session(Session(design=design), session_path)
    restored = load_session(session_path).design
    assert restored.splitters[0].output_locations == [
        "MUSIC TELECOM", "SPARE", "ADMIN OFFICE"]

    path = tmp_path / "locations.xlsx"
    write_dmp_xlsx(restored, ROOT / "DMP Installation Worksheet_template_blank.xlsx", path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    assert wb["710 Splitter-Repeater(KP-Bus) "]["D3"].value == "MUSIC TELECOM"
    assert wb["710 Splitter-Repeater(KP-Bus) "]["D4"].value == "SPARE"
    assert wb["710 Splitter-Repeater LX500"]["D3"].value == "2-STORY ELECT RM"

    reparsed = parse_dmp_worksheet(path)
    assert reparsed.splitters[0].output_locations == [
        "MUSIC TELECOM", "SPARE", "ADMIN OFFICE"]
