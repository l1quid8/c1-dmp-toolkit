"""Resolving an unknown RSP location must update the existing POWER entry."""

import copy
import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from editor_frame import EditorFrame
from generate_dmp_ws import LocationConflict
from parse_dmp_worksheet import PowerSupply
from session import Session
from test_riser_scene import branched_design
from tk_compat import install_scrollbar_redraw_fix


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


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
    design.rsps[1].location = "UNKNOWN"
    design.power_supplies = [PowerSupply(1, "MDF"), PowerSupply(2, "UNKNOWN")]
    design.conflicts = [LocationConflict("RSP", 2, "RSP 2 location",
                                         [("UNKNOWN", "riser"), ("ROOM 42", "zones")])]
    frame = EditorFrame(root, root, Session(design=design, path=tmp_path / "test.dmps"))
    frame.pack(fill="both", expand=True)
    root.update_idletasks()
    yield frame
    root.destroy()


def test_conflict_resolution_updates_power_without_recreating_entry_or_moving_riser(editor):
    design = editor.session.design
    power_entry = next(w for w in descendants(editor.power_tab)
                       if isinstance(w, ctk.CTkEntry) and w.get() == "UNKNOWN")
    geometry = copy.deepcopy(design.riser_document.elements)
    routes = copy.deepcopy(design.riser_document.routes)
    before_epoch = editor.edit_epoch
    banner = editor.splitters_tab.top.winfo_children()[0]
    custom = next(w for w in descendants(banner) if isinstance(w, ctk.CTkEntry))
    custom.insert(0, "ROOM 42")
    use_value = next(w for w in descendants(banner)
                     if isinstance(w, ctk.CTkButton) and w.cget("text") == "Use this value")

    use_value.invoke()

    assert power_entry.winfo_exists(), "do not discard focused widgets on external edits"
    assert power_entry.get() == "ROOM 42"
    assert design.rsps[1].location == design.power_supplies[1].location == "ROOM 42"
    assert design.power_supplies[0].location == "MDF"
    assert design.conflicts == []
    assert editor.edit_epoch == before_epoch + 1, "UI sync must not recurse into edit callbacks"
    assert design.riser_document.elements == geometry
    assert design.riser_document.routes == routes


def test_power_typing_preserves_spaces_and_single_edit_notification(editor):
    design = editor.session.design
    entry = next(w for w in descendants(editor.power_tab)
                 if isinstance(w, ctk.CTkEntry) and w.get() == "UNKNOWN")
    before_epoch = editor.edit_epoch

    entry.insert("end", " ")

    assert entry.get() == "UNKNOWN "
    assert design.rsps[1].location == "UNKNOWN"
    assert editor.edit_epoch == before_epoch + 1
