"""Vector riser renderers and revision-matched output bundles."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from riser_render import generate_riser_bundle, render_pdf, render_svg  # noqa: E402
from riser_scene import layout_riser  # noqa: E402
from test_riser_scene import branched_design  # noqa: E402


def test_svg_is_standalone_searchable_and_contains_engineering_content(tmp_path):
    design = branched_design()
    scene = layout_riser(design)
    path = tmp_path / "riser.svg"

    render_svg(design, scene, path)

    root = ET.parse(path).getroot()
    text = path.read_text()
    assert root.attrib["width"] == "36in"
    assert "data:image/png;base64," in text
    assert "BRANCH SCHOOL" in text
    assert "710-LX500-1" in text
    assert "(N)(1)WP240R" in text
    assert "INT-5.0" in text


def test_pdf_profiles_have_exact_pages_and_searchable_text(tmp_path):
    design = branched_design()
    scene = layout_riser(design)
    master = tmp_path / "master.pdf"
    small = tmp_path / "small.pdf"

    render_pdf(design, scene, master, profile="24x36")
    render_pdf(design, scene, small, profile="11x17")

    with fitz.open(master) as doc:
        assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (2592, 1728)
        assert "BRANCH SCHOOL" in doc[0].get_text()
        assert "710-LX500-1" in doc[0].get_text()
    with fitz.open(small) as doc:
        assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (1224, 792)
        assert "INT-5.0" in doc[0].get_text()


def test_bundle_uses_one_revision_number_for_all_outputs(tmp_path):
    design = branched_design()
    scene = layout_riser(design)

    first = generate_riser_bundle(design, scene, tmp_path)
    second = generate_riser_bundle(design, scene, tmp_path)

    assert {p.name for p in first} == {
        "BRANCH_SCHOOL_riser_rev1_24x36.pdf",
        "BRANCH_SCHOOL_riser_rev1_11x17.pdf",
        "BRANCH_SCHOOL_riser_rev1.svg",
    }
    assert all(p.exists() for p in first + second)
    assert all("rev2" in p.name for p in second)


def test_svg_uses_curved_bridge_geometry_at_crossings(tmp_path):
    design = branched_design()
    scene = layout_riser(design)
    first, second = list(scene.routes)[:2]
    scene.routes[first].points = [(100.0, 200.0), (500.0, 200.0)]
    scene.routes[second].points = [(300.0, 100.0), (300.0, 400.0)]
    path = tmp_path / "bridges.svg"

    render_svg(design, scene, path)

    assert " Q 300.00 190.00 " in path.read_text()
