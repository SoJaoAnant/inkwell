"""Turns photographed/scanned filled-in template pages into a glyph library.

Expects one image per template page, provided in an order that matches the
page order of the generated template (name your scan files so they sort in
that order, e.g. Scan_1.jpg, Scan_2.jpg, ... -- most scanner apps already do
this automatically since you scan pages in sequence).
"""

import io
import re
import warnings
import zipfile

import cv2
import numpy as np
from PIL import Image

from core.template_layout import build_placements, num_pages, Layout

# Phone photos of a printed page can legitimately be very large; we cap and
# downscale explicitly right after decoding (see load_images_from_zip), so
# raise PIL's default safety limit rather than have it hard-error first.
Image.MAX_IMAGE_PIXELS = 300_000_000

MIN_INK_PIXELS = 12
MIN_INK_CONTRAST = 12  # min (paper - darkest pixel) grayscale gap for a cell to count as non-empty
MAX_INK_FRACTION = 0.35  # cells shaded darker than this fraction look like a gradient/shadow, not writing
MIN_BLOB_PIXELS = 4  # connected components smaller than this are treated as noise, not ink
MAX_INPUT_SIDE = 4000  # downscale huge scans/photos before processing -- we warp down to page size anyway
CELL_INSET_PX = 6  # shrink each cell rect inward so we never capture the guide border


def _natural_sort_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


class UnsupportedArchiveError(ValueError):
    """Raised when the uploaded file isn't a real ZIP -- carries a message
    specific enough to tell the user what to do about it."""


_SIGNATURES = [
    (b"Rar!\x1a\x07\x01\x00", "This is a RAR5 archive, not a ZIP. In WinRAR, use "
                              "\"Add to archive...\" and set Archive format to ZIP "
                              "(or right-click the files -> \"Add to <name>.zip\") -- "
                              "don't just rename a .rar file to .zip."),
    (b"Rar!\x1a\x07\x00", "This is a RAR archive, not a ZIP. In WinRAR, use "
                           "\"Add to archive...\" and set Archive format to ZIP "
                           "(or right-click the files -> \"Add to <name>.zip\") -- "
                           "don't just rename a .rar file to .zip."),
    (b"7z\xbc\xaf\x27\x1c", "This is a 7z archive, not a ZIP. Please re-compress "
                            "choosing ZIP format instead."),
]


def _check_is_zip(data):
    if data[:2] == b"PK":
        return
    for magic, message in _SIGNATURES:
        if data.startswith(magic):
            raise UnsupportedArchiveError(message)
    raise UnsupportedArchiveError(
        "This doesn't look like a valid ZIP file (unrecognized file signature). "
        "Re-compress the scanned pages choosing ZIP format and try again."
    )


def load_images_from_zip(zip_bytes):
    """Returns a list of PIL RGB images, ordered by filename."""
    _check_is_zip(zip_bytes)
    images = []
    try:
        zf_ctx = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise UnsupportedArchiveError(
            f"Couldn't open this as a ZIP file ({e}). Re-compress the scanned "
            f"pages choosing ZIP format and try again."
        ) from e
    with zf_ctx as zf:
        names = [n for n in zf.namelist()
                 if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"))
                 and not n.startswith("__MACOSX")]
        names.sort(key=_natural_sort_key)
        for name in names:
            data = zf.read(name)
            # Phone photos can be huge -- we only ever warp down to the
            # template's canonical page size, so decoding at full resolution
            # buys nothing but slowness. PIL's decompression-bomb warning
            # fires during decode itself (before we can shrink anything), so
            # it's suppressed here deliberately -- we're consciously
            # accepting a large image and immediately capping it below.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                img = Image.open(io.BytesIO(data)).convert("RGB")
            if max(img.size) > MAX_INPUT_SIDE:
                img.thumbnail((MAX_INPUT_SIDE, MAX_INPUT_SIDE), Image.LANCZOS)
            images.append((name, img))
    return images


def _order_points(pts):
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).flatten()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, bl, br], dtype=np.float32)


def _find_fiducials(gray, page_w, page_h):
    """Locate the 4 solid black corner squares; return their centers or None."""
    h, w = gray.shape
    _, mask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    quadrants = {"tl": None, "tr": None, "bl": None, "br": None}
    min_area = (w * h) * 0.00015

    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        if cw == 0 or ch == 0 or max(cw, ch) / min(cw, ch) > 1.6:
            continue  # not roughly square
        cx, cy = x + cw / 2, y + ch / 2
        key = ("t" if cy < h / 2 else "b") + ("l" if cx < w / 2 else "r")
        best = quadrants[key]
        if best is None or area > best[0]:
            quadrants[key] = (area, (cx, cy))

    if any(v is None for v in quadrants.values()):
        return None
    return {k: v[1] for k, v in quadrants.items()}


