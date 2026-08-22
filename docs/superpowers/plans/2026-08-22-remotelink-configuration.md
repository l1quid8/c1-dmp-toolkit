# RemoteLink Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed RemoteLink clone-and-patch generator with a structured, inspectable account model and let field technicians configure supported RemoteLink programming from the project editor without silently enabling self-arming or ambush behavior.

**Architecture:** The bundled decrypted template remains the source of dealer defaults. A strict `AccountDoc` parser preserves its XML byte-for-byte, typed configuration is stored in the `.dmps` session, and generation applies explicit model edits before an intent-aware safety gate serializes and encrypts the account. Configuration is distributed across the existing ZONES, SITE, and KEYPADS surfaces plus a new REMOTELINK tab; every surface feeds one shared receipt/summary implementation.

**Tech Stack:** Python 3.13, dataclasses, CustomTkinter 5.2.2, tkinter/ttk, PyCryptodome AES, pytest, JSON `.dmps` sessions; no new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-07-29-remotelink-config-design.md`

## Global Constraints

- Work only on `feature/remotelink-configuration` in the isolated worktree.
- Preserve the encrypted export format: AES-128-ECB, MD5(passphrase), latin-1 plaintext, uppercase hexadecimal ciphertext.
- Preserve RemoteLink's DataType-1 codec: latin-1 text, NUL-pad to a multiple of three, base64 with no `=` padding.
- `parse_account_xml(text).serialize() == text` must hold byte-for-byte for the bundled template.
- The bundled template and committed tests must never contain a real school, account, IP address, user code, or panel serial number.
- Missing `remotelink` and `rl_type` values in old `.dmps` files must load with safe defaults.
- Dangerous behavior defaults off. Generation must fail if an output contains unrequested schedule, ambush, morning-ambush, auto-arm, or auto-disarm programming.
- Explicitly requested dangerous behavior is allowed only after UI confirmation and must remain visibly flagged in preview, generation receipt, summary sidecar, and inspector.
- Do not guess undocumented RemoteLink enum codes. Non-default choices become available only after Task 6's fake-account calibration proves their stored values.
- Use test-first red/green/refactor cycles for every production behavior.
- Do not merge, push, tag, or publish until the complete verification task passes.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/rl_injector/account_doc.py` | Byte-faithful document model/parser, safety findings, summary model, text renderer |
| `scripts/rl_injector/rl_config.py` | Persisted RemoteLink dataclasses, defaults/resolution, enum maps, validation, safety intent |
| `scripts/rl_injector/xml_export.py` | Apply design/config to `AccountDoc`, verify, encrypt, inspect, preview, sidecar |
| `scripts/rl_injector/schema.py` | Stage zones/keypads from `DMPDesign`, including zone-type overrides |
| `scripts/session.py` | Serialize/deserialize `Session.remotelink` and `ZoneInfo.rl_type` safely |
| `scripts/parse_dmp_worksheet.py` | Add the optional `ZoneInfo.rl_type` field only |
| `scripts/editor_remotelink.py` | New REMOTELINK configuration tab and shared receipt widget |
| `scripts/editor_zones.py` | Editable RemoteLink TYPE column |
| `scripts/editor_tabs.py` | RemoteLink keypad fields on keypad cards |
| `scripts/editor_frame.py` | SITE communication group, tab wiring, receipt refresh, confirmation routing |
| `scripts/validation.py` | REMOTELINK warnings and tab badges |
| `scripts/app.py` | Passphrase/receipt generation dialog and project-independent account inspector |
| `tests/test_account_doc.py` | Parser, round-trip, safety, summary tests |
| `tests/test_rl_config.py` | Defaults, persistence helpers, validation, intent tests |
| `tests/test_rl_account.py` | Export parity, mapping, generation, inspector, sidecar tests |
| `tests/test_session.py` | Session compatibility/round-trip tests |
| `README.md` | Operator workflow and safety behavior |

---

### Task 1: Structured RemoteLink document model and strict parser

**Files:**
- Create: `scripts/rl_injector/account_doc.py`
- Create: `tests/test_account_doc.py`
- Modify: `scripts/rl_injector/xml_export.py`

