# Riser equipment clustering and electrical-first layout

Status: approved and implemented in v1.5.0. See the corresponding implementation
plan for verification evidence and remaining platform/manual acceptance limits.

## Outcome

A reader should immediately identify a device, its physical location, and its
upstream connection. Use compact room-equipment clusters, arranged by electrical
flow, with prominent building/room headings. Do not impose a campus floor plan,
nest oversized building rectangles, or change electrical connections to simplify
the picture.

## Chosen approach and alternatives

- **Chosen: electrical-first placement with physical-location clusters.** Room
  membership is explicit; electrical edges determine branch order. Building names
  appear in cluster headings, not as enclosing geometry.
- Building-first packing makes buildings easy to find but can force long returning
  cables and confuse upstream/downstream order. Do not use it as the default.
- A fully ungrouped electrical tree is easy to trace but hides which hardware is
  actually co-located. Do not use it as the default.

## Shared location data

Add project-owned `EquipmentLocation` records, with stable IDs and separate
`building`, `floor`, and `room` fields. Keep a `full_label` for the original,
unabridged imported location and a review state indicating whether the structured
fields have been confirmed. Store records and a hardware-ID-to-location-ID map
on `DMPDesign`; these are domain data, not free drawing annotations.

- A room is not identified by its displayed text or by containment in a box.
- Renaming a location keeps its ID. Joining two locations is an explicit merge.
- Existing hardware `location` strings and `site_info.xr550_location` remain
  compatibility fields consumed by worksheet and door-chart code. Project-location
  operations update them through one shared service before downstream consumers.
- Migration preserves those strings byte-for-byte. Confirmed structured edits
  use a deterministic full label: nonempty building, floor, and room components
  joined by spaces. Preserve the service-keypad suffix on the relevant hardware.
  Unedited migrated records retain per-device imported spellings in compatibility
  fields; projection must not normalize them merely because a record exists.
- `Rename location globally` continues to update all assigned hardware, including
  paired RSP power supplies. It updates the location record and compatibility
  fields in one undoable operation. If a free-text rename no longer matches the
  structured fields, clear their confirmed status and display the full label
  until the user reviews the split; never show an old room under a new full label.
- Editing one hardware assignment in SITE/SPLITTERS/KEYPADS/POWER means selecting
  an existing location or creating another one, not renaming every occupant of
  its previous room. Global rename remains a distinct command.
- Sensor zone descriptions are not equipment locations and are not rewritten.

### Migration and unresolved locations

Upgrade schema 4 to schema 5 in memory; save schema 5 only on explicit Save.
Older apps reject newer schemas through the existing version check. Support the
existing schema 1–3 migration chain before applying this migration.

Seed deterministic location IDs from exact imported labels, normalizing only
case and whitespace for deduplication. Preserve explicit MSP-relative RSP aliases
as panel-location assignments. Do not infer co-location from RSP numbers, floor
omission, shared building words, a feeding cable, or nearby drawing coordinates.
Attach paired power supplies according to the existing RSP/PS domain contract.

Ambiguous variants may be offered as suggested matches, but must not merge until
confirmed. Do not add a fuzzy location parser in the first increment. Initially
retain the full imported label; users can fill in building/floor/room directly.
Blank or placeholder locations remain unresolved per hardware, rather than
forming one imaginary shared UNKNOWN room. Their proposed-layout clusters show
the device ID and `LOCATION UNCONFIRMED`.

Existing scene membership and geometry remain available as the legacy drawing;
new canonical location assignments do not silently repack that scene. A migration
warning identifies scene membership that needs review before the new layout is
applied.

## Cluster membership and headings

One cluster contains the drawn equipment assigned to one physical room. Its
frame references the project location ID separately from its own stable scene
ID; existing device `location_id` drawing-ownership references continue to point
to scene frames. Power supplies remain part of the RSP domain grouping; this
change does not invent new electrical ports or new power-supply symbols.

- **Head end:** one top-center cluster for MSP and its confirmed co-located RSPs,
  710s and keypad. RSP-1 and RSP-2 may both belong here, but are never moved here
  based on number alone.
- **Remote room:** group an RSP and local 710 when their physical location is
  the same. An electrical connection alone is insufficient evidence.
- **Separate keypad:** a keypad in another room gets a compact endpoint cluster,
  even when it is in the same building as its source.
- **Unresolved:** separate identifiable placeholders, with warnings, until their
  assignments are confirmed. They still retain their real electrical edges.

Heading example: bold `MAIN BUILDING`, then `1st Floor · Nurse’s Office`.
Within the frame, device IDs are the most prominent symbol text; model/zone
details remain secondary. Do not repeat the full location on every symbol.
If structured fields are incomplete, show the full label without fabricating a
building or room. Repeated building headings across different rooms are valid.

Use one light, unfilled cluster boundary. Distinguish a room grouping from an
equipment enclosure: a room outline does not claim all devices share a cabinet.
Measure and wrap headings using the shared font metrics, with heading height
included in layout and routing obstacles. Never clip, ellipsize, or shrink away
critical location text to fit a fixed frame.

## Placement and routing

Compute electrical ranks from the actual device graph, then place its clusters.
Within and between clusters, prefer KP branches to the left and LX branches to
the right; order LX500–LX900 and 710 numeric suffixes naturally. These are
preferences, not constraints that split a real co-located head-end cluster.

