# Create New Project / New Riser Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class, source-document-free Create New Project workflow that creates an ordinary schema-8 `.dmps` session and opens it in the existing Riser Designer.

**Architecture:** Treat PDF and worksheet data as optional ways to populate the existing `Session`/`DMPDesign`, not as prerequisites or separate project types. A small blank-session factory will initialize the existing site, topology, riser-title, and persistence structures; the normal editor, hardware mutations, location registry, topology service, save/recovery path, and exporters remain the only implementations of those behaviors.

**Tech stack:** Python 3.13, Tk/CustomTkinter, existing dataclass models and schema-8 JSON persistence, pytest, openpyxl, PyMuPDF, and the existing cross-platform PyInstaller application.

**Spec:** This document records the codebase audit and the implementation design requested for a first-class Create New Project / New Riser Project workflow.

## Global constraints

- A PDF, worksheet, imported school, and previously saved project must not be required.
- The new workflow must enter the existing `EditorFrame` and existing `RiserTab`.
- Do not add a second equipment model, topology graph, location model, editor, or project-file format.
- Existing PDF, XLSX, and DMPS entry paths must retain their current behavior.
- Existing schema-1 through schema-8 projects must continue to open under the current migration rules.
- Blank-created projects must save as ordinary `.dmps` files and use normal save, recovery, recent-project, reopen, and export behavior.
- macOS and Windows must use the same implementation; only existing platform-specific labels and shortcuts may differ.
- Setup must not require data that is only needed by a later export.
- Validation may explain downstream readiness, but must not prevent creating or editing a riser.

---

## 1. Audit basis

This audit was performed against the repository at:

- Working branch: `chore/project-hygiene-20260912`
- HEAD: `d966a8a` (`Remove combined deliverable generation from roadmap`)
- `VERSION`: `1.7.0`
- Latest release tag in the checkout: `v1.7.0`
- Current session schema: `8`
- Test baseline: `803 passed, 6 skipped, 5 dependency warnings`

The branch contains post-`v1.7.0` work from `main`, including the mixed-expander, worksheet-output, riser-layout, full-screen, and MSP-port-order fixes described in `docs/ROADMAP.md`. This is therefore a v1.7.0-plus audit, not an audit of the tag alone.

An additional source-free characterization probe constructed a `DMPDesign` in memory, added an LX splitter, RSP/power supply, service keypad, zones, explicit connections, and a detailed riser layout, then exercised the existing save/reopen and export code. Without a PDF or worksheet it successfully produced:

- a schema-8 `.dmps` project,
- a DMP worksheet that passed the worksheet re-import guard,
- a door chart,
- 11x17 PDF and SVG risers, and
- a verified encrypted RemoteLink account once a numeric account/local code and installed RSP zones were present.

The only riser validation findings in that fully connected probe were intentionally unfilled title-block fields. This demonstrates that the missing blank-site feature is primarily an entry, initialization, messaging, and persistence-lifecycle gap—not a missing editor or export engine.

## 2. Current-state findings: application entry flow

### 2.1 What happens at startup

`App.__init__` in `scripts/app.py` builds the shared shell and calls `_show_drop_zone()`. The home surface currently offers:

- a single drop/browse target accepting PDF, XLSX, or DMPS,
- up to four recent `.dmps` projects, and
- the output-folder picker.

There is no Create New Project action on the home surface.

### 2.2 File menu behavior

The File menu exposes **New Project** and binds `Cmd/Ctrl+N`, but both call `_process_another()`. That method only closes the current project, clears runtime fields, and returns to the same import-oriented home screen. When already on the home screen, **New Project** effectively refreshes the home screen; it does not construct a project.

This label/behavior mismatch is the most visible blocker.

### 2.3 The three real entry paths today

| User action | Current route | Result before editor entry |
|---|---|---|
| Open/drop PDF | `_start_input` → `_start_parse` → `build_dmp_design_from_pdf` | OCR/searchability check, PDF metadata/zone/topology parsing, school phone lookup, imported hardware, service keypad, conflicts, and topology provenance |
| Open/drop XLSX | `_start_input` → `_start_load_worksheet` → `parse_dmp_worksheet` | Worksheet model parsing; must satisfy `worksheet_looks_like_dmp` by having a school name or Master zones; gets a collision-safe session path |
| Open/drop DMPS or recent project | `_open_session_path` → `load_session`/`load_recovery` | Schema check, older-project migration, and optional recovery prompt |

All three call `_enter_editor(session)`.

### 2.4 Exact path into the Riser Designer

`_enter_editor` performs these source-independent steps:

1. `ensure_editable_zones(session.design)`
2. `normalize_rsp_tokens(session.design)`
3. `normalize_zone_descriptions(session.design)`
4. `ensure_explicit_topology(session.design)`
5. construct the existing `EditorFrame`

`EditorFrame._build_tabs()` constructs SITE, ZONES, SPLITTERS, KEYPADS, RSP/POWER, REMOTELINK, and RISER for every session, then always selects **ZONES**. A user can reach Riser Designer only after a PDF/XLSX/DMPS entry path and then selecting the **RISER** tab.

The editor does not technically require imported data. Its strict minimum is a `Session` containing a `DMPDesign`; both dataclasses already have safe empty defaults. The missing code is a supported application action that creates and enters such a session.

## 3. Current-state findings: project, persistence, and migration model

### 3.1 Session model

`scripts/session.py` defines a single `Session` with:

- `design: DMPDesign`
- `remotelink: RemoteLinkConfig`
- `source_kind` and `source_name`
- `topology_confirmed`
- `saved_at`
- runtime `path`

