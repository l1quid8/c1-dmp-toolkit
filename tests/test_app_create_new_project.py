"""Creation uses the normal editor and persists only after setup acceptance."""

import importlib
import sys
from datetime import date
from pathlib import Path
from types import MethodType, SimpleNamespace

import customtkinter as ctk
import pytest

from test_app_scrollbar_runtime import application, app_module
from test_app_recent_projects import labels
import session as session_module
from session import (create_blank_session, list_recent_sessions, load_recovery, load_session,
                     pending_recovery, recovery_path, save_session, write_recovery)
from generate_dmp_ws import SiteInfo
import editor_frame


@pytest.fixture
def background_editor(tmp_path):
    """Keep real save/status/persistence; replace only window/layout dependencies."""
    from test_session import _populated_design
    from riser_model import default_riser_document
    from rl_injector.rl_config import RemoteLinkConfig, RLUser
    project = create_blank_session(SiteInfo(school_name="ORIGINAL SCHOOL"))
    project.design = _populated_design()
    project.design.riser_document = default_riser_document(project.design)
    project.remotelink = RemoteLinkConfig(account_num="3141", users=[RLUser(7, "TECH", "7777", "2")])
    project.topology_confirmed = True
    save_session(project, tmp_path / "original.dmps")
    statuses, cancelled_jobs = [], []
    editor = SimpleNamespace(
        session=project, dirty=True, _recovery_job="pending-snapshot", _save_btn=None,
        root=SimpleNamespace(after_cancel=cancelled_jobs.append),
        flush_design_refresh=lambda: None,
    )
    for name in ("save", "_notify_status", "_update_save_button"):
        setattr(editor, name, MethodType(getattr(editor_frame.EditorFrame, name), editor))
    app = object.__new__(app_module.App)
    app.state, app.editor, app.session = "editing", editor, project
    app.root = editor.root
    header = {}
    app._set_project_title = lambda title, dirty=False, status="": header.update(
        title=title, dirty=dirty, status=status)
    app._set_toolbar_enabled = lambda enabled: None
    app.test_header = header

    def on_status_change(status, dirty):
        statuses.append((status, dirty))
        app._on_editor_status(status, dirty)

    editor.on_status_change = on_status_change
    return app, editor, statuses, cancelled_jobs


def test_background_create_reserves_old_save_and_recovery_and_lists_recents(monkeypatch,
                                                                           sessions_folder):
    sessions_folder.mkdir()
    original = create_blank_session(SiteInfo(school_name="NEW SCHOOL"))
    old = save_session(original, sessions_folder / "NEW_SCHOOL.dmps")
    old_text = old.read_text()
    orphan = sessions_folder / "NEW_SCHOOL (2).dmps.recovery"
    orphan.write_text("older never-saved project", encoding="utf-8")
    app = object.__new__(app_module.App)
    app._creating_project, app._generating, app.state = False, None, "idle"
    app.root, app.editor, app.session = None, None, None
    app.pdf_path = app.dmp_path = app.door_chart_path = old
    entered = []
    app._enter_editor = lambda project, *, initial_tab: entered.append((project, initial_tab))
    choose(monkeypatch, (SiteInfo(school_name="NEW SCHOOL"), {}))
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})

    app._create_new_project()

    assert len(entered) == 1
    project, tab = entered[0]
    assert tab == "RISER"
    assert project.path == sessions_folder / "NEW_SCHOOL (3).dmps"
    assert load_session(project.path).source_kind == "manual"
    assert project.saved_at is not None
    assert {r.path for r in list_recent_sessions()} == {old, project.path}
    assert old.read_text() == old_text
    assert orphan.read_text() == "older never-saved project"
    assert app.pdf_path is app.dmp_path is app.door_chart_path is None


