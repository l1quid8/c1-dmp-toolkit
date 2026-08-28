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
- [ ] Merge riser with `--no-commit --no-ff`; reconcile overlapping app, editor, session, validation, and generation code without wholesale ours/theirs resolution.
- [ ] Preserve independent changes even when Git merges a file without a conflict.

## Task 2: Cross-feature persistence and editor behavior

- [x] Write failing tests for schema-1, RemoteLink-schema-2, and riser-schema-2 migration into a combined project.
- [x] Prove normal save/load and recovery retain RemoteLink config/zone overrides plus riser connections/layout.
- [x] Update schema and reconcile topology mutations so zone override values survive structural edits.
- [ ] Confirm both RISER and REMOTELINK editors and their generation actions coexist, including invalidation/refresh after topology changes.
- [ ] Run relevant session, topology, validation, RemoteLink, and editor integration tests; commit the riser merge.

## Task 3: Print-layout integration

- [ ] Merge print-layout checkpoint with `--no-commit --no-ff`.
- [ ] Reconcile injector and door-chart test changes with riser ordering and eight-port support.
- [ ] Add behavioral regression coverage where the two change sets interact; run all chart/worksheet tests and commit the merge.

## Task 4: Release preparation and verification

- [ ] Prepare version 1.4.0 (new riser feature on top of already tagged 1.3.0); document compatibility and draft release notes with the established formatting.
- [ ] Run full pytest suite, import/compile checks, combined editor smoke test, and synthetic generation tests.
- [ ] Independently review integration diff for dropped feature paths and data loss; fix and recheck actionable findings.
- [ ] Verify all three original commits are ancestors, working tree is clean, and source branches are unchanged. Report any untested platform/release checks without claiming publication.

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
