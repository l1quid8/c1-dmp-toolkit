# Data Safety and Worksheet Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six bugs from the 2026-10-06 review: projects that get overwritten or locked, exports that run after a failed save, door charts built from the wrong or out-of-date worksheet, unsafe updates, and silent crash-protection failures. Then ship the fixes as 1.7.3 in the native macOS app.

**Architecture:** Most fixes are small guards at existing boundaries (`App` import/generate/update flows in `scripts/app.py`, `EditorFrame` save model, `session.py` persistence). The largest change records which worksheet a project generated. The `.dmps` file stores that worksheet's path plus a fingerprint (SHA-256) of the design it was written from. A door chart can then tell "current", "stale", and "unknown source" apart, even after the app is closed and reopened.

**Tech Stack:** Python 3.13, customtkinter/Tk 9, pytest 9, PyInstaller (`build_mac.command`), GitHub Actions.

**Spec:** No separate spec doc. The requirements are the 2026-10-06 group review findings, summarized under **Background** below.

## Background (the bugs, in plain terms)

1. **PDF import overwrites a saved project.** Re-importing a PDF for a school that already has a saved project replaces that project on Save. `_start_parse` creates the `Session` without `path=`, so `save_session` falls back to `default_session_path` (school name only). Worksheet and BOM imports already use `unique_session_path`.
2. **Windows crash-recovery lockout.** `_open_session_path` formats the recovery time with `%-d`/`%-I`. Windows `strftime` raises `ValueError` for those flags, so the project can't open until someone deletes the `.recovery` file by hand. The "Saved 8:19 AM" badge in `editor_frame.py` has the same problem, but there it is suppressed and only the time is lost. Related: a damaged recovery file raises an uncaught `JSONDecodeError`, which causes the same lockout.
3. **Failed save still generates.** Worksheet, riser, and RemoteLink exports ask "Save before generating?" but ignore `editor.save()` returning `False`.
4. **Door chart from the wrong or stale worksheet.** The staleness check is an in-memory edit counter (`_ws_epoch`), so it forgets everything when the project is reopened. PDF and worksheet projects also find "the" worksheet by school name, so another project's file can be picked.
5. **Update stays armed after Cancel.** `updater.apply_update` launches the detached swap script *before* `_quit_for_update` asks to save. If the user clicks Cancel, the swap still runs on the next quit. Downloads are also not checked against the SHA-256 that GitHub publishes for each release asset (confirmed: the v1.7.2 assets have a `digest` field). If the download fails, the error message crashes (`NameError`: `exc` is unbound inside the deferred lambda) instead of showing.
6. **Crash protection fails silently.** `EditorFrame._write_recovery` suppresses every exception.

## Global Constraints

- Python 3.13. No new dependencies. `updater.py` stays stdlib-only.
- Must work on macOS **and** Windows. Never use `strftime` flags `%-x` or `%#x`.
- `.dmps` stays readable by older 1.7.x apps: **add keys only, do not bump `SCHEMA_VERSION`**. Old apps ignore unknown keys.
- On-screen wording is for field technicians: plain language, no jargon.
- Run tests with `venv/bin/python -m pytest` from the repo root. Baseline before any change: `venv/bin/python -m pytest -q --no-gui` → **793 passed, 260 skipped**.
- Commit messages follow repo style: imperative, sentence case, no prefix (e.g. `Guard RemoteLink exports against unsupported zone text`).
- **Do not push, tag, open PRs, or publish releases without the user's explicit OK.** Local commits on the feature branch are fine.
- `AGENTS.md`: after changing the desktop app, run `./build_mac.command` and confirm `~/Applications/C1 DMP Toolkit.app` opens with the updated UI before reporting done. Quit the app first; the script refuses to build while it's running.

## Review Focus

Most likely to bite a real user. Each line has a test in the owning task.

1. Re-importing a PDF when the school already has a saved project **and** a leftover crash-recovery file. Expected: a new `(2)` project; neither existing file is touched. (Task 2)
2. Reopening a project whose recorded worksheet was moved or deleted. Expected: fall back to the newest worksheet for that school, *with* a confirmation, never silently. (Task 4)
3. The project file can't be written when the worksheet record is saved (locked or read-only). Expected: worksheet generation still succeeds, and the record stays in memory for the next save. (Task 4)
4. The crash-recovery file is damaged. Expected: the last saved version opens with a notice. No lockout. (Task 1)
5. Update downloaded while there are unsaved edits, then the user cancels the save prompt (or a generation is running). Expected: nothing is queued, temp files are removed, and quitting later does not swap the app. (Task 6)

## File Map

| File | Change |
|---|---|
| `scripts/timefmt.py` | **Create.** Portable "8:19 AM" / "Jul 1, 1:38 PM" formatting |
| `scripts/session.py` | `load_recovery` errors → `SessionLoadError`; `set_aside_recovery`; `Session.worksheet_path`/`worksheet_fingerprint`; `worksheet_fingerprint()`; `record_worksheet()` |
| `scripts/app.py` | PDF import path; recovery prompt; save guard; door-chart source + confirmation dialog; worksheet chaining; update staging |
| `scripts/editor_frame.py` | Saved badge time; crash-protection health status |
| `scripts/updater.py` | Asset digest, `verify_download`, `stage_update`/`launch_staged`/`discard_staged` (replaces `apply_update`) |
| `.github/workflows/ci.yml` | Run window-based tests too |
| `docs/ROADMAP.md`, `docs/releases/v1.7.3.md`, `VERSION` | Docs and version |
| Tests | New: `test_timefmt.py`, `test_generation_save_guard.py`, `test_worksheet_provenance.py`, `test_updater_safety.py`, `test_recovery_health.py`. Updated: `test_project_input_routing.py`, `test_session.py`, `test_create_new_project_workflow.py` |

---

### Task 0: Branch and baseline

- [ ] **Step 1:** `git switch main && git pull --ff-only && git switch -c fix/data-safety-1.7.3`. (`git pull` only reads from the remote; it does not push.)
- [ ] **Step 2:** `venv/bin/python -m pytest -q --no-gui`. Expected: `793 passed, 260 skipped`. If that differs, stop and report before changing anything.

---

### Task 1: Windows-safe dates, and damaged recovery never locks a project

**Files:**
- Create: `scripts/timefmt.py`, `tests/test_timefmt.py`
- Modify: `scripts/app.py:960-973` (`_recent_meta`), `scripts/app.py:1495-1515` (`_open_session_path`), import block at `scripts/app.py:34-52`
- Modify: `scripts/editor_frame.py:451-456` (`_notify_status`)
- Modify: `scripts/session.py:580-585` (`load_recovery`), add `set_aside_recovery`
- Test: `tests/test_session.py`, `tests/test_project_input_routing.py:164-198`

**Interfaces:**
- Produces: `timefmt.clock_time(when: datetime) -> str`, `timefmt.short_datetime(when: datetime) -> str`, `session.set_aside_recovery(session_path: Path) -> Path | None`. `session.load_recovery` now raises `SessionLoadError` for unreadable files.

- [ ] **Step 1: Write failing tests**

`tests/test_timefmt.py`:
```python
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from timefmt import clock_time, short_datetime


def test_clock_time_has_no_leading_zero():
    assert clock_time(datetime(2026, 10, 6, 8, 19)) == "8:19 AM"
    assert clock_time(datetime(2026, 10, 6, 0, 5)) == "12:05 AM"
    assert clock_time(datetime(2026, 10, 6, 13, 38)) == "1:38 PM"


def test_short_datetime():
    assert short_datetime(datetime(2026, 7, 1, 13, 38)) == "Jul 1, 1:38 PM"


def test_no_platform_specific_strftime_flags_in_app_code():
    # Dash flags crash strftime on Windows; hash flags crash it on macOS.
    offenders = []
    for path in (ROOT / "scripts").rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"%[-#][a-zA-Z]", line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert offenders == []
```

