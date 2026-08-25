"""Composites tokenized text onto procedural paper using a loaded glyph
library, with per-glyph jitter (rotation/scale/position), baseline wobble,
pressure-style opacity variance, and an occasional-overlap parameter -- all
the "naturalness" knobs live on RenderParams so the UI can expose them
directly as sliders."""

import math
from dataclasses import dataclass, field

from PIL import Image

from core.layout import tokenize_text
from core.paper import make_paper, apply_scan_look

OUTPUT_DPI = 150
PAGE_W_IN, PAGE_H_IN = 8.27, 11.69
PAGE_W_PX = round(PAGE_W_IN * OUTPUT_DPI)
PAGE_H_PX = round(PAGE_H_IN * OUTPUT_DPI)

INK_COLORS = {
    "Black": (24, 24, 27),
    "Blue": (23, 47, 133),
}

FALLBACK_ADVANCE_UNITS = 3.0  # x letter_spacing_px, used for unsupported characters
JUSTIFY_FILL_FRACTION = 0.96  # justification targets this fraction of usable_w, leaving jitter headroom
OVERFLOW_TOLERANCE_PX = 15  # ignore overshoots this small when flagging overflow_lines -- jitter noise, not real clipping


@dataclass
class RenderParams:
    ink_color_name: str = "Blue"
    font_px: int = 28

    rotation_jitter_deg: float = 3.0
    scale_jitter_pct: float = 0.08
    x_jitter_px: float = 1.4
    y_jitter_px: float = 2.0

    baseline_wobble_amp_px: float = 2.5
    baseline_wobble_freq: float = 1.3

    letter_spacing_px: float = 5.0
    spacing_jitter_pct: float = 0.35
    word_space_factor: float = 0.9  # x font_px

    line_height_factor: float = 2.1  # x font_px

    overlap_probability: float = 0.12
    overlap_strength_px: float = 6.0

    opacity_min: float = 0.78
    opacity_max: float = 1.0

    descender_drop_ratio: float = 0.32
    mid_anchor_ratio: float = 0.32  # x font_px, how far "mid" chars float above baseline
    high_anchor_ratio: float = 0.58  # x font_px, how far "high" chars float above baseline

    margin_left_in: float = 0.5
    margin_top_in: float = 0.5
    margin_right_in: float = 0.5
    margin_bottom_in: float = 0.6

    auto_wrap: bool = True
    justify_lines: bool = True
    justify_max_stretch: float = 1.5  # cap on extra word-spacing, x normal word_space -- keeps it looking natural

    paper_tone: str = "White"
    paper_noise_intensity: float = 0.06
    paper_seed: int = 0

    scan_blur_radius: float = 0.35
    scan_rotate_deg: float = 0.0
    jpeg_quality: int = field(default=0)  # 0 = skip re-compression


def _wobble(x_px, page_w_px, amp, freq, phase):
    if amp <= 0:
        return 0.0
    return amp * math.sin(2 * math.pi * freq * (x_px / page_w_px) + phase)


def _estimate_advance(token, glyph_lib, base_scale, params, word_space):
    """Approximate rendered width of one token, ignoring per-glyph jitter --
    close enough to decide wrap points without burning RNG draws meant for
    the real render pass."""
    if token == " ":
        return word_space
    avg_w = glyph_lib.average_width(token)
    if avg_w is None:
        return params.letter_spacing_px * FALLBACK_ADVANCE_UNITS
    return avg_w * base_scale + params.letter_spacing_px


