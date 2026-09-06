# Riser Equipment Clustering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Readable room clusters arranged by electrical flow, with stable shared locations and non-destructive preview/apply.

**Architecture:** Project-location records own physical identity; legacy text fields remain lossless compatibility projections. A pure version-2 layout consumes these records and the existing electrical graph. Existing version-1 scenes retain their recovery/synchronization path until Apply layout.

**Tech Stack:** Python, CustomTkinter/Tk Canvas, PyMuPDF, existing SVG serializer; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-05-riser-equipment-clustering-design.md`

## Global Constraints

- Upgrade schema 4 to schema 5 in memory; save schema 5 only on explicit Save (normal recovery sidecars remain enabled).
- No new runtime dependency or electrical topology representation.
- Retain canonical 24×36 coordinates, black electrical wiring, one INT-5.0 sheet, searchable vector PDFs and standalone SVG.
- Critical text at least 7 pt, secondary text at least 6 pt and lines at least 0.35 pt on 11×17.
- No source project edits during acceptance; use separate copies.
- Preserve existing worktree changes, manual geometry, title data and markup. Do not commit, push, or replace a running native app.
- Execute inline because schema migration, location transactions and editor history are coupled. Use task checkpoints below as the persistent execution ledger.

## Task 1: Stable locations and lossless migration

Files: create `scripts/location_model.py`, extend `scripts/project_locations.py`, `scripts/parse_dmp_worksheet.py`, `scripts/session.py`; tests in `tests/test_equipment_locations.py`.

Interfaces:
```python
@dataclass
class EquipmentLocation:
    id: str
    full_label: str
    building: str = ''
    floor: str = ''
    room: str = ''
    confirmed: bool = False