Append to `tests/test_session.py`:
```python
def test_unreadable_recovery_raises_load_error_and_can_be_set_aside(tmp_path):
    from session import SessionLoadError, load_recovery, recovery_path, set_aside_recovery
    target = tmp_path / "School.dmps"
    recovery_path(target).write_text("{not json", encoding="utf-8")

    with pytest.raises(SessionLoadError):
        load_recovery(target)

    moved = set_aside_recovery(target)
    assert moved == tmp_path / "School.dmps.recovery.unreadable"
    assert moved.read_text(encoding="utf-8") == "{not json"
    assert not recovery_path(target).exists()
```

In `tests/test_project_input_routing.py`, add `from datetime import datetime` to the imports. In `test_dmps_route_uses_normal_or_recovery_loader`, replace the fake `pending_recovery` and the `askyesno` patch for the recovery case:
```python
    if recovery_choice is not None:
        project.design.site_info.school_code = "RECOVERED"
        write_recovery(project)
        monkeypatch.setattr(app_module, "pending_recovery",
                            lambda target: datetime(2026, 9, 12, 12, 0))
    else:
        monkeypatch.setattr(app_module.messagebox, "askyesno",
                            lambda *args: pytest.fail("prompted without recovery"))
    prompts = []
    if recovery_choice is not None:
        monkeypatch.setattr(app_module.messagebox, "askyesno",
                            lambda title, message: prompts.append(message) or recovery_choice)
```
and after `controller._start_input(path)` add:
```python
    if recovery_choice is not None:
        assert "Sep 12, 12:00 PM" in prompts[0]
```
Add a new test below it:
```python
def test_damaged_recovery_opens_last_saved_version(controller, boundaries, monkeypatch, tmp_path):
    # A truncated backup must not lock the project: open the saved file, keep the backup aside.
    project = create_blank_session(SiteInfo(school_name="SAVED SCHOOL"))
    path = save_session(project, tmp_path / "existing.dmps")
    session_module.recovery_path(path).write_text("{truncated", encoding="utf-8")
    monkeypatch.setattr(app_module, "pending_recovery",
                        lambda target: datetime(2026, 9, 12, 12, 0))
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args: True)
    warnings = []
    monkeypatch.setattr(app_module.messagebox, "showwarning",
                        lambda title, message: warnings.append(title))

    controller._start_input(path)

    loaded, _tab = controller.entered[0]
    assert loaded.path == path and not controller.errors
    assert warnings == ["Couldn't recover unsaved changes"]
    assert not session_module.recovery_path(path).exists()
    assert (tmp_path / "existing.dmps.recovery.unreadable").exists()
```

- [ ] **Step 2: Run tests and confirm they fail**

`venv/bin/python -m pytest -q --no-gui tests/test_timefmt.py tests/test_session.py tests/test_project_input_routing.py`
Expected: `ModuleNotFoundError: timefmt`. The flag scan lists `scripts/app.py:964`, `scripts/app.py:1501`, `scripts/editor_frame.py:455`. On macOS the routing test fails with `ValueError`/`JSONDecodeError`, or because the `"Sep 12"` assertion doesn't match.

- [ ] **Step 3: Implement**

`scripts/timefmt.py`:
```python
"""Human-readable times that work on every platform.

strftime's "no leading zero" flags exist only on macOS/Linux; Windows raises
ValueError for them. Build those pieces by hand instead."""
from datetime import datetime


def clock_time(when: datetime) -> str:
    """'8:19 AM'"""
    return f"{when.hour % 12 or 12}:{when:%M} {when:%p}"


def short_datetime(when: datetime) -> str:
    """'Jul 1, 1:38 PM'"""
    return f"{when:%b} {when.day}, {clock_time(when)}"
```

`scripts/session.py`: replace `load_recovery` and add `set_aside_recovery` below it:
```python
def load_recovery(session_path: Path) -> Session:
    rec = recovery_path(session_path)
    try:
        d = json.loads(rec.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionLoadError(f"Couldn't read {rec.name}: {exc}") from exc
    try:
        return _session_from_dict(d, session_path)
    except SessionLoadError:
        raise
    except Exception as exc:
        raise SessionLoadError(f"{rec.name} is damaged: {exc}") from exc


def set_aside_recovery(session_path: Path) -> Path | None:
    """Rename an unreadable recovery file so it stops being offered on open,
    without destroying it. Returns the new path, or None if nothing moved."""
    rec = recovery_path(session_path)
    target = rec.with_name(rec.name + ".unreadable")
    try:
        os.replace(rec, target)
    except OSError:
        return None
    return target
```

`scripts/app.py`: add `set_aside_recovery` to the `from session import (...)` list, and add `from timefmt import short_datetime` after the `from session import` block. Replace `_recent_meta`:
```python
    @staticmethod
    def _recent_meta(summary) -> str:
        """"Saved Jul 1, 1:38 PM · from northview.pdf" — the ISO timestamp read
        as machine output, so it's rendered the way a person would say it."""
        saved = summary.saved_at or ""
        with contextlib.suppress(ValueError, TypeError):
            saved = short_datetime(datetime.fromisoformat(summary.saved_at))
        parts = [f"Saved {saved}"] if saved else []
        if summary.source_name:
            parts.append(f"from {summary.source_name}")
        return "  ·  ".join(parts)
```
Replace the `try:` block in `_open_session_path`:
```python
        try:
            session = None
            rec_time = pending_recovery(path)
            if rec_time and messagebox.askyesno(
                "Recover unsaved changes?",
                f"Unsaved changes from {short_datetime(rec_time)} "
                "were found for this project (the app may have closed "
                "unexpectedly).\n\nRecover them?",
            ):
                try:
                    session = load_recovery(path)
                except SessionLoadError as exc:
                    # A damaged backup must never lock the project: keep it
                    # aside for inspection and open the last saved version.
                    set_aside_recovery(path)
                    messagebox.showwarning(
                        "Couldn't recover unsaved changes",
                        f"{exc}\n\nOpening the last saved version instead.",
                    )
            if session is None:
                clear_recovery(path)
                session = load_session(path)
        except SessionLoadError as exc:
            self._show_parse_error(exc, title="Couldn't open project")
            return
```

`scripts/editor_frame.py`: add `from timefmt import clock_time` with the other local imports, and in `_notify_status` change the formatting line to:
```python
                when = clock_time(datetime.fromisoformat(self.session.saved_at))
```

- [ ] **Step 4: Run tests and confirm they pass**

`venv/bin/python -m pytest -q --no-gui tests/test_timefmt.py tests/test_session.py tests/test_project_input_routing.py` → all pass.

- [ ] **Step 5: Commit**
```bash
git add scripts/timefmt.py scripts/session.py scripts/app.py scripts/editor_frame.py tests/test_timefmt.py tests/test_session.py tests/test_project_input_routing.py
git commit -m "Keep crash recovery from locking projects on Windows"
```

---

### Task 2: PDF import starts a new project file

**Files:**
- Modify: `scripts/app.py:1332-1333` (`_start_parse.on_done`)
- Test: `tests/test_project_input_routing.py`

**Interfaces:**
- Consumes: existing `session.unique_session_path(design) -> Path` (already imported in `app.py`).