def _warp_to_canonical(pil_img, lay: Layout):
    arr = np.array(pil_img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    found = _find_fiducials(gray, lay.page_w, lay.page_h)

    if found is None:
        # Fallback: assume an already-straight scan, just resize to canonical size.
        resized = pil_img.resize((lay.page_w, lay.page_h), Image.LANCZOS)
        return resized, False

    src = np.array([found["tl"], found["tr"], found["bl"], found["br"]], dtype=np.float32)
    fr = lay.fiducial_rects()
    canon = np.array([
        ((fr[0][0] + fr[0][2]) / 2, (fr[0][1] + fr[0][3]) / 2),  # tl
        ((fr[1][0] + fr[1][2]) / 2, (fr[1][1] + fr[1][3]) / 2),  # tr
        ((fr[2][0] + fr[2][2]) / 2, (fr[2][1] + fr[2][3]) / 2),  # bl
        ((fr[3][0] + fr[3][2]) / 2, (fr[3][1] + fr[3][3]) / 2),  # br
    ], dtype=np.float32)

    matrix = cv2.getPerspectiveTransform(src, canon)
    warped = cv2.warpPerspective(arr, matrix, (lay.page_w, lay.page_h),
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    return Image.fromarray(warped), True


def _crop_ink(cell_gray, sensitivity=1.0):
    """Returns an RGBA PIL image (black ink, alpha = ink density) tightly
    cropped to content, or None if the cell looks empty.

    Contrast is measured per-cell (paper level vs. the cell's own darkest
    pixels) rather than against one fixed global threshold, so it adapts to
    whatever's actually on the page: a bold ballpoint, a light pencil, or a
    textured/semi-transparent digital brush that never gets fully dark.
    `sensitivity` > 1 stretches that range further so lighter ink saturates
    to full opacity sooner.
    """
    gray = cell_gray.astype(np.float32)
    paper_level = float(np.percentile(gray, 92))
    # A 1st-percentile *fraction* breaks down for tiny marks (a period/comma
    # might be well under 1% of the cell's pixels), so anchor on the mean of
    # a small fixed COUNT of the darkest pixels instead -- robust to both a
    # single noisy pixel and to marks far smaller than 1% of the cell.
    darkest_count = min(20, gray.size)
    dark_level = float(np.partition(gray, darkest_count - 1, axis=None)[:darkest_count].mean())
    contrast = paper_level - dark_level

    if contrast < MIN_INK_CONTRAST:
        return None  # nothing dark enough on this cell to be ink

    # A written mark is localized -- only a minority of the cell is dark.
    # A smooth lighting gradient/shadow (common in phone photos) can have
    # similar paper-vs-darkest contrast but shades a large, continuous
    # fraction of the cell, so reject that shape even if "contrast" alone
    # looks ink-like.
    midpoint = (paper_level + dark_level) / 2
    dark_fraction = float(np.mean(gray < midpoint))
    if dark_fraction > MAX_INK_FRACTION:
        return None

    span = max(contrast / max(sensitivity, 0.05), 1.0)
    alpha = np.clip((paper_level - gray) / span, 0.0, 1.0)
    alpha_u8 = (alpha * 255).astype(np.uint8)

    # Real ink forms contiguous blobs with a genuinely dark core; background
    # noise/JPEG artifacts crossing the faint end of the alpha threshold can
    # still form sizable connected regions by pure chance (a weak threshold
    # alone isn't enough to tell them apart from a faint but real stroke).
    # Hysteresis fixes this: only keep a weakly-lit component if it contains
    # at least one pixel that reaches a genuinely high-confidence "this is
    # definitely ink" depth -- noise fluctuating around the paper level never
    # gets that dark, no matter how large an area it happens to cover.
    weak_mask = alpha_u8 > 25
    strong_mask = alpha_u8 > 140
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(weak_mask.astype(np.uint8), connectivity=8)
    keep = [
        i for i in range(1, num_labels)
        if stats[i, cv2.CC_STAT_AREA] >= MIN_BLOB_PIXELS and np.any(strong_mask[labels == i])
    ]
    if not keep:
        return None
    component_mask = np.isin(labels, keep)
    alpha_u8 = np.where(component_mask, alpha_u8, 0).astype(np.uint8)

    ys, xs = np.where(component_mask)
    if len(xs) < MIN_INK_PIXELS:
        return None

    pad = 3
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, alpha_u8.shape[1])
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, alpha_u8.shape[0])
    alpha_crop = alpha_u8[y0:y1, x0:x1]
    rgb = np.zeros((*alpha_crop.shape, 3), dtype=np.uint8)
    rgba = np.dstack([rgb, alpha_crop])
    return Image.fromarray(rgba, mode="RGBA")


