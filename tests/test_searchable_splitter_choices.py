"""Splitter wiring choices stay complete, searchable, and bus-correct."""

from pathlib import Path
import sys
import tkinter as tk
from types import SimpleNamespace

import customtkinter as ctk
import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import editor_tabs  # noqa: E402
import ui_widgets  # noqa: E402
from parse_dmp_worksheet import DMPDesign, Keypad, RSP, Splitter  # noqa: E402
from riser_model import DevicePortRef, TopologyConnection  # noqa: E402
from session import Session  # noqa: E402
from tk_compat import install_scrollbar_redraw_fix  # noqa: E402


@pytest.fixture
def root():
    install_scrollbar_redraw_fix()
    try:
        window = ctk.CTk()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    window.withdraw()
    yield window
    window.destroy()


def _bare_tab(design):
    tab = object.__new__(editor_tabs.SplittersTab)
    tab.session = Session(design=design)
    return tab


def test_output_choices_include_devices_discovered_only_in_riser_connections():
    lx1 = Splitter("710-LX500-1", "LX", outputs=["RSP-1", "Spare", "Spare"])
    lx2 = Splitter("710-LX500-2", "LX", outputs=["RSP-4", "Spare", "Spare"])
    kp1 = Splitter("710-KP-1", "KP", outputs=["KEYPAD #2", "Spare", "Spare"])
    design = DMPDesign(
        splitters=[lx1, lx2, kp1],
        keypads=[Keypad(1)],
        connections=[
            TopologyConnection(
                "lx-rsp-1", DevicePortRef(lx1.id, "OUT1"),
                DevicePortRef("RSP-1", "IN")),
            TopologyConnection(
                "lx-rsp-4", DevicePortRef(lx2.id, "OUT1"),
                DevicePortRef("RSP-4", "IN")),
            TopologyConnection(
                "kp-keypad-2", DevicePortRef(kp1.id, "OUT1"),
                DevicePortRef("KEYPAD-2", "IN")),
        ],
    )
    tab = _bare_tab(design)

    assert tab._output_choices(lx1) == [
        "Spare", "RSP-1", "RSP-4", "To 710-LX500-2",
    ]
    assert tab._output_choices(kp1) == [
        "Spare", "KEYPAD #1", "KEYPAD #2",
    ]


def test_searchable_combo_filters_and_commits_a_valid_destination(root):
    combo_class = getattr(ui_widgets, "SearchableComboBox", None)
    assert combo_class is not None, "SearchableComboBox is not implemented"
    selected = []
    combo = combo_class(
        root,
        values=["Spare", "RSP-2", "RSP-12", "To 710-LX500-2"],
        command=selected.append,
    )
    combo.pack()
    combo.set("Spare")
    combo._entry.delete(0, "end")
    combo._entry.insert(0, "rsp")

    combo._on_key_release(SimpleNamespace(keysym="p"))
    root.update()

    assert combo._popup.get(0, "end") == ("RSP-2", "RSP-12")
    combo._popup.selection_set(0)
    combo._accept()
    assert combo.get() == "RSP-2"
    assert selected == ["RSP-2"]


def test_searchable_output_reverts_text_outside_its_choices(root):
    combo_class = getattr(ui_widgets, "SearchableComboBox", None)
    assert combo_class is not None, "SearchableComboBox is not implemented"
    combo = combo_class(root, values=["Spare", "RSP-2"])
    combo.set("Spare")
    combo._entry.delete(0, "end")
    combo._entry.insert(0, "RSP-99")

    combo._commit_entry()

    assert combo.get() == "Spare"


def test_searchable_input_commits_manual_diagram_wording(root):
    combo_class = getattr(ui_widgets, "SearchableComboBox", None)
    assert combo_class is not None, "SearchableComboBox is not implemented"
    selected = []
    combo = combo_class(
        root,
        values=["500 BUS IN FROM XR/550", "From 710-LX500-2"],
        allow_custom=True,
        command=selected.append,
    )
    combo.set("500 BUS IN FROM XR/550")
    combo._entry.delete(0, "end")
    combo._entry.insert(0, "FROM EXISTING FIELD TAP")

    combo._commit_entry()

    assert combo.get() == "FROM EXISTING FIELD TAP"
    assert selected == ["FROM EXISTING FIELD TAP"]


def test_popup_focus_leaving_the_combo_reverts_invalid_output_text(root):
    combo = ui_widgets.SearchableComboBox(
        root, values=["Spare", "RSP-2"])
    other = ctk.CTkEntry(root)
    combo.pack()
    other.pack()
    combo.set("Spare")
    combo._entry.delete(0, "end")
    combo._entry.insert(0, "RSP-99")
    combo._show(["RSP-2"])
    assert combo._popup.bind("<FocusOut>")
    combo._popup.focus_set()
    root.update()

    other.focus_set()
    root.update()
    combo._finish_focus_out()

    assert combo.get() == "Spare"
    assert combo._popup is None


def test_selection_callback_may_destroy_combo_without_tcl_error(root):
    holder = ctk.CTkFrame(root)
    holder.pack()
    combo = ui_widgets.SearchableComboBox(
        holder, values=["Spare", "RSP-2"],
        command=lambda _value: holder.destroy())
    combo.pack()

    combo._choose("RSP-2")

    assert not holder.winfo_exists()


def test_open_popup_closes_when_its_scrolling_anchor_moves(root):
    holder = ctk.CTkFrame(root, width=300, height=80)
    holder.place(x=10, y=10)
    combo = ui_widgets.SearchableComboBox(
        holder, values=["Spare", "RSP-2"], width=200, height=30)
    combo.place(x=10, y=10)
    root.update_idletasks()
    combo._show(["Spare", "RSP-2"])
    assert combo._popup is not None

    holder.place_configure(y=100)
    root.after(100, root.quit)
    root.mainloop()

    assert combo._popup is None


def test_open_popup_closes_when_owning_tab_is_hidden(root):
    tab = ctk.CTkFrame(root)
    tab.pack()
    combo = ui_widgets.SearchableComboBox(
        tab, values=["Spare", "KEYPAD #3"])
    combo.pack()
    root.update_idletasks()
    combo._show(["Spare", "KEYPAD #3"])
    assert combo._popup is not None

    tab.pack_forget()
    root.after(100, root.quit)
    root.mainloop()

    assert combo._popup is None


def test_splitter_card_uses_searchable_input_and_output_controls(root):
    combo_class = getattr(ui_widgets, "SearchableComboBox", None)
    assert combo_class is not None, "SearchableComboBox is not implemented"
    splitter = Splitter(
        "710-LX500-1", "LX",
        inputs={"LX-Bus In": "500 BUS IN FROM XR/550"},
        outputs=["RSP-1", "Spare", "Spare"],
    )
    tab = editor_tabs.SplittersTab(
        root,
        Session(design=DMPDesign(splitters=[splitter], rsps=[RSP(1)])),
        lambda: None,
    )

    def descendants(widget):
        found = []
        for child in widget.winfo_children():
            found.append(child)
            found.extend(descendants(child))
        return found

    combos = [widget for widget in descendants(tab)
              if isinstance(widget, combo_class)]
    assert len(combos) == 4
    assert sum(combo._allow_custom for combo in combos) == 1
