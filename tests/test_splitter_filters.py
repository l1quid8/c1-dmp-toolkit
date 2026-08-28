"""Card filters are presentation-only and include every supported LX bus."""

import copy
import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from editor_tabs import SplittersTab, build_topology
from parse_dmp_worksheet import DMPDesign, Keypad, RSP, Splitter
from session import Session
from tk_compat import install_scrollbar_redraw_fix


def mixed_design():
    splitters = [
        Splitter(f"710-LX{bus}-{number}", "LX",
                 inputs={"LX-Bus In": f"{bus} BUS IN FROM XR/550"},
                 outputs=["Spare"] * 3)
        for bus in (900, 500, 700, 600, 800) for number in (10, 2, 1)
    ]
    splitters.extend(Splitter(f"KP-710-{number}", "KP",
                             outputs=["Spare"] * 3) for number in (10, 2, 1))
    return DMPDesign(splitters=splitters,
                     rsps=[RSP(n) for n in (10, 2, 1)],
                     keypads=[Keypad(n) for n in (10, 2, 1)])


@pytest.fixture
def root():
    install_scrollbar_redraw_fix()
    try:
        root = ctk.CTk()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    root.withdraw()
    yield root
    root.destroy()


def test_filters_sort_all_lx_buses_and_kp_without_mutating_design(root):
    design = mixed_design()
    before = copy.deepcopy(design)
    calls = []
    tab = SplittersTab(root, Session(design=design), lambda: calls.append(True))
    expected_lx = [f"710-LX{bus}-{n}" for bus in range(500, 901, 100)
                   for n in (1, 2, 10)]
    expected_kp = [f"KP-710-{n}" for n in (1, 2, 10)]

    assert tab._filter_control.cget("values") == ["ALL", "LX", "KP"]
    assert list(tab._cards) == expected_lx + expected_kp
    topology = build_topology(design)
    tab._set_filter("LX")
    assert list(tab._cards) == expected_lx
    tab._set_filter("KP")
    assert list(tab._cards) == expected_kp
    tab.refresh()
    assert tab._filter_control.get() == "KP"
    assert list(tab._cards) == expected_kp
    tab._set_filter("ALL")
    assert list(tab._cards) == expected_lx + expected_kp
    assert build_topology(design) == topology
    assert design == before
    assert calls == []


def test_topology_jump_reveals_a_card_hidden_by_filter(root):
    tab = SplittersTab(root, Session(design=mixed_design()), lambda: None)
    tab._set_filter("KP")

    tab._focus_card("710-LX900-10")

    assert "710-LX900-10" in tab._cards
    assert tab._filter_control.get() == "ALL"


def test_wiring_choices_are_numerically_sorted_independent_of_card_filter(root):
    design = mixed_design()
    tab = SplittersTab(root, Session(design=design), lambda: None)
    tab._set_filter("KP")
    lx = next(s for s in design.splitters if s.id == "710-LX600-1")
    kp = next(s for s in design.splitters if s.id == "KP-710-1")

    assert tab._output_choices(lx) == [
        "Spare", "RSP-1", "RSP-2", "RSP-10", "To 710-LX600-2", "To 710-LX600-10"]
    assert tab._input_choices(lx) == [
        "600 BUS IN FROM XR/550", "From 710-LX600-2", "From 710-LX600-10"]
    assert tab._output_choices(kp) == [
        "Spare", "KEYPAD #1", "KEYPAD #2", "KEYPAD #10", "To KP-710-2", "To KP-710-10"]
    assert tab._input_choices(kp) == [
        "KEYPAD BUS IN FROM XR/550", "From KP-710-2", "From KP-710-10"]


def test_filter_keeps_uncommitted_conflict_choice(root):
    from generate_dmp_ws import LocationConflict

    design = mixed_design()
    design.conflicts = [LocationConflict("RSP", 1, "RSP 1 location",
                                         [("UNKNOWN", "riser")])]
    tab = SplittersTab(root, Session(design=design), lambda: None)
    banner = tab.top.winfo_children()[0]
    custom = next(w for w in banner.winfo_children() if isinstance(w, ctk.CTkEntry))
    custom.insert(0, "ROOM 42")

    tab._set_filter("LX")

    assert custom.winfo_exists()
    assert custom.get() == "ROOM 42"
