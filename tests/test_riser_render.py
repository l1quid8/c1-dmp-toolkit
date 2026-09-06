"""Vector riser renderers and revision-matched output bundles."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import riser_render  # noqa: E402
from riser_render import generate_riser_bundle, render_pdf, render_svg  # noqa: E402
from riser_scene import TITLE_BLOCK_WIDTH, layout_riser  # noqa: E402
from riser_model import RiserAnnotation  # noqa: E402
from test_riser_scene import branched_design, legacy_named_splitter_design  # noqa: E402


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
        spans = [span for block in doc[0].get_text("dict")["blocks"]
                 if "lines" in block for line in block["lines"] for span in line["spans"]]
        cable_spans = [span for span in spans if "WP240R" in span["text"]]
        assert cable_spans and min(span["size"] for span in cable_spans) >= 7
        assert min(span["size"] for span in spans if span["text"].strip()) >= 6


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


def test_bundle_failure_is_atomic_and_retry_reuses_revision_one(tmp_path, monkeypatch):
    design = branched_design()
    scene = layout_riser(design)
    original_render_pdf = riser_render.render_pdf

    def fail_second_artifact(design, document, output_path, *, profile="24x36"):
        if profile == "11x17":
            raise OSError("simulated second-artifact failure")
        return original_render_pdf(
            design, document, output_path, profile=profile)

    monkeypatch.setattr(riser_render, "render_pdf", fail_second_artifact)
    with pytest.raises(OSError, match="second-artifact"):
        riser_render.generate_riser_bundle(design, scene, tmp_path)
    monkeypatch.setattr(riser_render, "render_pdf", original_render_pdf)

    retry = riser_render.generate_riser_bundle(design, scene, tmp_path)
    expected = {
        "BRANCH_SCHOOL_riser_rev1_24x36.pdf",
        "BRANCH_SCHOOL_riser_rev1_11x17.pdf",
        "BRANCH_SCHOOL_riser_rev1.svg",
    }

    assert {path.name for path in retry} == expected
    assert {path.name for path in tmp_path.iterdir()} == expected


def test_svg_uses_curved_bridge_geometry_at_crossings(tmp_path):
    design = branched_design()
    scene = layout_riser(design)
    first, second = list(scene.routes)[:2]
    scene.routes[first].points = [(100.0, 200.0), (500.0, 200.0)]
    scene.routes[second].points = [(300.0, 100.0), (300.0, 400.0)]
    path = tmp_path / "bridges.svg"

    render_svg(design, scene, path)

    assert " Q 300.00 190.00 " in path.read_text()


def test_svg_uses_vertical_bridge_when_horizontal_route_ends_at_contact(tmp_path):
    design = branched_design()
    scene = layout_riser(design)
    horizontal, vertical = list(scene.routes)[:2]
    scene.routes[horizontal].points = [(100.0, 200.0), (300.0, 200.0)]
    scene.routes[vertical].points = [(300.0, 100.0), (300.0, 400.0)]
    path = tmp_path / "vertical-bridge.svg"

    render_svg(design, scene, path)

    assert " Q 310.00 200.00 300.00 209.00" in path.read_text()


def test_svg_keeps_arrow_markup_and_text_alignment_editable(tmp_path):
    design = branched_design()
    scene = layout_riser(design)
    scene.annotations.extend([
        RiserAnnotation("arrow-1", "arrow", [(10, 10), (80, 80)]),
        RiserAnnotation("note-1", "text", [(100, 100)], text="NOTE", alignment="right"),
    ])
    path = tmp_path / "markup.svg"

    render_svg(design, scene, path)

    text = path.read_text()
    assert 'id="arrow-1"' in text and 'marker-end="url(#arrowhead)"' in text
    assert 'id="note-1"' in text and 'text-anchor="end"' in text


def test_legacy_named_splitter_exports_splitter_port_labels(tmp_path):
    design = legacy_named_splitter_design()
    scene = layout_riser(design)
    path = render_svg(design, scene, tmp_path / "legacy.svg")
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    root = ET.parse(path).getroot()
    group = root.find('.//svg:g[@id="device:KP-710-1"]', namespace)

    assert group is not None
    labels = [node.text for node in group.findall("svg:text", namespace)]
    assert {"IN", "OUT 1", "OUT 2", "OUT 3"} <= set(labels)


def test_long_school_name_is_not_clipped_by_title_block(tmp_path):
    design = branched_design()
    design.site_info.school_name = "CANTERBURY ELEMENTARY SCHOOL"
    scene = layout_riser(design)
    scene.title_block.project_title = "SHORT PROJECT"
    scene.title_block.address = ""
    path = render_pdf(design, scene, tmp_path / "long-title.pdf")

    with fitz.open(path) as document:
        spans = [
            span
            for block in document[0].get_text("dict")["blocks"]
            if "lines" in block
            for line in block["lines"]
            for span in line["spans"]
            if span["text"].strip()
        ]

    title_left = scene.page_width - TITLE_BLOCK_WIDTH
    title_right = scene.page_width - 36
    school_spans = sorted(
        (span for span in spans
         if span["bbox"][0] > title_left - 100
         and 130 <= span["bbox"][1] < 220),
        key=lambda span: (span["bbox"][1], span["bbox"][0]),
    )

    assert " ".join(span["text"].strip() for span in school_spans) == (
        "CANTERBURY ELEMENTARY SCHOOL"
    )
    assert all(title_left <= span["bbox"][0]
               and span["bbox"][2] <= title_right
               for span in school_spans)


def test_svg_respects_annotation_z_order(tmp_path):
    design = branched_design()
    scene = layout_riser(design)
    scene.annotations = [
        RiserAnnotation("annotation-a", "rectangle", [(100, 100), (200, 200)]),
        RiserAnnotation("annotation-b", "rectangle", [(100, 100), (200, 200)]),
    ]
    scene.z_order = [
        item for item in scene.z_order
        if item not in {"annotation-a", "annotation-b"}
    ] + ["annotation-b", "annotation-a"]
    path = render_svg(design, scene, tmp_path / "z-order.svg")
    text = path.read_text()

    assert text.index('id="annotation-b"') < text.index('id="annotation-a"')


@pytest.mark.parametrize('formats, suffixes', [
    (('11x17',), ['_11x17.pdf']),
    (('24x36', 'svg'), ['_24x36.pdf', '.svg']),
])
def test_selected_outputs_create_only_requested_files(tmp_path, formats, suffixes):
    design = branched_design()
    scene = layout_riser(design)
    paths = generate_riser_bundle(design, scene, tmp_path, formats=formats)
    assert sorted(tmp_path.iterdir()) == sorted(paths)
    assert len(paths) == len(suffixes)
    assert all(path.name.endswith(suffix) for path, suffix in zip(paths, suffixes))
    if formats == ('11x17',):
        with fitz.open(paths[0]) as pdf:
            assert tuple(pdf[0].rect)[2:] == (1224, 792)


@pytest.mark.parametrize('formats', [(), ('bogus',)])
def test_invalid_output_choice_writes_nothing(tmp_path, formats):
    design = branched_design()
    with pytest.raises(ValueError):
        generate_riser_bundle(design, layout_riser(design), tmp_path, formats=formats)
    assert list(tmp_path.iterdir()) == []
