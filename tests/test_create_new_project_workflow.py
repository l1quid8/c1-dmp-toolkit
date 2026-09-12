"""Blank-project initialization and persistence contract."""

import json
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_dmp_worksheet import SiteInfo  # noqa: E402
from riser_model import RiserTitleBlock  # noqa: E402
from rl_injector.rl_config import RemoteLinkConfig  # noqa: E402
import session as session_mod  # noqa: E402
from session import (  # noqa: E402
    SCHEMA_VERSION,
    load_recovery,
    load_session,
    recovery_path,
    save_session,
    write_recovery,
)


def _create_blank_session(*args, **kwargs):
    factory = getattr(session_mod, "create_blank_session", None)
    assert factory is not None, "session.create_blank_session is missing"
    return factory(*args, **kwargs)


@pytest.fixture
def session_path(tmp_path):
    return tmp_path / "manual-site.dmps"


def test_blank_session_has_manual_identity_and_empty_canonical_graph():
    """The factory must create an ordinary manual session without imported data."""
    session = _create_blank_session(SiteInfo(school_name="MANUAL SITE"))

    assert session.source_kind == "manual"
    assert session.source_name == ""
    assert session.remotelink == RemoteLinkConfig()
    assert session.design.site_info.school_name == "MANUAL SITE"
    assert session.design.topology_source == "manual"
    assert session.design.splitters == []
    assert session.design.rsps == []
    assert session.design.keypads == []
    assert session.design.power_supplies == []
    assert session.design.zones == []
    assert session.design.master_zones == []
    assert session.design.connections == []
    assert session.design.equipment_locations == {}
    assert session.design.riser_document is not None
    assert session.design.riser_document.elements == {}
    assert session.design.riser_document.routes == {}
    assert session.design.riser_document.title_block.school_name == "MANUAL SITE"
    assert session.design.riser_document.title_block.project_title == "MANUAL SITE"


def test_blank_session_applies_persisted_title_fields_and_keeps_defaults():
    """Setup values map to real title fields; omitted values retain title defaults."""
    session = _create_blank_session(
        SiteInfo(school_name="MANUAL SITE"),
        title_block_updates={
            "school_name": "UPDATED SITE",
            "local_code": "A-123",
            "address": "1500 Sycamore Lane\nRiverton, CA 90000",
            "project_title": "UPDATED RISER",
            "sheet_number": "INT-7.0",
            "drawn_by": "FIELD TECH",
            "issue_date": "2026-09-12",
            "unknown_field": "must be ignored",
        },
    )

    title = session.design.riser_document.title_block
    assert title.school_name == "UPDATED SITE"
    assert title.local_code == "A-123"
    assert title.address == "1500 Sycamore Lane\nRiverton, CA 90000"
    assert title.project_title == "UPDATED RISER"
    assert title.sheet_number == "INT-7.0"
    assert title.drawn_by == "FIELD TECH"
    assert title.issue_date == "2026-09-12"
    assert title.drawing_title == RiserTitleBlock().drawing_title
    assert title.system == RiserTitleBlock().system
    assert title.checked_by == RiserTitleBlock().checked_by
    assert title.revisions == []
    assert not hasattr(title, "unknown_field")


@pytest.mark.parametrize("recovery", [False, True])
def test_blank_session_save_and_recovery_round_trip_preserve_manual_state(
    session_path, recovery
):
    """Normal and crash-recovery persistence must retain manual blank-project state."""
    session = _create_blank_session(
        SiteInfo(
            school_name="MANUAL SITE",
            school_code="A-123",
            address_line1="1500 Sycamore Lane",
            address_line2="Riverton, CA 90000",
            xr550_location="MDF",
        ),
        title_block_updates={
            "local_code": "A-123",
            "address": "1500 Sycamore Lane\nRiverton, CA 90000",
            "sheet_number": "INT-7.0",
        },
    )
    session.path = session_path

    if recovery:
        saved_path = write_recovery(session)
        restored = load_recovery(session_path)
        assert saved_path == recovery_path(session_path)
    else:
        saved_path = save_session(session)
        restored = load_session(saved_path)
        raw = json.loads(saved_path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == SCHEMA_VERSION == 8

    assert restored.source_kind == "manual"
    assert restored.design.connections == []
    assert restored.design.topology_source == "manual"
    assert restored.design.site_info.xr550_location == "MDF"
    assert len(restored.design.equipment_locations) == 1
    location = next(iter(restored.design.equipment_locations.values()))
    assert location.full_label == "MDF"
    assert restored.design.device_location_ids == session.design.device_location_ids
    assert restored.design.equipment_locations == session.design.equipment_locations
    assert restored.design.riser_document.title_block.local_code == "A-123"
    assert restored.design.riser_document.title_block.address == (
        "1500 Sycamore Lane\nRiverton, CA 90000"
    )
    assert restored.design.riser_document.title_block.sheet_number == "INT-7.0"