`source_kind` already defaults to an empty string and `source_name` is explicitly display-only. No source file path is stored in the project. After a saved PDF project is reopened, `App.pdf_path` is `None`, yet worksheet and riser generation work directly from the session design. That is strong evidence that an imported source is already optional in the durable architecture.

### 3.2 Schema-8 file shape

The `.dmps` file is human-readable JSON with these top-level fields:

```json
{
  "schema_version": 8,
  "app_version": "1.7.0",
  "saved_at": "2026-09-12T10:30:00-07:00",
  "source": {"kind": "manual", "name": ""},
  "topology_confirmed": false,
  "remotelink": {},
  "design": {}
}
```

The current serializer does not restrict `source.kind`; `"manual"` can be introduced without changing the shape or requiring a schema bump.

`design` persists:

- `SiteInfo`
- splitters, RSPs, keypads, power supplies
- editable and derived zone representations
- source conflicts and provenance strings
- canonical `connections`
- `riser_document`
- stable equipment locations and device-to-location bindings

The canonical electrical graph belongs to `DMPDesign.connections`. `RiserDocument` stores presentation only: title block, positioned elements, routes, annotations, z-order, unplaced devices, page size, layout version, and location-frame visibility.

### 3.3 Existing migrations

- Files with a schema newer than 8 are rejected so data is not silently lost.
- Schema 1 or a design without `connections` derives explicit edges from legacy splitter/keypad fields.
- A missing `riser_document` receives `default_riser_document(design)`.
- Missing newer dataclass keys use defaults; unknown design/site keys are ignored.
- Saving projects projects the canonical graph back into legacy splitter/keypad fields for worksheet compatibility.

### 3.4 Save, recovery, reopen, and Save As findings

- **Save:** `EditorFrame.save()` calls atomic `save_session()`. A session without a path uses `<output>/Sessions/<school slug>.dmps`.
- **Collision avoidance:** `unique_session_path()` creates `name (2).dmps`, etc., but is currently used only for a new XLSX import.
- **Recovery:** dirty edits write `<project>.dmps.recovery` after a five-second debounce. Recovery is offered when the base project path is opened.
- **Recent projects:** only base `.dmps` files in the active output folder's `Sessions` directory are scanned.
- **Save As:** there is no project Save As implementation or menu action. The only `asksaveasfilename` use is for a RemoteLink summary.

A never-saved session can write a recovery file, but the home scanner does not enumerate recovery-only files. This pre-existing weakness affects fresh PDF sessions and would also affect a blank session if it remained unsaved. For the new workflow, the safest behavior is to treat **Create New Project** as explicit creation of the initial `.dmps` record at `unique_session_path(design)`. Cancel creates nothing. Subsequent edits retain the existing explicit-save and recovery behavior.

## 4. What the application supports today: data and equipment model

### 4.1 Site metadata and where it originates

| Field | PDF import | XLSX import | Current UI | Downstream use |
|---|---|---|---|---|
| School/site name | PDF title/schedule parser | SITE INFO | SITE | project title, filename slug, worksheet, chart, RemoteLink, initial riser title |
| Local/school code | PDF parser | SITE INFO | SITE | worksheet, RemoteLink account fallback, initial riser title |
| Address line 1/2 | PDF title parser | hidden workbook custom properties | **Not editable in SITE despite help text saying it is**; combined address is editable only in the riser title block | door chart, RemoteLink, initial riser title |
| Phone | live school lookup after PDF parse | SITE INFO | SITE | worksheet and RemoteLink |
| Install tech/date | PDF path leaves gaps; editor preferences fill them | SITE INFO | SITE | worksheet validation/output |
| IP/default gateway | editor preferences or manual entry | SITE INFO | SITE | worksheet and validation |
| MSP/XR550 location | extracted riser or first RSP fallback | DMP XR550 | SITE and MSP edit form | topology root, locations, worksheet, riser symbol |
| Drawing/sheet number | not stored in `SiteInfo` | not stored in `SiteInfo` | RISER title block | riser only; defaults to `INT-5.0` |
| Prepared/drawn by | not imported into `SiteInfo` | not imported | RISER title block | riser only |
| Issue date | defaults to today in `RiserTitleBlock` | not imported as site data | RISER title block | riser only |

`default_riser_document()` copies school name, school code, address, and project title into a new title block once. After that, `SiteInfo` and `RiserTitleBlock` are independently editable. There is no automatic or explicit synchronization command. Automatic two-way linking should not be added because it could overwrite custom title blocks in existing projects. The new setup dialog should seed both once, and the RISER inspector should gain an explicit **Copy from SITE** action for later intentional synchronization.

### 4.2 MSP

The MSP is implicit and mandatory. `_device_ids(design)` always begins with `"MSP"`; there is no MSP list item to import or create. The MSP cannot be removed. Its location is `SiteInfo.xr550_location`, and its supported output ports are KP BUS, PROG, and LX500 through LX900.

An empty `DMPDesign` can therefore produce a valid drawing scene containing the MSP before any other hardware exists.

### 4.3 RSPs, power supplies, and zones

The existing `hardware.add_expander()` is already usable against an empty design. It:

- creates a 714-16 or 714-8 RSP,
- creates its paired power supply,
- allocates the lowest free RSP number,
- allocates a free consecutive zone range on LX500 through LX900 without moving existing zones,
- materializes SPARE points and the final two PS supervisory points, and
- enforces the 15-expander worksheet capacity.

Zones created this way immediately feed `master_zones` on save/export. No PDF schedule is required.

### 4.4 Splitters and keypads

