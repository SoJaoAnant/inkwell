# How Inkwell works

No AI, no generative model -- deterministic image compositing. Your actual handwriting is
captured once as a set of glyph images, then reassembled per character to render typed text.

## Pipeline

```
1. Template          -> a printable grid: every letter/digit/punctuation mark, 4 boxes each
2. Segmentation       -> a photo/scan of the filled template is cropped into individual glyphs
3. Profile            -> those glyphs are saved to disk as your reusable "handwriting profile"
4. Render             -> typed text is laid out and composited using that profile + paper texture
```

### 1. Template (`core/charset.py`, `core/template_layout.py`, `core/template_gen.py`)

`charset.py` defines every character the app knows how to draw: A-Z, a-z, 0-9, common
punctuation, and two multi-character tokens (`->`, `=>`) that get their own box because they
don't look right built from a hyphen and a greater-than sign.

Each character also gets a **vertical anchor class**, used later at render time:
- `baseline` (default) -- sits on the writing line
- `descender` -- hangs partway below it (g, j, p, q, y, comma, semicolon)
- `mid` -- floats around x-height (hyphen, equals sign)
- `high` -- floats up near cap-height (apostrophe, double-quote)

`template_layout.py` computes the pixel geometry of the printable grid (page size, cell size,
how many characters fit per row/page) -- both the template generator and the segmentation code
import this same geometry, so they can never drift out of sync with each other.

`template_gen.py` draws the actual blank template: labeled boxes plus four solid black squares
in the corners of every page (fiducial markers, used in step 2 to correct a wonky phone photo).

### 2. Segmentation (`core/segmentation.py`)

Given a zip of photographed/scanned filled-in pages:

1. **Perspective correction** -- the four corner squares are detected and used to warp each
   photo back to a perfect rectangle, so a tilted phone photo still lines up with the known grid.
2. **Per-box ink extraction** -- each box is cropped, and "ink" is separated from paper by
   measuring *that specific box's own* contrast (its paper level vs. its own darkest pixels),
   not one fixed threshold for the whole image. This is what lets it handle everything from a
   bold ballpoint pen to a light, textured tablet-brush stroke -- tunable via the "ink
   sensitivity" slider if the default read is too faint or too aggressive.
3. **Noise rejection** -- a hysteresis check (a box only counts as "ink" if it has both a
   plausible connected shape *and* a genuinely dark core) filters out camera noise and paper
   shadows/creases that might otherwise get mistaken for a faint pen stroke.
4. Each surviving box becomes a small transparent PNG: black ink, alpha channel = ink density.
   Color is *not* baked in here -- that's decided at render time.

### 3. Profile (`core/profile.py`)

The segmented glyphs get saved under `profiles/<name>/glyphs/`, with a `manifest.json` listing
which variants exist for each character. `GlyphLibrary` loads a profile back for rendering and
answers three questions the renderer needs: which variants exist for a character, what's its
average width (for word-wrap math), and which anchor class it uses.

### 4. Render (`core/layout.py`, `core/renderer.py`, `core/paper.py`)

Given typed text and a loaded profile:

- **Tokenize** (`layout.py`) -- split into lines on literal `\n` only; nothing is trimmed or
  auto-formatted, so your spacing/indentation/blank lines survive exactly as typed.
- **Word-wrap** (`renderer.py`) -- a line too wide for the page soft-wraps at the last space
  that keeps it fitting, never mid-word unless a single unbroken run (e.g. a URL) is wider than
  the whole page on its own. Your own line breaks are always literal; this only kicks in when
  one line by itself doesn't fit.
- **Justification** -- wrapped lines (but never a paragraph's true last line) get their
  word-spacing gently stretched to narrow the natural ragged-right gap, capped so it never
  looks like rigid typeset justification.
- **Per-glyph placement** -- for each character: pick a random captured variant, apply small
  random rotation/scale/position jitter and pressure-style opacity variance, then paste it so
  its ink sits on the writing line -- or above/below it, per its anchor class. An "overlap"
  parameter occasionally lets adjacent letters crowd into each other, like real handwriting does.
- **Baseline wobble** -- a per-line sine wave nudges the writing line up and down slightly, so
  text doesn't sit unnaturally dead straight.
- **Pagination** -- once a page's line budget (page height ÷ line height) is used up, rendering
  continues onto a fresh A4 page with its own paper texture.
- **Paper texture** (`paper.py`) -- procedural multi-octave noise stands in for a scanned page
  background instead of flat white, followed by a light blur/skew/re-compression pass for the
  "just scanned" look.

## Module map

| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI -- wires all the sliders to `RenderParams`, handles profile setup |
| `core/charset.py` | The character set + vertical anchor classes |
| `core/template_layout.py` | Shared pixel geometry between template generation and segmentation |
| `core/template_gen.py` | Draws the printable blank template |
| `core/segmentation.py` | Photo/scan -> perspective-corrected -> cropped glyph PNGs |
| `core/profile.py` | Saves/loads a handwriting profile; `GlyphLibrary` |
| `core/layout.py` | Text -> tokens, preserving whitespace/line breaks exactly |
| `core/paper.py` | Procedural paper texture + "scanned" post-processing |
| `core/renderer.py` | The core compositor: wrapping, justification, jitter, pagination |

## Everything is a tunable parameter

Nearly every number above (letter size, spacing, jitter amounts, overlap probability, margins,
paper noise, ink sensitivity, ...) lives on `RenderParams` (or is a segmentation argument) and
is exposed as a slider in the sidebar -- nothing about the "naturalness" is hardcoded.