- [ ] **Step 1: Write the failing test** (append to `tests/test_project_input_routing.py`)
```python
def test_pdf_import_never_overwrites_saved_project_for_same_school(controller, boundaries, tmp_path):
    # A revised design PDF must not replace the field-corrected project, or its
    # pending crash-recovery slot, when saved.
    existing = create_blank_session(SiteInfo(school_name="PDF SCHOOL"))
    old = save_session(existing)
    old_text = old.read_text()
    reserved = tmp_path / "Sessions" / "PDF_SCHOOL (2).dmps"
    session_module.recovery_path(reserved).write_text("{}", encoding="utf-8")
    boundaries["build_dmp_design_from_pdf"].side_effect = None
    boundaries["build_dmp_design_from_pdf"].return_value = create_blank_session(
        SiteInfo(school_name="PDF SCHOOL")).design

    controller._start_input(tmp_path / "revised.pdf")

    project, _tab = controller.entered[0]
    assert project.path == tmp_path / "Sessions" / "PDF_SCHOOL (3).dmps"
    save_session(project)
    assert old.read_text() == old_text
    assert session_module.recovery_path(reserved).read_text(encoding="utf-8") == "{}"
```

- [ ] **Step 2: Run it and confirm it fails**

`venv/bin/python -m pytest -q --no-gui tests/test_project_input_routing.py::test_pdf_import_never_overwrites_saved_project_for_same_school`
Expected: FAIL. `project.path` is `None`.

- [ ] **Step 3: Implement.** In `_start_parse.on_done`:
```python
            # A PDF import starts a NEW project: never reuse the school-name
            # slot, which may hold a field-corrected project for the same site.
            self._enter_editor(Session(design=design, source_kind="pdf",
                                       source_name=pdf_path.name,
                                       path=unique_session_path(design)))
```

- [ ] **Step 4:** Rerun the routing test file → all pass.

- [ ] **Step 5: Commit**: `git commit -am "Give PDF imports their own project file"`. Stage the test file if it shows as untracked; it shouldn't.

---

### Task 3: A failed save stops generation

**Files:**
- Modify: `scripts/app.py`: add `_save_before_generating` next to `_set_generating` (~line 1579); call it from `_generate_worksheet` (~1594), `_generate_riser` (~1719), `_generate_remotelink` (~1844)
- Create: `tests/test_generation_save_guard.py`

**Interfaces:**
- Produces: `App._save_before_generating(self, prompt: str) -> bool`. `False` means stop.

- [ ] **Step 1: Write failing tests** in `tests/test_generation_save_guard.py`
```python
"""Choosing Save and having it fail must stop the export, not run it anyway."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app as app_module
import session as session_module
from parse_dmp_worksheet import SiteInfo
from session import create_blank_session


class Reached(Exception):
    """Raised by a patched boundary to prove the export got past the save prompt."""


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(session_module, "output_dir", lambda: tmp_path)
    a = object.__new__(app_module.App)
    a.state, a._generating = "editing", None
    a.session = create_blank_session(SiteInfo(school_name="GUARD SCHOOL"))
    a.output_dir = tmp_path
    a.pdf_path = a.dmp_path = None
    a.editor = SimpleNamespace(
        dirty=True, save=Mock(return_value=False), edit_epoch=1,
        generation_allowed=lambda: True, flush_design_refresh=lambda: None,
        show_issues_dialog=Mock(), tabs=SimpleNamespace(set=lambda tab: None))
    a._set_generating = Mock()
    a._run_async = Mock()
    return a


@pytest.mark.parametrize("answer, saved, proceeds", [
    (True, False, False),   # chose Save, save failed -> stop
    (True, True, True),     # chose Save, saved -> continue
    (False, None, True),    # chose not to save -> continue from the screen
])
def test_worksheet_respects_failed_save(app, monkeypatch, answer, saved, proceeds):
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: answer)
    app.editor.save.return_value = saved
    app._generate_worksheet()
    assert app.editor.show_issues_dialog.called is proceeds
    assert app.editor.save.called is answer


@pytest.mark.parametrize("saved", [False, True])
def test_riser_respects_failed_save(app, monkeypatch, saved):
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    app.editor.save.return_value = saved
    app._choose_riser_outputs = lambda: ["11x17"]
    sync = Mock(side_effect=Reached)
    monkeypatch.setattr(app_module, "sync_riser_document", sync)
    if saved:
        with pytest.raises(Reached):
            app._generate_riser()
    else:
        app._generate_riser()
        sync.assert_not_called()
        app._set_generating.assert_not_called()


@pytest.mark.parametrize("saved", [False, True])
def test_remotelink_respects_failed_save(app, monkeypatch, saved):
    monkeypatch.setattr(app_module, "remotelink_readiness_issues", lambda *a: [])
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    app.editor.save.return_value = saved
    app._show_remotelink_dialog = Mock()
    app._generate_remotelink()
    assert app._show_remotelink_dialog.called is saved
```

- [ ] **Step 2:** `venv/bin/python -m pytest -q --no-gui tests/test_generation_save_guard.py`. Expected: the `saved=False` cases FAIL because the export continues.

- [ ] **Step 3: Implement.** Add to `App`:
```python
    def _save_before_generating(self, prompt: str) -> bool:
        """Offer to save unsaved edits before an export.

        Returns False when the export must stop: the user chose Save and it
        failed (the editor has already said why). Choosing not to save still
        generates from what's on screen."""
        if not self.editor.dirty:
            return True
        if not messagebox.askyesno("Save project?", prompt):
            return True
        return self.editor.save()
```
Replace each existing `if self.editor.dirty and messagebox.askyesno(...): self.editor.save()` block:
- `_generate_worksheet`: `if not self._save_before_generating("Save the project before generating?"): return`
- `_generate_riser`: `if not self._save_before_generating("Save the project before generating the riser?"): return`
- `_generate_remotelink`: `if not self._save_before_generating("Save the project before generating?"): return`

- [ ] **Step 4:** Rerun the new file and `tests/test_create_new_project_workflow.py` → all pass.

- [ ] **Step 5: Commit**: `git add scripts/app.py tests/test_generation_save_guard.py && git commit -m "Stop exports when the chosen save fails"`

---

### Task 4: Remember which worksheet a project made, across reopen

**Files:**
- Modify: `scripts/session.py`: `Session` fields, `_session_to_dict`, `_session_from_dict`, new `worksheet_fingerprint`, new `record_worksheet`
- Modify: `scripts/app.py`: `_start_load_worksheet.on_done` (~1403), `_enter_editor` (~1430-1475), `_latest_worksheet_path` (~1564), `_generate_worksheet` (~1599-1645), `_generate_door_chart` (~1765-1820), remove every `_ws_epoch` (lines ~279, 1445, 1465, 1636, 1780, 2062)
- Create: `tests/test_worksheet_provenance.py`
- Modify: `tests/test_create_new_project_workflow.py:373, 584`

**Interfaces:**
- Produces:
  - `Session.worksheet_path: Optional[str]`, `Session.worksheet_fingerprint: Optional[str]`, both `field(default=None, compare=False)`
  - `session.worksheet_fingerprint(design: DMPDesign) -> str` (hex SHA-256; does not mutate `design`)
  - `session.record_worksheet(session: Session, worksheet: Path, fingerprint: str, *, update_file: bool) -> None`
  - `App._door_chart_source(self) -> tuple[Path | None, str]`. Status is `"current"`, `"stale"`, or `"unknown"`.
  - `App._confirm_chart_source(self, src: Path, status: str) -> str`. Returns `"use"` or `"cancel"` (Task 5 adds `"regenerate"`).
  - `App._generate_worksheet(self, *, then=None)`. The `then` parameter is added in Task 5; leave the signature unchanged here.