The shared add/edit/remove forms already work from RISER and the domain tabs:

- LX or KP 710 splitters, up to 12 per family,
- keypads, up to 28,
- source selection from MSP or a compatible KP splitter,
- RemoteLink keypad device type/display configuration,
- shared location editing, and
- cascade-safe removal.

A first keypad added to an empty design becomes keypad 1; choosing MSP makes it the direct service keypad topology already used by PDF imports. There is no persisted `service_keypad` flag. Current service-keypad behavior is a convention consisting of keypad 1, direct MSP source, and often an MSP-location suffix. The new workflow should explain this in the existing form rather than create a second keypad type. A dedicated convenience checkbox can be considered later only if it maps to those same fields.

One current limitation matters for large manually built sites: calling `add_splitter(design, "LX")` always synthesizes a `710-LX500-N` ID. Imported projects can contain LX600-LX900 splitters and the graph supports the matching MSP ports, but the add dialog cannot choose those buses. The blank-site work should add an LX bus selector (500/600/700/800/900) to the existing splitter creation function and form, keeping 500 as the default.

### 4.5 Connections, cables, and labels

`scripts/topology_service.py` is already the single mutation boundary for connections. It validates compatible source/target ports, occupancy, cycles, and LX-bus matching. `TopologyConnection` already persists:

- stable ID,
- source and target device/port,
- cable type,
- new/existing status,
- quantity, and
- custom label.

`RiserRoute` independently persists drawing bends and callout placement/visibility. The existing Connect/Route tools, cable inspector, label hide/restore/reset behavior, and undo/redo work the same for manually added hardware.

### 4.6 Equipment locations

`scripts/project_locations.py` already maintains stable `EquipmentLocation` UUID records and device bindings. The authoritative writable location fields remain the current MSP, splitter, RSP, power-supply, and keypad model fields. RSPs and paired power supplies stay together. Blank or unknown locations are represented as unresolved records and are surfaced by riser validation, not rejected.

This model is source-independent and should be reused unchanged.

## 5. Source-document assumptions

### 5.1 Assumptions that are import-only and safe to bypass

- PDF OCR and page detection
- title-block and zone-schedule parsing
- school phone lookup
- device clustering from PDF spans
- vector-line topology reconstruction
- PDF source conflicts
- fallback auto-derived wiring
- worksheet shape recognition
- imported workbook custom properties

These are confined to `_start_parse`, `_start_load_worksheet`, and import helpers. A Create New Project handler should not call any of them.

### 5.2 Runtime fields that are already optional

- `App.pdf_path`: used only to repeat a PDF searchability check before worksheet generation in the same runtime.
- `App.dmp_path`: points to the imported or latest generated worksheet for door-chart generation.
- `Session.source_kind` / `source_name`: provenance/display metadata.
- `DMPDesign.conflicts`: may be empty.
- imported topology geometry: not persisted or required; the Riser Designer generates its own scene from the logical project.

### 5.3 Misleading assumptions that must be corrected

- The SPLITTERS banner treats every topology not marked `"riser"` as failed/incomplete extraction and tells the user to compare it to a source riser. That is false for a manual project.
- The topology validation warning always says wiring must be checked, but does not distinguish manually authored wiring from auto-derived PDF guesses.
- The help and README describe only import as the first step.
- The help says SITE edits address data, but the actual form does not.
- The editor always lands on ZONES even when the user intended to start a riser.

## 6. Exact blockers to first-class blank-site creation

1. No home-screen action constructs a session.
2. File → New Project and `Cmd/Ctrl+N` are wired to close/reset, not new-project creation.
3. There is no blank-session factory that intentionally initializes provenance, title data, and topology ownership.
4. `_enter_editor`/`EditorFrame` always selects ZONES.
5. Blank/manual topology would receive incorrect “riser extraction was incomplete” copy.
6. Address metadata cannot be edited in SITE, so blank-created address data cannot be maintained for door-chart and RemoteLink output.
7. Site metadata and riser title metadata are separate after initial document creation, with no explicit synchronization action.
8. Save As is absent, and a never-saved project’s recovery-only file is not discoverable from recents.
9. RemoteLink fails late if account/local code is nonnumeric or no installed RSP zones exist; a blank-site workflow needs readiness guidance before the export dialog.
10. The LX splitter add form cannot create a bus-specific LX600-LX900 device, although the graph and imported projects support those buses.
11. There is no direct characterization test proving home → blank session → RISER → shared hardware → save/reopen while import regressions remain intact.

None of these blockers justifies a parallel editor or a second persistent project type.

## 7. Recommended user experience

### 7.1 Home screen

Make the two intents explicit:

- **Create New Project** — create a project without a source document.
- **Import / Open** — retain the current PDF/XLSX/DMPS drop zone and browse behavior unchanged.

Recent projects and output-folder selection remain in their current positions. The File menu becomes:

- **Create New Project** (`Cmd/Ctrl+N`)
- **Open…** (`Cmd/Ctrl+O`)
- **Open Recent**
- **Close Project**
- **Save**
- **Save As…**

### 7.2 Setup dialog

Require only **Site / school name** because it is the human project identity and output slug. Everything else is optional or safely defaulted.

| Setup field | Existing destination | Default |
|---|---|---|
| Site / school name | `SiteInfo.school_name`; title `school_name` and `project_title` | required |
| Local code | `SiteInfo.school_code`; title `local_code` | blank |
| Address line 1 | `SiteInfo.address_line1`; first title address line | blank |
| City, state ZIP | `SiteInfo.address_line2`; second title address line | blank |
| MSP / XR550 location | `SiteInfo.xr550_location` | `UNSPECIFIED` or blank, surfaced for review |
| Drawing / sheet number | title `sheet_number` | `INT-5.0` |
| Prepared by | title `drawn_by` | optional; may prefill from the machine’s install-tech preference |
| Issue date | title `issue_date` | today, editable |