**Interfaces:**
- Produces: `Field`, `Row`, `Table`, `AccountDoc`, `parse_account_xml(text: str) -> AccountDoc`, `_b64(text: str) -> str`, `_unb64(raw: str) -> str`.
- Preserves: `rl_injector.xml_export._b64` and `_unb64` as re-exports for existing callers.

- [ ] **Step 1: Write the parser/model tests first**

  Add tests that assert:

  ```python
  def test_bundled_template_round_trips_byte_identically():
      text = BUNDLED_TEMPLATE.read_text(encoding="latin-1")
      assert parse_account_xml(text).serialize() == text

  def test_statlist_is_a_row_and_empty_schedule_list_is_a_table():
      doc = parse_account_xml(BUNDLED_TEMPLATE.read_text(encoding="latin-1"))
      assert isinstance(doc.find("StatList"), Row)
      assert isinstance(doc.find("TimeSchedsList"), Table)
      assert doc.table("TimeSchedsList").rows == []

  def test_ambush_fields_keep_per_occurrence_datatypes():
      doc = parse_account_xml(BUNDLED_TEMPLATE.read_text(encoding="latin-1"))
      assert doc.row("SysRpts").require("AMBUSH").data_type == "5"
      assert doc.row("OutOpts").require("AMBUSH").data_type == "3"
  ```

  Cover an empty DataType-1 field, negative integer, datetime, ragged Profiles rows, non-self-closing empty tables, and rejection of extra attributes, mismatched closes, nested containers inside rows, inter-tag text, and trailing content.

- [ ] **Step 2: Run the new tests and verify RED**

  Run: `../../venv/bin/python -m pytest tests/test_account_doc.py -q`

  Expected: import failure because `rl_injector.account_doc` does not exist.

- [ ] **Step 3: Implement the document model**

  Implement:

  ```python
  @dataclass
  class Field:
      name: str
      data_type: str
      raw: str
      @property
      def text(self) -> str: ...
      def set_text(self, value: str) -> None: ...
      def serialize(self) -> str: ...

  @dataclass
  class Row:
      name: str
      fields: list[Field]
      def find(self, tag: str) -> Field | None: ...
      def require(self, tag: str) -> Field: ...
      def text(self, tag: str, default: str | None = None) -> str | None: ...
      def set(self, tag: str, value: str) -> bool: ...
      def clone(self) -> "Row": ...
      def serialize(self) -> str: ...

  @dataclass
  class Table:
      name: str
      rows: list[Row]
      def serialize(self) -> str: ...

  @dataclass
  class AccountDoc:
      nodes: list[Row | Table]
      def find(self, name: str) -> Row | Table | None: ...
      def row(self, name: str) -> Row: ...
      def table(self, name: str) -> Table: ...
      def find_row(self, name: str) -> Row | None: ...
      def find_table(self, name: str) -> Table | None: ...
      def iter_rows(self) -> Iterator[Row]: ...
      def serialize(self) -> str: ...
  ```

  `Row.set()` is a no-op returning `False` when a partial test template lacks a field. `Table.serialize()` always emits `<Name></Name>` for an empty table.

- [ ] **Step 4: Implement the cursor parser**

  Require the literal `<Panels><Panel>` prefix, classify children structurally, consume every character exactly once, and raise `InjectorError` with an offset/snippet at the first unsupported character. Do not use ElementTree for serialization.

- [ ] **Step 5: Move the codec and retain compatibility**

  Move `_b64`/`_unb64` verbatim to `account_doc.py`; replace their definitions in `xml_export.py` with:

  ```python
  from .account_doc import _b64, _unb64
  ```

- [ ] **Step 6: Run targeted and full tests**

  Run:

  ```bash
  ../../venv/bin/python -m pytest tests/test_account_doc.py tests/test_rl_account.py -q
  ../../venv/bin/python -m pytest tests -q
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add scripts/rl_injector/account_doc.py scripts/rl_injector/xml_export.py tests/test_account_doc.py
  git commit -m "Add a byte-faithful RemoteLink account document model"
  ```

---

### Task 2: Safety findings, intent-aware gate, summary, and text receipt

**Files:**
- Modify: `scripts/rl_injector/account_doc.py`
- Modify: `scripts/rl_injector/errors.py`
- Modify: `tests/test_account_doc.py`

