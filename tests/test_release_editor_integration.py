"""Behavioral coverage for the shared RISER/REMOTELINK editor surface."""

from __future__ import annotations

import copy
import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import app as app_module
from editor_frame import EditorFrame, TAB_TITLES, TabBar
from hardware import remove_keypad, renumber_splitter
from parse_dmp_worksheet import ZoneInfo
from riser_model import DevicePortRef, TopologyConnection
from rl_injector.rl_config import RLKeypad, RemoteLinkConfig
from session import Session, sync_master_zones
from test_riser_scene import branched_design
from tk_compat import install_scrollbar_redraw_fix
from topology_service import connect, set_splitter_input
from validation import Issue, badge_counts_by_severity, validate_design


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(widget, text):
    return next(child for child in descendants(widget)
                if isinstance(child, ctk.CTkButton) and child.cget("text") == text)


def close_root(root):
    root.destroy()


@pytest.fixture
def editor(tmp_path):
    install_scrollbar_redraw_fix()
    try:
        root = ctk.CTk()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    root.withdraw()
    design = branched_design()
    # This fixture models an existing saved version-1 drawing. New scenes are
    # covered separately by the cluster workflow tests.
    from riser_scene import layout_riser
    design.riser_document = layout_riser(design)
    design.zones = [ZoneInfo(501, "OFFICE", "Motion", 1, "EX")]
    sync_master_zones(design)
    session = Session(
        design=design, path=tmp_path / "release.dmps",
        saved_at="2026-08-27T12:00:00", topology_confirmed=True,
        remotelink=RemoteLinkConfig(
            account_num="4321", receiver_num="7",
            keypads={1: RLKeypad(name="MAIN"), 2: RLKeypad(name="OFFICE")}),
    )
    calls = []
    try:
        frame = EditorFrame(
            root, root, session,
            on_generate_worksheet=lambda: calls.append("worksheet"),
            on_generate_chart=lambda: calls.append("chart"),
            on_generate_remotelink=lambda: calls.append("remotelink"),
            on_generate_riser=lambda: calls.append("riser"),
        )
        frame.pack(fill="both", expand=True)
        root.update_idletasks()
        yield frame, calls
    finally:
        close_root(root)


def test_both_tabs_and_four_independent_generation_actions(editor):
    frame, calls = editor
    for title in ("SITE", "ZONES", "SPLITTERS", "KEYPADS", "RSP/POWER", "REMOTELINK", "RISER"):
        frame.tabs.set(title)
        assert frame.tabs.get() == title
    assert frame.remotelink_tab.session is frame.riser_tab.session is frame.session
    button(frame, "Generate Door Chart").invoke()
    button(frame, "RemoteLink Account").invoke()
    button(frame, "Generate Riser").invoke()
    # The worksheet action is a composite label, with the same bound click API.
    frame._gen_ws_btn._invoke()
    assert calls == ["chart", "remotelink", "riser", "worksheet"]


@pytest.mark.parametrize("which", ["worksheet", "chart", "remotelink", "riser"])
def test_generation_interlock_keeps_all_four_actions_distinct(editor, which):
    frame, calls = editor
    buttons = [frame._gen_ws_btn, frame._gen_chart_btn,
               frame._gen_rl_btn, frame._gen_riser_btn]
    frame.set_generating(which)
    frame._gen_chart_btn.invoke()
    frame._gen_rl_btn.invoke()
    frame._gen_riser_btn.invoke()
    frame._gen_ws_btn._invoke()
    assert calls == []
    frame.set_generating(None)
    assert [item.cget("text") for item in buttons[1:]] == [
        "Generate Door Chart", "RemoteLink Account", "Generate Riser"]
    frame._gen_rl_btn.invoke()
    frame._gen_riser_btn.invoke()
    assert calls == ["remotelink", "riser"]


