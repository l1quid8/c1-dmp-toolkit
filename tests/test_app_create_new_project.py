"""Creation uses the normal editor and persists only after setup acceptance."""

import importlib
import sys
from datetime import date

import customtkinter as ctk
import pytest

from test_app_scrollbar_runtime import application, app_module
from test_app_recent_projects import labels
import session as session_module
from session import create_blank_session, load_recovery, load_session, recovery_path, save_session
from generate_dmp_ws import SiteInfo
import editor_frame


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