@pytest.fixture
def background_close_host(background_editor, monkeypatch):
    """Exercise the real close guard and deferred recovery without a Tk root."""
    app, editor, *_ = background_editor
    app._creating_project, app._generating = False, None
    editor.edit_epoch = 9
    jobs = []

    def after(delay, callback):
        jobs.append((delay, callback))
        return f"recovery-{len(jobs)}"

    editor.root.after = after
    for name in ("maybe_close", "_schedule_recovery", "_write_recovery"):
        setattr(editor, name, MethodType(getattr(editor_frame.EditorFrame, name), editor))
    app.pdf_path, app.dmp_path, app.door_chart_path = (
        editor.session.path.with_suffix(suffix) for suffix in (".pdf", ".xlsx", ".chart.xlsx"))
    choose(monkeypatch, (SiteInfo(school_name="NEW"), {}))
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(app_module.messagebox, "showerror",
                        lambda *a, **kw: pytest.fail(f"Unexpected creation error: {a}"))

    def forbidden(*a, **kw):
        pytest.fail("Aborted creation reached blank creation, initial save, or editor replacement")

    monkeypatch.setattr(app_module, "create_blank_session", forbidden)
    monkeypatch.setattr(app_module, "save_session", forbidden)
    app._enter_editor = forbidden
    return app, editor, jobs


@pytest.mark.parametrize("busy", ["generation", "parsing", "loading_xlsx"])
def test_background_work_started_during_dirty_close_restores_retained_recovery(
        background_close_host, monkeypatch, sessions_folder, busy):
    # Missing the post-close busy guard must start creating a new session here.
    app, editor, jobs = background_close_host
    old, epoch = app.session, editor.edit_epoch
    old.design.site_info.school_code = "UNSAVED"
    rec = write_recovery(old)
    before = old.path.read_text()
    paths = (app.pdf_path, app.dmp_path, app.door_chart_path)

    def discard_after_work_starts(*a, **kw):
        app._generating = "riser" if busy == "generation" else None
        app.state = "editing" if busy == "generation" else busy
        return False

    monkeypatch.setattr(editor_frame.messagebox, "askyesnocancel", discard_after_work_starts)

    app._create_new_project()

    assert app.session is old and app.editor is editor and editor.dirty
    assert editor.edit_epoch == epoch and old.path.read_text() == before
    assert (app.pdf_path, app.dmp_path, app.door_chart_path) == paths
    assert not app._creating_project and not list(sessions_folder.glob("*.dmps"))
    assert not rec.exists(), "The real Discard must clear recovery before restoration"
    assert len(jobs) == 1 and jobs[0][0] == 0
    jobs[0][1]()
    assert rec.exists() and editor._recovery_job is None
    assert load_recovery(old.path).design.site_info.school_code == "UNSAVED"
    assert editor.edit_epoch == epoch


@pytest.mark.parametrize("change", ["editor", "session", "editor_session", "both", "closed"])
def test_background_changed_close_target_aborts_without_touching_replacement(
        background_close_host, monkeypatch, tmp_path, sessions_folder, change):
    # Busy checks alone miss a project change that finishes during the modal wait.
    app, editor, jobs = background_close_host
    old, epoch = app.session, editor.edit_epoch
    old.design.site_info.school_code = "OLD UNSAVED"
    old_rec = write_recovery(old)
    replacement = create_blank_session(SiteInfo(school_name="REPLACEMENT"))
    save_session(replacement, tmp_path / "replacement.dmps")
    replacement.design.site_info.school_code = "REPLACEMENT UNSAVED"
    replacement_rec = write_recovery(replacement)
    replacement_text = replacement.path.read_text()
    replacement_recovery_text = replacement_rec.read_text()
    replacement_editor = SimpleNamespace(session=replacement, dirty=True,
        _schedule_recovery=lambda **kw: pytest.fail("Replacement recovery was rescheduled"))
    paths = (tmp_path / "replacement.pdf", tmp_path / "replacement.xlsx", tmp_path / "chart.xlsx")

    def discard_after_target_changes(*a, **kw):
        if change in {"editor", "both"}:
            app.editor = replacement_editor
        if change in {"session", "both"}:
            app.session = replacement
        if change == "closed":
            app.editor, app.session, app.state = None, None, "idle"
        app.pdf_path, app.dmp_path, app.door_chart_path = paths
        return False

    if change == "editor_session":
        real_close = editor.maybe_close
        monkeypatch.setattr(editor_frame.messagebox, "askyesnocancel", lambda *a, **kw: False)

        def close_then_reuse_editor():
            assert real_close() and not old_rec.exists()
            # Reuse between callbacks; the real Discard still clears only the old recovery.
            editor.session = replacement
            app.pdf_path, app.dmp_path, app.door_chart_path = paths
            return True

        editor.maybe_close = close_then_reuse_editor
    else:
        monkeypatch.setattr(editor_frame.messagebox, "askyesnocancel", discard_after_target_changes)

    app._create_new_project()

    assert app.state == ("idle" if change == "closed" else "editing")
    assert app.session is (replacement if change in {"session", "both"} else
                           None if change == "closed" else old)
    assert app.editor is (replacement_editor if change in {"editor", "both"} else
                          None if change == "closed" else editor)
    assert (app.pdf_path, app.dmp_path, app.door_chart_path) == paths
    assert replacement.path.read_text() == replacement_text
    assert replacement_rec.read_text() == replacement_recovery_text
    assert editor.edit_epoch == epoch and not app._creating_project
    assert not old_rec.exists() and not list(sessions_folder.glob("*.dmps"))
    if change == "session":
        # This distinct app-session change retained the guarded editor and its old session.
        assert len(jobs) == 1 and jobs[0][0] == 0
        jobs[0][1]()
        assert load_recovery(old.path).design.site_info.school_code == "OLD UNSAVED"
        assert replacement_rec.read_text() == replacement_recovery_text
    else:
        assert jobs == [], "Destroyed/replaced editors or sessions must not be rescheduled"