Do not ask for phone, network configuration, RemoteLink account/receiver, zones, hardware counts, or wiring in this dialog. Those already have appropriate editors and are not required to start a riser.

**Create New Project** atomically writes the initial schema-8 `.dmps` project to a collision-safe Sessions path, then opens the existing editor with RISER selected. **Cancel** writes nothing.

### 7.3 Initial RISER state

- Show the existing MSP symbol and title block.
- Use the existing sheet, inspector, layers, markup, zoom, layout, and generation controls.
- Show no invented RSP, splitter, keypad, zones, or cables.
- Keep **Add Device** as the entry to the shared splitter, keypad, and RSP/power-supply forms.
- Explain in the keypad form that keypad 1 sourced from MSP is the normal service keypad configuration.
- Newly added devices continue to enter the Unplaced tray; existing Auto-layout and direct placement handle them.

### 7.4 Downstream workflow policy

Do not add a “convert riser project” step. The manual project is an ordinary project immediately; each output becomes meaningful as its required data appears.

| Workflow | Recommended behavior for a manual project |
|---|---|
| Riser | Available immediately; existing riser warnings remain non-blocking |
| Worksheet | Available immediately from `DMPDesign`; warn clearly if no RSP/zones or worksheet-required site fields are missing |
| Door chart | Preserve current rule: generate a worksheet first, then build the chart from that file |
| RemoteLink | Keep the project editable, but show a focused readiness message until account/local code is numeric and at least one installed RSP contributes zones; no conversion step |
| Reopen/edit | Normal DMPS load path; no source document requested |

## 8. Recommended architecture

### 8.1 Single initialization boundary

Add a pure factory in `scripts/session.py`:

```python
def create_blank_session(
    site_info: SiteInfo,
    *,
    title_block_updates: Mapping[str, str] | None = None,
) -> Session:
    design = DMPDesign(site_info=site_info, topology_source="manual")
    document = default_riser_document(design)
    for field_name, value in (title_block_updates or {}).items():
        if hasattr(document.title_block, field_name):
            setattr(document.title_block, field_name, value)
    design.riser_document = document
    return Session(design=design, source_kind="manual", source_name="")
```

Its contract:

1. Create the existing `DMPDesign(site_info=site_info)`.
2. Set `design.topology_source = "manual"`.
3. Create `design.riser_document = default_riser_document(design)` so the empty canonical graph is intentional and cannot be re-derived from compatibility fields.
4. Apply only recognized `RiserTitleBlock` fields from `title_block_updates`.
5. Return `Session(design=design, source_kind="manual", source_name="")` with normal safe `RemoteLinkConfig` defaults.

The factory must not create hardware, zones, connections, or a second data model.

### 8.2 UI orchestration

Create `scripts/new_project_dialog.py` as a focused UI component. It should return existing domain values (`SiteInfo` plus title-block updates) or `None` on cancel. It owns no persistence and no alternate project representation.

`App._create_new_project()` should preserve the current screen/project until the dialog is accepted and the new file is safely created. It should:

1. honor the current generation-in-progress interlock,
2. open the setup dialog without tearing down the current project,
3. if Create was chosen, run the existing close/dirty guard for the current project,
4. apply the existing per-machine install-tech/IP/gateway defaults and today's install date to otherwise empty `SiteInfo` fields, matching other newly created sessions without making those fields required,
5. call `create_blank_session`,
6. assign `unique_session_path(session.design)`,
7. call `save_session(session)` to create the initial record,
8. only after that save succeeds, replace the active project and clear `pdf_path`, `dmp_path`, and `door_chart_path`, and
9. call `_enter_editor(session, initial_tab="RISER")`.

Cancel leaves the current project/home state untouched. A dialog or initial-save failure must likewise leave the prior active project intact.

The current `_process_another()` remains the Close Project implementation and is no longer used for Create New Project.

### 8.3 Editor entry contract

Extend editor entry with an optional `initial_tab` argument defaulting to `"ZONES"`: `App._enter_editor(self, session: Session, *, initial_tab: str = "ZONES")`.

Pass that selection into `EditorFrame` or set it once after construction. PDF, XLSX, and DMPS callers omit the argument and continue to land on ZONES; Create New Project passes RISER.

### 8.4 Manual provenance and messaging

Use `source_kind="manual"` and `topology_source="manual"`. These are new values in existing open strings, not schema changes.

Update the SPLITTERS banner to handle three states explicitly:

- `riser`: wiring extracted from a source riser;
- `auto-derived`: import fallback that must be checked against the source;
- `manual`: wiring is being authored here and should be marked reviewed when complete.

Unknown/legacy empty values retain the current conservative warning.

### 8.5 Title and site metadata

- Add address line 1 and address line 2 to the existing SITE tab.
- Seed both `SiteInfo` and `RiserTitleBlock` in the setup factory.
- Preserve title-block independence after creation for backward compatibility.
- Add an explicit **Copy from SITE** action in the RISER title section that updates only `school_name`, `local_code`, `address`, and—after confirmation when it differs—`project_title`.
- Never overwrite `drawing_title`, `system`, `sheet_number`, `drawn_by`, `checked_by`, issue date, or revisions from SITE.

### 8.6 Save As

Extend `EditorFrame.save()` to accept an optional target path and add `App._save_as()` around a native file dialog. On success:

