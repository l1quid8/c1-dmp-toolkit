"""Riser-topology extraction validation harness.

Runs the riser edge extraction against design PDFs supplied on the command line
and prints the derived wiring for manual review.

Usage:
    python validate_topology.py [extra_design.pdf ...]

Customer drawings and workstation paths are intentionally not stored in this
public repository. Keep any local fixture list outside the checkout and pass
its PDFs explicitly on the command line.
"""
from __future__ import annotations

import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from extract_topology import (extract_spans, merge_multiline_locations,
    cluster_devices, extract_line_segments, compute_device_footprints,
    reconstruct_edges)
from generate_dmp_ws import _detect_riser_page

def _norm(s: str) -> str:
    return s.replace(" ", "").upper()


# Local-only fixtures may be added at runtime by a developer, but the checked-in
# default stays empty so customer filenames and paths cannot leak into releases.
FIXTURES: list[tuple[str, str, set | None]] = []


def extract(pdf: str, page: int | None = None):
    pdf_p = Path(pdf)
    if page is None:
        page = _detect_riser_page(pdf_p)
    spans = merge_multiline_locations(extract_spans(pdf_p, page))
    devices = cluster_devices(spans)
    segs = extract_line_segments(pdf_p, page)
    footprints, son = compute_device_footprints(devices, segs, pdf_p, page)
    edges = reconstruct_edges(segs, devices, spans, footprints=footprints)
    return devices, edges, son, page


def run_one(name: str, pdf: str, truth: set | None):
    devices, edges, son, page = extract(pdf)
    print(f"\n{'=' * 66}\n{name}  (riser page {page + 1})\n{'=' * 66}")
    print(f"  devices={len(devices)}  splitter-on-RSP={son}")
    got = set()
    for e in edges:
        got.add((_norm(e.src.id), _norm(e.dst.id)))
        print(f"   {e.src.id:<14} -> {e.dst.id}")
    if truth is None:
        print("  (no ground truth — manual review)")
        return None
    tn = {(_norm(a), _norm(b)) for a, b in truth}
    hit = tn & got
    missed = tn - got
    ok = not missed
    print(f"  GROUND TRUTH: {len(hit)}/{len(tn)} edges found — "
          f"{'PASS' if ok else 'FAIL'}")
    if missed:
        print(f"   MISSED: {sorted(missed)}")
    return ok


def main() -> None:
    results = []
    for name, pdf, truth in FIXTURES:
        if not Path(pdf).exists():
            print(f"\n{name}: SKIP (not found: {pdf})")
            continue
        try:
            results.append((name, run_one(name, pdf, truth)))
        except Exception as e:
            print(f"\n{name}: ERROR — {e}")
            results.append((name, False))
    for extra in sys.argv[1:]:
        try:
            run_one(Path(extra).stem, extra, None)
        except Exception as e:
            print(f"\n{extra}: ERROR — {e}")
    graded = [ok for _, ok in results if ok is not None]
    print(f"\n{'=' * 66}")
    print(f"FIXTURES WITH GROUND TRUTH: {sum(1 for ok in graded if ok)}/"
          f"{len(graded)} pass")


if __name__ == "__main__":
    main()