**Design notes (read before coding):**
- **Fingerprint** = SHA-256 of `json.dumps(design_to_dict(copy), sort_keys=True)` with `"riser_document"` removed, after the same `project_legacy_topology` + `sync_master_zones` that worksheet generation applies. Moving riser symbols doesn't change the worksheet, so it must not mark the worksheet stale. Verified 2026-10-06 against two real worksheets: the hash is identical before save, after save, after reopen, and after a second save/reopen.
- **`compare=False`** on the two new fields: `tests/test_create_new_project_workflow.py` asserts `controller.session == before` after exports to prove exports don't change the project's content. Recording the worksheet is bookkeeping, not content.
- **`record_worksheet(update_file=True)`** patches only the `"worksheet"` key in the existing `.dmps` (atomic write, `saved_at` unchanged). Without this, the common "save → generate → close" sequence would lose the record, because the editor isn't dirty and nothing prompts a save. App passes `update_file=not self.editor.dirty`. **Never patch the file while dirty:** that would bump the `.dmps` mtime past the `.recovery` file's, and `pending_recovery` would then stop offering the unsaved work after a crash. When dirty, the record rides along with the next save or recovery snapshot.
- **Lookup order** in `_door_chart_source`: (1) recorded path exists → compare fingerprints → `current`/`stale`; (2) `manual`/`bom` projects → `(None, "unknown")`, same as today; (3) otherwise the newest `{slug}_dmp_revN.xlsx` → `"unknown"`, which needs a confirmation. `_latest_worksheet_path()` becomes a thin wrapper returning only the path. Keep the name: `tests/test_app_recent_projects.py:22` monkeypatches it.

- [ ] **Step 1: Write failing tests** in `tests/test_worksheet_provenance.py`
```python
"""A door chart must know which worksheet this project made, and whether the
project changed since, even after the app is closed and reopened."""
import copy
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app as app_module
import session as session_module
from parse_dmp_worksheet import SiteInfo
from riser_model import RiserElement
from session import (create_blank_session, load_session, record_worksheet,
                     save_session, worksheet_fingerprint)


def _project():
    return create_blank_session(SiteInfo(school_name="TRACK SCHOOL"))


def test_fingerprint_survives_save_and_reopen_without_mutating(tmp_path):
    project = _project()
    snapshot = copy.deepcopy(project.design)
    before = worksheet_fingerprint(project.design)
    assert project.design == snapshot
    loaded = load_session(save_session(project, tmp_path / "track.dmps"))
    assert worksheet_fingerprint(loaded.design) == before


def test_fingerprint_tracks_worksheet_content_not_riser_layout():
    project = _project()
    before = worksheet_fingerprint(project.design)
    project.design.riser_document.elements["device:MSP"] = RiserElement(
        "device:MSP", "device", "MSP", 1.0, 2.0, 90.0, 110.0, manual=True)
    assert worksheet_fingerprint(project.design) == before
    project.design.site_info.school_code = "EDITED"
    assert worksheet_fingerprint(project.design) != before


def test_record_round_trips_without_counting_as_a_save(tmp_path):
    project = _project()
    path = save_session(project, tmp_path / "track.dmps")
    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev3.xlsx"
    record_worksheet(project, sheet, "abc123", update_file=True)
    loaded = load_session(path)
    assert (loaded.worksheet_path, loaded.worksheet_fingerprint) == (str(sheet), "abc123")
    assert loaded.saved_at == project.saved_at
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("worksheet")                      # a project saved by 1.7.2
    path.write_text(json.dumps(data), encoding="utf-8")
    legacy = load_session(path)
    assert legacy.worksheet_path is None and legacy.worksheet_fingerprint is None


def test_record_while_dirty_leaves_disk_alone(tmp_path):
    project = _project()
    path = save_session(project, tmp_path / "track.dmps")
    text = path.read_text(encoding="utf-8")
    record_worksheet(project, tmp_path / "x.xlsx", "abc", update_file=False)
    assert path.read_text(encoding="utf-8") == text
    assert project.worksheet_fingerprint == "abc"


def test_unwritable_project_file_does_not_break_recording(tmp_path, monkeypatch):
    project = _project()
    save_session(project, tmp_path / "track.dmps")
    def locked(path, text):
        raise PermissionError("locked")
    monkeypatch.setattr(session_module, "_atomic_write", locked)
    record_worksheet(project, tmp_path / "x.xlsx", "abc", update_file=True)
    assert project.worksheet_fingerprint == "abc"


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "output_dir", lambda: tmp_path)
    c = object.__new__(app_module.App)
    c.session = _project()
    c.output_dir = tmp_path
    c.state, c._generating = "editing", None
    c.pdf_path = c.dmp_path = c.door_chart_path = None
    c._redirector = io.StringIO()
    c.root = SimpleNamespace(after=lambda ms, fn: fn())
    c.editor = SimpleNamespace(dirty=False, edit_epoch=0, generation_allowed=lambda: True)
    c.confirmations, c.started = [], []
    c._confirm_chart_source = lambda src, status: (
        c.confirmations.append((src.name, status)), "cancel")[1]
    c._set_generating = lambda which: c.started.append(which)
    c._run_async = lambda work, done, error: None
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **k: None)
    return c


def _record_current(c, sheet):
    sheet.touch()
    record_worksheet(c.session, sheet, worksheet_fingerprint(c.session.design), update_file=False)


def test_recorded_worksheet_is_used_after_reopen_without_prompt(controller, tmp_path):
    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev3.xlsx"
    _record_current(controller, sheet)
    controller.session = load_session(save_session(controller.session, tmp_path / "track.dmps"))

    assert controller._door_chart_source() == (sheet, "current")
    controller._generate_door_chart()
    assert controller.confirmations == [] and controller.started == ["chart"]


def test_edits_after_generation_are_stale_after_reopen(controller, tmp_path):
    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev3.xlsx"
    _record_current(controller, sheet)
    controller.session.design.site_info.school_code = "EDITED AFTER REV 3"
    controller.session = load_session(save_session(controller.session, tmp_path / "track.dmps"))

    controller._generate_door_chart()
    assert controller.confirmations == [(sheet.name, "stale")]
    assert controller.started == []


@pytest.mark.parametrize("source_kind", ["pdf", "xlsx"])
def test_worksheet_found_only_by_school_name_needs_confirmation(controller, tmp_path, source_kind):
    controller.session.source_kind = source_kind
    other = tmp_path / "TRACK_SCHOOL_dmp_rev9.xlsx"
    other.touch()

    controller._generate_door_chart()
    assert controller.confirmations == [(other.name, "unknown")]
    assert controller.started == []


def test_moved_worksheet_falls_back_with_confirmation(controller, tmp_path):
    record_worksheet(controller.session, tmp_path / "moved-away.xlsx",
                     worksheet_fingerprint(controller.session.design), update_file=False)
    controller.session.source_kind = "pdf"
    fallback = tmp_path / "TRACK_SCHOOL_dmp_rev2.xlsx"
    fallback.touch()
    assert controller._door_chart_source() == (fallback, "unknown")


def test_generating_worksheet_records_it_on_disk(controller, tmp_path, monkeypatch):
    path = save_session(controller.session, tmp_path / "track.dmps")
    controller.editor.show_issues_dialog = lambda proceed, **kw: proceed()
    controller._run_async = lambda work, done, error: done(work())
    controller._show_toast = lambda *a, **k: None
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})
    monkeypatch.setattr(app_module, "save_prefs", lambda prefs: None)
    monkeypatch.setattr(app_module, "write_dmp_xlsx",
                        lambda design, template, out: out.touch())

    controller._generate_worksheet()

    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev1.xlsx"
    assert controller.session.worksheet_path == str(sheet)
    assert controller.session.worksheet_fingerprint == worksheet_fingerprint(controller.session.design)
    assert load_session(path).worksheet_path == str(sheet)   # survives closing without saving
```