def _wrap_line(tokens, glyph_lib, base_scale, params, usable_w, word_space):
    """Splits one input line's tokens into one or more visual lines that fit
    usable_w, breaking at the last space before the overflow point (never
    mid-word) -- falls back to a hard break only if a single unspaced run
    is wider than the whole page on its own."""
    if not tokens:
        return [tokens]

    def adv(tok):
        return _estimate_advance(tok, glyph_lib, base_scale, params, word_space)

    lines = []
    line_start = 0
    width = 0.0
    last_break = None  # index of the most recent space token since line_start

    i = 0
    n = len(tokens)
    while i < n:
        a = adv(tokens[i])
        if width + a > usable_w and i > line_start:
            # Only defer to the last space if the whole unbreakable word
            # after it would actually fit on a fresh line -- checking just
            # its width *so far* doesn't work, since that's ~usable_w by
            # construction the moment we hit overflow. Without this lookahead,
            # a long unspaced run (a URL, a wall of repeated characters)
            # strands whatever came before it (e.g. "Q: ") on its own line
            # for no benefit, since the run overflows again regardless.
            use_hard_break = True
            if last_break is not None:
                j = last_break + 1
                word_total = 0.0
                while j < n and tokens[j] != " ":
                    word_total += adv(tokens[j])
                    j += 1
                if word_total <= usable_w:
                    use_hard_break = False
            if use_hard_break:
                lines.append(tokens[line_start:i])
                line_start = i
            else:
                lines.append(tokens[line_start:last_break])
                line_start = last_break + 1  # drop the space itself
            width = sum(adv(t) for t in tokens[line_start:i])
            last_break = None
            continue  # re-check tokens[i] against the freshly started line
        if tokens[i] == " ":
            last_break = i
        width += a
        i += 1

    lines.append(tokens[line_start:])
    return lines