- the complete current session is written through `save_session(session, target)`,
- `session.path` switches to the new file,
- the old base project remains intact,
- any recovery file associated with the old current path is cleared,
- future recovery and Save operations use the new path, and
- the title/save state updates without altering `source_kind`.

Default the dialog to the active Sessions folder and `.dmps`. Saving elsewhere remains valid, but only projects under the configured Sessions folder appear in the current recent-project scan; the dialog/help should say so unless a later project-history registry is added.

### 8.7 Target-specific readiness

Do not make global editor validation depend on a source kind. Add pure readiness helpers for exports so messages reflect actual requirements:

- `worksheet_readiness_issues(design, session)` — missing worksheet fields, no RSPs/zones, topology review.
- `remotelink_readiness_issues(design, config)` — numeric account/local code, installed RSP zones, and existing RemoteLink configuration warnings.
- existing `validate_riser(design, document)` — drawing-specific geometry, topology, title, cable, location, and print warnings.

Keep the current “warnings do not block worksheet/riser generation” policy. RemoteLink's hard format requirements should be reported before the passphrase dialog rather than failing only during XML construction.

### 8.8 LX bus-aware splitter creation

Extend the existing API rather than introducing a manual-only helper: `add_splitter(design: DMPDesign, splitter_type: str, location: str | None = None, *, lx_bus: str = "500") -> Splitter`.

Validate `lx_bus` against `{"500", "600", "700", "800", "900"}` for LX splitters, keep the current default, number within each bus using the existing bus-aware helper, and show the bus picker only when LX is selected. Imported and existing callers remain source-compatible.

## 9. Files and modules likely to change

| File | Responsibility in this change |
|---|---|
| `scripts/app.py` | Home CTA, File menu/shortcut wiring, `_create_new_project`, initial-tab selection, Save As orchestration, source label, help text |
| `scripts/new_project_dialog.py` (new) | Lightweight cross-platform setup dialog returning existing model values |
| `scripts/session.py` | `create_blank_session`, manual source round-trip coverage, normal schema-8 save path |
| `scripts/editor_frame.py` | Optional initial tab, SITE address fields, Save target support, explicit title sync callback if hosted here |
| `scripts/editor_tabs.py` | Manual-topology banner copy and LX bus choice in the existing splitter dialog |
| `scripts/hardware.py` | Optional bus argument for the existing splitter factory; no parallel hardware model |
| `scripts/validation.py` | Manual-topology wording and/or export readiness helpers |
| `scripts/riser_editor.py` | Explicit Copy from SITE control in the existing title-block inspector |
| `README.md` | Create New Project workflow, source-free project behavior, Save As/recent-project note |
| `tests/test_create_new_project_workflow.py` (new) | Pure and end-to-end blank-project contract |
| `tests/test_app_create_new_project.py` (new) | Home/menu/dialog/editor-entry behavior |
| Existing session, hardware, validation, riser, app, and release integration tests | Backward-compatibility and import regressions |

No change is expected in `parse_zone_schedule.py`, `prepare_pdf.py`, `extract_topology.py`, or `parse_dmp_worksheet.py`; the new path should bypass those modules.

## 10. Data-model/schema changes and decision

### Decision

Keep `SCHEMA_VERSION = 8` for the initial implementation.

### Rationale

- All required site fields already exist in `SiteInfo`.
- All drawing metadata already exists in `RiserTitleBlock`.
- Empty hardware lists and an empty canonical graph are already valid.
- Stable locations, connections, and the riser document already persist.
- `source.kind` and `topology_source` are unrestricted strings; adding `manual` changes values, not structure.
- Current v1.7-era readers will open a schema-8 manual project. They may show old fallback wording, but they will not discard its data.

### When a schema bump would be required

Bump the schema only if implementation adds durable fields such as a typed project mode, persistent workflow locks, title-field link state, or a new equipment concept. None is recommended for this feature.

## 11. Migration and backward-compatibility strategy

- Preserve `_start_parse`, `_start_load_worksheet`, and `_open_session_path` as separate existing handlers.
- Preserve ZONES as the default tab for every existing caller.
- Keep `source.kind` and `source.name` in the same JSON location.
- Keep the canonical graph-to-legacy projection on save/export.
- Do not rewrite a loaded title block from SITE data automatically.
- Do not auto-add or renumber equipment in an existing project.
- Keep the default LX bus at 500 for all existing `add_splitter` callers.
- Add literal older-schema fixtures to ensure migration behavior does not depend on the current serializer.
- Confirm that a manual schema-8 file can be opened, edited, and resaved by the new build and that older schema-8 builds preserve its unknown source string.

## 12. Detailed implementation phases

### Task 1: Phase 1 — Characterize and create the blank session

**Files:** `tests/test_create_new_project_workflow.py`, `scripts/session.py`

- [ ] Add a failing test that `create_blank_session(SiteInfo(school_name="MANUAL SITE"))` returns a normal `Session` with `source_kind == "manual"`, schema-compatible default RemoteLink config, no imported inventory, `topology_source == "manual"`, an explicit empty graph, and a non-`None` riser document.
- [ ] Add a failing test that the title-block setup mapping fills school, local code, two-line address, project title, sheet number, prepared by, and issue date while retaining `RiserTitleBlock` defaults for omitted fields.
- [ ] Implement the minimal factory using only `DMPDesign`, `SiteInfo`, `Session`, `RiserTitleBlock`, and `default_riser_document`.
- [ ] Add save/load and recovery round-trip tests for `source_kind="manual"`, empty connections, title data, and stable equipment-location data.
- [ ] Assert the saved file still has `schema_version == 8`.
- [ ] Run `venv/bin/pytest -q tests/test_create_new_project_workflow.py tests/test_session.py tests/test_release_persistence.py`.

