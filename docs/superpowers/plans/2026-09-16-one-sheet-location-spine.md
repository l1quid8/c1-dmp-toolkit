# One-Sheet Location-Spine Riser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the electrical-row Preview layout with a one-sheet, location-grouped KP/LX spine layout, remove the Auto-layout button, and rebuild the native macOS app.

**Architecture:** Keep `layout_presentation` pure and preserve the existing scene, router, and renderer contracts. Extract content-aware group sizing and spine placement into a focused helper, then route actual topology edges through the existing obstacle-aware router. Preview/Apply continue to be the only full-relayout path.

**Tech Stack:** Python 3.13, CustomTkinter, PyMuPDF, pytest, PyInstaller/macOS.

**Spec:** `docs/superpowers/specs/2026-09-16-one-sheet-riser-location-spine-design.md`

## Global Constraints

- One INT-5.0 sheet, rendered to existing 11×17, 24×36, and SVG profiles.
- Real canonical location IDs and real graph edges only; never infer or alter wiring.
- Critical equipment, port, route, and location text remains at least 7 pt on 11×17; secondary annotations at least 6 pt.
- Existing saved manual scenes do not repack on open; Preview/Cancel stays a no-op and Apply stays one undo step.
- Preserve all unrelated dirty-worktree edits. Stage only files owned by this feature.
- Build to a new staging directory; never run the installer step that replaces the running app.

---

### Task 1: Grouped symbol geometry

**Files:**
- Modify: `scripts/riser_symbols.py`
- Modify: `scripts/riser_drawing.py`
- Test: `tests/test_riser_render.py`

**Interfaces:**
- Consumes: canonical device ref, `DMPDesign`, `RiserElement`.
- Produces: `grouped_size(design, ref) -> tuple[float, float]`, and `device_text(design, element, small=...)` for `symbol_style='grouped'`.

- [ ] **Step 1: Write failing tests** proving grouped RSP/710/keypad symbols retain device IDs, input/output names, and zone ranges while omitting duplicated full-room captions. Measure their text bounds inside their body and verify the 11×17 critical-text floor.
- [ ] **Step 2: Run** `pytest tests/test_riser_render.py -q` and confirm the new assertions fail for the missing grouped style.
- [ ] **Step 3: Implement** a content-measured grouped style, reusing detailed shapes and terminal parts. Use a size function such as:

```python
def grouped_size(design, ref):
    """Minimum body that fits ID, port names, and device-specific detail."""
    if ref == 'MSP':
        base = (440.0, 180.0)
    elif ref.startswith('RSP-'):
        base = (300.0, 150.0)
    elif ref.startswith('KEYPAD-'):
        base = (180.0, 120.0)
    else:
        base = (210.0, 120.0)
    # Expand base using the actual grouped TextRun bounds before returning.
    return base
```

  Route `symbol_style in {'detailed', 'grouped'}` through the detailed geometry paths. The grouped style prints room names once in the group heading; full device/location data remains in the design.
- [ ] **Step 4: Re-run** `pytest tests/test_riser_render.py -q`; inspect output for no text clipping or sub-minimum runs.
- [ ] **Step 5: Commit** only grouped-symbol files and their tests.

### Task 2: Deterministic location-spine placement

**Files:**
- Create: `scripts/riser_spine_layout.py`
- Modify: `scripts/riser_presentation.py`
- Test: `tests/test_riser_dense_presentation.py`
- Test: `tests/test_riser_cluster_workflows.py`

**Interfaces:**
- Consumes: isolated design with synchronized location IDs, `grouped_size`, and page drawing bounds.
- Produces: `place_spine_groups(design, sizes, page_width, page_height) -> tuple[dict[str, tuple[float,float]], dict[str, tuple[float,float,float,float]], list[str]]`; the maps contain device positions and group rectangles, and the list contains fit diagnostics.

- [ ] **Step 1: Write failing tests** for one cohesive group per physical location ID, same-room MSP/RSP co-location, separate unresolved devices, KP-left/LX-right preference, real parent-above-child order, input-list permutation stability, and 27-device dense-school one-sheet behavior.
- [ ] **Step 2: Run** `pytest tests/test_riser_dense_presentation.py tests/test_riser_cluster_workflows.py -q` and confirm failures identify current scattered placements.
- [ ] **Step 3: Implement** a pure planner with stable graph traversal and group membership. Candidate layouts reserve a central corridor and use one or two subcolumns per side when needed; measure wrapped headings and symbol footprints before packing. Score candidates for page fit, clearances, crossings, depth order, and whitespace. Return a diagnostic if no candidate fits above the type floor.