- [ ] **Step 2:** `venv/bin/python -m pytest -q --no-gui tests/test_worksheet_provenance.py` → FAIL (`ImportError: record_worksheet`).

- [ ] **Step 3: Implement `session.py`**

Add `import copy`, `import contextlib`, `import hashlib` to the imports if missing. Extend `Session` (after `topology_confirmed`):
```python
    # Which worksheet a door chart should be built from, and a fingerprint of
    # the design it was written from. Bookkeeping, not project content, so
    # it's excluded from equality (exports must not "change" a project).
    worksheet_path: Optional[str] = field(default=None, compare=False)
    worksheet_fingerprint: Optional[str] = field(default=None, compare=False)
```
In `_session_to_dict` add:
```python
        "worksheet": {"path": session.worksheet_path,
                      "fingerprint": session.worksheet_fingerprint},
```
In `_session_from_dict`, read `worksheet = d.get("worksheet") or {}` and pass `worksheet_path=worksheet.get("path"), worksheet_fingerprint=worksheet.get("fingerprint")` to `Session(...)`.

Add below `recovery_path`:
```python
def worksheet_fingerprint(design: DMPDesign) -> str:
    """A hash of everything the worksheet is written from.

    The riser drawing is left out: moving riser symbols doesn't change the
    worksheet, so it shouldn't make a door chart look out of date."""
    snapshot = copy.deepcopy(design)
    project_legacy_topology(snapshot)
    sync_master_zones(snapshot)
    data = design_to_dict(snapshot)
    data.pop("riser_document", None)
    text = json.dumps(data, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_worksheet(session: Session, worksheet: Path, fingerprint: str,
                     *, update_file: bool) -> None:
    """Remember the worksheet a door chart should use.

    update_file also writes the record into the saved project, so it survives
    closing the app without another save. Only pass it when the editor has no
    unsaved changes: touching the file then would make a pending crash-recovery
    snapshot look older than the project and stop it from being offered."""
    session.worksheet_path = str(worksheet)
    session.worksheet_fingerprint = fingerprint
    if not update_file or session.path is None or not Path(session.path).exists():
        return
    # Bookkeeping only: a failed write leaves the record in memory for the next save.
    with contextlib.suppress(OSError, ValueError):
        target = Path(session.path)
        data = json.loads(target.read_text(encoding="utf-8"))
        data["worksheet"] = {"path": session.worksheet_path, "fingerprint": fingerprint}
        _atomic_write(target, json.dumps(data, indent=1))
```
(`worksheet_fingerprint` sits below `sync_master_zones`/`design_to_dict` in the file, or only calls them at runtime, so definition order is fine either way.)

- [ ] **Step 4: Implement `app.py`**

Import `record_worksheet` and `worksheet_fingerprint` from `session`.

`_start_load_worksheet.on_done`, the DMP worksheet branch: pass `worksheet_path=str(xlsx_path)` to the `Session(...)` call.

`_enter_editor`: delete `self._ws_epoch = None` and the `if session.source_kind == "xlsx" and self.dmp_path is not None:` block. **After** the `prefill_site_defaults` block, add:
```python
        if (session.source_kind == "xlsx" and session.saved_at is None
                and session.worksheet_path and not session.worksheet_fingerprint):
            # The imported worksheet IS the design as of right now (machine
            # defaults included), so the door chart treats it as current.
            record_worksheet(session, Path(session.worksheet_path),
                             worksheet_fingerprint(session.design), update_file=False)
```

Replace `_latest_worksheet_path` with:
```python
    def _door_chart_source(self) -> tuple[Path | None, str]:
        """The worksheet a door chart would be built from, and whether it
        matches the project: 'current', 'stale', or 'unknown' (found by school
        name; this project never recorded making it)."""
        session = self.session
        if session is None:
            return None, "unknown"
        if session.worksheet_path and Path(session.worksheet_path).exists():
            fresh = worksheet_fingerprint(session.design) == session.worksheet_fingerprint
            return Path(session.worksheet_path), "current" if fresh else "stale"
        # A manual/BOM project must generate its own worksheet; a matching
        # school slug on disk can belong to a different project.
        if session.source_kind in {"manual", "bom"}:
            return None, "unknown"
        # Projects saved before worksheet tracking, or whose worksheet moved.
        return latest_rev_path(self.output_dir, f"{self._school_slug()}_dmp"), "unknown"

    def _latest_worksheet_path(self) -> Path | None:
        return self._door_chart_source()[0]

    def _confirm_chart_source(self, src: Path, status: str) -> str:
        """Ask before building from a worksheet that may not match. 'use' | 'cancel'."""
        if status == "stale":
            title = "Worksheet may be stale"
            body = (f"The design has changed since {src.name} was generated.\n\n"
                    "The door chart is built from that file, so it won't include "
                    "the newer edits. Generate the door chart anyway?\n\n"
                    "(Tip: regenerate the worksheet first to pick up your edits.)")
        else:
            title = "Confirm worksheet"
            body = (f"This project has no record of generating a worksheet. The "
                    f"newest one saved for this school is {src.name}, but it may "
                    "belong to a different project or be out of date.\n\n"
                    "Build the door chart from it anyway?")
        return "use" if messagebox.askyesno(title, body) else "cancel"
```

`_generate_worksheet.proceed`: delete `worksheet_epoch = ...`; add `session = self.session` at the top of `proceed`. In `work()`, `return out_path, worksheet_fingerprint(design)`. Replace `on_done`:
```python
            def on_done(result):
                out_path, fingerprint = result
                self._set_generating(None)
                if self.session is session:
                    self.dmp_path = out_path
                    record_worksheet(session, out_path, fingerprint,
                                     update_file=not self.editor.dirty)
                rev = out_path.stem.rsplit("_rev", 1)[-1]
                self._show_toast(
                    f"Worksheet rev {rev} ready",
                    action=("Open", lambda: open_file(out_path)),
                    meta=f"{out_path.name} · {len(design.zones)} zones",
                    folder=out_path)
```

`_generate_door_chart`: replace from `src = self._latest_worksheet_path()` through the end of the stale prompt with:
```python
        src, status = self._door_chart_source()
        if src is None:
            messagebox.showinfo(
                "No worksheet yet",
                "Generate a worksheet first — the door chart is built from it.")
            return
        if status != "current" and self._confirm_chart_source(src, status) != "use":
            return
```

Delete the remaining `_ws_epoch` lines (`__init__` ~279, `_close_project` ~2062). Check with `grep -n _ws_epoch scripts/app.py` → no output.

- [ ] **Step 5: Update existing tests** in `tests/test_create_new_project_workflow.py`:
- line ~373: change `controller._ws_epoch, controller._redirector = None, io.StringIO()` to `controller._redirector = io.StringIO()`
- line ~584: replace `assert controller._ws_epoch == 4` with:
```python
    from session import worksheet_fingerprint
    assert controller.session.worksheet_path == str(controller.dmp_path)
    assert controller.session.worksheet_fingerprint != worksheet_fingerprint(controller.session.design)
```

- [ ] **Step 6:** `venv/bin/python -m pytest -q --no-gui tests/test_worksheet_provenance.py tests/test_create_new_project_workflow.py tests/test_project_input_routing.py tests/test_session.py` → all pass. Then run the full `--no-gui` suite → no new failures.

