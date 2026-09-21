"""Conservative import of an equipment BOM with an RSP/zone breakdown."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from parse_dmp_worksheet import DMPDesign, Keypad, PowerSupply, RSP, SiteInfo, Splitter, ZoneInfo
from riser_model import default_riser_document


class NotBOMError(ValueError):
    """The workbook does not contain the supported BOM layout."""


@dataclass
class BOMImport:
    design: DMPDesign
    review_text: str


_RSP = re.compile(r"^RSP[\s-]*(\d+)$", re.I)
_ZONE = re.compile(r"^Z\s*(\d+)$", re.I)
_KP_ROUTE = re.compile(r"FROM\s+(KP[-\s]*710[-\s]*\d+)\s+TO\s+(.+?)\s+KEYPAD\b", re.I)


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _quantity(value) -> int:
    try:
        number = int(value)
        return number if number >= 0 else 0
    except (TypeError, ValueError):
        return 0


def parse_bom(path: str | Path) -> BOMImport:
    """Create a draft only from identifiable BOM facts; report every inference."""
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, ValueError, zipfile.BadZipFile,
            openpyxl.utils.exceptions.InvalidFileException) as exc:
        raise NotBOMError("This is not a readable BOM workbook.") from exc
    try:
        if not {"BOM", "BOM Breakdown"} <= set(wb.sheetnames):
            raise NotBOMError("This workbook has no BOM and BOM Breakdown sheets.")
        bom, breakdown = wb["BOM"], wb["BOM Breakdown"]
        school = _text(bom["A1"].value)
        if not school or _text(bom["B4"].value).upper() != "MANUFACTURER":
            # This layout labels the manufacturer in B4; reject unrelated sheets.
            raise NotBOMError("The BOM sheet does not have the expected headers.")
        address = _text(bom["A2"].value)
        location_id = re.search(r"LOCATION\s+ID\s*:\s*([A-Za-z0-9-]+)", address, re.I)
        address = re.sub(r",?\s*LOCATION\s+ID\s*:\s*[A-Za-z0-9-]+", "", address, flags=re.I)
        parts = [part.strip() for part in address.split(",")]
        site = SiteInfo(school_name=school,
                        school_code=location_id.group(1) if location_id else None,
                        address_line1=parts[0] if parts else None,
                        address_line2=", ".join(parts[1:]) if len(parts) > 1 else None,
                        xr550_location="UNSPECIFIED")
        design = DMPDesign(site_info=site, topology_source="manual")
        evidence = ["BOM draft — review these suggestions before using the design:"]
        counts: dict[str, int] = {}
        equipment = wb["DMP Equipment"] if "DMP Equipment" in wb.sheetnames else bom
        model_col, qty_col = (2, 4) if equipment.title == "DMP Equipment" else (3, 5)
        for row in equipment.iter_rows(min_row=2, max_col=max(model_col, qty_col)):
            model = _text(row[model_col - 1].value).upper()
            if model:
                counts[model] = _quantity(row[qty_col - 1].value)

        groups = []
        for row_number, row in enumerate(breakdown.iter_rows(max_col=19), 1):
            match = _RSP.fullmatch(_text(row[1].value))
            if not match:
                continue
            number = int(match.group(1))
            columns = {}
            for col in range(3, 17):
                zone_match = _ZONE.fullmatch(_text(row[col - 1].value))
                if zone_match:
                    columns[col] = int(zone_match.group(1))
            if not columns:
                continue
            groups.append((row_number, number, columns, _text(row[18].value)))
        if not groups or len({g[1] for g in groups}) != len(groups):
            raise NotBOMError("The BOM Breakdown has no unique RSP zone groups.")
        groups.sort(key=lambda group: group[1])
        if len(groups) > 15:
            raise ValueError("The BOM lists more RSPs than the worksheet supports.")

        models = {"714-8": counts.get("714-8", 0), "714-16": counts.get("714-16", 0)}
        if sum(models.values()) != len(groups):
            raise ValueError("BOM expander quantities do not match the RSP groups; review the sheet.")
        assigned_models = []
        for index, (rownum, number, columns, location) in enumerate(groups):
            start = min(columns.values())
            if index + 1 < len(groups):
                distance = min(groups[index + 1][2].values()) - start
                model = {8: "714-8", 16: "714-16"}.get(distance)
            else:
                remaining = {name: count - assigned_models.count(name) for name, count in models.items()}
                model = next((name for name, count in remaining.items() if count == 1), None)
            if model not in models or assigned_models.count(model) >= models[model]:
                raise ValueError(f"Cannot identify the expander model for RSP {number} from its zone range.")
            assigned_models.append(model)
            size = 8 if model == "714-8" else 16
            if any(zone not in range(start, start + size) for zone in columns.values()):
                raise ValueError(f"RSP {number} has zone labels outside its {model} range.")
            label = location or "UNSPECIFIED"
            design.rsps.append(RSP(number, label, list(range(start, start + size)), model))
            design.power_supplies.append(PowerSupply(number, label))
            evidence.append(f"RSP-{number} / PS-{number}: {label} ({model}, Z{start}–Z{start + size - 1}) — BOM Breakdown!S{rownum}")
            for zone in range(start, start + size):
                if zone == start + size - 2:
                    zi = ZoneInfo(zone, f"PS-{number}: A/C LOSS", "Supervisory", 1)
                elif zone == start + size - 1:
                    zi = ZoneInfo(zone, f"PS-{number}: BATT. TRBL", "Supervisory", 1)
                else:
                    zi = ZoneInfo(zone, "SPARE", "Spare", 1)
                design.zones.append(zi)

            next_row = groups[index + 1][0] if index + 1 < len(groups) else breakdown.max_row + 1
            for detail_row, cells in enumerate(breakdown.iter_rows(
                    min_row=rownum + 1, max_row=next_row - 1, max_col=19), rownum + 1):
                label = _text(cells[0].value).upper()
                if label in {"MOTION SENS", "MOTION SENSOR", "MOTION SENSORS"}:
                    for col, zone in columns.items():
                        if _quantity(cells[col - 1].value):
                            zi = next(item for item in design.zones if item.number == zone)
                            zi.location = "MOTION - LOCATION NEEDS REVIEW"
                            zi.device_type = "Motion"
                            evidence.append(f"Z{zone}: motion sensor, room unknown — BOM Breakdown!{cells[col - 1].column_letter}{detail_row}")
                note = _text(cells[18].value)
                route = _KP_ROUTE.search(note)
                if route:
                    evidence.append(f"Keypad destination: {route.group(2).strip()} via {route.group(1)} — BOM Breakdown!S{detail_row}")

        # A model count identifies devices but not their type, position, or wiring.
        # Only the explicitly named KP splitter can be safely created.
        kp_routes = [line for line in evidence if line.startswith("Keypad destination:")]
        if kp_routes and counts.get("710", 0):
            design.splitters.append(Splitter(id="KP-710-1", splitter_type="KP",
                                             location="UNSPECIFIED", outputs=["Spare"] * 3))
        unresolved_splitters = counts.get("710", 0) - len(design.splitters)
        if unresolved_splitters > 0:
            evidence.append(f"{unresolved_splitters} other 710 modules need KP/LX type, location, and wiring review — DMP Equipment model 710")
        keypad_count = counts.get("7000 SERIES", 0)
        for number in range(1, min(keypad_count, 28) + 1):
            design.keypads.append(Keypad(number, location="UNSPECIFIED"))
        if keypad_count:
            evidence.append(f"{keypad_count} keypads need individual source and location review — DMP Equipment model 7000 Series")
        motion_count = sum(zone.device_type == "Motion" for zone in design.zones)
        if counts.get("DS9370", motion_count) != motion_count:
            evidence.append(f"Motion point count ({motion_count}) differs from equipment quantity ({counts['DS9370']}).")
        evidence.append("MSP location, 710 wiring, and exact sensor rooms were not supplied by this BOM.")
        design.riser_document = default_riser_document(design)
        return BOMImport(design, "\n".join(evidence))
    finally:
        wb.close()
