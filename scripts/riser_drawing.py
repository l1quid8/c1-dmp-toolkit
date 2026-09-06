"""Canonical symbol and text geometry shared by Canvas, SVG and PDF."""

from dataclasses import dataclass

import fitz

TITLE_BLOCK_WIDTH = 270.0


@dataclass(frozen=True)
class TextRun:
    x: float
    y: float  # Baseline, in master-sheet PDF points.
    text: str
    size: float
    bold: bool = False
    anchor: str = "middle"


def device_shape(element):
    return "ellipse" if element.ref.startswith("KEYPAD-") else "rectangle"


def device_text(design, element, *, small=False):
    if element.symbol_style == 'detailed':
        from riser_symbols import detailed_text
        return detailed_text(design, element)
    x, y, w, h = element.x, element.y, element.width, element.height
    result = [TextRun(x + w / 2, y + h / 2 - 3, element.ref, 18, True)]
    if element.ref == 'MSP':
        result.append(TextRun(x + 12, y + h / 2 - 7, 'KP BUS',
                              13.2 if small else 11, anchor='start'))
    if element.ref.startswith("RSP-"):
        rsp = next((r for r in design.rsps if f"RSP-{r.number}" == element.ref), None)
        if rsp:
            zones = f"Z{min(rsp.zones)}–Z{max(rsp.zones)}" if rsp.zones else ""
            result.append(TextRun(x + w / 2, y + h / 2 + 17,
                                  " · ".join(v for v in (rsp.model, zones) if v),
                                  13.2 if small else 13))
    if any(s.id == element.ref for s in design.splitters):
        size = 13.2 if small else 11
        result.append(TextRun(x + w / 2, y + 12, "IN", size))
        result.extend(TextRun(x + w * i / 4, y + h - 7, f"OUT {i}", size)
                      for i in range(1, 4))
    return result


def location_text(element, *, small=False):
    return TextRun(element.x + 12, element.y + 22, element.ref,
                   13.2 if small else 13, True, "start")


def title_bounds(document):
    return (document.page_width - (348 if document.layout_version >= 3 else TITLE_BLOCK_WIDTH), 36,
            document.page_width - 36, document.page_height - 36)


def location_text_runs(element, *, small=False):
    if not element.heading_lines:
        return [location_text(element, small=small)]
    result = []
    y = element.y + 24
    for index, text in enumerate(element.heading_lines):
        size = 16.0  # Both building and room are critical on the 11x17 profile.
        for line in wrap_text(text, max(24, element.width - 24), size, index == 0):
            result.append(TextRun(element.x + 12, y, line, size, index == 0, 'start'))
            y += size * 1.3
    return result


def logo_bounds(document):
    if document.layout_version >= 3:
        x = document.page_width - 348
        return x+30, 62, x+282, 173
    x = document.page_width - TITLE_BLOCK_WIDTH + 22
    return x, 64, x + TITLE_BLOCK_WIDTH - 80, 144


def wrap_text(text, width, size, bold=False):
    """Wrap against the same font metrics used in vector export."""
    font = "hebo" if bold else "helv"
    def fits(value):
        return fitz.get_text_length(value, fontname=font, fontsize=size) <= width
    lines, current = [], ""
    for word in str(text or "").replace("\n", " ").split():
        candidate = f"{current} {word}".strip()
        if current and not fits(candidate):
            lines.append(current)
            current = ""
        # Long single tokens must fit too (addresses and custom school codes).
        for char in word:
            candidate = current + char
            if current and not fits(candidate):
                lines.append(current)
                current = ""
            current += char
        current += " "
    if current.strip() or not lines:
        lines.append(current.strip())
    return [line.strip() for line in lines]


def title_text(document, *, small=False):
    if document.layout_version >= 3:
        from riser_symbols import modern_title
        return modern_title(document)[0]
    tb = document.title_block
    rows = [
        (170, tb.school_name, 18, True),
        (205, tb.address.replace("\n", " · "), 12, False),
        (245, f"LOCAL CODE: {tb.local_code}", 12, True),
        (310, tb.project_title, 14, True),
        (370, tb.drawing_title, 18, True),
        (410, f"SYSTEM: {tb.system}", 12, True),
        (470, f"DRAWN BY: {tb.drawn_by}", 11, False),
        (500, f"CHECKED BY: {tb.checked_by}", 11, False),
        (530, f"DATE: {tb.issue_date}", 11, False),
        *[(590 + i * 25, f"REV: {rev}", 11, False) for i, rev in enumerate(tb.revisions[-6:])],
        (document.page_height - 115, f"SHEET {tb.sheet_number}", 20, True),
    ]
    left, _, right, _ = title_bounds(document)
    result, previous_bottom = [], 144.0
    for y, value, size, bold in rows:
        size = max(size, 13.2 if small else 11)
        lines = wrap_text(value, right - left - 24, size, bold)
        line_height = size * 1.15
        first_y = max(y - (len(lines) - 1) * line_height / 2, previous_bottom + size + 6)
        for index, text in enumerate(lines):
            result.append(TextRun((left + right) / 2, first_y + index * line_height, text, size, bold))
        previous_bottom = result[-1].y + size * .25
    return result
