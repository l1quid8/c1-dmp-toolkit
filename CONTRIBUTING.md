# Contributing to C1 DMP Toolkit

Start with the [README](README.md) for the app workflow and repository layout.
Keep changes focused on a clear problem, and describe the behavior someone can
verify in the app or generated files.

## Local development

Install Git and clone the repository outside OneDrive or other synced folders.
Run the following commands from the repository root. Development uses Python
3.13 and a local `.venv`; `requirements-dev.txt` installs the pinned application
dependencies and pytest.

### macOS

Install Homebrew first, then:

```sh
brew install python@3.13 python-tk@3.13 tesseract ghostscript
"$(brew --prefix python@3.13)/bin/python3.13" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python scripts/app.py
```

Use the Homebrew interpreter above so Python and Tk match the release build.
On Apple Silicon, the pinned `tkinterdnd2` package requires Tk 9; mixing it with
a Python distribution that bundles Tk 8.6 can crash at startup.

### Windows

Install Python 3.13 with its Python launcher and Tcl/Tk support. Install
Tesseract-OCR (the UB Mannheim build) and Ghostscript in their default locations,
as described in [the Windows build script](build_windows.bat).

In Command Prompt:

```bat
py -3.13 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python scripts/app.py
```

The app needs Tesseract and Ghostscript for OCR. Packaging is separate from
development: `build_mac.command` and `build_windows.bat` use their own build
environment under `~/.dmp-doorchart/` (Windows: `%USERPROFILE%\.dmp-doorchart\`).
Those scripts also replace the previously installed local build; close that app
before running them.

On macOS, the build script selects `python3.13` from `PATH` and reuses an
existing build environment. Before rebuilding with that environment, verify it
can start the required runtime:

```sh
"$HOME/.dmp-doorchart/venv/bin/python" -c "from tkinterdnd2 import TkinterDnD; root = TkinterDnD.Tk(); root.withdraw(); print('Tk', root.tk.call('info', 'patchlevel')); root.destroy()"
```

On Apple Silicon this should report Tk 9. If an existing build environment uses
an incompatible runtime, recreate that environment with the Homebrew interpreter
before packaging. A new source `.venv` does not change an older build environment.

## Testing

With the development environment active, run the full suite from the repository
root:

```sh
python -m pytest -q -ra
```

During development, narrow the run to the affected behavior, for example:

```sh
python -m pytest tests/test_zone_schedule.py -q -ra
```

- Put tests in `tests/test_*.py`, near related coverage. For behavior changes,
  add a regression that exercises the reported failure and checks the resulting
  domain state, saved project, generated output, or visible interaction.
- Prefer small fictional designs and generated fixtures. Use `tmp_path` for
  files and isolate preferences, recent projects, and update/network calls with
  `monkeypatch`; tests must not alter a developer's projects or settings.
- Run GUI tests with a working Tk display. Review the `-ra` skip summary:
  unavailable displays, platform-specific input events, and optional local
  fixtures can skip tests. Record those gaps in the PR instead of treating them
  as verified behavior.
- For GUI, OCR, or export changes, also exercise the affected workflow in the
  app with fictional data and inspect the output. Source tests do not replace a
  native packaged-app check. Documentation-only changes need link and command
  review, not new application tests.

## Public data hygiene

This repository, its issues, and PR attachments are public. Never commit or
attach real school or customer designs, worksheets, project files, screenshots,
panel user codes, credentials, or network settings. Use a fictional reproducer
and remove identifying details from logs, paths, metadata, and screenshots.

`.dmps` files and RemoteLink summary `.txt` files are readable records and may
contain configured user codes. A real encrypted RemoteLink export is not a safe
public fixture either. Keep job data in approved private storage; `input/` is
ignored deliberately, and its contents are not required public test fixtures.

## Branches and pull requests

1. Start a focused branch from current `main`, using a descriptive name such as
   `fix/riser-selection` or `chore/update-docs`.
2. Make the smallest cohesive change and update relevant user or developer
   documentation when behavior changes.
3. Review `git diff` and `git status` for accidental generated output, local
   environments, or sensitive data. Keep build products out of the commit.
4. Open a PR describing the problem, resulting behavior, and validation. Include
   commands, results, OS, skipped checks, and sanitized screenshots when useful.
5. Resolve review feedback and wait for the cross-platform CI checks before
   merging. Call out any native workflow you could not verify.

Use the issue templates for reproducible bugs or workflow improvements. Search
existing issues first and keep each report focused on one problem.

## Release checklist

- [ ] Update `VERSION` and add `docs/releases/vX.Y.Z.md` with user-facing changes,
      validation results, and any migration or compatibility notes.
- [ ] Run `python -m pytest -q -ra`, review skipped checks, and confirm the
      cross-platform CI checks pass on the intended release commit. Release
      builds run only after their test and version checks succeed.
- [ ] Build and smoke-test the native macOS and Windows packages using fictional
      inputs: launch, open/import, edit, save/reopen, and generate the affected
      outputs. Include drag-and-drop and OCR when packaging or dependencies
      change. Record platforms and any outstanding checks in the release notes.
- [ ] Commit the release changes and verify the intended tag is exactly `v`
      followed by the contents of `VERSION`, for example `1.7.1` → `v1.7.1`.
- [ ] Follow the [README release steps](README.md#releasing) to push that version
      tag. Confirm both packaged assets and the release notes are available on
      the published release. The workflow generates GitHub notes automatically;
      copy or link the curated `docs/releases/vX.Y.Z.md` notes into the published
      release so users also see the validation and compatibility details.
- [ ] Preserve published tags and version history. If a release needs a fix,
      publish a new version; do not move, delete, or rewrite a published tag.
