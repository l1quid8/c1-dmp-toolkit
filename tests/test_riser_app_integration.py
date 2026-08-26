"""Production editor/app integration contract for the RISER surface."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from editor_frame import TAB_TITLES  # noqa: E402


def test_riser_is_a_normal_production_tab():
    assert TAB_TITLES[-1] == "RISER"


def test_app_exposes_revisioned_riser_generation_action():
    source = (ROOT / "scripts" / "app.py").read_text(encoding="utf-8")
    assert "def _generate_riser(self):" in source
    assert "generate_riser_bundle" in source
    assert 'on_generate_riser=self._generate_riser' in source
