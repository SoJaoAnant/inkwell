"""Procedural, plain (non-lined) paper backgrounds -- a subtly noisy page
instead of flat white, plus the light global post-processing that gives a
"just scanned" look."""

import numpy as np
from PIL import Image, ImageFilter

TONES = {
    "White": (250, 250, 248),
    "Warm White": (250, 246, 236),
    "Cream": (248, 240, 219),
    "Light Grey": (236, 236, 234),
    "Recycled": (241, 235, 219),
}


def _value_noise(width, height, rng, octaves):
    # Weighted blend of upscaled random grids at different cell densities.
    # Deliberately NOT stretched to fill [0, 1] -- with weights summing to 1
    # the blend already sits in a gentle bell around 0.5, which is what
    # keeps this looking like fine grain instead of high-contrast clouds.
    noise = np.zeros((height, width), dtype=np.float32)
    for cells, weight in octaves:
        small = rng.random((max(cells, 2), max(cells, 2))).astype(np.float32)
        up = np.array(
            Image.fromarray((small * 255).astype(np.uint8)).resize((width, height), Image.BICUBIC),
            dtype=np.float32,
        ) / 255.0
        noise += up * weight
    return noise - 0.5


def make_paper(width, height, seed, tone="White", noise_intensity=0.06, vignette=0.06):
    rng = np.random.default_rng(seed)
    base_rgb = np.array(TONES.get(tone, TONES["White"]), dtype=np.float32)

    # Mostly fine grain (last two octaves) with just a touch of large-scale
    # unevenness (first octave) for a faint "uneven lighting" scan feel.
    noise = _value_noise(width, height, rng, octaves=[(8, 0.2), (40, 0.3), (110, 0.3), (260, 0.2)])
    noise = noise * 2 * noise_intensity  # already centered on 0, just scale

    img = np.tile(base_rgb, (height, width, 1))
    img += noise[..., None] * 255.0

    if vignette > 0:
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        cx, cy = width / 2, height / 2
        dist = np.sqrt(((xx - cx) / (width / 2)) ** 2 + ((yy - cy) / (height / 2)) ** 2)
        shade = 1.0 - vignette * np.clip(dist - 0.55, 0, None)
        img *= shade[..., None]

    img = np.clip(img, 0, 255).astype(np.uint8)
    return Image.fromarray(img, mode="RGB")


def apply_scan_look(img, blur_radius=0.35, jpeg_quality=None, rotate_deg=0.0):
    """Mild global post-processing so a rendered page reads as 'scanned'."""
    out = img
    if rotate_deg:
        out = out.convert("RGB").rotate(
            rotate_deg, resample=Image.BICUBIC, expand=False,
            fillcolor=(250, 250, 248),
        )
    if blur_radius > 0:
        out = out.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    if jpeg_quality:
        import io
        buf = io.BytesIO()
        out.convert("RGB").save(buf, format="JPEG", quality=jpeg_quality)
        buf.seek(0)
        out = Image.open(buf).convert("RGB")
    return out
