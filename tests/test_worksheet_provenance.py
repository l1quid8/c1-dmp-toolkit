"""A door chart must know which worksheet this project made, and whether the
project changed since, even after the app is closed and reopened."""
import copy
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app as app_module
import session as session_module
from parse_dmp_worksheet import SiteInfo
from riser_model import RiserElement
from session import (create_blank_session, load_session, record_worksheet,
                     save_session, worksheet_fingerprint)


def _project():
    return create_blank_session(SiteInfo(school_name="TRACK SCHOOL"))


def test_fingerprint_survives_save_and_reopen_without_mutating(tmp_path):
    project = _project()
    snapshot = copy.deepcopy(project.design)
    before = worksheet_fingerprint(project.design)
    assert project.design == snapshot
    loaded = load_session(save_session(project, tmp_path / "track.dmps"))
    assert worksheet_fingerprint(loaded.design) == before


def test_fingerprint_tracks_worksheet_content_not_riser_layout():
    project = _project()
    before = worksheet_fingerprint(project.design)
    project.design.riser_document.elements["device:MSP"] = RiserElement(
        "device:MSP", "device", "MSP", 1.0, 2.0, 90.0, 110.0, manual=True)
    assert worksheet_fingerprint(project.design) == before
    project.design.site_info.school_code = "EDITED"
    assert worksheet_fingerprint(project.design) != before


def test_record_round_trips_without_counting_as_a_save(tmp_path):
    project = _project()
    path = save_session(project, tmp_path / "track.dmps")
    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev3.xlsx"
    record_worksheet(project, sheet, "abc123", update_file=True)
    loaded = load_session(path)
    assert (loaded.worksheet_path, loaded.worksheet_fingerprint) == (str(sheet), "abc123")
    assert loaded.saved_at == project.saved_at
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("worksheet")                      # a project saved by 1.7.2
    path.write_text(json.dumps(data), encoding="utf-8")
    legacy = load_session(path)
    assert legacy.worksheet_path is None and legacy.worksheet_fingerprint is None


def test_record_while_dirty_leaves_disk_alone(tmp_path):
    project = _project()
    path = save_session(project, tmp_path / "track.dmps")
    text = path.read_text(encoding="utf-8")
    record_worksheet(project, tmp_path / "x.xlsx", "abc", update_file=False)
    assert path.read_text(encoding="utf-8") == text
    assert project.worksheet_fingerprint == "abc"


def test_unwritable_project_file_does_not_break_recording(tmp_path, monkeypatch):
    project = _project()
    save_session(project, tmp_path / "track.dmps")
    def locked(path, text):
        raise PermissionError("locked")
    monkeypatch.setattr(session_module, "_atomic_write", locked)
    record_worksheet(project, tmp_path / "x.xlsx", "abc", update_file=True)
    assert project.worksheet_fingerprint == "abc"


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "output_dir", lambda: tmp_path)
    c = object.__new__(app_module.App)
    c.session = _project()
    c.output_dir = tmp_path
    c.state, c._generating = "editing", None
    c.pdf_path = c.dmp_path = c.door_chart_path = None
    c._redirector = io.StringIO()
    c.root = SimpleNamespace(after=lambda ms, fn: fn())
    c.editor = SimpleNamespace(dirty=False, edit_epoch=0, generation_allowed=lambda: True)
    c.confirmations, c.started = [], []
    c._confirm_chart_source = lambda src, status: (
        c.confirmations.append((src.name, status)), "cancel")[1]
    c._set_generating = lambda which: c.started.append(which)
    c._run_async = lambda work, done, error: None
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **k: None)
    return c


def _record_current(c, sheet):
    sheet.touch()
    record_worksheet(c.session, sheet, worksheet_fingerprint(c.session.design), update_file=False)


def test_recorded_worksheet_is_used_after_reopen_without_prompt(controller, tmp_path):
    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev3.xlsx"
    _record_current(controller, sheet)
    controller.session = load_session(save_session(controller.session, tmp_path / "track.dmps"))

    assert controller._door_chart_source() == (sheet, "current")
    controller._generate_door_chart()
    assert controller.confirmations == [] and controller.started == ["chart"]


def test_edits_after_generation_are_stale_after_reopen(controller, tmp_path):
    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev3.xlsx"
    _record_current(controller, sheet)
    controller.session.design.site_info.school_code = "EDITED AFTER REV 3"
    controller.session = load_session(save_session(controller.session, tmp_path / "track.dmps"))

    controller._generate_door_chart()
    assert controller.confirmations == [(sheet.name, "stale")]
    assert controller.started == []


@pytest.mark.parametrize("source_kind", ["pdf", "xlsx"])
def test_worksheet_found_only_by_school_name_needs_confirmation(controller, tmp_path, source_kind):
    controller.session.source_kind = source_kind
    other = tmp_path / "TRACK_SCHOOL_dmp_rev9.xlsx"
    other.touch()

    controller._generate_door_chart()
    assert controller.confirmations == [(other.name, "unknown")]
    assert controller.started == []


def test_moved_worksheet_falls_back_with_confirmation(controller, tmp_path):
    record_worksheet(controller.session, tmp_path / "moved-away.xlsx",
                     worksheet_fingerprint(controller.session.design), update_file=False)
    controller.session.source_kind = "pdf"
    fallback = tmp_path / "TRACK_SCHOOL_dmp_rev2.xlsx"
    fallback.touch()
    assert controller._door_chart_source() == (fallback, "unknown")


def test_generating_worksheet_records_it_on_disk(controller, tmp_path, monkeypatch):
    path = save_session(controller.session, tmp_path / "track.dmps")
    controller.editor.show_issues_dialog = lambda proceed, **kw: proceed()
    controller._run_async = lambda work, done, error: done(work())
    controller._show_toast = lambda *a, **k: None
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})
    monkeypatch.setattr(app_module, "save_prefs", lambda prefs: None)
    monkeypatch.setattr(app_module, "write_dmp_xlsx",
                        lambda design, template, out: out.touch())

    controller._generate_worksheet()

    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev1.xlsx"
    assert controller.session.worksheet_path == str(sheet)
    assert controller.session.worksheet_fingerprint == worksheet_fingerprint(controller.session.design)
    assert load_session(path).worksheet_path == str(sheet)   # survives closing without saving