**Interfaces:**
- Produces: `SafetyKind`, `SafetyFinding`, `SafetyIntent`, `collect_safety_findings`, `verify_account_doc`, `AccountSummary`, `summarize_account`, `render_text`.
- Consumes: Task 1 `AccountDoc` APIs.

- [ ] **Step 1: Write failing tests for all six safety classes**

  Mutate a parsed bundled document through model methods and assert findings for:

  ```python
  SafetyKind.SCHEDULE
  SafetyKind.AREA_SCHEDULE_LINK
  SafetyKind.AMBUSH_REPORTS
  SafetyKind.AMBUSH_OUTPUT
  SafetyKind.MORNING_AMBUSH
  SafetyKind.AUTO_ARM_DISARM
  ```

  Assert clean template + empty `SafetyIntent()` passes, unintended settings raise one `AccountSafetyError` listing every finding, and intended kinds pass without removing warnings from the summary.

- [ ] **Step 2: Verify RED**

  Run: `../../venv/bin/python -m pytest tests/test_account_doc.py -q`

  Expected: missing safety APIs.

- [ ] **Step 3: Add the error and safety types**

  ```python
  class AccountSafetyError(InjectorError):
      """Generated programming contains dangerous behavior not requested by the operator."""

  class SafetyKind(str, Enum): ...

  @dataclass(frozen=True)
  class SafetyFinding:
      kind: SafetyKind
      message: str

  @dataclass(frozen=True)
  class SafetyIntent:
      allowed: frozenset[SafetyKind] = frozenset()
  ```

  `verify_account_doc(doc, intent)` computes findings and raises only for kinds not in `intent.allowed`.

- [ ] **Step 4: Write failing summary tests**

  Assert identity, receiver, address, schedules, links, ambush values, areas, users, 61 template zones, spare count, keypad list, and safety warnings. Assert clean text has no warning glyph; unsafe text contains `⚠` and names each active behavior.

- [ ] **Step 5: Implement summary/rendering**

  `summarize_account(doc)` must tolerate absent blocks in mini templates and always reuse `collect_safety_findings(doc)`. `render_text(summary)` produces stable mono-friendly sections: Account, Safety checks, Users, and Contents.

- [ ] **Step 6: Verify and commit**

  ```bash
  ../../venv/bin/python -m pytest tests/test_account_doc.py -q
  ../../venv/bin/python -m pytest tests -q
  git add scripts/rl_injector/account_doc.py scripts/rl_injector/errors.py tests/test_account_doc.py
  git commit -m "Add RemoteLink safety verification and readable receipts"
  ```

---

### Task 3: Replace regex mutation with structured edits and prove parity

**Files:**
- Modify: `scripts/rl_injector/xml_export.py`
- Modify: `tests/test_rl_account.py`

**Interfaces:**
- Produces: `build_account_doc(acct, template_xml, *, sentinel=None, safety_intent=None) -> AccountDoc`.
- Preserves: `build_account_xml(acct, template_xml, *, sentinel=None) -> str`.

- [ ] **Step 1: Add a temporary legacy/model parity test**

  Preserve the current implementation as `_legacy_build_account_xml`. Write a test comparing it byte-for-byte with the new model path for bundled/mini templates and standard/no-keypad/long-zone-name designs.

- [ ] **Step 2: Verify RED because `build_account_doc` is absent**

  Run: `../../venv/bin/python -m pytest tests/test_rl_account.py -q`

- [ ] **Step 3: Implement `_apply_account_edits`**

  Apply edits in this order:

  1. Rebadge `Account.ID`, every `ACCOUNT_ID`, every `ACCOUNT_NUM`/`ACCT_NUM`, Account name, and every AreaInfo name by tag—not global text replacement.
  2. Apply non-empty address fields.
  3. Preserve legacy user-code derivation for users 1 and 9999.
  4. Rebuild ZoneInfo rows by cloning the first prototype for each TYPE.
  5. Rebuild DeviceInfo rows by cloning the first prototype.

  Parse → edit → `verify_account_doc(..., SafetyIntent())` → return `AccountDoc`.