@pytest.mark.parametrize("outcome", ["discard", "save", "cancel", "initial_write_failure"])
def test_background_close_guard_keeps_existing_creation_and_failure_behavior(
        background_close_host, monkeypatch, sessions_folder, outcome):
    app, editor, jobs = background_close_host
    old, epoch = app.session, editor.edit_epoch
    old.design.site_info.school_code = "UNSAVED"
    rec = write_recovery(old)
    old_text = old.path.read_text()
    paths = (app.pdf_path, app.dmp_path, app.door_chart_path)
    answer = True if outcome == "save" else None if outcome == "cancel" else False
    monkeypatch.setattr(editor_frame.messagebox, "askyesnocancel", lambda *a, **kw: answer)
    monkeypatch.setattr(app_module, "create_blank_session", create_blank_session)
    monkeypatch.setattr(app_module, "save_session", save_session)
    entered, errors = [], []
    app._enter_editor = lambda project, *, initial_tab: entered.append((project, initial_tab))
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **kw: errors.append(a))
    if outcome == "initial_write_failure":
        atomic_write = session_module._atomic_write

        def fail_initial_write(target, payload):
            if target.name == "NEW.dmps":
                assert not rec.exists(), "Discard must clear the real old recovery first"
                raise OSError("initial save unavailable")
            return atomic_write(target, payload)

        monkeypatch.setattr(session_module, "_atomic_write", fail_initial_write)

    app._create_new_project()

    assert editor.edit_epoch == epoch and not app._creating_project
    if outcome in {"discard", "save"}:
        assert len(entered) == 1 and entered[0][1] == "RISER"
        assert load_session(entered[0][0].path).design.site_info.school_name == "NEW"
        assert app.pdf_path is app.dmp_path is app.door_chart_path is None
        assert not rec.exists() and jobs == [] and not errors
        if outcome == "save":
            assert load_session(old.path).design.site_info.school_code == "UNSAVED"
        else:
            assert old.path.read_text() == old_text
        assert editor.dirty is (outcome == "discard")
    else:
        assert not entered and not list(sessions_folder.glob("*.dmps"))
        assert app.session is old and app.editor is editor and editor.dirty
        assert (app.pdf_path, app.dmp_path, app.door_chart_path) == paths
        assert old.path.read_text() == old_text
        assert bool(errors) is (outcome == "initial_write_failure")
        if outcome == "initial_write_failure":
            assert not rec.exists() and len(jobs) == 1 and jobs[0][0] == 0
            jobs[0][1]()
        else:
            assert jobs == []
        assert load_recovery(old.path).design.site_info.school_code == "UNSAVED"


def test_background_ordinary_save_keeps_current_path_and_clears_recovery(background_editor):
    app, editor, statuses, cancelled_jobs = background_editor
    old = app.session.path
    app.session.design.site_info.school_code = "EDITED"
    rec = write_recovery(app.session)

    app._save_shortcut()

    assert app.session.path == old
    assert load_session(old).design.site_info.school_code == "EDITED"
    assert not rec.exists() and not editor.dirty
    assert editor._recovery_job is None and cancelled_jobs == ["pending-snapshot"]
    assert statuses[-1][1] is False


