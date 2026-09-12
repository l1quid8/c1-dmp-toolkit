# Project priorities

Reviewed September 12, 2026. This is a proposed backlog, not a promise that a
feature is available. Effort estimates are relative: **S** is a focused change,
**M** spans several components, **L** needs a separate design and migration plan.

## First: repository and release hygiene

The September cleanup introduces macOS/Windows PR and main-branch tests, runs
the same suite before release builds, pins the developer test runner, ignores
local tooling output, and adds contribution and issue/PR templates.

Remaining work, in order:

1. **Establish green hosted checks, then require them on `main`.** The repository
   had no branch protection or rulesets at review time. Choose required check
   names after the first macOS/Windows CI run; keep native packaged-app smoke
   testing in the release checklist. GUI runtime checks must fail if Tk cannot
   start rather than allowing the editor suite to silently skip.
2. **Ship the fixes already on `main` in a new version.** The latest download is
   `v1.7.0` (September 6); subsequent commits fix mixed expander allocation and
   worksheet output, improve riser layout/full-screen controls, and order MSP
   bus terminals. Review the [release comparison](https://github.com/l1quid8/c1-dmp-toolkit/compare/v1.7.0...main),
   run native macOS/Windows acceptance, and record release notes before tagging.
   The README demo was recorded from September 11 `main`; check that the next
   packaged release matches the demonstrated behavior. Published tags stay fixed.
3. **Enable and triage dependency and secret alerts.** Dependabot alerts/security
   updates, secret scanning, and push protection were disabled at review time.
   Review initial findings privately; test dependency changes on both platforms.
   Routine dependency upgrades should be small PRs, with no automatic merges.
4. **Reconcile preserved work before retiring it.** The local
   `feature/remotelink-account-address` worktree contains unfinished changes.
   The `archive/riser-sync-followup-20260906` tag retains unmerged synchronization
   work. Classify each archived change as already covered, worth porting with a
   regression test, or obsolete; keep its reference until that review is recorded.
   See the [existing preservation record](releases/2026-09-06-git-integration.md).
5. **Decide distribution/licensing policy.** The public repository has no license
   file. The project owner should choose the intended permissions before a
   license or third-party contribution policy is added.

There were no open issues or pull requests and only one remote branch (`main`)
at review time. There is no stale remote-branch backlog to delete. Preserve
published releases and archive tags; add focused issues from the priorities
below as they are selected for work.

## Then: application improvements

| Priority | Improvement | Why it matters | Effort | Done when |
|---|---|---|---|---|
| P0 | Snapshot exports and track the revision actually generated | Editing while a worksheet is generated can make an older export appear current | S–M | Exports use a captured design/configuration and starting edit epoch; completion cannot update another project's state; an edit during generation leaves the worksheet stale |
| P1 | Show recovery-save health | A failed recovery write is currently suppressed, so crash protection can fail without notice | S–M | Failed writes produce a persistent, actionable status; slow storage does not block editing; successful retry clears the warning |
| P1 | Verify updater payloads and preserve rollback | Updating should recover cleanly from corrupt downloads, wrong bundles, failed replacements, or launch failures | M | Bundle/version and integrity checks precede replacement; failure paths are tested on both platforms; the previous build survives until a successful startup |
| P2 | Generate a coordinated deliverable set | Users currently sequence worksheet, door chart and riser generation themselves | M | One action generates selected deliverables from the same snapshot and records their project/revision association; reopened projects show which outputs are current |
| P2 | Extend undo to hardware changes | Hardware additions/removals change shared zones and wiring and do not share canvas undo behavior | L | Undo restores device, zones, wiring and layout together across tabs; redo and save/reopen preserve the result |

### Evidence and scope

- **Export consistency:** [`App._generate_worksheet`](../scripts/app.py) captures
  the live `session.design`, and its completion callback sets `_ws_epoch` from
  the editor's current epoch. A write-free reproduction started at epoch 7,
  edited to 8 during generation, and completed with `_ws_epoch == 8` and a false
  stale check. Riser generation already uses `copy.deepcopy`; use that precedent
  and test edit-during-export and project-switch behavior. Audit RemoteLink's
  asynchronous generation at the same time.
- **Recovery:** [`EditorFrame._write_recovery`](../scripts/editor_frame.py)
  invokes disk writing on the Tk callback and suppresses exceptions.
  [`session.write_recovery`](../scripts/session.py) already writes atomically;
  keep that protection while improving status and background I/O.
- **Updates:** [`updater.py`](../scripts/updater.py) downloads and selects a
  bundle, then generates platform-specific replacement scripts. Add explicit
  payload verification and tests around backup retention and failure recovery.
- **Deliverable sets:** `_latest_worksheet_for_school` in
  [`app.py`](../scripts/app.py) searches by school slug. Saved
  [`Session`](../scripts/session.py) records do not retain an export manifest.
  Account for multiple projects at the same school and reopening old revisions.
- **Undo:** The [documented hardware workflow](../README.md#hardware-and-cable-callouts-in-riser)
  commits changes immediately and advises saving a copy before major changes.
  Expand undo through shared project transactions, with explicit cascade tests.

## Keep refactoring tied to these outcomes

`app.py` and `riser_editor.py` are the main maintenance hotspots (roughly 2,300
and 3,000 lines at review time). Extract an export service while implementing
export consistency/deliverable sets, and shared transactions while extending
undo. Avoid a broad file reshuffle before the new CI establishes a baseline.
Existing design plans under `docs/superpowers/` are historical implementation
records; use this roadmap for current priorities and retain incomplete native
acceptance items until there is evidence to close them.
