# RemoteLink account configuration — design

Date: 2026-07-29
Status: approved in discussion; pending spec review

## Goal

Make RemoteLink account generation configurable from the app — zone types,
users & codes, panel comm settings, arming/area behavior, and keypad device
options — instead of shipping fixed dealer defaults that the tech corrects
in RemoteLink after import. Configuration lives in the project, persists in
the `.dmps`, and generation stamps it.

Prompted by the Shirley EL incident and the follow-up decision: the first
response (visibility only — preview, inspector, summary sidecar, hard safety
gates) is already designed and planned; the user has decided configurability
is also wanted. This spec extends that foundation; it does not replace it.

## Foundation dependency

Builds on the **structured document model refactor** (plan:
`~/.claude/plans/use-the-claude-design-mcp-wobbly-summit.md`): template →
`AccountDoc` model → edits → verify → serialize → encrypt. That work lands
first. Everything here is expressed as model edits and summary additions on
top of it.

## Decisions already made (with the user)

1. Scope: zone types, users & codes, panel comm, arming & area behavior,
   keypad device setup. All of it.
2. Safety model: **safe defaults + explicit override.** Everything dangerous
   defaults OFF exactly as today; enabling requires a deliberate act in a
   marked advanced group plus a one-time confirm; enabled items show ⚠ in
   preview, dialog receipt, summary `.txt`, and inspector.
3. UI approach C: **config lives where the data lives** — ZONES grid column,
   SITE panel group, KEYPADS card fields, plus a small two-pane REMOTELINK
   tab for account-level settings; the generate dialog shrinks to
   passphrase + receipt + Generate.

## Data model & persistence

### `RemoteLinkConfig` (new module `scripts/rl_injector/rl_config.py`)

Stored in the session JSON under a new top-level `"remotelink"` key.

```
RemoteLinkConfig
├─ account_num: str      ("" = prefill from school code, as today)
├─ receiver_num: str     (default "1")
├─ users: list[RLUser(number:int, name:str, code:str, profile:str)]
│    default = [ (1, "USER", <site code>, "1"),
│                (9999, "TECHNICIAN", "1"+<site code>, "99") ]
│    (codes stored explicitly once the tab is touched; until then derived)
├─ comm: RLComm(connect_type:str, port:str="2001", serial:str="")
│    IP is NOT stored here — read from SiteInfo (SITE tab) at build time
├─ arming: RLArming(
│    entry_delays: [30,60,90,120], exit_delay: 120, arm_mode: "Area",
│    advanced: RLAdvanced(
│      schedule_enabled: False, schedule: {day: (open,close) times},
│      ambush_reports: False, ambush_output: "0",
│      morn_ambush_min: "0", auto_arm: False, auto_disarm: False))
└─ keypads: dict[int, RLKeypad(name:str, device_type:str,
                               comm_type:str, disp_areas:str)]
```

### Zone types

Per-zone field `rl_type: str` on the design's zone objects, values
`""` (= Auto), `"NT"`, `"EX"`, `"SV"`. Auto resolves through the existing
`derive_zone_type` (spares always `"--"`, supervisory points `"SV"`). Rides
the zone through renumbering/expander changes; serialized with the zone in
the session JSON.

### Invariants

- **Untouched config ⇒ byte-identical output to pre-feature generation.**
  Every default equals current behavior. Enforced by a permanent golden
  test.
- **Old `.dmps` files load clean**: missing `"remotelink"` key (and missing
  `rl_type` on zones) → defaults. No migration step.

## UI surfaces

### ZONES grid
New **TYPE** column after DEVICE TYPE. Inline dropdown per row:
`Auto`, `Night`, `Exit`, `Supervisory`; Auto renders grayed as
"Auto → Night" (resolved value). Spare rows locked (shown as Spare).

### SITE tab
New **Panel** group beside the existing IP/gateway fields: connect type
(dropdown, default Network), port (default 2001), serial number (optional,
blank = keep template value).

### KEYPADS tab
Each keypad card gains: display name (default "KEYPAD n"), device type,
comm type, displayed areas. Door/access-control fields (strike, fire exit,
door flags) are **out of scope** — school designs carry no access control;
template defaults ride through untouched.

