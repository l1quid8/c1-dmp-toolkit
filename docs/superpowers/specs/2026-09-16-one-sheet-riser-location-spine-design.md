# One-sheet riser: location groups on electrical bus spines

Status: design selected from Arminta layout studies; awaiting written-spec review.

## Outcome and constraints

Use the selected **Option A, CAD-style spine** as the visual direction for the
RISER Preview layout and the scene committed by Apply. The 24×36 INT-5.0 scene
remains one sheet, including its title block, and renders to the existing 11×17,
24×36, and SVG outputs. A reader should see the MSP, the KP and LX feeds, each
physical location, and the exact upstream device/port for every connection
without tracing lines through labels or other devices.

This is a schematic, not a campus floor plan. The mockup was illustrative; its
room names and edges are not project data. The implementation must use each
project's actual location IDs, devices, ports, and connections. It must not
invent, delete, or rewire equipment to make the page look balanced.

## Chosen visual structure

- Place the MSP/head-end near the top and reserve separate, labeled KP and LX
  vertical routing corridors. Keep the toolkit's explicit input names, numbered
  outputs, port dots, and connection labels; do not copy the CAD reference's
  tiny port labels or address schedules into the riser.
- Form one cohesive group for each confirmed physical location ID. Devices in
  the MSP location share its head-end group; co-located RSPs and 710s stay
  together. A device in another room remains in its own group, even if fed by
  a nearby device. Unresolved locations stay individually identifiable and
  carry the existing review warning; no fuzzy co-location is inferred.
- Put each group beside the corridor carrying its upstream feed, with KP
  generally left and LX generally right. Within a group, order devices by
  actual electrical parent/child relationship and source-port order. A subtle,
  unfilled or very light location boundary and a prominent wrapped heading
  convey membership without suggesting a common equipment enclosure.
- Arrange downstream groups in electrical depth order. Use consistent gaps and
  a balanced left/right page without forcing symmetry or scattering a room's
  devices. A room revisited by different electrical branches still has one
  group; a return connection uses an outer corridor, not a duplicate device.

Room-first shelves were rejected because long return wires obscure ancestry.
The current pure electrical rows were rejected because co-located hardware can
be scattered. The selected spine keeps the traceable electrical tree and the
CAD team's location-first reading pattern together.

## Measurement, fit, and routing

The layout planner measures each detailed symbol, its full wrapped text,
location heading, terminals, and cable label before placement. It reserves
the title block, page margins, group clearances, and bus/branch routing lanes.
It evaluates deterministic candidates with different column widths, group
ordering, compact symbol-body sizes, and spacing. Candidate priority is:

1. All equipment and required text on the one sheet at the print-legibility
   floor, with no symbol, heading, or caption overlap.
2. Every electrical edge traceable from the correct source output to the
   correct target input; no cable through a device or critical label.
3. Few crossings and bends, distinct parallel runs, and bridge hops only for
   unavoidable independent crossings.
4. Electrical parent-above-child order, then short routes and balanced whitespace.

Compact bodies and whitespace before reducing type. Device count alone is not
a sizing rule: long labels and bus fan-out consume different space. Keep all
equipment, port, route, and location text at or above the existing 11×17
legibility threshold (7 pt for critical text; 6 pt only for truly secondary
annotations), and preserve the more readable current input/output treatment.
The same measured geometry must feed Canvas, PDF, and SVG.

A finite sheet cannot guarantee a readable fit for arbitrary equipment counts.
When no candidate fits cleanly at the type-size floor, Preview shows the best
one-sheet candidate with that floor intact and a specific fit warning (which
groups, text, or cables do not fit). It never shrinks text below the floor or
creates a second sheet. Existing validation
and Generate Anyway behavior remain available; this warning does not alter the
electrical graph or saved scene.

## Workflow and compatibility

- **Preview layout** builds the proposal from a cloned scene, opens at a
  content-centered drafting zoom rather than a tiny full-sheet fit, and displays
  fit/legibility issues. Fit remains an explicit view control. Cancel changes
  nothing. Apply confirms replacement of manual device positions/routes and
  commits one undoable scene change; title data and markup survive.
- Remove the **Auto-layout** toolbar button and its user-facing command. Keep
  the internal missing-item placement used by Add Device and topology updates;
  removing the button must not strand newly added equipment. Retain the
  Unplaced indication when no clear placement is possible.
- Existing saved drawings retain their manual positions and routes on open.
  The new planner runs for new scenes and explicit Preview/Apply, with a new
  layout-engine version only if needed to distinguish legacy repair behavior.
  Undo/redo, schema round-trip, layer visibility, direct editing, and all
  generated outputs continue to work.

## Component boundaries

- `riser_presentation.py`: pure location grouping, electrical ordering,
  candidate placement, and fit scoring. It reads the graph and canonical
  location assignments without mutating either.
- `riser_symbols.py` and `riser_drawing.py`: content-aware symbol and text
  measurement shared by screen and vector output; no independent export-only
  layout.
- `riser_scene.py`: obstacle-aware connection routing, line/label placement,
  and legibility diagnostics against the selected candidate.
- `riser_editor.py` and `editor_frame.py`: remove the exposed Auto-layout action,
  preserve Add Device placement, and present Preview fit warnings and zoom.

Avoid changing worksheet, door-chart, RemoteLink, or site-location semantics.

## Acceptance and verification

- Tests on Arminta-like, dense-school, same-room head-end, same-building but
  different-room, unresolved-location, and room-revisiting graph fixtures.
  Assert one scene group per confirmed location, exact device and edge counts,
  correct port attachments, stable deterministic placement, and unchanged
  design data.
- Reject off-page symbols/text, overlaps, cable-through-device/heading/caption,
  illegible critical text, and uncovered independent intersections for feasible
  fixtures. For deliberately impossible fixtures, assert a specific warning
  instead of a silent type-size violation or an extra page.
- Preview/Cancel leaves the saved scene byte-equivalent; Apply/Undo/Redo and
  save/reopen preserve scene, title, markup, and electrical connections. Add
  Device still auto-places in clear space with the button removed.
- Compare 11×17 and 24×36 PDFs and SVG with the Canvas scene, including
  searchable labels, readable ports, one INT-5.0 title block, and no clipped
  content. Visually inspect real Arminta data in a QA copy at page fit and
  readable drafting zoom; do not treat the illustrative mockup as acceptance.

No native installation, replacement of the running app, or modification of a
user project is part of this design phase.
