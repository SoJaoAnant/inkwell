"""Generates the printable, blank glyph template.

The user prints this, writes each character in its box (4 variants per
character, for variety), then scans/photographs the filled pages back in
through the Streamlit app's "set up handwriting" flow.
"""

from PIL import Image, ImageDraw, ImageFont

from core.template_layout import build_placements, num_pages

GUIDE_COLOR = (170, 170, 170)
LABEL_COLOR = (90, 90, 90)
FIDUCIAL_COLOR = (0, 0, 0)
BG_COLOR = (255, 255, 255)


def _font(size):
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def generate_pages():
    lay, placements, section_breaks = build_placements()
    n_pages = num_pages(placements)

    pages = [Image.new("RGB", (lay.page_w, lay.page_h), BG_COLOR) for _ in range(n_pages)]
    draws = [ImageDraw.Draw(p) for p in pages]

    label_font = _font(round(lay.cell * 0.42))
    header_font = _font(round(lay.section_header_h * 0.62))
    footer_font = _font(round(lay.margin * 0.35))

    for page_idx, y_top, section_name in section_breaks:
        draws[page_idx].text(
            (lay.margin, y_top + lay.section_header_h * 0.12),
            section_name, fill=LABEL_COLOR, font=header_font,
        )

    for p in placements:
        d = draws[p["page"]]
        x0, y0, x1, y1 = p["rect"]
        d.rectangle([x0, y0, x1, y1], outline=GUIDE_COLOR, width=2)
        if p["variant"] == 0:
            lx0, ly0, lx1, ly1 = p["label_rect"]
            d.text(((lx0 + lx1) / 2, (ly0 + ly1) / 2), p["char"],
                   fill=LABEL_COLOR, font=label_font, anchor="mm")

    for page_idx, img in enumerate(pages):
        d = draws[page_idx]
        for fx0, fy0, fx1, fy1 in lay.fiducial_rects():
            d.rectangle([fx0, fy0, fx1, fy1], fill=FIDUCIAL_COLOR)
        d.text(
            (lay.page_w / 2, lay.page_h - lay.margin * 0.55),
            f"Inkwell handwriting template  —  page {page_idx + 1} of {len(pages)}  "
            f"—  write in dark blue or black pen, one variant per box",
            fill=LABEL_COLOR, font=footer_font, anchor="mm",
        )

    return pages


def save_template(png_dir, pdf_path):
    pages = generate_pages()
    png_paths = []
    for i, page in enumerate(pages):
        path = png_dir / f"template_page_{i + 1}.png"
        page.save(path, dpi=(300, 300))
        png_paths.append(path)
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:])
    return png_paths, pdf_path


if __name__ == "__main__":
    import pathlib
    out_dir = pathlib.Path(__file__).resolve().parent.parent / "assets" / "templates"
    out_dir.mkdir(parents=True, exist_ok=True)
    pngs, pdf = save_template(out_dir, out_dir / "inkwell_template.pdf")
    print(f"Wrote {len(pngs)} page(s) and {pdf}")
