"""Shared pixel-geometry for the printable template.

template_gen.py (drawing) and segmentation.py (cropping a filled-in scan)
both import this so the two can never drift out of sync with each other.
"""

from core.charset import all_entries, VARIANTS_PER_GLYPH

DPI = 300

PAGE_W_IN, PAGE_H_IN = 8.27, 11.69  # A4
MARGIN_IN = 0.45
LABEL_COL_IN = 0.5
CELL_GAP_IN = 0.07
GROUP_GAP_IN = 0.35
ROW_GAP_IN = 0.16
SECTION_HEADER_H_IN = 0.34
GROUPS_PER_ROW = 2

FIDUCIAL_SIZE_IN = 0.28
FIDUCIAL_INSET_IN = 0.16


def _px(inches):
    return round(inches * DPI)


def _cell_size_px():
    usable_w_in = PAGE_W_IN - 2 * MARGIN_IN - GROUP_GAP_IN * (GROUPS_PER_ROW - 1)
    group_w_in = usable_w_in / GROUPS_PER_ROW
    cells_w_in = group_w_in - LABEL_COL_IN - CELL_GAP_IN * (VARIANTS_PER_GLYPH - 1)
    cell_in = cells_w_in / VARIANTS_PER_GLYPH
    return _px(cell_in)


class Layout:
    def __init__(self):
        self.page_w = _px(PAGE_W_IN)
        self.page_h = _px(PAGE_H_IN)
        self.margin = _px(MARGIN_IN)
        self.label_col = _px(LABEL_COL_IN)
        self.cell_gap = _px(CELL_GAP_IN)
        self.group_gap = _px(GROUP_GAP_IN)
        self.row_gap = _px(ROW_GAP_IN)
        self.section_header_h = _px(SECTION_HEADER_H_IN)
        self.cell = _cell_size_px()
        self.group_w = self.label_col + VARIANTS_PER_GLYPH * self.cell + (VARIANTS_PER_GLYPH - 1) * self.cell_gap

        self.fiducial_size = _px(FIDUCIAL_SIZE_IN)
        self.fiducial_inset = _px(FIDUCIAL_INSET_IN)

    def fiducial_rects(self):
        s, inset = self.fiducial_size, self.fiducial_inset
        w, h = self.page_w, self.page_h
        return [
            (inset, inset, inset + s, inset + s),  # top-left
            (w - inset - s, inset, w - inset, inset + s),  # top-right
            (inset, h - inset - s, inset + s, h - inset),  # bottom-left
            (w - inset - s, h - inset - s, w - inset, h - inset),  # bottom-right
        ]


def build_placements():
    """Returns (layout, placements, section_breaks).

    placements: list of dicts:
        {page, char, safe_name, variant, section, anchor, rect}
        rect = (x0, y0, x1, y1) in page pixels
    section_breaks: list of (page, y_top, section_name) for drawing headers.
    """
    lay = Layout()
    entries = all_entries()

    top_y = lay.margin
    bottom_y = lay.page_h - lay.margin

    page = 0
    slot = 0  # which of the GROUPS_PER_ROW slots in the current row
    row_top = top_y

    placements = []
    section_breaks = []
    current_section = None

    def ensure_fits(extra=0):
        nonlocal page, row_top
        if row_top + extra + lay.cell > bottom_y:
            page += 1
            row_top = top_y

    for entry in entries:
        if entry.section != current_section:
            current_section = entry.section
            if slot != 0:
                row_top += lay.cell + lay.row_gap
                slot = 0
                ensure_fits()
            ensure_fits(extra=lay.section_header_h)
            section_breaks.append((page, row_top, current_section))
            row_top += lay.section_header_h

        group_x0 = lay.margin + slot * (lay.group_w + lay.group_gap)
        cells_x0 = group_x0 + lay.label_col

        for variant in range(VARIANTS_PER_GLYPH):
            x0 = cells_x0 + variant * (lay.cell + lay.cell_gap)
            y0 = row_top
            placements.append({
                "page": page,
                "char": entry.char,
                "safe_name": entry.safe_name,
                "variant": variant,
                "section": entry.section,
                "anchor": entry.anchor,
                "label_rect": (group_x0, y0, group_x0 + lay.label_col, y0 + lay.cell),
                "rect": (x0, y0, x0 + lay.cell, y0 + lay.cell),
            })

        slot += 1
        if slot >= GROUPS_PER_ROW:
            row_top += lay.cell + lay.row_gap
            slot = 0
            ensure_fits()

    return lay, placements, section_breaks


def num_pages(placements):
    return max(p["page"] for p in placements) + 1