- [ ] **Step 4: Make parity green, flip the public API, then delete regex**

  After parity passes, make `build_account_xml` serialize `build_account_doc`. Remove `_set`, `_field`, `_replace_list`, `_rebadge_identity`, `_legacy_build_account_xml`, temporary parity test, and `import re` in one change.

- [ ] **Step 5: Add precision and receiver tests**

  Add a decoy numeric field equal to `90000` and prove it is unchanged. Add `RECVR_NUM` to the mini template and prove a non-empty receiver writes while blank keeps the template value.

- [ ] **Step 6: Verify and commit**

  ```bash
  ../../venv/bin/python -m pytest tests/test_account_doc.py tests/test_rl_account.py -q
  ../../venv/bin/python -m pytest tests -q
  rg "_rebadge_identity|_replace_list|def _set\(|def _field\(|_legacy_build|^import re" scripts/rl_injector/xml_export.py
  git add scripts/rl_injector/xml_export.py tests/test_rl_account.py
  git commit -m "Route RemoteLink generation through the structured model"
  ```

  Expected `rg`: no matches.

---

### Task 4: Persist typed RemoteLink configuration and zone overrides

**Files:**
- Create: `scripts/rl_injector/rl_config.py`
- Modify: `scripts/parse_dmp_worksheet.py`
- Modify: `scripts/session.py`
- Create: `tests/test_rl_config.py`
- Modify: `tests/test_session.py`

**Interfaces:**
- Produces: `RemoteLinkConfig`, `RLUser`, `RLComm`, `RLArming`, `RLAdvanced`, `RLScheduleDay`, `RLKeypad`, `ResolvedRemoteLinkConfig`, `resolve_config(config, design)`.
- Adds: `Session.remotelink: RemoteLinkConfig`, `ZoneInfo.rl_type: str`.

- [ ] **Step 1: Write failing dataclass/default tests**

  Define expected defaults explicitly:

  ```python
  RemoteLinkConfig(
      account_num="",
      receiver_num="1",
      users=[],
      users_customized=False,
      comm=RLComm(connect_type="network", port="2001", serial=""),
      arming=RLArming(),
      keypads={},
  )
  ```

  `resolve_config` uses the current school code when account is blank and derives users 1 and 9999 until `users_customized` becomes true.

- [ ] **Step 2: Write failing session tests**

  Assert:

  - Saving emits schema version 2 and top-level `"remotelink"`.
  - A schema-1 file without `remotelink` loads safe defaults.
  - A schema-2 config round-trips nested users, comm, arming, schedule days, and integer keypad keys.
  - `ZoneInfo.rl_type` round-trips and missing values load as `""`.
  - An old application supporting schema 1 would reject schema 2 rather than silently erase configuration.

- [ ] **Step 3: Verify RED**

  Run: `../../venv/bin/python -m pytest tests/test_rl_config.py tests/test_session.py -q`

- [ ] **Step 4: Implement dataclasses and explicit JSON conversion**

  Do not rely on unvalidated `RemoteLinkConfig(**raw)`. Add `config_to_dict` and `config_from_dict` that coerce malformed/missing values to safe defaults, JSON keypad keys back to `int`, and schedule day keys to the fixed `sun`…`sat` set.

- [ ] **Step 5: Update Session persistence**

  Set `SCHEMA_VERSION = 2`, add `remotelink` to `Session`, write it at the top level, and load missing configuration with `RemoteLinkConfig()`.

- [ ] **Step 6: Add `rl_type` to `ZoneInfo` and its loader**

  Only allow `""`, `"NT"`, `"EX"`, `"SV"`; unknown serialized values normalize to `""`.

- [ ] **Step 7: Verify and commit**

  ```bash
  ../../venv/bin/python -m pytest tests/test_rl_config.py tests/test_session.py -q
  ../../venv/bin/python -m pytest tests -q
  git add scripts/rl_injector/rl_config.py scripts/parse_dmp_worksheet.py scripts/session.py tests/test_rl_config.py tests/test_session.py
  git commit -m "Persist RemoteLink account configuration in project sessions"
  ```

---

### Task 5: Stage zone overrides and validate RemoteLink configuration

**Files:**
- Modify: `scripts/rl_injector/schema.py`
- Modify: `scripts/rl_injector/rl_config.py`
- Modify: `scripts/validation.py`
- Modify: `tests/test_rl_account.py`
- Modify: `tests/test_rl_config.py`
- Modify: `tests/test_validation.py`