def test_site_zone_and_keypad_programming_updates_live_receipt(editor):
    frame, _calls = editor
    frame._rl_comm_vars["port"].set("3001")
    assert frame.session.remotelink.comm.port == "3001"
    frame._site_vars["school_name"].set("RELEASE SCHOOL")
    assert "RELEASE SCHOOL" in frame.remotelink_tab.receipt.get("1.0", "end")

    frame.tabs.set("ZONES")
    # A withdrawn editor has no cell bounding box; supply the real input widget
    # at the same commit boundary used by double-click editing.
    frame.zones._edit_widget = ctk.CTkComboBox(frame.zones, values=["Supervisory"])
    frame.zones._edit_widget.set("Supervisory")
    frame.zones._edit_item, frame.zones._edit_col = "501", "rl_type"
    frame.zones._commit_edit()
    assert frame.session.design.zones[0].rl_type == "SV"
    assert frame.zones.tree.set("501", "rl_type") == "Supervisory"

    keypad = next(k for k in frame.session.design.keypads if k.number == 2)
    frame.keypads_tab._set_rl_field(keypad, "device_type", "zone_expander")
    frame.root.update_idletasks()
    programmed = frame.session.remotelink.keypads[2]
    assert (programmed.device_type, programmed.disp_areas) == ("zone_expander", "00")
    frame.keypads_tab._set_rl_field(keypad, "name", "LOBBY")
    assert "2 (LOBBY)" in frame.remotelink_tab.receipt.get("1.0", "end")
    assert frame.session.remotelink.arming.advanced.ambush_reports is False
    receipt = frame.remotelink_tab.receipt
    before_text = receipt.get("1.0", "end")
    receipt.insert("1.0", "UNSAVED EDIT")
    assert receipt.get("1.0", "end") == before_text
    assert frame.dirty


def test_generation_warning_sheet_combines_remotelink_and_topology_issues(editor):
    frame, _calls = editor
    frame._rl_comm_vars["port"].set("invalid")
    frame.session.design.connections.append(TopologyConnection(
        "import-conflict", DevicePortRef("710-LX500-1", "OUT3"), DevicePortRef("RSP-2", "IN")))
    proceeded = []

    frame.show_issues_dialog(lambda: proceeded.append(True), proceed_label="Generate anyway")

    labels = [child.cget("text") for child in descendants(frame._sheet["card"])
              if isinstance(child, ctk.CTkLabel)]
    assert "REMOTELINK" in labels
    assert any("incoming connections" in text for text in labels)
    assert proceeded == []
    button(frame._sheet["card"], "Generate anyway").invoke()
    assert proceeded == [True]


def test_issue_navigation_selects_riser_route_and_remotelink_field(editor):
    frame, _calls = editor
    route_id = next(iter(frame.session.design.riser_document.routes))
    frame.goto_issue(Issue("riser.test", "warning", "RISER", route_id, "Check cable"))
    assert frame.tabs.get() == "RISER"
    assert frame.riser_tab.selected == ("route", route_id)

    frame.goto_issue(Issue("remotelink.test", "warning", "REMOTELINK",
                           "field:account_num", "Check account"))
    assert frame.tabs.get() == "REMOTELINK"


def test_riser_disconnect_undo_redo_invalidates_review_and_preserves_programming(editor, monkeypatch):
    frame, _calls = editor
    design = frame.session.design
    original_config = copy.deepcopy(frame.session.remotelink)
    edge = next(edge for edge in design.connections if edge.target.device_id == "KEYPAD-2")
    edge_id = edge.id
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **kw: True)
    frame.riser_tab.selected = ("route", edge_id)

    frame.riser_tab.delete_selected()

    assert not frame.session.topology_confirmed
    assert not any(edge.id == edge_id for edge in design.connections)
    assert next(k for k in design.keypads if k.number == 2).source in (None, "")
    assert frame.session.remotelink == original_config
    assert design.zones[0].rl_type == "EX"

    frame.session.topology_confirmed = True
    frame.riser_tab.undo()
    assert not frame.session.topology_confirmed
    assert next(k for k in design.keypads if k.number == 2).source == "710-KP-1"
    assert any(edge.id == edge_id for edge in design.connections)
    frame.riser_tab.redo()
    assert not any(edge.id == edge_id for edge in design.connections)
    assert frame.session.remotelink == original_config
    assert design.zones[0].rl_type == "EX"


def test_riser_geometry_edit_preserves_wiring_review_and_programming(editor):
    frame, _calls = editor
    before = copy.deepcopy(frame.session.remotelink)
    frame.riser_tab.selected = ("element", "device:KEYPAD-2")
    frame.riser_tab.nudge(18, 0)
    frame.riser_tab.undo()
    assert frame.session.topology_confirmed
    assert frame.session.remotelink == before
    assert frame.session.design.zones[0].rl_type == "EX"


def test_hardware_removal_prunes_only_removed_keypad_programming(editor, monkeypatch):
    frame, _calls = editor
    design = frame.session.design
    before = copy.deepcopy(frame.session.remotelink)
    monkeypatch.setattr(frame, "_report_structure_change", lambda _changes: None)

    frame.apply_hardware_change(lambda: remove_keypad(design, 2))

    assert [keypad.number for keypad in design.keypads] == [1]
    assert not any(edge.target.device_id == "KEYPAD-2" for edge in design.connections)
    del before.keypads[2]
    assert frame.session.remotelink == before
    assert design.zones[0].rl_type == "EX"
    assert not frame.session.topology_confirmed