def test_background_target_save_preserves_complete_project_and_old_file(background_editor, tmp_path,
                                                                       sessions_folder):
    app, editor, statuses, cancelled_jobs = background_editor
    project, old = app.session, app.session.path
    old_text = old.read_text()
    old_recovery = write_recovery(project)
    project.design.site_info.school_code = "EDITED"
    project.design.riser_document.title_block.drawn_by = "New Designer"
    target = tmp_path / "elsewhere" / "renamed.dmps"

    assert editor.save(target)

    loaded = load_session(target)
    assert project.path == target and loaded.path == target
    assert loaded.design == project.design
    assert loaded.remotelink == project.remotelink
    assert loaded.source_kind == "manual" and loaded.source_name == ""
    assert loaded.topology_confirmed is True
    assert loaded.saved_at == project.saved_at
    assert loaded.design.site_info.school_code == "EDITED"
    assert loaded.design.riser_document.title_block.drawn_by == "New Designer"
    assert old.read_text() == old_text and not old_recovery.exists()
    assert not editor.dirty and statuses[-1][1] is False
    assert app.test_header["dirty"] is False and app.test_header["status"].startswith("Saved")
    assert target not in {r.path for r in list_recent_sessions()}
    assert cancelled_jobs == ["pending-snapshot"]
    project.design.site_info.school_code = "LATER UNSAVED"
    editor.dirty = True
    rec = write_recovery(project)
    assert rec == target.parent / "renamed.dmps.recovery"
    assert pending_recovery(target) is not None
    recovered = load_recovery(target)
    assert recovered.path == target
    assert recovered.design.site_info.school_code == "LATER UNSAVED"
    assert editor.save()
    assert load_session(target).design.site_info.school_code == "LATER UNSAVED"
    assert not rec.exists() and old.read_text() == old_text


@pytest.mark.parametrize("dirty", [True, False])
def test_background_failed_target_save_retains_save_state(background_editor, tmp_path, monkeypatch, dirty):
    app, editor, statuses, cancelled_jobs = background_editor
    editor.dirty = dirty
    project, old = app.session, app.session.path
    timestamp, old_text = project.saved_at, old.read_text()
    rec = write_recovery(project)
    target = tmp_path / "failed.dmps"
    (tmp_path / "failed.dmps.tmp").mkdir()
    errors = []
    monkeypatch.setattr(editor_frame.messagebox, "showerror", lambda *a, **kw: errors.append(a))

    assert not editor.save(target)

    assert project.path == old and project.saved_at == timestamp and editor.dirty is dirty
    assert old.read_text() == old_text and rec.exists() and not target.exists()
    assert editor._recovery_job == "pending-snapshot"
    assert not cancelled_jobs and not statuses and errors


@pytest.mark.parametrize("dirty", [True, False])
def test_background_save_as_cancel_preserves_project(background_editor, monkeypatch, sessions_folder, dirty):
    app, editor, statuses, cancelled_jobs = background_editor
    editor.dirty = dirty
    old, timestamp = app.session.path, app.session.saved_at
    before = old.read_text()
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: "")

    assert not app._save_as()

    assert app.session.path == old and app.session.saved_at == timestamp
    assert editor.dirty is dirty and old.read_text() == before
    assert not statuses and not cancelled_jobs
    assert not list(sessions_folder.glob("*.dmps"))


def test_background_save_as_dialog_targets_sessions_and_uses_shared_save(background_editor, monkeypatch,
                                                                        sessions_folder):
    app, editor, statuses, cancelled_jobs = background_editor
    old = app.session.path
    target = sessions_folder / "copy.dmps"
    options = []

    def choose_target(**kwargs):
        options.append(kwargs)
        return str(target)

    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", choose_target)

    assert app._save_as()

    assert Path(options[0]["initialdir"]) == sessions_folder
    assert options[0]["defaultextension"] == ".dmps"
    assert options[0]["filetypes"][0][1] == "*.dmps"
    assert "Recent" in options[0]["title"] and "Sessions" in options[0]["title"]
    assert app.session.path == target and old.exists() and target.exists()
    assert not editor.dirty and statuses[-1][1] is False