- [ ] **Step 7: Commit**: `git add -A scripts tests && git commit -m "Remember which worksheet a project generated across reopen"`

---

### Task 5: Show the door chart's source and offer "Regenerate worksheet first"

**Files:**
- Modify: `scripts/app.py`: `_confirm_chart_source` (replace with a 3-button dialog), `_generate_door_chart` (handle `"regenerate"`; toast names the source), `_generate_worksheet` (`then=` continuation)
- Test: `tests/test_worksheet_provenance.py` (append), `tests/test_create_new_project_workflow.py:585-586`

**Interfaces:**
- Consumes: `App._door_chart_source`, `App._confirm_chart_source` (Task 4)
- Produces: `App._confirm_chart_source(...) -> "regenerate" | "use" | "cancel"`; `App._generate_worksheet(self, *, then=None)`. `then` runs only after a successful worksheet write for the same project.

- [ ] **Step 1: Write failing tests** (append to `tests/test_worksheet_provenance.py`)
```python
from unittest.mock import Mock


def test_regenerate_choice_makes_worksheet_then_chart(controller, tmp_path):
    sheet = tmp_path / "TRACK_SCHOOL_dmp_rev3.xlsx"
    _record_current(controller, sheet)
    controller.session.design.site_info.school_code = "EDITED"
    controller._confirm_chart_source = lambda src, status: "regenerate"
    chained = []
    controller._generate_worksheet = lambda then=None: chained.append(then)

    controller._generate_door_chart()

    assert chained == [controller._generate_door_chart]
    assert controller.started == []


def _worksheet_ready(controller, monkeypatch, write):
    controller.editor.show_issues_dialog = lambda proceed, **kw: proceed()
    controller._show_toast = lambda *a, **k: None
    monkeypatch.setattr(app_module, "load_prefs", lambda: {})
    monkeypatch.setattr(app_module, "save_prefs", lambda prefs: None)
    monkeypatch.setattr(app_module, "write_dmp_xlsx", write)


def test_continuation_runs_after_successful_worksheet(controller, monkeypatch):
    _worksheet_ready(controller, monkeypatch, lambda d, t, out: out.touch())
    controller._run_async = lambda work, done, error: done(work())
    then = Mock()
    controller._generate_worksheet(then=then)
    then.assert_called_once_with()


def test_failed_worksheet_never_chains_into_chart(controller, monkeypatch):
    def broken(design, template, out):
        raise OSError("disk full")
    _worksheet_ready(controller, monkeypatch, broken)
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: None)

    def run(work, done, error):
        try:
            done(work())
        except OSError as exc:
            error(exc)

    controller._run_async = run
    then = Mock()
    controller._generate_worksheet(then=then)
    then.assert_not_called()
```

- [ ] **Step 2:** Run the file → the three new tests FAIL (`then` is an unexpected keyword; `"regenerate"` is ignored).

- [ ] **Step 3: Implement.**

`_generate_worksheet(self, *, then=None)`. In `on_done`, after the toast:
```python
                if then is not None and self.session is session:
                    # Only after a successful write for this same project, so a
                    # failed or cancelled worksheet can't loop back into the chart.
                    self.root.after(0, then)
```

`_generate_door_chart`, replace the confirmation line with:
```python
        if status != "current":
            choice = self._confirm_chart_source(src, status)
            if choice == "regenerate":
                self._generate_worksheet(then=self._generate_door_chart)
                return
            if choice != "use":
                return
```
and change its toast meta to `meta=f"{chart_path.name} · from {src.name}"` so the tech can see which worksheet was used.

Replace `_confirm_chart_source` with a dialog (same structure as `_choose_riser_outputs`):
```python
    def _confirm_chart_source(self, src: Path, status: str) -> str:
        """Ask before building a door chart from a worksheet that may not match
        the project. Returns 'regenerate', 'use', or 'cancel'."""
        if status == "stale":
            title = "Worksheet is out of date"
            body = (f"You've changed this project since {src.name} was made. "
                    "The door chart is built from that file, so it won't "
                    "include those changes.")
        else:
            title = "Check the worksheet"
            body = (f"This project has no record of making a worksheet. The newest "
                    f"one saved for this school is {src.name}, but it may belong "
                    "to a different project or be out of date.")
        dlg = ctk.CTkToplevel(self.root)
        dlg.title(title)
        dlg.geometry("560x230")
        dlg.configure(fg_color=theme.APP_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        choice = "cancel"
        ctk.CTkLabel(dlg, text=title, font=theme.ui_font(16, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=20, pady=(20, 8))
        ctk.CTkLabel(dlg, text=body, wraplength=520, justify="left", anchor="w",
                     text_color=theme.TEXT_SECOND).pack(fill="x", padx=20)

        def pick(value):
            nonlocal choice
            choice = value
            dlg.destroy()

        buttons = ctk.CTkFrame(dlg, fg_color="transparent")
        buttons.pack(fill="x", side="bottom", padx=20, pady=16)
        ctk.CTkButton(buttons, text="Regenerate worksheet first", width=210,
                      command=lambda: pick("regenerate")).pack(side="right")
        ctk.CTkButton(buttons, text="Use this file anyway", width=160,
                      fg_color=theme.SURFACE_CHIP, text_color=theme.TEXT,
                      command=lambda: pick("use")).pack(side="right", padx=8)
        ctk.CTkButton(buttons, text="Cancel", width=80,
                      command=dlg.destroy).pack(side="left")
        dlg.bind("<Escape>", lambda _event: dlg.destroy())
        self.root.wait_window(dlg)
        return choice
```

- [ ] **Step 4: Update** `tests/test_create_new_project_workflow.py::test_worksheet_snapshot_keeps_mid_generation_edits_stale_for_chart`. Before `controller._generate_door_chart()` add
`seen = []; controller._confirm_chart_source = lambda src, status: (seen.append(status), "cancel")[1]`
and replace the final assertion with `assert seen == ["stale"]`.

- [ ] **Step 5:** Run `tests/test_worksheet_provenance.py tests/test_create_new_project_workflow.py` → pass. Full `--no-gui` suite → no new failures.

- [ ] **Step 6: Commit**: `git commit -am "Offer to regenerate the worksheet before a stale door chart"`

---

### Task 6: Updates — verify the download, and Cancel really cancels

**Files:**
- Modify: `scripts/updater.py`: `_select_asset`, `fetch_latest`; add `UpdateError`, `verify_download`, `StagedUpdate`, `stage_update`, `launch_staged`, `discard_staged`; **remove** `apply_update` (its only caller is `app.py`)
- Modify: `scripts/app.py`: `_start_update.work` (~2383-2397), replace `_quit_for_update` with `_finish_update`
- Create: `tests/test_updater_safety.py`

**Interfaces:**
- Produces: `fetch_latest()` result gains `"asset_digest": str | None`. `updater.verify_download(zip_path: Path, digest: str | None) -> None` (raises `UpdateError`). `updater.stage_update(zip_path: Path) -> StagedUpdate | None`. `updater.launch_staged(staged) -> None`. `updater.discard_staged(staged) -> None`. `App._finish_update(self, staged) -> None`.

