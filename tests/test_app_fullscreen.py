"""Full-screen controls share native state and never edit the project."""

from types import SimpleNamespace

from test_app_scrollbar_runtime import application, app_module
from session import Session
from test_riser_scene import branched_design


def native_fullscreen_boundary(application, monkeypatch):
    # Substitute only the OS window transition, avoiding desktop Space changes
    # in automated tests while exercising the real menu and application shell.
    native = application.root.attributes
    state = {"value": False}

    def attributes(*args):
        if args and args[0] == "-fullscreen":
            if len(args) == 2:
                state["value"] = bool(args[1])
            return int(state["value"])
        return native(*args)

    monkeypatch.setattr(application.root, "attributes", attributes)
    return state


def test_view_menu_toggles_and_escape_exits_native_fullscreen(application, monkeypatch):
    state = native_fullscreen_boundary(application, monkeypatch)
    changes = []
    application.editor = SimpleNamespace(
        riser_tab=SimpleNamespace(set_fullscreen=changes.append))
    try:
        application._view_menu.invoke(0)
        assert state["value"] is True
        assert changes[-1] is True
        assert application._view_menu.entrycget(0, "label") == "Exit full screen"

        assert application._exit_fullscreen() == "break"
        assert state["value"] is False
        assert changes[-1] is False
        assert application._view_menu.entrycget(0, "label") == "Full screen"
        assert application._exit_fullscreen() is None
    finally:
        application.editor = None


def test_native_window_change_refreshes_fullscreen_controls(application, monkeypatch):
    state = native_fullscreen_boundary(application, monkeypatch)
    state["value"] = True  # macOS green window button / system command
    application._sync_fullscreen_controls()
    assert application._view_menu.entrycget(0, "label") == "Exit full screen"
    application._toggle_fullscreen()
    assert state["value"] is False


def test_riser_button_and_canvas_escape_share_app_state_without_edits(application, monkeypatch):
    state = native_fullscreen_boundary(application, monkeypatch)
    application._enter_editor(Session(branched_design(), saved_at="2026-09-11T00:00:00"))
    application.editor.tabs.set("RISER")
    application.root.update()
    tab = application.editor.riser_tab
    tab.selected = ("element", "RSP-1")
    epoch = application.editor.edit_epoch

    tab._fullscreen_button.invoke()
    assert state["value"] is True
    assert tab._fullscreen_button.cget("text") == "Exit full screen"
    assert tab._escape_view() == "break"
    assert state["value"] is False
    assert tab.selected == ("element", "RSP-1")
    assert application.editor.edit_epoch == epoch