def estimate_lines_per_page(params: RenderParams):
    margin_top = round(params.margin_top_in * OUTPUT_DPI)
    margin_bottom = round(params.margin_bottom_in * OUTPUT_DPI)
    line_height = params.font_px * params.line_height_factor
    return max(1, int((PAGE_H_PX - margin_top - margin_bottom) // line_height))


def render_pages(text, glyph_lib, params: RenderParams, rng):
    """Returns (pages, missing_tokens, overflow_lines).

    pages: list[PIL.Image], one per A4 page -- lines are auto-paginated
        top-to-bottom. Blank lines and line breaks you typed are always
        kept literal; a single input line too wide for the page is soft-
        wrapped at the last space (params.auto_wrap=False disables this
        and lets it run off the edge instead, like a typewriter).
    missing_tokens: sorted list of characters with no glyph in the profile.
    overflow_lines: 1-indexed input line numbers whose rendered width still
        ran past the right margin -- with auto_wrap on this should be rare
        (only a single unspaced run wider than the whole page forces this),
        surfaced instead of silently clipping.
    """
    ink_color = INK_COLORS.get(params.ink_color_name, INK_COLORS["Blue"])
    margin_left = round(params.margin_left_in * OUTPUT_DPI)
    margin_top = round(params.margin_top_in * OUTPUT_DPI)
    margin_right = round(params.margin_right_in * OUTPUT_DPI)
    margin_bottom = round(params.margin_bottom_in * OUTPUT_DPI)

    usable_w = PAGE_W_PX - margin_left - margin_right
    line_height = params.font_px * params.line_height_factor
    word_space = params.font_px * params.word_space_factor

    base_scale = params.font_px / max(glyph_lib.reference_height(), 1)

    input_lines = tokenize_text(text)
    if params.auto_wrap:
        lines = []
        line_origin = []
        for orig_idx, tokens in enumerate(input_lines):
            for wrapped in _wrap_line(tokens, glyph_lib, base_scale, params, usable_w, word_space):
                lines.append(wrapped)
                line_origin.append(orig_idx + 1)
    else:
        lines = input_lines
        line_origin = list(range(1, len(input_lines) + 1))

    # Greedy word-wrap always leaves some ragged-right slack (the next word
    # genuinely doesn't fit) -- mild justification narrows that gap without
    # forcing a rigid, unnatural flush-right edge. Only lines that actually
    # got wrapped (i.e. a sibling continuation follows) are stretched; a
    # paragraph's true last line stays naturally ragged, same as normal
    # justified typography.
    extra_word_space = [0.0] * len(lines)
    if params.justify_lines:
        for i, tokens in enumerate(lines):
            wrapped_onward = i + 1 < len(lines) and line_origin[i + 1] == line_origin[i]
            if not wrapped_onward:
                continue
            space_count = sum(1 for t in tokens if t == " ")
            if space_count == 0:
                continue
            est_width = sum(_estimate_advance(t, glyph_lib, base_scale, params, word_space) for t in tokens)
            # Target just under the full width, not 100% of it -- per-glyph
            # jitter/scale variance in the real render means a line stretched
            # to exactly fill the estimate will often overshoot it slightly.
            slack = usable_w * JUSTIFY_FILL_FRACTION - est_width
            if slack > 0:
                extra_word_space[i] = min(slack / space_count, word_space * params.justify_max_stretch)

    lines_per_page = estimate_lines_per_page(params)

    page_chunks = [lines[i:i + lines_per_page] for i in range(0, len(lines), lines_per_page)] or [[]]
    origin_chunks = [line_origin[i:i + lines_per_page] for i in range(0, len(line_origin), lines_per_page)] or [[]]
    extra_chunks = [extra_word_space[i:i + lines_per_page] for i in range(0, len(extra_word_space), lines_per_page)] or [[]]

    pages = []
    missing_tokens = set()
    overflow_lines = set()

    for page_idx, chunk in enumerate(page_chunks):
        paper = make_paper(PAGE_W_PX, PAGE_H_PX, seed=params.paper_seed + page_idx,
                            tone=params.paper_tone, noise_intensity=params.paper_noise_intensity)
        canvas = paper.convert("RGBA")

        for line_idx, tokens in enumerate(chunk):
            line_top = margin_top + line_idx * line_height
            baseline_y = line_top + line_height * 0.68
            phase = rng.uniform(0, 2 * math.pi)
            line_word_space = word_space + extra_chunks[page_idx][line_idx]

            cursor_x = float(margin_left)
            for token in tokens:
                if token == " ":
                    cursor_x += line_word_space * (1 + rng.uniform(-0.2, 0.2))
                    continue

                variant = glyph_lib.random_variant(token, rng)
                if variant is None:
                    if not token.isspace():
                        missing_tokens.add(token)
                    cursor_x += params.letter_spacing_px * FALLBACK_ADVANCE_UNITS
                    continue

                scale = base_scale * (1 + rng.uniform(-params.scale_jitter_pct, params.scale_jitter_pct))
                new_w = max(1, round(variant.width * scale))
                new_h = max(1, round(variant.height * scale))
                resized = variant.resize((new_w, new_h), Image.LANCZOS)

                pad = int(0.22 * max(new_w, new_h)) + 4
                padded = Image.new("RGBA", (new_w + 2 * pad, new_h + 2 * pad), (0, 0, 0, 0))
                padded.paste(resized, (pad, pad), resized)

                angle = rng.uniform(-params.rotation_jitter_deg, params.rotation_jitter_deg)
                rotated = padded.rotate(angle, resample=Image.BICUBIC, expand=False)

                bbox = rotated.getbbox()
                if bbox is None:
                    cursor_x += new_w + params.letter_spacing_px
                    continue

                wob = _wobble(cursor_x, PAGE_W_PX, params.baseline_wobble_amp_px,
                               params.baseline_wobble_freq, phase)
                anchor = glyph_lib.anchor_for(token)
                if anchor == "descender":
                    vertical_offset = new_h * params.descender_drop_ratio
                elif anchor == "high":
                    vertical_offset = -params.high_anchor_ratio * params.font_px
                elif anchor == "mid":
                    vertical_offset = -params.mid_anchor_ratio * params.font_px
                else:
                    vertical_offset = 0

                paste_x = round(cursor_x - bbox[0] + rng.uniform(-params.x_jitter_px, params.x_jitter_px))
                paste_y = round(baseline_y + wob - bbox[3] + vertical_offset
                                 + rng.uniform(-params.y_jitter_px, params.y_jitter_px))

                opacity_factor = rng.uniform(params.opacity_min, params.opacity_max)
                alpha = rotated.split()[3]
                if opacity_factor < 0.999:
                    alpha = alpha.point(lambda a, f=opacity_factor: int(a * f))
                colored = Image.new("RGBA", rotated.size, ink_color + (0,))
                colored.putalpha(alpha)

                canvas.paste(colored, (paste_x, paste_y), colored)

                gap = params.letter_spacing_px * (1 + rng.uniform(-params.spacing_jitter_pct, params.spacing_jitter_pct))
                if rng.random() < params.overlap_probability:
                    gap -= rng.uniform(0, params.overlap_strength_px)
                cursor_x += new_w + gap

            if cursor_x - margin_left > usable_w + OVERFLOW_TOLERANCE_PX:
                overflow_lines.add(origin_chunks[page_idx][line_idx])  # 1-indexed, original input line

        finished = apply_scan_look(
            canvas.convert("RGB"),
            blur_radius=params.scan_blur_radius,
            jpeg_quality=params.jpeg_quality or None,
            rotate_deg=params.scan_rotate_deg,
        )
        pages.append(finished)

    return pages, sorted(missing_tokens), sorted(overflow_lines)