**Interfaces:**
- Produces: `derive_zone_type(zone)`, `validate_config(config, design) -> list[RLConfigIssue]`, optional `remotelink` argument on `validate_design`.

- [ ] **Step 1: Write zone-override tests**

  Prove `rl_type="EX"` overrides automatic Night, `rl_type=""` preserves current derivation, supervisory zones remain SV by default, and spares remain `--` regardless of override.

- [ ] **Step 2: Verify RED and implement the override**

  Read `ZoneInfo.rl_type` by number while staging master zones. Apply only allowed overrides and keep the spare invariant highest priority.

- [ ] **Step 3: Write validation tests**

  Assert warning-severity REMOTELINK issues for duplicate user numbers/codes, nonnumeric or <3-digit codes, port outside 1–65535, incomplete schedule day pairs, and invalid display-area masks. Assert valid defaults produce no RemoteLink issue.

- [ ] **Step 4: Implement validation and tab routing**

  Add `TAB_REMOTELINK`; make `validate_design(..., remotelink=None)` backward-compatible. Route account/users/arming/keypad-config issues to REMOTELINK and leave existing SITE IP validation unchanged.

- [ ] **Step 5: Verify and commit**

  ```bash
  ../../venv/bin/python -m pytest tests/test_rl_account.py tests/test_rl_config.py tests/test_validation.py -q
  ../../venv/bin/python -m pytest tests -q
  git add scripts/rl_injector/schema.py scripts/rl_injector/rl_config.py scripts/validation.py tests/test_rl_account.py tests/test_rl_config.py tests/test_validation.py
  git commit -m "Add RemoteLink zone overrides and configuration validation"
  ```

---

### Task 6: Calibrate undocumented RemoteLink enum and schedule mappings

**Files:**
- Modify: `scripts/rl_injector/rl_config.py`
- Create: `tests/fixtures/rl_field_maps.json`
- Modify: `tests/test_rl_config.py`

**Interfaces:**
- Produces immutable maps consumed by generation/UI: `CONNECT_TYPES`, `ARM_MODES`, `KEYPAD_DEVICE_TYPES`, `KEYPAD_COMM_TYPES`, `SCHEDULE_FIELD_MAP`.

- [ ] **Step 1: Create a fake RemoteLink calibration account**

  In RemoteLink, use only fabricated identity (`9999`, `CALIBRATION SCHOOL`, `192.0.2.10`, fake users). Export a baseline plus one export for each non-default choice. Use distinct schedule times per day so every serialized field is identifiable. Do not use or commit a real site export.

- [ ] **Step 2: Decrypt and compare only the relevant fields**

  Use `decode_account` and `parse_account_xml` to compare Account.CONNECT_TYPE, SysOpts.ARM_MODE, DeviceInfo TYPE/COMM_TYPE/DISP_AREAS, TimeScheds fields, and AreaTimeScheds links. Record only friendly-label → raw-value mappings in `tests/fixtures/rl_field_maps.json`; delete the encrypted/decrypted calibration exports afterward.

- [ ] **Step 3: Write failing mapping tests**

  Assert the known baseline from the bundled template (`network`, port 2001, `Area`, keypad type/communication, eight-area display mask) and every calibrated alternative from the fixture.

- [ ] **Step 4: Implement frozen mapping constants**

  UI choices derive from these maps; no arbitrary raw values enter production. Schedule field construction must use the calibrated weekday/date/time representation exactly.

- [ ] **Step 5: Verify and commit**

  ```bash
  ../../venv/bin/python -m pytest tests/test_rl_config.py -q
  git add scripts/rl_injector/rl_config.py tests/fixtures/rl_field_maps.json tests/test_rl_config.py
  git commit -m "Add verified RemoteLink field mappings from a fake account"
  ```

  **Execution gate:** Stop here if the fake calibration exports are unavailable. Continue with Tasks 7–12 only after this evidence exists; do not substitute inferred enum values.

---

### Task 7: Apply complete configuration, verify intent, and write sidecars

**Files:**
- Modify: `scripts/rl_injector/xml_export.py`
- Modify: `scripts/rl_injector/account_doc.py`
- Modify: `tests/test_rl_account.py`