def test_splitter_renumber_updates_keypad_source_without_losing_overrides(editor, monkeypatch):
    frame, _calls = editor
    design = frame.session.design
    before = copy.deepcopy(frame.session.remotelink)
    monkeypatch.setattr(frame, "_report_structure_change", lambda _changes: None)

    frame.apply_hardware_change(lambda: renumber_splitter(design, "710-KP-1", 4))

    assert next(k for k in design.keypads if k.number == 2).source == "710-KP-4"
    assert "device:710-KP-4" in design.riser_document.elements
    assert frame.session.remotelink == before
    assert design.zones[0].rl_type == "EX"


def test_generation_preview_is_read_only_except_for_passphrase(editor):
    frame, _calls = editor
    app = object.__new__(app_module.App)
    app.root, app.session = frame.root, frame.session
    submitted = []
    app._run_generate_remotelink = submitted.append
    config = copy.deepcopy(frame.session.remotelink)

    app._show_remotelink_dialog()

    dialog = next(w for w in frame.root.winfo_children() if isinstance(w, ctk.CTkToplevel))
    entries = [w for w in descendants(dialog) if isinstance(w, ctk.CTkEntry)]
    assert [entry.get() for entry in entries] == ["4321", "7", ""]
    for entry in entries[:2]:
        before = entry.get()
        entry.insert(0, "9999")
        assert entry.get() == before
    receipt = next(w for w in descendants(dialog) if isinstance(w, ctk.CTkTextbox))
    before = receipt.get("1.0", "end")
    assert "Account   4321" in before
    receipt.insert("1.0", "UNSAVED EDIT")
    assert receipt.get("1.0", "end") == before
    entries[2].insert(0, "test-passphrase")
    button(dialog, "Generate").invoke()
    assert submitted == ["test-passphrase"]
    assert frame.session.remotelink == config


def test_help_inspector_opens_without_a_project(editor):
    frame, _calls = editor
    app = object.__new__(app_module.App)
    app.root, app.session = frame.root, None
    app._build_menubar()
    help_menu = app.root.nametowidget(app._menubar.entrycget("Help", "menu"))

    help_menu.invoke("Inspect RemoteLink Account…")

    dialog = next(w for w in frame.root.winfo_children() if isinstance(w, ctk.CTkToplevel))
    assert dialog.title() == "Inspect RemoteLink Account"
    assert app.root.grab_current() is None
    assert button(dialog, "Inspect").cget("state") == "normal"
    assert button(dialog, "Save Summary…").cget("state") == "disabled"
    button(dialog, "Close").invoke()


@pytest.mark.parametrize("port", ["input", "output"])
def test_compatibility_only_clear_notifies_editor_once(editor, port):
    frame, _calls = editor
    design = frame.session.design
    splitter = next(s for s in design.splitters if s.id == "710-LX500-1")
    if port == "input":
        set_splitter_input(design, splitter.id, None)
        splitter.inputs = {"LX-Bus In": "FROM EXISTING FIELD TAP"}
        clear = lambda: frame.splitters_tab._set_input(splitter, "")
    else:
        splitter.outputs[2] = "TO EXISTING FIELD TAP"
        clear = lambda: frame.splitters_tab._set_output(splitter, 2, "Spare")
    frame.refresh_all_tabs()
    frame.session.topology_confirmed = True
    frame.dirty = False
    before_graph = copy.deepcopy(design.connections)
    before_epoch = frame.edit_epoch

    clear()

    assert splitter.inputs == {} if port == "input" else splitter.outputs[2] == "Spare"
    assert design.connections == before_graph
    assert frame.dirty, "compatibility-only edits must be saved and recovered"
    assert frame.edit_epoch == before_epoch + 1
    assert not frame.session.topology_confirmed
    assert frame.splitters_tab._topo_after is not None

    # Repeating the same clear is a genuine no-op, not another edit.
    frame.dirty = False
    frame.session.topology_confirmed = True
    clear()
    assert frame.edit_epoch == before_epoch + 1
    assert not frame.dirty
    assert frame.session.topology_confirmed