1. Measure cluster geometry from symbols, wrapped headings and local cable lanes.
2. Position the head end at the upper center of the available drawing region.
3. Order downstream clusters using electrical rank and source-port order. Pack
   sibling branches with balanced widths and consistent whitespace.
4. Reserve inter-cluster orthogonal routing corridors. Keep independent cables
   distinct, and use transparent bridge hops for unavoidable crossings.
5. Score candidate positions in priority order: readable in-page geometry,
   clearance from symbols/headings, fewer crossings, electrical order, shorter
   routes, then balanced whitespace. Do not force visual symmetry.

An acyclic device graph can revisit the same room (room A → room B → room A).
Do not misreport that as an electrical cycle, duplicate devices, or split a real
room solely to make the cluster graph acyclic. Keep the room cluster and route
returning edges through an outer corridor; downstream-below-source is a preference
when physical co-location prevents it. Actual device-graph cycles remain warnings.

Retain canonical 24×36 coordinates, black electrical wiring, one INT-5.0 sheet,
searchable vector PDFs and standalone SVG. The 11×17 profile uses the same scene,
with critical text at least 7 pt, secondary text at least 6 pt and lines at least
0.35 pt. Evaluate wrapping with the small-sheet profile too; warn on an impossible
fit rather than silently reducing legibility.

## Editor workflow and compatibility

The selection inspector shows readable location headings and Building/Floor/Room
fields, with an explicit Apply action that previews affected hardware. Offer
location assignment and global rename as distinct actions. Confirm merges and
show any unresolved assignments before applying a new layout.

Add **Preview layout** using a cloned scene. Display proposed geometry and its
validation results without changing the project, undo history or source file.
**Apply layout** replaces the scene as one undoable command after confirmation
that manual geometry/routes will be replaced. Title-block content and markup
survive. Cancel leaves the original scene exactly as it was.

New projects use this layout by default. Existing projects retain saved positions,
routes, title data and markup on ordinary open; no automatic conversion to the new layout.
Existing recovery of genuinely broken legacy scenes must use the legacy layout
path until the user explicitly applies the new layout. Store a layout-engine
version in the document so recovery and synchronization make that distinction.

Auto-layout retains its non-destructive role: synchronize and place only missing
items around retained geometry. Full replacement goes through Preview/Apply
(including the existing Re-layout All action). New hardware still goes to the
Unplaced tray; assignment alone never moves its geometry behind the user's back.

Reusing a location ID must preserve selection, undo/redo and routes on rename.
External hardware/location edits must invalidate conflicting history without
losing unrelated edits, following the existing v1.4.5 safeguards.

## Component boundaries

- Project-location models/service: domain records, assignments, migration,
  rename/merge and compatibility projections. Move normalization out of
  `riser_scene.py` so the domain service does not depend on drawing code.
- Pure cluster/layout component: membership, sizing, ranking and positioning;
  consume the graph and locations without mutating either.
- `riser_scene.py`: scene synchronization, routing integration and validation.
- `riser_drawing.py`: shared multi-line heading and symbol text geometry.
- Canvas/SVG/PDF renderers: consume the same geometry and text runs.
- `riser_editor.py` / `editor_frame.py`: preview/apply, inspector controls,
  domain notifications and combined location/scene undo transactions.
- `session.py`: schema-5 serialization and legacy migration coverage.

No new runtime dependency, electrical topology representation, CAD subsystem or
standalone location-management application is required. Preserve unrelated
RemoteLink, worksheet trimming and door-chart changes.

## Acceptance criteria

- Migration changes no electrical edge or original location text, and never
  overwrites the source file. Schema-5 round-trip preserves stable IDs, location
  assignments and layout version.
- Same-building/different-room devices remain separate. Confirmed same-room
  MSP/RSP-1/RSP-2 share one cluster. Unknown devices do not imply co-location.
- Global rename, single-device reassignment and explicit merge have distinct,
  tested effects in RISER, other tabs and newly generated worksheets/door charts.
- Preview/Cancel is a no-op; Apply/Undo restores exact prior manual geometry,
  routes, title and markup. Reopen is stable and does not re-layout by itself.
- Electrical rank, natural device order and placement are deterministic across
  shuffled input lists, multiple buses and room-revisiting paths.
- Headings and labels fit their measured bounds; routes avoid protected
  footprints. Independent wiring stays distinct with no white-out crossings.
- Both PDF sizes and SVG agree on layout and labels. Dense-sheet warnings remain
  available with Generate Anyway.
- Check Riverside first, including its explicitly different MSP/RSP room data;
  do not relocate those RSPs to the MSP without a confirmed assignment. Also run
  the three-RSP fixture, Fourth Street and Griffin/seven-RSP cases when available;
  synthetic fixtures cannot substitute for claiming a real-campus acceptance.
- Measure drag responsiveness against the v1.4.5 baseline: retain coalesced
  incremental motion previews and one committed redraw on release.
- Inspect real saved-project open, preview, apply, drag, rename, undo and export
  in the native macOS build. Windows packaging/interaction must be reported
  separately if not verified. Retain the previous native app for rollback.

## Review checkpoint

The user approved this detailed scope before implementation. Existing project
files were not modified by implementation or acceptance; separate QA copies
were used. The implementation plan records the final native release checks.