def _load_and_warp_pages(zip_bytes, lay, n_pages, progress_cb=None):
    """Shared by process_scan_zip and the sensitivity preview: loads the zip,
    perspective-corrects each page, returns (by_page: {idx: grayscale array}, warnings)."""
    named_images = load_images_from_zip(zip_bytes)
    warnings = []

    if not named_images:
        return {}, ["No image files found inside the zip."]

    if len(named_images) < n_pages:
        warnings.append(
            f"Expected {n_pages} page images but the zip only had {len(named_images)}. "
            f"Missing pages will just have fewer/no variants for their characters."
        )
    elif len(named_images) > n_pages:
        warnings.append(
            f"Zip had {len(named_images)} images, only using the first {n_pages} "
            f"(sorted by filename) as template pages."
        )

    by_page = {}
    for idx, (name, img) in enumerate(named_images[:n_pages]):
        warped, used_fiducials = _warp_to_canonical(img, lay)
        if not used_fiducials:
            warnings.append(
                f"Couldn't find the 4 corner markers in '{name}' -- used a plain resize instead. "
                f"Results may be misaligned if that page was photographed at an angle."
            )
        by_page[idx] = np.array(warped.convert("L"))
        if progress_cb:
            progress_cb((idx + 1) / len(named_images))

    return by_page, warnings


def extract_sample_glyphs(zip_bytes, sample_chars, sensitivity=1.0):
    """Segments just a handful of characters (by their literal char, e.g.
    'A', 'a', '.') for a quick preview -- lets the sensitivity slider be
    tuned against real ink before committing to processing all 340 boxes."""
    lay, placements, _ = build_placements()
    n_pages = num_pages(placements)
    by_page, warnings = _load_and_warp_pages(zip_bytes, lay, n_pages)

    wanted = set(sample_chars)
    result = {}
    for p in placements:
        if p["char"] not in wanted:
            continue
        page_gray = by_page.get(p["page"])
        if page_gray is None:
            continue
        x0, y0, x1, y1 = p["rect"]
        x0, y0 = x0 + CELL_INSET_PX, y0 + CELL_INSET_PX
        x1, y1 = x1 - CELL_INSET_PX, y1 - CELL_INSET_PX
        cell = page_gray[y0:y1, x0:x1]
        glyph = _crop_ink(cell, sensitivity=sensitivity)
        result.setdefault(p["char"], []).append(glyph)  # keep None slots -> "blank" in the preview

    return result, warnings


def process_scan_zip(zip_bytes, progress_cb=None, sensitivity=1.0):
    """Returns (glyph_images: dict[safe_name] -> list[PIL RGBA], warnings: list[str]).

    `sensitivity` controls how readily faint/textured ink is treated as
    fully-opaque -- raise it for light pencil or textured tablet-brush
    scans, lower it if faint paper shadows/creases are getting picked up
    as false ink.
    """
    lay, placements, _ = build_placements()
    n_pages = num_pages(placements)
    by_page, warnings = _load_and_warp_pages(zip_bytes, lay, n_pages, progress_cb=progress_cb)
    if not by_page:
        return {}, warnings

    glyph_images = {}
    missing = []
    placements_by_char = {}
    for p in placements:
        placements_by_char.setdefault(p["safe_name"], []).append(p)

    for safe_name, plist in placements_by_char.items():
        variants = []
        for p in plist:
            page_gray = by_page.get(p["page"])
            if page_gray is None:
                continue
            x0, y0, x1, y1 = p["rect"]
            x0, y0 = x0 + CELL_INSET_PX, y0 + CELL_INSET_PX
            x1, y1 = x1 - CELL_INSET_PX, y1 - CELL_INSET_PX
            cell = page_gray[y0:y1, x0:x1]
            glyph = _crop_ink(cell, sensitivity=sensitivity)
            if glyph is not None:
                variants.append(glyph)
        if variants:
            glyph_images[safe_name] = variants
        else:
            missing.append(safe_name)

    if missing:
        warnings.append(f"{len(missing)} character(s) had no usable variants at all: "
                         + ", ".join(missing[:20]) + (", ..." if len(missing) > 20 else ""))

    return glyph_images, warnings
