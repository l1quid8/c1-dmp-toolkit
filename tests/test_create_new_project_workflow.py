"""Blank-project initialization and persistence contract."""

import copy
import json
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_dmp_worksheet import SiteInfo  # noqa: E402
from riser_model import RiserTitleBlock  # noqa: E402
from riser_editor import RiserEditorController  # noqa: E402
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


@pytest.mark.parametrize("project_title, confirm, prompt_count, copied", [
    ("", False, 0, True),
    ("UPDATED SITE", False, 0, True),
    ("Independent project", False, 1, False),
    ("Independent project", True, 1, True),
])
def test_explicit_site_copy_confirms_title_conflicts_and_preserves_drawing_fields(
    session_path, project_title, confirm, prompt_count, copied
):
    session = _create_blank_session(
        SiteInfo(school_name="Original site"),
        title_block_updates={
            "project_title": project_title, "drawing_title": "Custom drawing",
            "system": "Custom system", "sheet_number": "S-9",
            "drawn_by": "PREPARED", "checked_by": "CHECKED",
            "issue_date": "2025-01-02", "revisions": ["Revision A"],
        },
    )
    session.path = session_path
    save_session(session)
    loaded = load_session(session_path)
    controller = RiserEditorController(loaded.design, loaded.design.riser_document)
    before = copy.deepcopy(controller.document.title_block)
    site = loaded.design.site_info
    site.school_name = "UPDATED SITE"
    site.school_code = "LC-9"
    site.address_line1 = "1500 Sycamore Lane"
    site.address_line2 = "Riverton, CA 90000"
    controller.sync_external()
    assert controller.document.title_block == before
    prompts = []

    def confirm_overwrite(current, replacement):
        prompts.append((current, replacement))
        return confirm

    result = controller.copy_title_from_site(confirm_overwrite=confirm_overwrite)

    assert result is copied
    assert len(prompts) == prompt_count
    if prompt_count:
        assert prompts == [("Independent project", "UPDATED SITE")]
    if not copied:
        assert controller.document.title_block == before
        assert not controller.can_undo
        return
    expected = copy.deepcopy(before)
    expected.school_name = "UPDATED SITE"
    expected.local_code = "LC-9"
    expected.address = "1500 Sycamore Lane\nRiverton, CA 90000"
    expected.project_title = "UPDATED SITE"
    assert controller.document.title_block == expected
    assert controller.undo()
    assert controller.document.title_block == before
    assert site.school_name == "UPDATED SITE"
    assert controller.redo()
    assert controller.document.title_block == expected


def test_site_copy_without_confirmation_cannot_replace_an_independent_title():
    session = _create_blank_session(SiteInfo(school_name="Site"),
                                    title_block_updates={"project_title": "Independent"})
    controller = RiserEditorController(session.design, session.design.riser_document)
    before = copy.deepcopy(controller.document)

    assert controller.copy_title_from_site() is False

    assert controller.document == before
    assert not controller.can_undo


def test_site_copy_clears_missing_site_fields_without_copying_install_metadata():
    session = _create_blank_session(
        SiteInfo(install_tech="SITE TECH", install_date="2026-09-12"),
        title_block_updates={"school_name": "Old school", "local_code": "Old code",
                             "address": "Old address", "project_title": ""},
    )
    controller = RiserEditorController(session.design, session.design.riser_document)

    assert controller.copy_title_from_site() is True

    title = controller.document.title_block
    assert (title.school_name, title.local_code, title.address, title.project_title) == ("", "", "", "")
    assert title.drawn_by == RiserTitleBlock().drawn_by
    assert title.issue_date == RiserTitleBlock().issue_date
    assert controller.copy_title_from_site() is False


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