- [ ] **Step 1: Write failing tests** in `tests/test_updater_safety.py`
```python
import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app as app_module
import updater


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_verify_download_accepts_matching_checksum(tmp_path):
    z = tmp_path / "update.zip"
    z.write_bytes(b"new build")
    updater.verify_download(z, _digest(b"new build"))


@pytest.mark.parametrize("digest", [None, "", "md5:abc", _digest(b"something else")])
def test_verify_download_refuses_unverifiable_or_damaged(tmp_path, digest):
    z = tmp_path / "update.zip"
    z.write_bytes(b"new build")
    with pytest.raises(updater.UpdateError):
        updater.verify_download(z, digest)


def test_fetch_latest_reports_platform_asset_digest(monkeypatch):
    monkeypatch.setattr(updater, "_ASSET_SUFFIX", {sys.platform: "macOS.zip"})
    payload = {"tag_name": "v9.0.0", "assets": [
        {"name": "C1-DMP-Toolkit-Windows.zip", "browser_download_url": "w", "digest": "sha256:w"},
        {"name": "C1-DMP-Toolkit-macOS.zip", "browser_download_url": "m", "digest": "sha256:m"},
    ]}
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(json.dumps(payload).encode()))
    info = updater.fetch_latest()
    assert (info["asset_url"], info["asset_digest"]) == ("m", "sha256:m")


def test_stage_update_is_unavailable_in_dev_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_dir", lambda: None)
    assert updater.stage_update(tmp_path / "update.zip") is None


def test_discard_staged_removes_scratch_files(tmp_path):
    work = tmp_path / ".dmp_update_1"
    (work / "extracted").mkdir(parents=True)
    updater.discard_staged(updater.StagedUpdate(["true"], work))
    assert not work.exists()


@pytest.fixture
def app(monkeypatch):
    a = object.__new__(app_module.App)
    a.state, a._generating = "editing", None
    a.editor = SimpleNamespace(maybe_close=Mock(return_value=True))
    a._close_update_dialog = Mock()
    a._shutdown = Mock()
    calls = []
    monkeypatch.setattr(app_module.updater, "launch_staged", lambda s: calls.append(("launch", s)))
    monkeypatch.setattr(app_module.updater, "discard_staged", lambda s: calls.append(("discard", s)))
    return a, calls


def test_cancelled_save_prompt_leaves_no_update_armed(app):
    a, calls = app
    a.editor.maybe_close.return_value = False
    staged = object()
    a._finish_update(staged)
    assert calls == [("discard", staged)]
    a._shutdown.assert_not_called()
    a._close_update_dialog.assert_called_once_with()


def test_running_generation_can_postpone_update(app, monkeypatch):
    a, calls = app
    a._generating = "worksheet"
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kw: False)
    staged = object()
    a._finish_update(staged)
    assert calls == [("discard", staged)]
    a._shutdown.assert_not_called()


def test_confirmed_quit_launches_update_then_exits(app):
    a, calls = app
    staged = object()
    a._finish_update(staged)
    assert calls == [("launch", staged)]
    a._shutdown.assert_called_once_with()
```

- [ ] **Step 2:** Run it → FAIL (`AttributeError: verify_download`, etc.).

- [ ] **Step 3: Implement `updater.py`.** Add `import hashlib`, `import shutil`, `from dataclasses import dataclass`.
```python
class UpdateError(Exception):
    """A download that must not be installed; the message is shown to the user."""


def _select_asset(assets: list[dict]) -> dict | None:
    suffix = _ASSET_SUFFIX.get(sys.platform)
    if not suffix:
        return None
    for a in assets:
        if (a.get("name") or "").endswith(suffix):
            return a
    return None
```
In `fetch_latest`: `asset = _select_asset(data.get("assets") or []) or {}`, then `"asset_url": asset.get("browser_download_url"), "asset_digest": asset.get("digest"),`. Update the docstring's key list.
```python
def verify_download(zip_path: Path, digest: str | None) -> None:
    """Refuse a download that doesn't match the SHA-256 GitHub published for it."""
    algo, _, expected = (digest or "").partition(":")
    if algo != "sha256" or not expected:
        raise UpdateError("This release has no checksum, so the download "
                          "can't be verified.")
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != expected.lower():
        raise UpdateError("The download was damaged or incomplete.")


@dataclass
class StagedUpdate:
    """An extracted build plus the swap script that will install it. Nothing
    runs until launch_staged(), so the user can still back out."""
    command: list[str]
    work: Path
    creationflags: int = 0


def stage_update(zip_path: Path) -> StagedUpdate | None:
    """Extract the new build and write the swap script, without starting it.

    Returns None if self-update isn't possible (dev run / unknown platform /
    malformed archive)."""
    app = install_dir()
    if app is None or sys.platform not in _ASSET_SUFFIX:
        return None
    work = _work_dir(app)
    payload = prepare_payload(zip_path, work)
    if payload is None:
        shutil.rmtree(work, ignore_errors=True)
        return None
    if sys.platform == "darwin":
        script = _write_mac_script(payload, app, work)
        return StagedUpdate(["/bin/bash", str(script)], work)
    script = _write_windows_script(payload, app, work)
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return StagedUpdate(["cmd", "/c", str(script)], work, creationflags=flags)


def launch_staged(staged: StagedUpdate) -> None:
    """Start the detached helper; it swaps files once this process exits."""
    if staged.creationflags:
        subprocess.Popen(staged.command, creationflags=staged.creationflags,
                         close_fds=True)
    else:
        subprocess.Popen(staged.command, start_new_session=True)


def discard_staged(staged: StagedUpdate) -> None:
    shutil.rmtree(staged.work, ignore_errors=True)
```
Delete `apply_update`. Update the section heading comment to "Install (extract, stage, detached swap-and-relaunch)".

- [ ] **Step 4: Implement `app.py`.** In `_start_update`, replace `work()`:
```python
        def work():
            zip_path = Path(tempfile.mkdtemp(prefix="dmp_dl_")) / "update.zip"
            try:
                updater.download(info["asset_url"], zip_path, progress)
                updater.verify_download(zip_path, info.get("asset_digest"))
                staged = updater.stage_update(zip_path)
            except Exception as exc:
                msg = str(exc) or type(exc).__name__  # `exc` is unbound once this block ends
                self.root.after(0, lambda: self._update_failed(msg))
                return
            finally:
                shutil.rmtree(zip_path.parent, ignore_errors=True)
            if staged is None:
                self.root.after(0, lambda: self._update_failed(
                    "Self-update isn't available for this build."))
                return
            self.root.after(0, lambda: self._finish_update(staged))
```
(add `import shutil` to `app.py`). Replace `_quit_for_update` with:
```python
    def _finish_update(self, staged):
        """Settle unsaved work first; only a confirmed quit arms the swap."""
        if self._generating is not None and not messagebox.askyesno(
                "Update now?", "Generation in progress — quit and update anyway?"):
            updater.discard_staged(staged)
            self._close_update_dialog()
            return
        if self.state == "editing" and self.editor and not self.editor.maybe_close():
            updater.discard_staged(staged)
            self._close_update_dialog()
            return  # user cancelled the save prompt — app stays open, nothing queued
        updater.launch_staged(staged)
        self._shutdown()
```
Check with `grep -n "apply_update\|_quit_for_update" -r scripts tests` → no output.

- [ ] **Step 5:** Run `tests/test_updater_safety.py` plus the full `--no-gui` suite → pass.

- [ ] **Step 6: Commit**: `git add scripts/updater.py scripts/app.py tests/test_updater_safety.py && git commit -m "Verify update downloads and arm the swap only after quitting"`

---

### Task 7: Show when crash protection stops working

**Files:**
- Modify: `scripts/editor_frame.py`: `__init__` (next to `self.edit_epoch = 0`, ~336), `_write_recovery` (~392-400), `save` (~402-425), `_notify_status` (~446-456)
- Create: `tests/test_recovery_health.py`

**Interfaces:**
- Produces: `EditorFrame._recovery_failed: bool`. The status text while dirty becomes `"⚠  Unsaved — auto-backup failed, save now"` when backups fail.