**Interfaces:**
- Produces: `build_configured_account_doc(design, config, template_xml)`, `generate_configured_account_xml`, `preview_account_summary`, `inspect_account`.
- Consumes: Tasks 2–6 models and calibrated mappings.

- [ ] **Step 1: Add one failing read-back test per configured field**

  Decode generated output and assert account/receiver, connection type, site IP, port, serial, users, entry delays, exit delay, arm mode, all six dangerous settings, keypad name/type/comm/display areas, and zone TYPE.

- [ ] **Step 2: Add failing user-rebuild tests**

  Assert configured users carry number/name/code/profile and cloned rows retain unmodeled template fields.

- [ ] **Step 3: Add failing schedule/gate tests**

  Assert schedule-enabled configuration creates one calibrated TimeScheds row and Area 1 SCHED_1 link; output summary flags both. Assert equivalent output with false intent raises `AccountSafetyError`. Assert requested dangerous settings pass and remain warnings.

- [ ] **Step 4: Implement configuration edits**

  Resolve dynamic defaults, apply all documented fields through `Row.set`, rebuild users/keypads/zones by cloning prototypes, and construct schedule rows only from Task 6's verified map.

- [ ] **Step 5: Add post-application verification**

  Before serialization, compare identity/configured fields back from `AccountDoc` to resolved configuration. Missing or mismatched requested programming raises `InjectorError`; unexpected dangerous behavior remains `AccountSafetyError`.

- [ ] **Step 6: Implement generation, preview, inspector, and sidecar**

  ```python
  def generate_configured_account_xml(
      design, config, *, template_path, passphrase, out_dir,
      sentinel: int | None = None, write_summary: bool = True,
  ) -> Path: ...

  def preview_account_summary(design, config, template_path) -> str: ...

  def inspect_account(path, passphrase: str) -> AccountSummary: ...
  ```

  Generation writes `{account}_remotelink.xml` plus UTF-8 `{account}_remotelink_summary.txt`. Inspector reports safety findings without intent and never changes the file.

- [ ] **Step 7: Keep legacy API compatibility**

  Existing `generate_account_xml(design, account_num, receiver_num="", ...)` wraps a safe default config so all pre-feature callers/tests continue working.

- [ ] **Step 8: Verify and commit**

  ```bash
  ../../venv/bin/python -m pytest tests/test_account_doc.py tests/test_rl_account.py tests/test_rl_config.py -q
  ../../venv/bin/python -m pytest tests -q
  git add scripts/rl_injector/account_doc.py scripts/rl_injector/xml_export.py tests/test_rl_account.py
  git commit -m "Generate verified RemoteLink accounts from project configuration"
  ```

---

### Task 8: Add RemoteLink TYPE editing to the ZONES grid

**Files:**
- Modify: `scripts/editor_zones.py`
- Modify: `tests/test_rl_config.py`

**Interfaces:**
- Consumes: `ZoneInfo.rl_type`, `derive_zone_type`.
- Produces: displayed labels `Auto → Night`, `Night`, `Exit`, `Supervisory`, `Spare`.

- [ ] **Step 1: Add pure display/parse helper tests**

  Test mapping between stored `""/NT/EX/SV` and labels, automatic resolution, and locked spare behavior without constructing Tk.

- [ ] **Step 2: Verify RED and implement helpers**

- [ ] **Step 3: Add the `rl_type` tree column**

  Insert TYPE after DEVICE TYPE, adjust widths without shrinking DESCRIPTION below 160px, include TYPE in search text, and use a readonly ttk Combobox for inline editing.

- [ ] **Step 4: Preserve edit semantics**

  Spare rows must not open the TYPE editor. A TYPE commit updates only `zi.rl_type`, refreshes the row, calls `on_change`, and never rewrites device type/description.