def test_background_save_as_write_failure_is_reported_without_switching(background_editor, monkeypatch,
                                                                      tmp_path, sessions_folder):
    app, editor, statuses, cancelled_jobs = background_editor
    old, timestamp = app.session.path, app.session.saved_at
    rec = write_recovery(app.session)
    target = tmp_path / "blocked.dmps"
    (tmp_path / "blocked.dmps.tmp").mkdir()
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: str(target))
    errors = []
    monkeypatch.setattr(editor_frame.messagebox, "showerror", lambda *a, **kw: errors.append(a))

    assert not app._save_as()

    assert app.session.path == old and app.session.saved_at == timestamp
    assert editor.dirty and rec.exists() and not target.exists()
    assert errors and not statuses and not cancelled_jobs


@pytest.mark.parametrize("state", ["idle", "parsing", "loading_xlsx"])
def test_background_save_as_without_active_editor_never_opens_dialog(background_editor, monkeypatch, state):
    app, *_ = background_editor
    app.state = state
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename",
                        lambda **kw: pytest.fail("Save As opened without an active editor"))
    assert not app._save_as()


def test_background_rename_changes_output_slug_not_save_filename(background_editor, tmp_path):
    app, editor, *_ = background_editor
    old = app.session.path
    app.session.design.site_info.school_name = "RENAMED SCHOOL"

    assert app._school_slug() == "RENAMED_SCHOOL"
    assert editor.save()
    assert app.session.path == old
    assert load_session(old).design.site_info.school_name == "RENAMED SCHOOL"
    target = tmp_path / "RENAMED_SCHOOL.dmps"
    assert editor.save(target)
    assert app.session.path == target and old.exists()


def test_save_as_menu_enabled_only_with_editor(application):
    application._refresh_file_menu()
    assert application._file_menu.entrycget("Save As…", "state") == "disabled"
    project = create_blank_session(SiteInfo(school_name="TEST"))
    application._enter_editor(project)
    application._refresh_file_menu()
    assert application._file_menu.entrycget("Save As…", "state") == "normal"


@pytest.fixture
def sessions_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(session_module, "output_dir", lambda: tmp_path)
    return tmp_path / "Sessions"


def choose(monkeypatch, result):
    monkeypatch.setattr(app_module, "ask_new_project", lambda *a, **kw: result,
                        raising=False)


def existing_project(application, tmp_path):
    project = create_blank_session(SiteInfo(school_name="OLD SCHOOL"))
    save_session(project, tmp_path / "old.dmps")
    application._enter_editor(project)
    application.pdf_path = tmp_path / "old.pdf"
    application.dmp_path = tmp_path / "old.xlsx"
    application.door_chart_path = tmp_path / "old-chart.xlsx"
    return project, application.editor


def dialog_module():
    assert importlib.util.find_spec("new_project_dialog"), "Missing creation setup dialog"
    return importlib.import_module("new_project_dialog")


def test_home_create_action_keeps_file_drop_and_browse(application):
    buttons = [w for w in descendants(application.input_section)
               if isinstance(w, ctk.CTkButton) and w.cget("text") == "Create New Project"]
    assert len(buttons) == 1
    assert buttons[0].winfo_ismapped()
    assert buttons[0].winfo_rooty() < application._drop_zone.winfo_rooty()
    assert {"PDF", "XLSX", "DMPS", "browse…"} <= set(labels(application.input_section))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_menu_and_shortcut_create_but_close_still_resets(application, monkeypatch, tmp_path):
    # An unaccepted setup must preserve the project: the old New/Close routing fails this.
    old, editor = existing_project(application, tmp_path)
    choose(monkeypatch, None)
    assert application._file_menu.entrycget(0, "label") == "Create New Project"
    application._file_menu.invoke(0)
    assert application.session is old and application.editor is editor
    mod = "Command" if sys.platform == "darwin" else "Control"
    binding = application.root.bind_all(f"<{mod}-n>")
    application.root.tk.eval(binding.replace("%#", "0").replace("%b", "0")
                             .replace("%f", "0").replace("%h", "0").replace("%k", "0")
                             .replace("%s", "0").replace("%t", "0").replace("%w", "0")
                             .replace("%x", "0").replace("%y", "0").replace("%A", "n")
                             .replace("%E", "0").replace("%K", "n").replace("%N", "0")
                             .replace("%W", ".").replace("%T", "2").replace("%X", "0")
                             .replace("%Y", "0").replace("%D", "0"))
    assert application.session is old and application.editor is editor
    application._file_menu.invoke("Close Project")
    assert application.session is None and application.editor is None
    assert application.state == "idle"


