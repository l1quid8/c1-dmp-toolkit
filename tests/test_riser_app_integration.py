"""Production editor/app integration contract for the RISER surface."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from editor_frame import TAB_TITLES  # noqa: E402
from riser_editor import RiserTab  # noqa: E402
from session import Session  # noqa: E402
from test_riser_scene import branched_design  # noqa: E402


def test_riser_is_a_normal_production_tab():
    assert TAB_TITLES[-1] == "RISER"


def test_app_exposes_revisioned_riser_generation_action():
    source = (ROOT / "scripts" / "app.py").read_text(encoding="utf-8")
    assert "def _generate_riser(self):" in source
    assert "generate_riser_bundle" in source
    assert 'on_generate_riser=self._generate_riser' in source


def test_riser_toolbar_stays_compact_and_initial_sheet_fits_canvas():
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    try:
        root.geometry("1600x850")
        tab = RiserTab(root, Session(design=branched_design()), lambda: None)
        tab.pack(fill="both", expand=True)
        root.update()
        root.update_idletasks()

        assert tab.toolbar.winfo_height() == 48
        assert tab.canvas.winfo_height() > 650
        left, top, right, bottom = tab.canvas.bbox("page")
        assert left >= 20 and top >= 20
        assert right <= tab.canvas.winfo_width()
        assert bottom <= tab.canvas.winfo_height()
    finally:
        root.destroy()