@pytest.mark.parametrize("field,value", [
    ("name", "LOBBY"), ("device_type", "zone_expander"), ("disp_areas", "invalid"),
])
def test_keypad_programming_preserves_riser_undo(editor, field, value):
    frame, _calls = editor
    document = frame.session.design.riser_document
    element_id = "device:KEYPAD-2"
    original_xy = (document.elements[element_id].x, document.elements[element_id].y)
    frame.riser_tab.selected = ("element", element_id)
    frame.riser_tab.nudge(18, 0)
    assert document.elements[element_id].x == original_xy[0] + 18
    before_epoch = frame.edit_epoch
    frame.dirty = False
    keypad = next(k for k in frame.session.design.keypads if k.number == 2)

    frame.keypads_tab._set_rl_field(keypad, field, value)
    frame.root.update_idletasks()  # device-type edits defer rebuilding their card

    assert frame.dirty
    assert frame.edit_epoch == before_epoch + 1
    assert frame.session.topology_confirmed
    if field == "name":
        assert "2 (LOBBY)" in frame.remotelink_tab.receipt.get("1.0", "end")
    elif field == "disp_areas":
        labels = [w.cget("text") for w in descendants(frame._issue_chips)
                  if isinstance(w, ctk.CTkLabel)]
        assert any("REMOTELINK" in text for text in labels)
    else:
        assert frame.session.remotelink.keypads[2].disp_areas == "00"

    frame.riser_tab.undo()

    assert (document.elements[element_id].x, document.elements[element_id].y) == original_xy
    assert getattr(frame.session.remotelink.keypads[2], field) == value
    assert frame.session.design.zones[0].rl_type == "EX"


def test_keypad_source_edit_still_invalidates_riser_history(editor):
    frame, _calls = editor
    design = frame.session.design
    frame.riser_tab.selected = ("element", "device:KEYPAD-2")
    frame.riser_tab.nudge(18, 0)
    assert frame.riser_tab.controller.can_undo
    moved_x = design.riser_document.elements["device:KEYPAD-2"].x
    keypad = next(k for k in design.keypads if k.number == 2)

    frame.keypads_tab._set_source(keypad, "")

    assert not frame.riser_tab.controller.can_undo
    assert not frame.session.topology_confirmed
    frame.riser_tab.undo()
    assert not any(edge.target.device_id == "KEYPAD-2" for edge in design.connections)
    assert design.riser_document.elements["device:KEYPAD-2"].x == moved_x


@pytest.mark.parametrize("width,height", [(1000, 680), (860, 560)])
def test_footer_and_seven_tabs_fit_supported_window_sizes(width, height):
    # Exercise the real shell with validation badges, without constructing the
    # unrelated form editors (which can trigger nested option-menu redraws).
    install_scrollbar_redraw_fix()
    try:
        root = ctk.CTk()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    try:
        root.geometry(f"{width}x{height}")
        frame = EditorFrame.__new__(EditorFrame)
        ctk.CTkFrame.__init__(frame, root)
        frame.root = root
        frame.session = Session(design=branched_design())
        frame.session.remotelink.comm.port = "invalid"
        frame.dirty = True
        frame._sheet = None
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        for name in ("worksheet", "chart", "remotelink", "riser"):
            setattr(frame, f"_on_generate_{name}", lambda: None)
        frame.tabs = TabBar(frame)
        frame.tabs.grid(row=0, column=0, sticky="nsew")
        for title in TAB_TITLES:
            frame.tabs.add(title)
        frame._build_footer()
        issues = validate_design(frame.session.design, remotelink=frame.session.remotelink)
        frame._refresh_issue_chips(issues)
        frame.tabs.set_badges(badge_counts_by_severity(issues))
        frame.pack(fill="both", expand=True)
        root.update_idletasks()

        controls = {"Save": frame._save_btn, "Worksheet": frame._gen_ws_btn,
                    "Chart": frame._gen_chart_btn, "RemoteLink": frame._gen_rl_btn,
                    "Riser": frame._gen_riser_btn}
        controls.update({title: item["item"] for title, item in frame.tabs._items.items()})
        bounds = {name: (widget.winfo_rootx() - root.winfo_rootx(),
                         widget.winfo_rooty() - root.winfo_rooty(),
                         widget.winfo_width(), widget.winfo_height())
                  for name, widget in controls.items()}
        print(f"{width}x{height}: {bounds}")
        for name, (x, y, w, h) in bounds.items():
            assert controls[name].winfo_ismapped(), name
            assert 0 <= x and x + w <= width, (name, bounds[name])
            assert 0 <= y and y + h <= height, (name, bounds[name])
        frame.show_issues_dialog(lambda: None, proceed_label="Generate anyway")
        root.update_idletasks()
        sheet = frame._sheet["card"]
        assert sheet.winfo_rooty() + sheet.winfo_height() <= frame._footer_bar.winfo_rooty()
        root.geometry(f"1400x{height}")
        root.update()
        assert abs(frame._save_btn.winfo_rooty() - frame._gen_ws_btn.winfo_rooty()) < 8
        assert sheet.winfo_rooty() + sheet.winfo_height() <= frame._footer_bar.winfo_rooty()
        root.geometry(f"{width}x{height}")
        root.update()
        assert sheet.winfo_rooty() + sheet.winfo_height() <= frame._footer_bar.winfo_rooty()
    finally:
        root.destroy()