# DMPDesign: equipment_locations, device_location_ids, location_sync_values
def sync_project_locations(design) -> None: ...
def edit_location(design, location_id, *, building, floor, room, allow_merge=False) -> str: ...
def assign_location(design, device_id, location_id) -> None: ...
```

- [x] Write failing tests for distinct unknowns, same building/different floors, MSP-relative RSPs, paired PSs, stable rename identity and schema round-trip. Representative assertions:
```python
sync_project_locations(d)
assert d.device_location_ids['RSP-1'] == d.device_location_ids['MSP']
assert d.device_location_ids['RSP-2'] != d.device_location_ids['MSP']
assert location_values(d) == original_text
```
- [x] Run `python -m pytest tests/test_equipment_locations.py -q`; observe missing behavior before implementing.
- [x] Implement deterministic UUID5 seeding using case/whitespace-only keys; unresolved keys include device ID. Reconcile externally edited compatibility strings against persisted `location_sync_values`, assigning new/existing records without renaming old occupants. Preserve untouched strings, including aliases/suffixes. Separate legacy normalization from drawing dependencies.
- [x] Add schema-5 fields and tolerant legacy parsing; update schema expectations in existing tests. Run location/session/rename tests and inspect diff.

## Task 2: Pure cluster layout and shared heading geometry

Files: create `scripts/riser_cluster_layout.py`; extend `scripts/riser_model.py`, `scripts/riser_drawing.py`, `scripts/riser_scene.py`, `scripts/riser_render.py`; tests in `tests/test_riser_cluster_layout.py`.

Interfaces:
```python
# RiserElement: physical_location_id, heading_lines, heading_height
# RiserDocument: layout_version (legacy default 1)
def layout_clusters(design, *, title_source=None) -> RiserDocument: ...
def location_text_runs(element, *, small=False) -> list[TextRun]: ...
```

- [x] Add failing tests for co-location, distinct unknown clusters, deterministic ordering, no device/heading overlap, room revisits and no input mutation.
```python
before = deepcopy(d)
scene = layout_clusters(d)
assert d == before
assert scene.layout_version == 2
assert scene.elements['device:RSP-1'].location_id == scene.elements['device:MSP'].location_id
```
- [x] Run focused tests and observe failures.
- [x] On a cloned design, synchronize location records, calculate graph depth and naturally ordered members. Measure bounded-width wrapped headings and symbol rows. Center head end; rank remote clusters by device graph, not a contracted room DAG. Pack balanced rows and evaluate compact spacing profiles for clearance/crossings before whitespace. Reuse existing obstacle-aware orthogonal routing and label placement; preserve returning-room edges and independent routes.
- [x] Store multiline heading geometry in frames; share text runs across Canvas and vector renderers. Protect full heading bands and validate small-profile metrics.
- [x] Verify deterministic outputs and searchable PDFs/SVG headings, then rerun legacy scene/render tests.

## Task 3: Version-aware synchronization and transactional preview

Files: `scripts/riser_scene.py`, `scripts/riser_editor.py`, `scripts/session.py`; tests in `tests/test_riser_cluster_workflows.py`.

Interfaces:
```python
def sync_cluster_ownership(design, document) -> None: ...
RiserEditorController.preview_layout() -> RiserDocument
RiserEditorController.apply_layout(preview) -> None
RiserEditorController.edit_location(location_id, **fields) -> None
RiserEditorController.assign_location(device_id, location_id) -> None
```

- [x] Add failing tests for preview no-op, exact Apply/Undo/Redo, unchanged ordinary reopen, preserved manual coordinates on rename/reassignment, stale-preview rejection and non-destructive missing-item placement.
```python
before = deepcopy(design)
preview = controller.preview_layout()
assert design == before
controller.apply_layout(preview)
assert controller.undo()
assert design == before
```
- [x] Run focused tests before implementation.
- [x] Route version-1 documents through legacy layout/recovery; never upgrade them automatically. Version-2 ownership uses physical IDs. Extend history with registry snapshots restored only when a command changed registry/assignments; rebase conflicting external changes. Fingerprint preview source and reject applying after project edits.
- [x] Add new items without moving retained devices/routes; remove only unused generated frames. Keep live manual work and unresolved/stale warnings.
- [x] Run controller, saved-scene, drag and rename regression tests.

## Task 4: Inspector editing and preview/apply UI

Files: `scripts/riser_editor.py`, `scripts/editor_frame.py`, `scripts/editor_tabs.py`; tests in `tests/test_riser_cluster_workflows.py`.

- [x] Add failing real-widget tests for Building/Floor/Room Apply, global rename versus reassignment, merge confirmation, preview mode, Cancel/Esc and disabled editing during preview.
- [x] Add location inspector controls with explicit affected-hardware confirmation. Device properties select existing room records, while existing free-text domain inputs reconcile as reassignment. Refresh all affected views without invalidating reviewed wiring.
- [x] Add Preview layout and Apply/Cancel controls. Render the cloned document without rebinding live design/controller data; block mutation/generation while previewing. Cancel restores live rendering; Apply confirms manual-geometry replacement and commits one undo step. Existing Re-layout All enters preview.
- [x] New project scenes use version 2; existing nonempty scenes keep their saved version. Verify the actual EditorFrame and all existing keyboard/drag tests.

## Task 5: Acceptance and native release

Files: `VERSION`, `README.md`, `docs/releases/v1.5.0.md`; acceptance artifacts in a new temporary QA directory outside the repository.

- [x] Run all tests with `/Users/tylercaldwell/.dmp-doorchart/venv/bin/python -m pytest -q` and `git diff --check`.
- [ ] Open separate Riverside and Griffin copies; inspect legacy open, preview, apply, save/reopen, location edits and exports. Inspect 24×36/11×17 images, not just assertions. Check available Fourth Street data; report unverified corpus explicitly if missing.
- [x] Compare drag preview behavior and release timing to existing regression tests. Review changed model/service/layout/controller interfaces for missed serialization or mutation paths.
- [x] Build native macOS v1.5.0 using the existing PyInstaller spec, verify signature/version, retain v1.4.5 backup, install only when app is closed and smoke-test the native window. Report Windows unverified.
- [x] Update task checkboxes with actual evidence. Leave changes uncommitted and report any remaining warnings or release blockers honestly.

## Execution evidence / deviations

- Baseline: 577 passed, 6 skipped. New location, layout and transaction tests
  were observed failing before implementation. Before the native-menu fix,
  final full run: 602 passed, 6 skipped; five existing SWIG warnings.
- Read-only code review found two important issues, both reproduced and fixed:
  missing items overlapping retained manual geometry, and a legacy frame's
  global rename failing to request a registry merge. Regression tests pass.
- Spacing candidates preserve electrical/source order instead of arbitrarily
  permuting sibling branches. Roomy, compact and dense profiles are scored for
  off-page/clearance failures, contacts and whitespace; symbols never shrink.
  Griffin's initial clipped bottom row is now on-page. This is an intentional
  bounded first increment, not a general-purpose global crossing optimizer.
- Riverside/Griffin previews rendered at both sheet profiles and visually
  inspected. Original Riverside source remains byte-identical to its QA copy.
  Riverside retains occupied-port/orphan and four independent-contact warnings;
  Griffin retains only three missing title fields. No topology was repaired.
- Native arm64 v1.5.0 initially built and passed strict signature verification.
  v1.4.5 was backed up before installation; native v1.5.0 opened the separate
  Riverside copy. Native menu inspection then reproduced a pre-existing freeze:
  Tk menu postcommand -> filesystem scandir -> blocked open on the main thread.
  Menus now use cached recent projects and defer worksheet discovery until the
  explicit action. Two failing regression tests now pass; rebuild underway.
- QA artifacts and old native bundle are outside the repository:
  /Users/tylercaldwell/.dmp-doorchart/cluster-qa-20260905-HHTMtj/
- Fourth Street was not found in the checkout or Sessions folder. Windows
  packaging and complete native pointer/drag coverage remain unverified.
- No commits, pushes, original-project writes, or unrelated cleanup performed.

### Final acceptance checkpoint

- Final full suite after the native menu correction: **604 passed, 6 skipped**,
  five existing SWIG deprecation warnings, 118.76 seconds; diff whitespace clean.
- The blocked process contained only our unchanged QA copy and was terminated
  for test teardown. Final bundle was rebuilt, signed/verified, installed while
  closed, and reopened successfully. Native keyboard riser validation and its
  jump back to RISER are responsive. The app is left showing the already-applied
  Riverside QA drawing, not the user's original project.
- Separate Riverside and Griffin copies passed Preview/Apply/Undo/Redo,
  structured global editing, movement/Undo, and exact save/reopen comparisons.
  Room A -> B -> A was checked with no false electrical cycle or input mutation.
- Original Riverside SHA256 remained
  922b99d8ecb470194c5d16763140228efa6ef2f945a9771a48649d9d5d7cea89.
- Task 5's complete real/native corpus acceptance remains unchecked: pointer
  automation does not reliably activate the custom Tk controls, so complete
  native drag/edit/apply acceptance is not claimed. Actual-widget tests cover
  those actions. Fourth Street and Windows checks remain unavailable.