### Task 2: Phase 2 — Add the first-class home and menu flow

**Files:** `scripts/new_project_dialog.py`, `scripts/app.py`, `scripts/editor_frame.py`, `tests/test_app_create_new_project.py`

- [ ] Add a UI test that the home screen exposes a visible **Create New Project** action without removing the current PDF/XLSX/DMPS drop/browse target.
- [ ] Add a menu test that **Create New Project** and `Cmd/Ctrl+N` call `_create_new_project`, while **Close Project** still calls the existing close/reset path.
- [ ] Build the setup dialog with the exact field mapping in section 7.2, keyboard Return/Create and Escape/Cancel behavior, and no file write on cancel.
- [ ] Add a test that a missing site name keeps the dialog open with a focused inline/message error; every other field may be blank.
- [ ] Implement `_create_new_project` with the existing dirty-close guard, factory, collision-safe path, atomic initial save, and cleared PDF/XLSX runtime paths.
- [ ] Add an `initial_tab="ZONES"` default to editor entry and test that only Create New Project opens RISER.
- [ ] Add a runtime GUI test that the created blank project shows `device:MSP`, the title block, and the existing **Add Device** button.
- [ ] Run `venv/bin/pytest -q tests/test_app_create_new_project.py tests/test_full_app_project_open.py tests/test_app_recent_projects.py tests/test_riser_app_integration.py`.

### Task 3: Phase 3 — Make manual topology honest and fully authorable

**Files:** `scripts/editor_tabs.py`, `scripts/validation.py`, `scripts/hardware.py`, `tests/test_validation.py`, `tests/test_hardware.py`, `tests/test_riser_hardware_forms.py`

- [ ] Add tests for separate banner/validation copy for `riser`, `auto-derived`, `manual`, and legacy-empty topology provenance.
- [ ] Implement manual copy that never references failed extraction or a source riser.
- [ ] Add failing hardware tests for the first LX500, LX600, and LX900 splitter IDs, independent per-bus numbering, invalid buses, and unchanged KP numbering.
- [ ] Extend `add_splitter` with the optional `lx_bus` keyword and preserve the default 500 behavior.
- [ ] Add a dynamic LX bus field to the existing splitter dialog and prove RISER still launches that same form.
- [ ] Add a keypad-form test demonstrating that the first keypad sourced from MSP produces the existing service-keypad edge and survives save/reopen.
- [ ] Run `venv/bin/pytest -q tests/test_hardware.py tests/test_validation.py tests/test_riser_hardware_forms.py tests/test_topology_service.py`.

### Task 4: Phase 4 — Complete metadata and title-block behavior

**Files:** `scripts/editor_frame.py`, `scripts/riser_editor.py`, `tests/test_release_editor_integration.py`, `tests/test_riser_control_audit.py`, `tests/test_create_new_project_workflow.py`

- [ ] Add address line 1 and line 2 to `_SITE_FIELDS` and test edits mutate `SiteInfo`, persist, and reach worksheet/door-chart/RemoteLink staging.
- [ ] Add the RISER **Copy from SITE** action and a controller-level test for its exact field scope.
- [ ] Require confirmation before overwriting a different nonempty `project_title`; cancel must be a no-op.
- [ ] Prove SITE edits do not silently alter title blocks in existing loaded projects until the explicit action is used.
- [ ] Prove title edits remain riser-only and do not rewrite `SiteInfo`.
- [ ] Run `venv/bin/pytest -q tests/test_create_new_project_workflow.py tests/test_riser_control_audit.py tests/test_release_editor_integration.py tests/test_rl_account.py tests/test_release_chart_layout.py`.

### Task 5: Phase 5 — Save As and project lifecycle

**Files:** `scripts/app.py`, `scripts/editor_frame.py`, `scripts/session.py`, `tests/test_app_create_new_project.py`, `tests/test_session.py`

- [ ] Add a failing test that Create New Project writes an initial collision-safe `.dmps`, appears in recents, and does not overwrite a same-named project.
- [ ] Extend `EditorFrame.save(path=None)` and test that ordinary Save behavior is unchanged.
- [ ] Add **Save As…** and test full data preservation, current-path switching, old-file retention, and old-recovery cleanup.
- [ ] Test Save As cancellation and target-write failure without changing `session.path` or dirty state.
- [ ] Test that recovery after Save As is written beside and recovered through the new path.
- [ ] Test project rename behavior explicitly: editing the site name changes future output slugs but not the existing session filename until Save As.
- [ ] Run `venv/bin/pytest -q tests/test_session.py tests/test_app_create_new_project.py tests/test_app_recent_projects.py tests/test_output_dir_rename.py`.

### Task 6: Phase 6 — Export readiness and source-free end-to-end coverage

**Files:** `scripts/validation.py`, `scripts/app.py`, `tests/test_create_new_project_workflow.py`, `tests/test_riser_workflows.py`, `tests/test_release_persistence.py`, `tests/test_rl_account.py`

