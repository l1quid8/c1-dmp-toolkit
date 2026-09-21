"""Background controller regressions at existing import/persistence boundaries."""

import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app as app_module
import session as session_module
from parse_dmp_worksheet import DMPDesign, SiteInfo
from parse_bom import BOMImport
from riser_model import RiserElement
from session import create_blank_session, load_session, save_session, write_recovery


@pytest.fixture
def controller(monkeypatch, tmp_path):
    """Run real routing and completion logic; replace only UI and scheduling."""
    monkeypatch.setattr(session_module, "output_dir", lambda: tmp_path)
    app = object.__new__(app_module.App)
    app.root = app.editor = app.session = None
    app.output_dir = tmp_path
    app.state, app._generating, app._creating_project = "idle", None, False
    app.pdf_path = app.dmp_path = app.door_chart_path = None
    app.parsed_design = None
    app._redirector = io.StringIO()
    app._teardown_editor = lambda: None
    app._stop_spinners = lambda: None
    app._show_file_card = lambda *args, **kwargs: None
    app.entered, app.errors = [], []

    def enter(project, *, initial_tab="ZONES"):
        app.session = project
        app.state = "editing"
        app.entered.append((project, initial_tab))

    def run(work, done, error):
        try:
            result = work()
        except Exception as exc:
            error(exc)
        else:
            done(result)

    app._enter_editor, app._run_async = enter, run
    app._show_parse_error = lambda exc, **kwargs: app.errors.append((exc, kwargs))
    return app


@pytest.fixture
def boundaries(monkeypatch):
    """Spies retain real schema/recovery/shape behavior; parsing is external."""
    spies = {}
    for name in ("build_dmp_design_from_pdf", "parse_dmp_worksheet"):
        spies[name] = Mock(side_effect=AssertionError(f"unexpected {name}"))
    for name in ("worksheet_looks_like_dmp", "load_session", "load_recovery"):
        spies[name] = Mock(wraps=getattr(app_module, name))
    for name, spy in spies.items():
        monkeypatch.setattr(app_module, name, spy)
    monkeypatch.setattr(app_module, "ensure_searchable_pdf", lambda path: path)
    monkeypatch.setattr(app_module, "resolve_original_pdf", lambda path: path)
    return spies


def test_pdf_route_uses_searchable_and_original_paths(controller, boundaries, monkeypatch, tmp_path):
    # A PDF routed to XLSX/DMPS, or parser given the wrong OCR/original path, fails.
    source, searchable, original = (tmp_path / name for name in
                                    ("source.PDF", "searchable.pdf", "original.pdf"))
    monkeypatch.setattr(app_module, "ensure_searchable_pdf", lambda path: searchable
                        if path == source else pytest.fail("wrong PDF input"))
    monkeypatch.setattr(app_module, "resolve_original_pdf", lambda path: original
                        if path == source else pytest.fail("wrong original input"))
    boundaries["build_dmp_design_from_pdf"].side_effect = None
    boundaries["build_dmp_design_from_pdf"].return_value = DMPDesign(
        site_info=SiteInfo(school_name="PDF SCHOOL"))

    controller._start_input(source)

    project, tab = controller.entered[0]
    assert project.design.site_info.school_name == "PDF SCHOOL"
    assert (project.source_kind, project.source_name, tab) == ("pdf", "source.PDF", "ZONES")
    assert controller.pdf_path == source and not controller.errors
    boundaries["build_dmp_design_from_pdf"].assert_called_once_with(
        searchable, original, non_interactive=True)
    for name in ("parse_dmp_worksheet", "worksheet_looks_like_dmp", "load_session", "load_recovery"):
        boundaries[name].assert_not_called()


@pytest.mark.parametrize("valid", [True, False])
def test_xlsx_route_retains_shape_guard_and_own_chart_source(controller, boundaries, tmp_path, valid):
    # Wrong route, bypassed real shape guard, lost imported source, or path collision fails.
    source = tmp_path / "input.XLSX"
    source.touch()
    existing = create_blank_session(SiteInfo(school_name="XLSX SCHOOL"))
    old = save_session(existing)
    old_text = old.read_text()
    design = DMPDesign(site_info=SiteInfo(school_name="XLSX SCHOOL" if valid else ""))
    boundaries["parse_dmp_worksheet"].side_effect = None
    boundaries["parse_dmp_worksheet"].return_value = design

    controller._start_input(source)

    boundaries["parse_dmp_worksheet"].assert_called_once_with(source)
    boundaries["worksheet_looks_like_dmp"].assert_called_once_with(design)
    assert old.read_text() == old_text
    if valid:
        project, tab = controller.entered[0]
        assert project.design.site_info.school_name == "XLSX SCHOOL"
        assert (project.source_kind, project.source_name, tab) == ("xlsx", "input.XLSX", "ZONES")
        assert project.path == tmp_path / "Sessions" / "XLSX_SCHOOL (2).dmps"
        assert controller.dmp_path == source and controller._latest_worksheet_path() == source
        assert not controller.errors
    else:
        assert controller.state == "idle" and controller.dmp_path is None
        assert not controller.entered and len(controller.errors) == 1
        assert controller.errors[0][1]["title"] == "Not a DMP worksheet"
    for name in ("build_dmp_design_from_pdf", "load_session", "load_recovery"):
        boundaries[name].assert_not_called()