- [ ] **Step 5: Run tests and manual headless construction smoke**

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/editor_zones.py tests/test_rl_config.py
  git commit -m "Add per-zone RemoteLink type overrides"
  ```

---

### Task 9: Add SITE communication and KEYPADS device configuration

**Files:**
- Modify: `scripts/editor_frame.py`
- Modify: `scripts/editor_tabs.py`
- Modify: `scripts/rl_injector/rl_config.py`
- Modify: `tests/test_rl_config.py`

**Interfaces:**
- SITE writes `session.remotelink.comm` while panel IP remains `SiteInfo.ip_address`.
- KEYPADS writes `session.remotelink.keypads[number]`.

- [ ] **Step 1: Add reconciliation helper tests**

  `effective_keypad(config, keypad)` derives `KEYPAD n` and verified template defaults; `reconcile_keypads` removes stale keys after hardware deletion and keeps existing settings through renumbering only when the keypad number survives.

- [ ] **Step 2: Implement SITE Panel group**

  Add connect-type dropdown, numeric port, and optional serial next to existing network fields. Every edit marks dirty, refreshes validation, and refreshes the receipt.

- [ ] **Step 3: Extend keypad cards**

  Add display name, verified device type, verified communication type, and displayed-area mask. Keep access-control fields out of scope.

- [ ] **Step 4: Reconcile on add/remove and refresh**

  Ensure removing a keypad cannot leave stale output programming.

- [ ] **Step 5: Verify UI smoke and full tests, then commit**

  ```bash
  ../../venv/bin/python -m pytest tests/test_rl_config.py tests/test_validation.py -q
  ../../venv/bin/python -m pytest tests -q
  git add scripts/editor_frame.py scripts/editor_tabs.py scripts/rl_injector/rl_config.py tests/test_rl_config.py
  git commit -m "Expose panel communication and keypad programming in the editor"
  ```

---

### Task 10: Build the REMOTELINK tab and live receipt

**Files:**
- Create: `scripts/editor_remotelink.py`
- Modify: `scripts/editor_frame.py`
- Modify: `scripts/validation.py`

**Interfaces:**
- Produces: `RemoteLinkTab.refresh()`, `RemoteLinkTab.refresh_receipt()`, `RemoteLinkTab.focus_issue(ref)`.
- Consumes: session config, `preview_account_summary`, existing theme/widgets.

- [ ] **Step 1: Implement the two-pane shell**

  Left pane: account/receiver, users, arming, collapsed advanced. Right pane: readonly mono receipt. Follow SPLITTERS sizing and `auto_hide_scrollbar` patterns.

- [ ] **Step 2: Implement users and ordinary arming fields**

  Add/edit/remove user rows; editing the first user sets `users_customized=True`. Provide a “Reset to site defaults” action. Entry delays, exit delay, and arm mode update typed config.

- [ ] **Step 3: Implement guarded advanced controls**

  The first transition from all-safe to any dangerous value in the current editor lifetime calls a confirmation callback. Cancel restores the previous value. The warning copy must explicitly name self-arming and silent ambush/duress signaling.

- [ ] **Step 4: Implement schedule rows**

  Show Sun–Sat enabled/open/close controls only when schedule is enabled; enforce `HH:MM` input and rely on validation for incomplete pairs.

- [ ] **Step 5: Wire cross-tab receipt refresh**

  After every SITE/ZONES/SPLITTERS/KEYPADS/POWER/REMOTELINK edit, call `remotelink_tab.refresh_receipt()` after synchronization. Receipt errors render inline and never escape a Tk variable trace.

- [ ] **Step 6: Add validation navigation and badges**

  Include REMOTELINK in `TAB_TITLES`, footer issue chips, `goto_issue`, and badge counts.

- [ ] **Step 7: Run headless construction/interaction smoke and full tests**

- [ ] **Step 8: Commit**

  ```bash
  git add scripts/editor_remotelink.py scripts/editor_frame.py scripts/validation.py
  git commit -m "Add a configurable RemoteLink editor with live safety receipt"
  ```

---

### Task 11: Replace the generation prompt and add the account inspector

**Files:**
- Modify: `scripts/app.py`
- Modify: `tests/test_rl_account.py`

**Interfaces:**
- Generate dialog reads `session.remotelink`; account/receiver are readonly.
- Inspector works without an open project.

- [ ] **Step 1: Replace generate dialog inputs**

  Show readonly resolved account/receiver, passphrase, and the shared receipt. Keep Cancel/Generate, async work, and toast behavior. Do not duplicate editable account fields here.

- [ ] **Step 2: Pass typed config into generation**

  `_run_generate_remotelink(passphrase)` calls `generate_configured_account_xml`. Toast mentions both `.xml` and summary `.txt`.

- [ ] **Step 3: Add the project-independent inspector**

  Add Help → Inspect RemoteLink Account…. The dialog selects an encrypted `.xml`, accepts passphrase, renders the same receipt, and saves a UTF-8 summary. It is transient but not grabbed so native file dialogs remain reliable on macOS.

- [ ] **Step 4: Verify error behavior**

  Wrong passphrase, non-hex input, malformed XML, and unsupported structure each produce one clean messagebox and no traceback.

- [ ] **Step 5: Run full tests and manual light/dark UI walk**

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/app.py tests/test_rl_account.py
  git commit -m "Finish RemoteLink generation preview and account inspection workflow"
  ```