### REMOTELINK tab (new; two panes, SPLITTERS-pattern)
- **Left — config:** account number (prefilled from school code) +
  receiver number; **Users** table (add/edit/remove; columns number, name,
  code, profile); **Arming** group (entry delays 1–4, exit delay, arming
  type); **Advanced — "⚠ panel acts on its own"** group, collapsed by
  default: arming schedule (per-day open/close), ambush reports, ambush
  output, morning ambush minutes, auto-arm, auto-disarm. First enable of
  any advanced item per session → confirm dialog restating the ⚠ warning.
- **Right — live receipt:** the account summary (`render_text` output),
  rebuilt on any edit from any tab; ⚠ lines appear as soon as an advanced
  item is enabled.

### Generate dialog
Reduced to: passphrase + the same live receipt + Generate. Account and
receiver display read-only (edited on the REMOTELINK tab). Cancel/Generate
semantics, background generation, toast: unchanged.

## Generation flow

```
design + RemoteLinkConfig
  → staging   (zone types: rl_type or derive; users from config)
  → parse template → AccountDoc
  → _apply_account_edits writes, additionally:
      CONNECT_TYPE / PANEL_IP (from SiteInfo) / PANEL_IP_PRT / SERIAL_NUMBER   [Account]
      UsersList rows rebuilt from config (clone prototype row per user)
      ENT_DLY_1..4, ARM_MODE                                                   [SysOpts]
      EXIT_DELAY, MORN_AMBSH                                                   [PartInfo]
      AMBUSH (reports)                                                         [SysRpts]
      AMBUSH (output number)                                                   [OutOpts]
      AUTO_ARM, AUTO_DISRM                                                     [AreaInfo]
      TimeScheds row + AreaTimeScheds SCHED_1 link (only when schedule_enabled)
      NAME / TYPE / COMM_TYPE / DISP_AREAS per keypad                          [DeviceInfo]
  → safety gate (intent-aware, below)
  → serialize → encrypt → .xml + summary .txt
```

`ARM_MODE` char mapping (Area / All-Perimeter / Home-Sleep-Away → stored
letter) is verified at implementation by saving each mode from RemoteLink
and decoding the export — not guessed. Same verification for
`connect_type` and keypad `device_type`/`comm_type` enum chars.

### Intent-aware safety gate

`verify_account_doc(doc, intent)` where intent is derived from
`RLAdvanced`. For each of the six dangerous classes (schedule rows, SCHED_*
links, SysRpts.AMBUSH, OutOpts.AMBUSH, MORN_AMBSH, AUTO_ARM/AUTO_DISRM):

- present in output but **not** in intent → `AccountSafetyError`, nothing
  written (catches template/code drift — Shirley EL stays impossible)
- present **and** intended → passes; the violation text moves to
  `AccountSummary.warnings` → ⚠ in every surface
- inspector (foreign exports, no intent available) → report-and-flag only,
  unchanged from the foundation design.

## Validation (warn, never block)

REMOTELINK tab joins the per-tab status-chip system. Checks: duplicate user
numbers; duplicate codes; non-numeric or <3-digit codes; port outside
1–65535; schedule enabled with an incomplete day row (open without close or
vice versa); displayed-areas not matching the expected hex-mask format.
SITE keeps its existing IP validation. The only hard stop anywhere is the
gate's unintended-setting case.

## Testing (headless; UI by manual walk, per repo convention)

- **Golden**: untouched config → byte-identical output vs pre-feature path.
- Session: config round-trips through save/load; pre-feature `.dmps`
  (no key) loads with defaults; zones round-trip `rl_type`.
- Staging: `rl_type` override wins; `""` matches today's derivation.
- Field mapping: one assert per config field → template field (port →
  `PANEL_IP_PRT`, morning ambush → `MORN_AMBSH`, etc.), via decode + model
  read-back of a generated account.
- Gate: unintended dangerous setting → raises; intended → generates with ⚠
  present in `render_text` output and summary `.txt`.
- Users: rebuilt rows carry number/name/code/profile; prototype cloning
  keeps unmodeled fields at template defaults.
- Schedule write: enabled schedule produces a TimeScheds row + SCHED_1 link
  that the inspector then flags on read-back.

## Out of scope

Access-control keypad fields; editing dealer defaults outside the listed
groups (zone action messages, output groups, profiles beyond the user's
profile number); multi-area partitioning (C1 schools are single-area);
RemoteLink import-side automation; release/versioning (decided after
implementation).
