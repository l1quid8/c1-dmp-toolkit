# Riser, RemoteLink, and Print Layout Integration Plan

> For agentic workers: use superpowers:executing-plans for the tightly coupled merge; use test-driven-development for behavioral fixes and requesting-code-review before handoff.

**Goal:** Combine the three existing integrations without losing project data or existing functionality, ready for a subsequent release.

**Architecture:** Start from RemoteLink `40b5dcc`, merge riser `746b4eb`, then print-layout `eaa8885` with explicit merge commits. Preserve both data models and both editor surfaces. Use schema 3 for combined projects so older schema-2 builds reject them instead of silently dropping fields.

**Tech stack:** Python, CustomTkinter, pytest, openpyxl, existing PDF/riser renderers.

**Spec:** User request to merge the integrations in preparation for release, expressly preserving their newer riser work.

## Global constraints

- Work only in the isolated `integration/riser-remotelink-release` checkout; leave source branches and installed application intact.
- Preserve RemoteLink verified mappings, safe defaults, encrypted read-back verification, zone overrides, and privacy cleanup.
- Preserve riser topology mutations, editable scene/layout, PDF/bundle output, and worksheet fixes.
- Preserve door-chart print layout, headers, full splitter coverage, and eight-port support.
- Use fabricated fixtures only. Do not commit calibration exports, credentials, real school data, or local personal paths.
- Merge/commit locally; do not push, publish/tag, install over the current app, or replace an existing release.

## Task 1: Baselines and merge

- [x] Run both source suites using the existing virtual environment; record any pre-existing failures.
- [x] Merge riser with `--no-commit --no-ff`; reconcile overlapping app, editor, session, validation, and generation code without wholesale ours/theirs resolution.
- [x] Preserve independent changes even when Git merges a file without a conflict.

## Task 2: Cross-feature persistence and editor behavior

- [x] Write failing tests for schema-1, RemoteLink-schema-2, and riser-schema-2 migration into a combined project.
- [x] Prove normal save/load and recovery retain RemoteLink config/zone overrides plus riser connections/layout.
- [x] Update schema and reconcile topology mutations so zone override values survive structural edits.
- [x] Confirm both RISER and REMOTELINK editors and their generation actions coexist, including invalidation/refresh after topology changes.
- [x] Run relevant session, topology, validation, RemoteLink, and editor integration tests; commit the riser merge.

## Task 3: Print-layout integration

- [x] Merge print-layout checkpoint with `--no-commit --no-ff`.
- [x] Reconcile injector and door-chart test changes with riser ordering and eight-port support.
- [x] Add behavioral regression coverage where the two change sets interact; run all chart/worksheet tests and commit the merge.

## Task 4: Release preparation and verification

- [x] Prepare version 1.4.0 (new riser feature on top of already tagged 1.3.0); document compatibility and draft release notes with the established formatting.
- [x] Run full pytest suite, import/compile checks, combined editor smoke test, and synthetic generation tests.
- [x] Independently review integration diff for dropped feature paths and data loss; fix and recheck actionable findings.
- [x] Verify all three original commits are ancestors and source branches are unchanged; commit the reviewed preparation and confirm a clean integration checkout. Report untested platform/release checks without claiming publication.

## Execution notes

- Separate worktree created successfully from `40b5dcc`; original worktrees clean.
- Both source branches use schema 2 for different fields. Combined schema 3 is intentional backward-read protection, not a loss of support for older projects.
- Integration is tightly coupled; controller owns session/UI merge. A bounded independent chart worker may resolve only the chart-specific files while that work proceeds.
- Source baselines: RemoteLink 251 passed/6 skipped; riser 355 passed/6 skipped. First combined suite: 443 passed/6 skipped before additional editor regressions.
- New regression initially failed because a schema-2 reader accepted a combined save. Schema 3 fixes that guard; all eight combined persistence/multi-export tests pass.
- Chart reconciliation decision: retain checkpoint `eaa8885` physical layout and fixed-scale pagination, not the competing 68% vertical-stack rewrite from the riser branch. Riser topology/worksheet changes remain intact; mixed-port mapping and per-RSP AUX labels must be retained.
- Independent persistence review reproduced three inherited riser risks. Five failing regressions pin exact legacy splitter-ID collisions, pre-drawing graph loss during save/recovery, last-edge resurrection, and lossy legacy refresh of canonical drafts. Fixes preserve canonical graph ownership and reject ID collisions before any mutation.
- UI merge resolved; 16 new editor tests cover both tabs/four actions, combined warnings, read-only RemoteLink preview, project-independent inspector, undo/redo, and narrow-window footer/sheet layout.
- Print reconciliation runs in a separate detached scratch worktree from `746b4eb`, merging `eaa8885`. Its verified merge commit will join the integration branch after the first merge, preserving both original histories.
- First integration merge `7465eac`: 464 tests passed, 6 private-corpus tests skipped; visual demo verified both editors and four output actions. Follow-up `b701a21` closes direct-import last-edge deletion and passes 44 focused tests plus scoped review.
- Chart scratch merge `233ce00`: checkpoint code retained (only anonymized/corrected comments differ), 13 new interaction tests, 68 chart/worksheet tests passed; controller independently reran 30 chart tests successfully. It merged into integration without conflicts.
- Combined merge `8684b5f`: all 482 tests passed, 6 private-corpus tests skipped, 5 existing PyMuPDF deprecation warnings. Independent final review then identified wiring-note dirty tracking and RemoteLink keypad edits clearing riser undo history; release preparation remains open until both regressions are fixed and rechecked.
- First macOS preview built successfully with version 1.4.0, both feature module families, and the unchanged RemoteLink template. Strict ad-hoc signature verification passes after copying without Finder metadata outside the synced workspace. The source app was visually tested with isolated fabricated data; packaged-runtime and Windows/RemoteLink/Excel checks remain separate gates.
- Final UI review regressions: five failed and the topology-history control passed before fixes; all six then passed, along with all 22 editor integration cases. Wiring-note clears compare compatibility fields as well as graph state; programming-only keypad edits use a separate callback that preserves riser history.
- Final corrected source: 488 passed, 6 private-corpus tests skipped, 5 existing PyMuPDF warnings in 95.83 seconds; compile and whitespace checks clean. Rebuilt preview outside the synced workspace, verified bundled version/modules/template, archived and re-extracted it, and verified strict deep ad-hoc signing again. No installed application, published release, or original branch was changed.
- Independent final scoped review: PASS, both UI blockers resolved. The temporary chart-merge checkout was removed after its commit became part of the integration history; all work remains recoverable from that history. Original RemoteLink/riser/checkpoint checkouts and the combined release-prep checkout are retained.