---

### Task 12: Documentation, version, packaging, and release readiness

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify: `docs/screenshots/remotelink.png`

**Interfaces:**
- Produces release candidate `v1.3.0` after merge.

- [ ] **Step 1: Update README workflow**

  Document distributed configuration surfaces, safe defaults/explicit override, receipt, summary sidecar, and Help-menu inspector. State that imported accounts still require technician review before sending programming to a panel.

- [ ] **Step 2: Capture a fabricated-data screenshot**

  Use only a demo project. Show the REMOTELINK tab with safe settings and receipt; verify no real school/account/IP/user code appears.

- [ ] **Step 3: Bump `VERSION` to `1.3.0`**

- [ ] **Step 4: Run final automated verification from a clean branch state**

  ```bash
  ../../venv/bin/python -m pytest tests -q
  ../../venv/bin/python -c "import sys; sys.path.insert(0, 'scripts'); from rl_injector.account_doc import parse_account_xml; t=open('remotelink_account_template.xml', encoding='latin-1').read(); assert parse_account_xml(t).serialize()==t; print('byte-identical')"
  ../../venv/bin/python -c "import sys; sys.path.insert(0, 'scripts'); import app, editor_frame, editor_remotelink; from rl_injector.xml_export import generate_configured_account_xml, inspect_account, preview_account_summary; print('imports ok')"
  rg "_rebadge_identity|_replace_list|def _set\(|def _field\(|_legacy_build|TEMPORARY" scripts tests
  git diff --check
  ```

  Expected: all tests pass; round-trip/import smokes print success; regex grep prints nothing; diff check exits zero.

- [ ] **Step 5: Run manual acceptance walk**

  Verify old session load, save/reopen config, all editable surfaces, safe generation, one intentional-danger generation, summary parity, inspector, dark/light modes, and generated import into RemoteLink using a fake account. Do not send programming to a live panel.

- [ ] **Step 6: Request code review and fix findings**

  Use `superpowers:requesting-code-review`, inspect every diff locally, and rerun Step 4 after changes.

- [ ] **Step 7: Commit release candidate**

  ```bash
  git add README.md VERSION docs/screenshots/remotelink.png
  git commit -m "Prepare v1.3.0: configurable and safety-verified RemoteLink accounts"
  ```

- [ ] **Step 8: Finish the branch only after verification**

  Use `superpowers:finishing-a-development-branch`: merge into `main`, rerun tests, push `main`, create annotated tag `v1.3.0`, push the tag, monitor the release workflow, and verify Windows/macOS assets plus the Latest flag. Release notes should use the established emoji-rich format and explicitly call out the schedule/ambush safety protections.

---

## Self-Review

- Spec coverage: structured model, persisted config, zone types, SITE comm, KEYPADS options, REMOTELINK tab, advanced confirmation, live receipt, generation gate, inspector, summary sidecar, validation, compatibility, documentation, and v1.3.0 release are each assigned to a task.
- Known evidence dependency: Task 6 is intentionally a hard gate because RemoteLink's internal enum and schedule field values are undocumented. The plan provides an exact fake-account calibration method and forbids inference.
- Type consistency: `Session.remotelink` always stores `RemoteLinkConfig`; generation accepts typed config; summary is derived from built/read-back `AccountDoc`; safety uses `SafetyIntent` derived from `RLAdvanced`.
- Placeholder scan: clear. Task 6's external fixture is an explicit deliverable, not a guessed mapping.