def test_bom_xlsx_requires_review_and_is_not_a_worksheet(controller, boundaries, monkeypatch, tmp_path):
    source = tmp_path / "site.xlsx"
    source.touch()
    boundaries["parse_dmp_worksheet"].side_effect = None
    boundaries["parse_dmp_worksheet"].return_value = DMPDesign()
    draft = DMPDesign(site_info=SiteInfo(school_name="BOM SCHOOL"))
    monkeypatch.setattr(app_module, "parse_bom", lambda path: BOMImport(draft, "RSP-1 — row S1"))
    seen = []
    monkeypatch.setattr(app_module, "ask_bom_review", lambda root, text:
                        seen.append(text) or True)

    controller._start_input(source)

    assert seen == ["RSP-1 — row S1"]
    project, tab = controller.entered[0]
    assert (project.source_kind, project.source_name, tab) == ("bom", "site.xlsx", "ZONES")
    assert project.path == tmp_path / "Sessions" / "BOM_SCHOOL.dmps"
    assert controller.dmp_path is None
    assert controller._latest_worksheet_path() is None
    assert not controller.errors


def test_cancel_bom_review_creates_no_project(controller, boundaries, monkeypatch, tmp_path):
    source = tmp_path / "site.xlsx"
    source.touch()
    boundaries["parse_dmp_worksheet"].side_effect = None
    boundaries["parse_dmp_worksheet"].return_value = DMPDesign()
    monkeypatch.setattr(app_module, "parse_bom", lambda path: BOMImport(
        DMPDesign(site_info=SiteInfo(school_name="BOM SCHOOL")), "review"))
    monkeypatch.setattr(app_module, "ask_bom_review", lambda root, text: False)

    controller._start_input(source)

    assert controller.state == "idle"
    assert controller.dmp_path is None
    assert not controller.entered


@pytest.mark.parametrize("recovery_choice", [None, False, True])
def test_dmps_route_uses_normal_or_recovery_loader(controller, boundaries, monkeypatch, tmp_path,
                                                recovery_choice):
    # Wrong import route, inverted recovery choice, or discarded saved title/geometry fails.
    project = create_blank_session(SiteInfo(school_name="SAVED SCHOOL"),
                                   title_block_updates={"project_title": "INDEPENDENT DRAWING"})
    project.design.riser_document.title_block.address = "DRAWING ADDRESS"
    project.design.riser_document.elements["device:MSP"] = RiserElement(
        "device:MSP", "device", "MSP", 123.0, 456.0, 90.0, 110.0, manual=True)
    path = save_session(project, tmp_path / "existing.DMPS")
    if recovery_choice is not None:
        project.design.site_info.school_code = "RECOVERED"
        write_recovery(project)
        # Avoid platform-dependent %-d formatting while exercising the recovery branch.
        monkeypatch.setattr(app_module, "pending_recovery", lambda target:
                            SimpleNamespace(strftime=lambda pattern: "Sep 12, 12:00 PM"))
    else:
        monkeypatch.setattr(app_module.messagebox, "askyesno",
                            lambda *args: pytest.fail("prompted without recovery"))
    if recovery_choice is not None:
        monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args: recovery_choice)

    controller._start_input(path)

    chosen = "load_recovery" if recovery_choice else "load_session"
    boundaries[chosen].assert_called_once_with(path)
    loaded, tab = controller.entered[0]
    assert loaded.path == path and loaded.source_kind == "manual" and tab == "ZONES"
    assert loaded.design.riser_document == project.design.riser_document
    assert loaded.design.riser_document.title_block.address == "DRAWING ADDRESS"
    assert loaded.design.riser_document.elements["device:MSP"].x == 123.0
    assert loaded.design.site_info.school_code == ("RECOVERED" if recovery_choice else None)
    assert controller.pdf_path is controller.dmp_path is None and not controller.errors
    for name in set(boundaries) - {chosen}:
        boundaries[name].assert_not_called()
    assert session_module.recovery_path(path).exists() is bool(recovery_choice)


def test_dmps_load_error_does_not_enter_editor(controller, boundaries, tmp_path):
    # Swallowing schema/load failures or entering a replacement editor fails.
    source = tmp_path / "missing.dmps"

    controller._start_input(source)

    assert not controller.entered and len(controller.errors) == 1
    assert controller.errors[0][1]["title"] == "Couldn't open project"
    boundaries["load_session"].assert_called_once_with(source)
    for name in set(boundaries) - {"load_session"}:
        boundaries[name].assert_not_called()


def test_create_project_calls_no_import_or_recovery_boundary(controller, boundaries, monkeypatch, tmp_path):
    # Any hidden source-parser/loader call, missing initial save, or wrong landing tab fails.
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})
    monkeypatch.setattr(app_module, "ask_new_project", lambda *args, **kwargs:
                        (SiteInfo(school_name="MANUAL SCHOOL"), {"drawn_by": "TECH"}))

    controller._create_new_project()

    for spy in boundaries.values():
        spy.assert_not_called()
    project, tab = controller.entered[0]
    loaded = load_session(tmp_path / "Sessions" / "MANUAL_SCHOOL.dmps")
    assert tab == "RISER" and project == loaded
    assert (loaded.source_kind, loaded.source_name, loaded.design.topology_source) == ("manual", "", "manual")
    assert loaded.design.riser_document.title_block.drawn_by == "TECH"
    assert controller.pdf_path is controller.dmp_path is controller.door_chart_path is None


@pytest.mark.parametrize("suffix", [".pdf", ".xlsx", ".dmps"])
def test_input_close_guard_prevents_all_import_boundaries(controller, boundaries, tmp_path, suffix):
    # Bypassing an existing editor's close refusal must not tear down or import.
    controller.editor = SimpleNamespace(maybe_close=lambda: False)
    old = controller.session = create_blank_session(SiteInfo(school_name="KEEP ME"))
    controller._teardown_editor = lambda: pytest.fail("closed refused project")

    controller._start_input(tmp_path / ("input" + suffix))

    assert controller.session is old and not controller.entered and not controller.errors
    for spy in boundaries.values():
        spy.assert_not_called()