- [ ] Add a pure test that an MSP-only manual project can open and generate a riser after the existing warning/confirmation path.
- [ ] Add worksheet readiness tests for no RSPs, no zones, missing worksheet site fields, and a ready manually populated design.
- [ ] Add RemoteLink readiness tests for blank/nonnumeric account, no installed RSP zones, and a ready manually populated design.
- [ ] Surface those issues before the relevant export dialog without introducing a conversion or project-mode flag.
- [ ] Add an end-to-end test that manually adds an RSP, splitter, service keypad, locations, connections, and cable metadata; saves/reopens; then generates worksheet, door chart, riser PDF/SVG, and verified RemoteLink XML from the same session.
- [ ] Assert all exports leave the in-memory design, graph, locations, title document, and RemoteLink configuration unchanged.
- [ ] Run `venv/bin/pytest -q tests/test_create_new_project_workflow.py tests/test_riser_workflows.py tests/test_release_persistence.py tests/test_rl_account.py tests/test_validation.py`.

### Task 7: Phase 7 — Regression, documentation, and native parity

**Files:** `README.md`, `scripts/app.py` help text, relevant release documentation/tests

- [ ] Update Workflow step 1 to present Create New Project and Import/Open as peers.
- [ ] Correct SITE/address documentation and describe the intentional title-block separation.
- [ ] Document service-keypad convention, manual topology review, Save As/recent behavior, and downstream readiness.
- [ ] Add mocked routing tests proving PDF still calls the PDF parser, XLSX still calls the worksheet parser/shape guard, and DMPS still calls the session/recovery loader; Create New Project must call none of them.
- [ ] Run the full suite: `venv/bin/pytest -q`.
- [ ] Run import/compile checks used by the current release process.
- [ ] Execute the manual acceptance matrix below on packaged macOS and Windows builds.

## 13. Automated test plan

### Pure model tests

- Blank-session factory maps setup values exactly.
- Empty graph remains authoritative through save and recovery.
- Manual source/provenance round-trips under schema 8.
- Existing older schemas migrate unchanged.
- Unknown/new source strings do not affect loading.

### Hardware/topology tests

- Empty-design add flows for RSP/PS/zones, splitter, and keypad.
- Service keypad direct-MSP edge.
- LX500-LX900 creation and bus-specific validation.
- Connection compatibility, occupancy, cycle, disconnect, and legacy projection.
- Number allocation, capacity, removal cascades, and unplaced reconciliation.

### UI tests

- Home CTA, File menu, and shortcut.
- Setup field validation and cancel safety.
- Initial RISER tab only for Create New Project.
- MSP-only scene renders.
- Existing Add/Edit/Remove Device forms are reused.
- Manual topology copy.
- SITE address editing and explicit title synchronization.
- Save, Save As, close guard, recent project, and recovery.
- Tk runtime tests at the existing supported minimum window size.

### Export tests

- Riser from MSP-only and from a connected manual design.
- Worksheet from manual inventory without PDF access.
- Door chart only after worksheet creation.
- RemoteLink readiness and successful verified export after zones/account exist.
- Revision naming for blank/manual sites and behavior after site rename.

### Import regression tests

- PDF parse route and source labeling unchanged.
- XLSX guard, `dmp_path`, unique path, and immediate door-chart behavior unchanged.
- DMPS schema/recovery path unchanged.
- Existing projects still land on ZONES and retain saved riser geometry/title data.

## 14. Manual acceptance criteria

Run every item on both macOS and Windows packaged builds.

1. Launch to home; **Create New Project** and the existing drop/browse import surface are both visible.
2. Cancel Create New Project; no project or recovery file is created.
3. Create a site with only a name; no source chooser or parser runs.
4. Confirm RISER opens first with the existing MSP, title block, inspector, and Add Device control.
5. Add a direct MSP service keypad, a KP splitter/keypad, an LX splitter, and an RSP/power supply.
6. Assign/edit locations, including MSP; confirm RSP/PS stay paired.
7. Connect compatible ports; reject occupied, cyclic, cross-family, and wrong-LX-bus connections with current messages.
8. Edit cable type, status, quantity, label, route, and label position/visibility.
9. Add and edit markup; use undo/redo; save and reopen; confirm exact state.
10. Edit SITE address, then explicitly copy SITE identity into the riser title block; verify no silent two-way overwrite.
11. Save As; confirm the old file remains, the new file becomes current, and recovery follows the new file.
12. Generate a riser without a worksheet or source document.
13. Generate a worksheet, then a door chart, from manually created zones/inventory.
14. Verify RemoteLink clearly explains missing prerequisites, then succeeds after numeric account/local code and RSP zones exist.
15. Close and reopen from Recent; no PDF/XLSX is requested.
16. Open representative existing PDF, XLSX, schema-1/2, and schema-8 projects and repeat their normal workflows.
17. Confirm `Cmd+N`/`Ctrl+N`, `Cmd+O`/`Ctrl+O`, Save, Save As, Close, full-screen, and Escape behavior on each platform.

## 15. Risks and edge cases

### Project identity and paths

- Same-name sites must use `unique_session_path`; never overwrite silently.
- A later school-name edit must not silently rename the session file.
- Output revision lookup is slug-based; changing the name intentionally starts a different output filename series.
- Changing the configured output folder changes the active Sessions/recent-project folder under current behavior.

### Initial persistence

- Initial save must occur only after Create New Project, never while the dialog is incomplete or canceled.
- If initial save fails, remain outside the editor and show the existing style of actionable error; do not create an in-memory project that cannot recover.

### Title and site duplication

- Existing projects may intentionally have a drawing title/address different from SITE. Never auto-normalize on load.
- Setup and explicit Copy from SITE are the only synchronization points.

### Empty and partial designs

- MSP-only is valid editing state.
- Orphan/unplaced equipment is valid work-in-progress and must remain saveable.
- Worksheet and riser warnings remain non-blocking.
- RemoteLink has genuine hard requirements and should explain them before passphrase entry.

