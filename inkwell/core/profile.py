"""Persists a segmented handwriting profile to disk and loads it back for
rendering. A profile is a folder of glyph PNGs (RGBA, black ink, alpha =
ink density) plus a manifest listing which variants exist for each char.
"""

import json
import pathlib
import random
import re

from PIL import Image

from core.charset import all_entries

PROFILES_DIR = pathlib.Path(__file__).resolve().parent.parent / "profiles"

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_profile_name(name):
    """Makes a profile name safe to use as a Windows directory name, or
    returns None if nothing usable is left.

    Windows silently drops trailing spaces/dots when a directory is
    actually created, which -- if not accounted for up front -- causes a
    mismatch between the path Python thinks it created and the one Windows
    actually did, breaking the very next file write into it.
    """
    if name is None:
        return None
    name = name.strip().rstrip(" .")
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    if not name or name.upper() in _RESERVED_WINDOWS_NAMES:
        return None
    return name


def list_profiles():
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.name for p in PROFILES_DIR.iterdir()
                  if p.is_dir() and (p / "manifest.json").exists())


def save_profile(name, glyph_images, warnings=None):
    name = sanitize_profile_name(name)
    if not name:
        raise ValueError("That profile name isn't usable (empty, or only invalid characters) -- try a different name.")
    profile_dir = PROFILES_DIR / name
    glyphs_dir = profile_dir / "glyphs"
    glyphs_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"characters": {}, "warnings": warnings or []}
    for safe_name, variants in glyph_images.items():
        filenames = []
        for i, img in enumerate(variants):
            fname = f"{safe_name}_{i}.png"
            img.save(glyphs_dir / fname)
            filenames.append(fname)
        manifest["characters"][safe_name] = filenames

    with open(profile_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    return name  # sanitized -- may differ from the name the caller passed in


def delete_profile(name):
    import shutil
    name = sanitize_profile_name(name)
    if not name:
        return
    profile_dir = PROFILES_DIR / name
    if profile_dir.exists():
        shutil.rmtree(profile_dir)


class GlyphLibrary:
    def __init__(self, profile_name):
        self.name = profile_name
        profile_dir = PROFILES_DIR / profile_name
        with open(profile_dir / "manifest.json") as f:
            manifest = json.load(f)

        self.warnings = manifest.get("warnings", [])
        self._char_to_safe = {e.char: e.safe_name for e in all_entries()}
        self._anchors = {e.char: e.anchor for e in all_entries()}

        self._images = {}
        for safe_name, filenames in manifest["characters"].items():
            imgs = []
            for fname in filenames:
                path = profile_dir / "glyphs" / fname
                if path.exists():
                    imgs.append(Image.open(path).convert("RGBA"))
            if imgs:
                self._images[safe_name] = imgs

    def anchor_for(self, token):
        return self._anchors.get(token, "baseline")

    def has(self, token):
        safe_name = self._char_to_safe.get(token)
        return safe_name is not None and safe_name in self._images

    def random_variant(self, token, rng: random.Random):
        safe_name = self._char_to_safe.get(token)
        if safe_name is None:
            return None
        imgs = self._images.get(safe_name)
        if not imgs:
            return None
        return rng.choice(imgs)

    def average_width(self, token):
        """Mean pixel width across a token's variants -- used to estimate
        line width for word-wrapping without needing to actually pick (and
        thereby consume randomness for) a specific variant."""
        if not hasattr(self, "_avg_width_cache"):
            self._avg_width_cache = {}
        if token in self._avg_width_cache:
            return self._avg_width_cache[token]
        safe_name = self._char_to_safe.get(token)
        imgs = self._images.get(safe_name) if safe_name else None
        width = (sum(im.width for im in imgs) / len(imgs)) if imgs else None
        self._avg_width_cache[token] = width
        return width

    def reference_height(self):
        """Median height of a few common uppercase letters -- used to derive
        a sensible default scale factor for rendering."""
        heights = []
        for ch in "HXTMN":
            safe_name = self._char_to_safe.get(ch)
            imgs = self._images.get(safe_name)
            if imgs:
                heights.extend(im.height for im in imgs)
        if not heights:
            all_h = [im.height for imgs in self._images.values() for im in imgs]
            return sorted(all_h)[len(all_h) // 2] if all_h else 150
        heights.sort()
        return heights[len(heights) // 2]
