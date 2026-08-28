"""Full destination IDs must remain readable in the production OUT controls."""

import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import customtkinter as ctk
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from editor_tabs import SplittersTab
from parse_dmp_worksheet import DMPDesign, Splitter
from session import Session
from test_topology_service_regressions import connected_design
from tk_compat import install_scrollbar_redraw_fix
from ui_widgets import SearchableComboBox


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@pytest.fixture
def root():
    install_scrollbar_redraw_fix()
    try:
        root = ctk.CTk()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    yield root
    root.destroy()
    ctk.set_widget_scaling(1)


@pytest.mark.parametrize("window_width,scale", [(900, 1), (1350, 1), (1350, 1.25)])
def test_out_destinations_fit_without_clipping(root, window_width, scale):
    ctk.set_widget_scaling(scale)
    root.geometry(f"{window_width}x770")
    outputs = ["To 710-LX900-10", "To 710-LX900-11", "To 710-LX900-12"]
    design = DMPDesign(splitters=[Splitter("710-LX900-2", "LX", "MDF",
                                          outputs=outputs.copy())])
    tab = SplittersTab(root, Session(design=design), lambda: None)
    tab.pack(fill="both", expand=True)
    root.update_idletasks()
    menus = [w for w in descendants(tab.body)
             if isinstance(w, SearchableComboBox) and not w._allow_custom]

    assert [m.get() for m in menus] == outputs
    for menu in menus:
        entry = menu._entry
        text_width = tkfont.Font(root=root, font=entry.cget("font")).measure(menu.get())
        assert entry.winfo_width() >= text_width, f"clipped destination: {menu.get()}"
        card = tab._cards["710-LX900-2"]
        assert menu.winfo_rootx() + menu.winfo_width() <= card.winfo_rootx() + card.winfo_width()


@pytest.mark.parametrize("port_index", [0, 1, 2])
def test_each_stacked_out_control_edits_only_its_own_port(root, port_index):
    design = connected_design()
    splitter = next(s for s in design.splitters if s.id == "710-LX500-1")
    before = list(splitter.outputs)
    calls = []
    tab = SplittersTab(root, Session(design=design), lambda: calls.append(True))
    menus = [w for w in descendants(tab._cards[splitter.id])
             if isinstance(w, SearchableComboBox) and not w._allow_custom]

    menus[port_index]._dropdown_callback("RSP-3")

    expected = before.copy()
    expected[port_index] = "RSP-3"
    assert splitter.outputs == expected
    edge = next(e for e in design.connections
                if e.source.device_id == splitter.id
                and e.source.port_id == f"OUT{port_index + 1}")
    assert edge.target.device_id == "RSP-3"
    assert calls == [True]
