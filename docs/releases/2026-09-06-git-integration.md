# Local release integration — September 6, 2026

The v1.6.0 integration includes the committed RemoteLink configuration,
door-chart print-layout checkpoints, riser editor and subsequent local
v1.4.1–v1.6.0 fixes. The user requested a local commit, merge to main and
Git cleanup. Publishing or pushing a release is separate.

## Preserved follow-up

The older `feature/riser-editor` worktree contained six modified files.
They are preserved in commit `bc47e32` on that branch. These synchronization,
location-membership and regression-test edits have not been reconciled with
the substantially newer v1.6.0 implementation. During the subsequent branch
consolidation, the commit was preserved locally and on GitHub under the tag
`archive/riser-sync-followup-20260906`. The old branch and worktree were
retired; do not treat this archived work as merged into main.

## Artifact preservation

Before removing the merged release worktree, preserve its ignored `build`,
`dist`, `output` and `tmp` directories at:

`/Users/tylercaldwell/.dmp-doorchart/git-cleanup-20260906-jdB8Jy/`

These include visual samples and local builds, not source files to commit.
Keep the main checkout's input/output data, virtual environment, user tooling
state and the installed application unchanged. Retain release tags and remote
tags. The merged remote integration branch was retired as part of branch
consolidation. Main is the sole active branch; no application code changed.

The older riser worktree's build outputs are also preserved in the archive
directory as `riser-editor-build` and `riser-editor-dist`.