def test_create_saves_collision_safe_project_and_opens_existing_riser(application, monkeypatch,
                                                                    sessions_folder):
    monkeypatch.setattr(editor_frame, "date", type("Today", (), {
        "today": staticmethod(lambda: date(2026, 9, 12))}))
    choose(monkeypatch, (SiteInfo(school_name="NEW SCHOOL"), {"drawn_by": "Designer"}))
    monkeypatch.setattr(app_module, "load_prefs", lambda: {
        "install_tech": "Tech", "ip_address": "192.0.2.5", "default_gateway": "192.0.2.1"})
    sessions_folder.mkdir()
    occupied = sessions_folder / "NEW_SCHOOL.dmps"
    occupied.write_text("keep existing", encoding="utf-8")
    application.pdf_path = application.dmp_path = application.door_chart_path = occupied
    application._create_new_project()
    assert occupied.read_text() == "keep existing"
    assert application.session.path == sessions_folder / "NEW_SCHOOL (2).dmps"
    saved = load_session(application.session.path)
    assert saved.source_kind == "manual" and saved.source_name == ""
    assert saved.design.site_info.install_tech == "Tech"
    assert saved.design.site_info.ip_address == "192.0.2.5"
    assert saved.design.site_info.default_gateway == "192.0.2.1"
    assert saved.design.site_info.install_date == "SEPTEMBER 12th 2026"
    assert saved.design.riser_document.title_block.drawn_by == "Designer"
    assert application.pdf_path is application.dmp_path is application.door_chart_path is None
    application.root.update()
    assert application.editor.tabs.get() == "RISER"
    tab = application.editor.riser_tab
    assert "device:MSP" in tab.controller.document.elements
    assert tab.canvas.find_withtag("titleblock")
    assert "Add Device" in labels(tab)
    assert not application.editor.dirty
    application._open_session_path(application.session.path)
    assert application.editor.tabs.get() == "ZONES"


@pytest.mark.parametrize("outcome", ["cancel", "dialog_error", "save_error", "dirty_cancel"])
def test_aborted_creation_preserves_editor_and_runtime_paths(application, monkeypatch,
                                                           tmp_path, sessions_folder, outcome):
    old, editor = existing_project(application, tmp_path)
    paths = (application.pdf_path, application.dmp_path, application.door_chart_path)
    errors = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **kw: errors.append(a))
    choose(monkeypatch, None if outcome == "cancel" else (SiteInfo(school_name="NEW"), {}))
    if outcome == "dialog_error":
        def fail_dialog(*a, **kw):
            raise RuntimeError("dialog unavailable")
        monkeypatch.setattr(app_module, "ask_new_project", fail_dialog)
    if outcome == "save_error":
        def fail_write(*a, **kw):
            raise OSError("disk full")
        monkeypatch.setattr(session_module, "_atomic_write", fail_write)
    if outcome == "dirty_cancel":
        editor.mark_dirty(write_recovery_now=False)
        monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **kw: None)
    application._create_new_project()
    assert application.session is old and application.editor is editor
    assert editor.winfo_exists() and application.state == "editing"
    assert (application.pdf_path, application.dmp_path, application.door_chart_path) == paths
    assert not list(sessions_folder.glob("*.dmps"))
    assert bool(errors) == (outcome in {"dialog_error", "save_error"})
    # The scoped guard must be released on every exit.
    choose(monkeypatch, None)
    seen = []
    monkeypatch.setattr(app_module, "ask_new_project", lambda *a, **kw: seen.append(True))
    application._create_new_project()
    assert seen == [True]