- [ ] **Step 1: Write failing tests**
```python
"""A failed crash-protection write must be visible, and clear once a retry works."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import editor_frame
from parse_dmp_worksheet import SiteInfo
from session import create_blank_session


def _editor(tmp_path):
    ed = object.__new__(editor_frame.EditorFrame)
    ed.session = create_blank_session(SiteInfo(school_name="HEALTH"))
    ed.session.path = tmp_path / "HEALTH.dmps"
    ed.dirty, ed._recovery_job, ed._recovery_failed = True, None, False
    ed.statuses = []
    ed.on_status_change = lambda text, dirty: ed.statuses.append(text)
    return ed


def test_failed_backup_is_announced_and_retry_clears_it(tmp_path, monkeypatch):
    ed = _editor(tmp_path)
    def full_disk(session):
        raise OSError("No space left on device")
    monkeypatch.setattr(editor_frame, "write_recovery", full_disk)
    ed._write_recovery()
    assert ed.statuses[-1] == "⚠  Unsaved — auto-backup failed, save now"

    monkeypatch.setattr(editor_frame, "write_recovery", lambda session: None)
    ed._write_recovery()
    assert ed.statuses[-1] == "●  Unsaved changes"


def test_healthy_backup_does_not_spam_status(tmp_path, monkeypatch):
    ed = _editor(tmp_path)
    monkeypatch.setattr(editor_frame, "write_recovery", lambda session: None)
    ed._write_recovery()
    assert ed.statuses == []
```

- [ ] **Step 2:** Run → FAIL (the status never changes).

- [ ] **Step 3: Implement.** In `__init__`: `self._recovery_failed = False`. Replace the `with contextlib.suppress(Exception): write_recovery(self.session)` line in `_write_recovery` with:
```python
        try:
            write_recovery(self.session)
            failed = False
        except Exception:
            failed = True
        if failed != self._recovery_failed:
            self._recovery_failed = failed
            self._notify_status()
```
In `save()`, after `self.dirty = False`, add `self._recovery_failed = False`. In `_notify_status`:
```python
        if self.dirty:
            if self._recovery_failed:
                self.on_status_change("⚠  Unsaved — auto-backup failed, save now", True)
            else:
                self.on_status_change("●  Unsaved changes", True)
            return
```
Retry needs no new code: every edit reschedules `_write_recovery`.

- [ ] **Step 4:** Run the new tests plus the full `--no-gui` suite → pass.

- [ ] **Step 5: Commit**: `git add scripts/editor_frame.py tests/test_recovery_health.py && git commit -m "Show when crash protection stops working"`

---

### Task 8: Run the window-based tests in GitHub CI

**Files:** Modify `.github/workflows/ci.yml`

- [ ] **Step 1: Run the full suite locally with windows enabled** (windows will open briefly):
`venv/bin/python -m pytest -q -ra` → expected about 1,053 tests, all passing or with the same small skip set as v1.7.2's "1,047 passed, 6 skipped" plus this plan's new tests. **If anything fails or hangs here, fix it or stop and report before touching CI.**

- [ ] **Step 2: Edit `ci.yml`.** Raise `timeout-minutes` on the `tests` job from `25` to `45`. After the `Run regression suite` step, add:
```yaml
      - name: Run window-based tests
        # The step above skips everything that opens a window (about a quarter
        # of the suite). "Verify GUI runtime" proves this runner can create
        # one, so run the full suite here too; a hang hits this step's timeout
        # instead of the job's.
        timeout-minutes: 20
        run: python -m pytest -q -ra --junitxml=test-results/pytest-gui.xml
```
and change the upload step's `path:` to `test-results/`.

- [ ] **Step 3: Commit**: `git commit -am "Run window-based tests in CI"`

- [ ] **Step 4:** CI can only be proven by pushing the branch. **Ask the user** before pushing. After approval: `git push -u origin fix/data-safety-1.7.3`, then watch with `gh run watch`. Treat a hang as a bug to investigate, not something to mark `continue-on-error`.

---

### Task 9: Docs and version

**Files:** `docs/ROADMAP.md`, `docs/releases/v1.7.3.md` (create), `VERSION`

- [ ] **Step 1:** `VERSION` → `1.7.3`.
- [ ] **Step 2:** `docs/releases/v1.7.3.md`, following `docs/releases/v1.7.2.md`'s sections (Changes / Validation / Compatibility). Plain-language **Changes**: one line per Tasks 1–7. **Validation**: the real numbers from Task 10. **Compatibility**: ".dmps files gain an optional worksheet record; 1.7.2 and earlier still open them. Projects saved before 1.7.3 ask you to confirm the worksheet the first time you build a door chart."
- [ ] **Step 3:** `docs/ROADMAP.md` application table:
  - Mark **P0 Snapshot exports** done. It was already true in code; Task 4's fingerprint now makes "edit during generation → stale" survive reopen.
  - Mark **P1 Show recovery-save health** done (Task 7).
  - Narrow **P1 Verify updater payloads** to the remaining work: bundle/version check of the extracted app, and keeping the previous build until the new one starts successfully. Note that checksum verification and cancel-safety shipped in 1.7.3.
  - Update the "Reviewed" date to October 6, 2026.
- [ ] **Step 4: Commit**: `git add VERSION docs && git commit -m "Document version 1.7.3 release"`

---

### Task 10: Verify, rebuild, and install the Mac app (AGENTS.md)

- [ ] **Step 1:** `venv/bin/python -m pytest -q --no-gui` → record counts (expect 793 + new tests passed, 260 skipped).
- [ ] **Step 2:** `venv/bin/python -m pytest -q -ra` (full, with windows) → record counts. Any failure blocks the build.
- [ ] **Step 3:** Quit the installed app if it's running (`osascript -e 'quit app "C1 DMP Toolkit"'`), then `./build_mac.command`. It must end with `==> Done. App installed at:`. Confirm the bundle is fresh: `stat -f '%Sm' ~/Applications/"C1 DMP Toolkit.app"`.
- [ ] **Step 4:** `open ~/Applications/"C1 DMP Toolkit.app"`, wait about 10 s, run `pgrep -fl "C1 DMP Toolkit"` (it must still be running), then `screencapture -x /tmp/c1-1.7.3.png` and look at the image. The window should be up, and the version shown (title/About) should be 1.7.3.
- [ ] **Step 5: Smoke test in the installed app** using a copy of a real worksheet from `input/`. Don't edit the originals.
  1. Import the worksheet, save, generate a worksheet, close the app, reopen the project, click Generate Door Chart → **no prompt**. The toast should say "from …_rev1.xlsx".
  2. Edit a zone description, save, close, reopen, click Generate Door Chart → **"Worksheet is out of date"** with "Regenerate worksheet first". Pick it → a worksheet rev, then a door chart rev.
  3. Re-import the same file → the project path ends in `(2).dmps`. Repeat with a PDF from `input/` for a school that already has a saved project.
- [ ] **Step 6:** Report to the user and the group: commits on `fix/data-safety-1.7.3`, both test counts, build result, smoke results, and anything skipped (Windows was not tested interactively; CI push still needs approval). **Do not push, tag, or open a PR without the user's OK.**

---

## Out of scope (ask the user before starting)

- Refreshing README screenshots (from v1.3.0). Needs a capture pass of the 1.7.3 UI.
- Updater bundle/version check and keep-old-build-until-new-one-starts (remaining roadmap P1).
- Required status checks / branch protection on `main` (roadmap hygiene item 1).
- Revert on a never-saved imported project shows "Couldn't revert" (pre-existing for worksheet/BOM imports; Task 2 makes PDF imports behave the same).