### Numbering and buses

- RSP module number and zone address are intentionally independent.
- Deleted ranges are not renumbered; the allocator reuses only safe free ranges.
- LX splitter numbering must be unique within the correct bus while IDs remain globally unique.
- Keypad 1/direct-MSP service convention must not create an extra persisted equipment type.

### Existing technical debt

- The roadmap's asynchronous export snapshot/epoch issue is pre-existing. Do not expand this feature into an export-service rewrite, but ensure new tests do not rely on mutable live state during background generation.
- `app.py` and `riser_editor.py` are already large. The dedicated setup-dialog module and pure session factory keep this feature from adding another embedded subsystem.

## 16. Historical v1.3.0 plan comparison

### Source availability caveat

The named `c1-riser-designer-v1.3.0-implementation-plan.md` is not present in the current working tree, current/remote branches, release tags, deleted-file history, reachable/unreachable Git objects searched for its title, or the local Documents/Desktop/Downloads locations searched during this audit. A literal line-by-line classification is therefore not possible without a copy of that document.

The classification below is grounded in identifiable v1.3-era intent, current code, the v1.3 README, and the post-v1.3 riser implementation commits (`187f965` through `1ba20bb`, later merged for v1.4.0). If the original document is supplied later, add an appendix mapping each of its numbered requirements to this table.

### Already implemented

- Riser is a normal tab in the unified project editor.
- The electrical topology is a persisted, port-specific graph.
- The drawing has a persisted title block, device/location elements, orthogonal routes, annotations, z-order, and unplaced tray.
- MSP, LX/KP splitters, RSPs, keypads, paired power supplies, and zones share the worksheet/project models.
- Add/edit/remove hardware in RISER reuses the domain-tab forms and mutation rules.
- Equipment locations have stable shared identities and global rename/assignment behavior.
- Cable metadata, custom labels, route editing, callout visibility/position, and undo/redo are implemented.
- Layout preview/apply, auto-layout, detailed symbols, validation jumps, PDF/SVG generation, and revisioned riser bundles are implemented.
- Riser and RemoteLink data coexist in one backward-migrated `.dmps` project.
- macOS and Windows share one UI/codebase.

### Obsolete or superseded

- Any proposal for a standalone Riser Designer application or replacement editor is superseded by `RiserTab` inside `EditorFrame`.
- Any separate riser project file/schema is superseded by schema-8 `.dmps` sessions.
- Any design in which PDF geometry remains the editable drawing background/source of truth is superseded by the logical graph plus generated `RiserDocument` scene.
- Any requirement to duplicate splitter, keypad, RSP, cable, or location state in a drawing-only model is superseded by shared `DMPDesign` ownership.
- v1.3 schema numbers, UI file names, modal job-details forms, and generate-then-exit flows are superseded by current schema 8, the tabbed editor, in-editor repeatable generation, and current modules.
- Treating an exported worksheet as the working document is superseded by the saved session as source of truth.

### Still relevant

- A source-free Create New Project/New Riser entry point.
- Lightweight initial site and title metadata.
- Immediate entry into the existing riser editor.
- Manual addition and configuration of all supported equipment and wiring.
- Shared persistence and reopening with imported projects.
- Title-block editing, equipment locations, numbering, validation, and cross-platform behavior.
- Protection of PDF/XLSX import and old project compatibility.
- Tests proving manual creation through every downstream output.

### Never implemented or still absent

- A home/menu action that actually creates a blank project.
- A blank-session initialization boundary with manual provenance.
- Starting Create New Project directly on RISER.
- Source-appropriate manual topology messaging.
- SITE address editing that matches the help text.
- Explicit SITE-to-title-block synchronization.
- Project Save As.
- A first-save/recovery policy for source-free projects.
- RemoteLink readiness guidance before its generation dialog.
- Bus selection when manually adding LX600-LX900 splitters.
- An end-to-end blank-site regression test.

## 17. Clear definition of done

The feature is done when:

- A user can launch the app, choose Create New Project, enter only a site name, and reach the existing RISER tab without selecting or importing a file.
- The created session is a normal schema-8 `.dmps` with manual provenance and a collision-safe path.
- The initial drawing contains the implicit MSP and current title block, with no invented downstream equipment.
- All existing supported hardware, location, topology, cable, label, markup, and layout operations work through their current shared implementations.
- Save, recovery, reopen, recent projects, and Save As work for the manual project.
- Riser generation never requires a source document.
- Worksheet and door-chart flows work once the manual design contains appropriate data, using the existing generation path.
- RemoteLink gives clear readiness guidance and succeeds from the same project once its genuine requirements are met.
- Existing PDF, XLSX, and DMPS workflows behave the same as before.
- Existing schemas migrate without data loss and schema remains 8 unless implementation introduces an unplanned persistent field.
- The full automated suite is green and the acceptance matrix passes on packaged macOS and Windows builds.

## 18. Smallest safe implementation sequence

The smallest safe, reviewable sequence is:

1. Add characterization tests and `create_blank_session()` using only existing schema-8 models.
2. Add Create New Project on home/File menu, create the initial collision-safe `.dmps`, and enter the existing editor with RISER selected.
3. Add manual topology provenance/copy so the new project does not claim a failed PDF extraction.
4. Prove MSP-only render plus existing Add Device → save/reopen → riser export, while running the PDF/XLSX/DMPS routing regressions.

That slice delivers a safe first-class blank riser without touching import parsing or creating a second model. Address/SITE synchronization, Save As, bus-aware LX creation, and downstream readiness can then land as focused follow-up phases on the same project representation.