def test_shared_site_defaults_exclude_stale_phone_and_install_date(monkeypatch):
    monkeypatch.setattr(editor_frame, "date", type("Today", (), {
        "today": staticmethod(lambda: date(2026, 9, 12))}))
    # Both new-entry paths must get today's date rather than a stale preference.
    assert hasattr(editor_frame, "_site_defaults"), "Missing shared per-machine defaults helper"
    assert editor_frame._site_defaults({
        "install_tech": "Tech", "ip_address": "192.0.2.5", "default_gateway": "192.0.2.1",
        "install_date": "OLD DATE", "phone": "555-0100",
    }) == {
        "install_tech": "Tech", "ip_address": "192.0.2.5", "default_gateway": "192.0.2.1",
        "install_date": "SEPTEMBER 12th 2026",
    }


def test_failed_initial_save_restores_discarded_old_recovery(application, monkeypatch,
                                                          tmp_path, sessions_folder):
    old, editor = existing_project(application, tmp_path)
    old.design.site_info.school_code = "UNSAVED"
    editor.mark_dirty(write_recovery_now=True)
    application.root.update()
    assert recovery_path(old.path).exists()
    epoch = editor.edit_epoch
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **kw: False)
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **kw: None)
    choose(monkeypatch, (SiteInfo(school_name="NEW"), {}))
    atomic_write = session_module._atomic_write
    def fail_new_project(target, payload):
        if target.name == "NEW.dmps":
            # The real discard guard has removed the recovery before this failure.
            assert not recovery_path(old.path).exists()
            raise OSError("initial save unavailable")
        return atomic_write(target, payload)
    monkeypatch.setattr(session_module, "_atomic_write", fail_new_project)
    application._create_new_project()
    application.root.update()
    assert recovery_path(old.path).exists()
    assert load_recovery(old.path).design.site_info.school_code == "UNSAVED"
    assert application.session is old and application.editor is editor and editor.dirty
    assert editor.edit_epoch == epoch
    assert not list(sessions_folder.glob("*.dmps"))


@pytest.mark.parametrize("save_old", [True, False])
def test_dirty_close_choice_and_initial_save_order(application, monkeypatch, tmp_path,
                                                 sessions_folder, save_old):
    old, editor = existing_project(application, tmp_path)
    old.design.site_info.school_code = "EDITED"
    editor.mark_dirty(write_recovery_now=False)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **kw: save_old)
    choose(monkeypatch, (SiteInfo(school_name="NEW", install_tech="Existing Tech",
                                 install_date="Existing Date", ip_address="198.51.100.5",
                                 default_gateway="198.51.100.1"), {}))
    monkeypatch.setattr(app_module, "load_prefs", lambda: {
        "install_tech": "Wrong Tech", "install_date": "STALE", "phone": "STALE",
        "ip_address": "192.0.2.5", "default_gateway": "192.0.2.1"})
    atomic_write = session_module._atomic_write
    def checked_write(target, payload):
        # Saving either record must not destroy the editor prematurely.
        assert application.editor is editor and application.session is old
        assert editor.winfo_exists()
        return atomic_write(target, payload)
    monkeypatch.setattr(session_module, "_atomic_write", checked_write)
    application._create_new_project()
    assert application.session is not old and application.editor is not editor
    assert not editor.winfo_exists()
    assert load_session(old.path).design.site_info.school_code == ("EDITED" if save_old else None)
    site = load_session(application.session.path).design.site_info
    assert (site.install_tech, site.install_date, site.ip_address, site.default_gateway) == (
        "Existing Tech", "Existing Date", "198.51.100.5", "198.51.100.1")
    assert site.phone is None


@pytest.mark.parametrize("busy", ["generation", "parsing", "loading_xlsx"])
def test_busy_work_refuses_creation_before_dialog(application, monkeypatch, sessions_folder, busy):
    application._generating = "riser" if busy == "generation" else None
    application.state = "editing" if busy == "generation" else busy
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(app_module, "ask_new_project",
                        lambda *a, **kw: pytest.fail("setup opened while busy"), raising=False)
    application._create_new_project()
    assert not list(sessions_folder.glob("*.dmps"))


def test_modal_reentry_does_not_stack_dialogs(application, monkeypatch, sessions_folder):
    calls = []
    def setup(*a, **kw):
        calls.append(True)
        application._create_new_project()
        return None
    monkeypatch.setattr(app_module, "ask_new_project", setup, raising=False)
    application._create_new_project()
    application._create_new_project()
    assert calls == [True, True]
    assert not list(sessions_folder.glob("*.dmps"))