```python
positions, frames, fit_issues = place_spine_groups(
    isolated, sizes, doc.page_width, doc.page_height
)
for ref, (x, y) in positions.items():
    doc.elements[f'device:{ref}'] = RiserElement(
        f'device:{ref}', 'device', ref, x, y, sizes[ref][0], sizes[ref][1],
        location_id='location:' + isolated.device_location_ids[ref],
        symbol_style='grouped')
```

  Build one scene location per canonical ID, use a light group outline and measured heading, preserve annotations/title/z-order, and keep legacy saved geometry untouched.
- [ ] **Step 4: Re-run** the targeted layout tests; fix planner geometry until feasible fixtures fit without overlaps or off-page content.
- [ ] **Step 5: Commit** planner, integration, and tests only.

### Task 3: Traceable routing and fit feedback

**Files:**
- Modify: `scripts/riser_presentation.py`
- Modify: `scripts/riser_scene.py`
- Modify: `scripts/riser_editor.py`
- Test: `tests/test_riser_dense_presentation.py`
- Test: `tests/test_riser_cluster_workflows.py`

**Interfaces:**
- Consumes: Task 2 scene positions and group frames; actual `TopologyConnection` records.
- Produces: unchanged `RiserRoute`/`RiserIssue` contracts, plus a specific preview fit warning when clearance cannot be met.

- [ ] **Step 1: Write failing tests** for exact route endpoints, no line through device/heading/caption, distinct parallel segments, deterministic bridge handling, fit warning for intentionally impossible count/labels, and unchanged design/scene after Preview/Cancel.
- [ ] **Step 2: Run** the targeted tests and verify expected routing/fit failures.
- [ ] **Step 3: Route** edges in stable upstream/port order, reserving central KP/LX and branch segments before later edges. Keep source/target ports from `port_point`; use the existing obstacle-aware `route_topology_connection`, adding corridor bias only where it reduces conflicts. Place labels after routes and turn unresolved clearance failures into specific `RiserIssue` messages. Surface those messages in Preview without mutating the live scene.
- [ ] **Step 4: Re-run** targeted tests and the riser test subset; inspect both 11×17/24×36 render output and a QA preview for readability.
- [ ] **Step 5: Commit** routing/feedback and tests only.

### Task 4: Remove exposed Auto-layout and preserve Add Device

**Files:**
- Modify: `scripts/riser_editor.py`
- Modify: `README.md`
- Test: `tests/test_riser_hardware_forms.py`
- Test: `tests/test_riser_cluster_workflows.py`

**Interfaces:**
- Consumes: existing internal `RiserController.auto_layout(device_ids=...)` called after Add Device.
- Produces: no toolbar Auto-layout action; internal placement remains callable.

- [ ] **Step 1: Write failing UI test** asserting the Auto-layout button is absent while Add Device still places a new device without moving existing manual geometry.
- [ ] **Step 2: Run** the focused UI tests and confirm the button-presence assertion fails.
- [ ] **Step 3: Remove** only the button and user-facing callback; retain controller behavior and update RISER documentation to describe Preview/Apply and automatic new-device placement.
- [ ] **Step 4: Re-run** the focused UI tests and all riser tests.
- [ ] **Step 5: Commit** feature-owned UI/doc/test changes only, without staging unrelated edits.

### Task 5: Full verification and native macOS build

**Files:**
- No production-file changes unless a verified test/build defect requires a focused fix.
- Build artifact: staged `C1 DMP Toolkit.app` outside the running installation.

**Interfaces:**
- Consumes: completed Tasks 1–4 and current `dmp_doorchart.spec`.
- Produces: a verified native macOS `.app`; no installation or user-project rewrite.

- [ ] **Step 1: Run** the full pytest suite with the repository's Python 3.13 environment; record pass/skip counts and investigate any failures.
- [ ] **Step 2: Render** an isolated QA riser to 11×17, 24×36, and SVG, check one-page dimensions, searchable text, true port labels, and no off-page geometry. Verify source project hashes unchanged.
- [ ] **Step 3: Build** with the existing PyInstaller spec into a newly named staging directory, not `build_mac.command` (its last step deletes/replaces `~/Applications/C1 DMP Toolkit.app`).
- [ ] **Step 4: Verify** app bundle existence, version, executable, macOS signature, and launch/quit of the staged build without opening or overwriting a user project.
- [ ] **Step 5: Report** exact verification results, staged app path, unresolved platform limits, and whether installation remains a separate user action.