@pytest.mark.parametrize("busy", ["generation", "parsing", "loading_xlsx"])
def test_work_started_during_modal_setup_refuses_commit(application, monkeypatch,
                                                       sessions_folder, busy):
    def setup(*a, **kw):
        application._generating = "riser" if busy == "generation" else None
        application.state = "idle" if busy == "generation" else busy
        return SiteInfo(school_name="NEW"), {}
    monkeypatch.setattr(app_module, "ask_new_project", setup)
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **kw: None)
    application._create_new_project()
    assert not list(sessions_folder.glob("*.dmps"))
    assert application.session is None


def test_dialog_content_and_footer_fit_minimum_size(application):
    dialog = dialog_module().NewProjectDialog(application.root)
    try:
        dialog.window.geometry("420x360")
        application.root.update()
        for widget in descendants(dialog.window):
            if isinstance(widget, (ctk.CTkLabel, ctk.CTkButton)) and widget.winfo_ismapped():
                left = widget.winfo_rootx() - dialog.window.winfo_rootx()
                assert left >= 0
                assert left + widget.winfo_width() <= dialog.window.winfo_width()
        buttons = [w for w in descendants(dialog.window) if isinstance(w, ctk.CTkButton)]
        for button in buttons:
            bottom = button.winfo_rooty() - dialog.window.winfo_rooty() + button.winfo_height()
            assert bottom <= dialog.window.winfo_height()
    finally:
        dialog.window.destroy()


def test_modal_cancel_returns_none_without_persistence(application, sessions_folder):
    dialog = dialog_module().NewProjectDialog(application.root)
    application.root.after(10, dialog._cancel)
    assert dialog.show() is None
    assert not list(sessions_folder.glob("*.dmps"))


def test_dialog_requires_only_name_and_focuses_error(application):
    dialog = dialog_module().NewProjectDialog(application.root)
    try:
        dialog._create()
        application.root.update()
        assert dialog.window.winfo_exists() and dialog.result is None
        assert dialog.error.cget("text")
        assert application.root.focus_get() is dialog.entries["school_name"]._entry
        for name, var in dialog.values.items():
            var.set("  Test School  " if name == "school_name" else "")
        dialog._create()
        site, title = dialog.result
        assert site.school_name == "Test School"
        assert site.school_code == site.address_line1 == site.address_line2 == ""
        assert title["school_name"] == title["project_title"] == "Test School"
    finally:
        if dialog.window.winfo_exists():
            dialog.window.destroy()


def test_dialog_maps_metadata_and_default_values(application):
    dialog = dialog_module().NewProjectDialog(application.root, prepared_by="Install Tech")
    assert dialog.window.title() == "Create New Project"
    assert "Create New Project" in labels(dialog.window)
    assert dialog.values["sheet_number"].get() == "INT-5.0"
    assert dialog.values["drawn_by"].get() == "Install Tech"
    assert dialog.values["issue_date"].get() == date.today().isoformat()
    assert dialog.values["xr550_location"].get() == "UNSPECIFIED"
    data = dict(school_name="School", school_code="LC", address_line1="123 Main",
                address_line2="City, ST 12345", xr550_location="Office", sheet_number="S-9",
                drawn_by="Engineer", issue_date="2026-10-01")
    for name, value in data.items():
        dialog.values[name].set(value)
    dialog._create()
    site, title = dialog.result
    assert (site.school_name, site.school_code, site.address_line1,
            site.address_line2, site.xr550_location) == ("School", "LC", "123 Main",
                                                       "City, ST 12345", "Office")
    assert title == dict(school_name="School", project_title="School", local_code="LC",
                         address="123 Main\nCity, ST 12345", sheet_number="S-9",
                         drawn_by="Engineer", issue_date="2026-10-01")


@pytest.mark.parametrize("key", ["Return", "Escape"])
def test_dialog_keyboard_accept_or_cancel_writes_nothing(application, sessions_folder, key):
    dialog = dialog_module().NewProjectDialog(application.root)
    dialog.values["school_name"].set("Keyboard School")
    dialog.window.focus_force()
    application.root.update()
    dialog.window.event_generate(f"<{key}>")
    application.root.update()
    assert not dialog.window.winfo_exists()
    assert (dialog.result is not None) == (key == "Return")
    assert not list(sessions_folder.glob("*.dmps"))
