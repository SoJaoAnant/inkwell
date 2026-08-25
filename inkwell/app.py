import io
import random

import streamlit as st
from PIL import Image

from core import profile as profile_store
from core.paper import TONES
from core.renderer import RenderParams, render_pages, estimate_lines_per_page
from core.segmentation import process_scan_zip, extract_sample_glyphs, UnsupportedArchiveError
from core.template_gen import generate_pages

st.set_page_config(page_title="Inkwell", page_icon="✒️", layout="wide")


@st.cache_resource(show_spinner=False)
def _load_library(profile_name, _version):
    return profile_store.GlyphLibrary(profile_name)


def get_library(profile_name):
    # _version busts the cache after re-processing a profile with the same name.
    version = st.session_state.get(f"profile_version::{profile_name}", 0)
    return _load_library(profile_name, version)


def bump_profile_version(profile_name):
    key = f"profile_version::{profile_name}"
    st.session_state[key] = st.session_state.get(key, 0) + 1


if "seed" not in st.session_state:
    st.session_state.seed = random.randint(0, 1_000_000)


def to_pdf_bytes(pages):
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    buf.seek(0)
    return buf.getvalue()


def to_png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def render_preview_strip(variants, tile=70, ink_color=(23, 47, 133)):
    """One row of small tiles, one per template box, showing the extracted
    ink (tinted, on white) or 'blank' for a box nothing was detected in --
    lets sensitivity be judged visually before committing to full processing."""
    strip = Image.new("RGB", (tile * len(variants), tile), (255, 255, 255))
    for i, glyph in enumerate(variants):
        x0 = i * tile
        strip.paste((235, 235, 235), (x0, 0, x0 + tile - 2, tile))
        if glyph is None:
            continue
        thumb = glyph.copy()
        thumb.thumbnail((tile - 8, tile - 8))
        colored = Image.new("RGBA", thumb.size, ink_color + (0,))
        colored.putalpha(thumb.split()[3])
        px = x0 + (tile - thumb.width) // 2
        py = (tile - thumb.height) // 2
        strip.paste(colored, (px, py), colored)
    return strip


st.title("✒️ Inkwell")
st.caption("Turns typed notes into handwritten-looking, slightly-scanned page images -- from your own handwriting.")

# ---------------------------------------------------------------- sidebar --
with st.sidebar:
    st.header("Handwriting profile")
    profiles = profile_store.list_profiles()

    if profiles:
        active_profile = st.selectbox("Active profile", profiles, key="active_profile")
    else:
        active_profile = None
        st.info("No handwriting profile yet -- set one up below.")

    with st.expander("➕ Set up / add a profile", expanded=not profiles):
        st.markdown(
            "1. Download the blank template below, print it.\n"
            "2. Write each character in its box (a different natural variant "
            "in each of the 4 boxes) using a dark blue or black pen.\n"
            "3. Scan or photograph the filled pages flat, in good light, "
            "keeping all 4 black corner squares visible on every page.\n"
            "4. Zip the page images (name them so they sort in page order, "
            "e.g. page_1.jpg, page_2.jpg -- most scanner apps already do this) "
            "and upload the zip here."
        )

        template_pages = generate_pages()
        template_pdf = io.BytesIO()
        template_pages[0].save(template_pdf, format="PDF", save_all=True, append_images=template_pages[1:])
        st.download_button(
            "📄 Download blank template (PDF)", data=template_pdf.getvalue(),
            file_name="inkwell_template.pdf", mime="application/pdf",
        )

        new_name_raw = st.text_input("Profile name", placeholder="e.g. my-handwriting")
        new_name = profile_store.sanitize_profile_name(new_name_raw)
        zip_file = st.file_uploader("Filled-in template pages (.zip of images)", type=["zip"])
        if new_name_raw and new_name != new_name_raw:
            if new_name:
                st.caption(f"Will be saved as '{new_name}' (trailing spaces/invalid filename characters removed).")
            else:
                st.caption("That name isn't usable as a folder name -- try something else.")
        if zip_file and not new_name:
            st.caption("Enter a profile name above to enable \"Process template\".")
        elif new_name and not zip_file:
            st.caption("Upload a zip above to enable \"Process template\".")

        ink_sensitivity = st.slider(
            "Ink sensitivity", 0.5, 4.0, 1.0, step=0.1,
            help="Contrast between paper and ink is measured per-box, so this usually doesn't "
                 "need touching. Raise it if your pen/brush is light, textured, or semi-transparent "
                 "(e.g. a tablet brush with a pen texture) and glyphs come out too faint or patchy. "
                 "Lower it if faint paper shadows/creases are getting picked up as stray ink.",
        )

        if zip_file:
            st.caption("Preview at this sensitivity (before running the full 340-box process):")
            sample_chars = ["A", "a", "g", "."]
            try:
                with st.spinner("Extracting preview..."):
                    sample_glyphs, _ = extract_sample_glyphs(
                        zip_file.getvalue(), sample_chars, sensitivity=ink_sensitivity
                    )
                cols = st.columns(len(sample_chars))
                for col, ch in zip(cols, sample_chars):
                    with col:
                        st.caption(f"'{ch}'")
                        st.image(render_preview_strip(sample_glyphs.get(ch, [])))
            except Exception as e:
                st.caption(f"Couldn't preview yet: {e}")

        if st.button("Process template", disabled=not (new_name and zip_file)):
            progress = st.progress(0.0, text="Reading scans...")
            try:
                glyph_images, warnings = process_scan_zip(
                    zip_file.getvalue(), progress_cb=lambda f: progress.progress(f, text="Segmenting glyphs..."),
                    sensitivity=ink_sensitivity,
                )
            except UnsupportedArchiveError as e:
                progress.empty()
                st.error(f"⚠️ {e}")
                glyph_images, warnings = None, []
            except Exception as e:
                progress.empty()
                st.error(f"Unexpected error while processing that zip: {e}")
                glyph_images, warnings = None, []
            else:
                progress.empty()

            if glyph_images is None:
                pass
            elif not glyph_images:
                st.error("Couldn't extract any glyphs from that zip. " + " ".join(warnings))
            else:
                try:
                    saved_name = profile_store.save_profile(new_name, glyph_images, warnings)
                except ValueError as e:
                    st.error(str(e))
                else:
                    bump_profile_version(saved_name)
                    st.success(f"Profile '{saved_name}' saved with {len(glyph_images)} characters.")
                    for w in warnings:
                        st.warning(w)
                    st.rerun()

    if profiles:
        with st.expander("🗑️ Delete a profile"):
            del_target = st.selectbox("Profile", profiles, key="del_target")
            confirm = st.checkbox(f"Yes, permanently delete '{del_target}'")
            if st.button("Delete", disabled=not confirm):
                profile_store.delete_profile(del_target)
                st.rerun()

    st.divider()
    st.header("Ink")
    ink_color_name = st.radio("Colour", ["Blue", "Black"], horizontal=True)

    st.divider()
    st.header("Page background")
    paper_tone = st.selectbox("Paper tone", list(TONES.keys()))
    if st.button("🎲 Randomize background"):
        st.session_state.seed = random.randint(0, 1_000_000)
    st.caption(f"Look seed: {st.session_state.seed}")

    st.divider()
    st.header("Tweak parameters")

    with st.expander("Layout & spacing"):
        font_px = st.slider("Letter size (px)", 18, 60, 28)
        letter_spacing_px = st.slider("Base letter spacing", 0.0, 20.0, 5.0)
        word_space_factor = st.slider("Word spacing (x letter size)", 0.3, 2.0, 0.9)
        line_height_factor = st.slider("Line height (x letter size)", 1.4, 3.0, 2.1)
        margin_left_in = st.slider("Left offset (in)", 0.1, 2.0, 0.5, step=0.05,
                                    help="Where the writing starts from the left edge of the page.")
        margin_top_in = st.slider("Top offset (in)", 0.1, 2.0, 0.5, step=0.05,
                                   help="Where the writing starts from the top edge of the page.")
        auto_wrap = st.checkbox(
            "Wrap long lines automatically", value=True,
            help="A single line too wide for the page soft-wraps onto the next line at the last "
                 "space, instead of running off the right edge. Your own line breaks and blank "
                 "lines are always kept exactly as typed either way -- this only kicks in when "
                 "one line by itself is too long to fit.",
        )
        justify_lines = st.checkbox(
            "Even out line endings", value=True,
            help="Word-wrapped lines naturally end at varying points (whatever word fits last), "
                 "leaving some ragged space on the right. This gently stretches the spacing "
                 "between words on wrapped lines to narrow that gap -- capped so it never looks "
                 "artificially justified, and never applied to a paragraph's true last line.",
        )

    with st.expander("Naturalness / jitter"):
        rotation_jitter_deg = st.slider("Rotation jitter (deg)", 0.0, 10.0, 3.0)
        scale_jitter_pct = st.slider("Size jitter (%)", 0.0, 0.3, 0.08)
        x_jitter_px = st.slider("Horizontal jitter (px)", 0.0, 6.0, 1.4)
        y_jitter_px = st.slider("Vertical jitter (px)", 0.0, 8.0, 2.0)
        baseline_wobble_amp_px = st.slider("Baseline wobble amplitude (px)", 0.0, 10.0, 2.5)
        baseline_wobble_freq = st.slider("Baseline wobble frequency", 0.0, 4.0, 1.3)
        spacing_jitter_pct = st.slider("Spacing jitter (%)", 0.0, 1.0, 0.35)

    with st.expander("Overlap"):
        overlap_probability = st.slider("Overlap probability", 0.0, 1.0, 0.12)
        overlap_strength_px = st.slider("Overlap strength (px)", 0.0, 20.0, 6.0)

    with st.expander("Ink pressure"):
        opacity_range = st.slider("Opacity range", 0.3, 1.0, (0.78, 1.0))

    with st.expander("Paper & scan look"):
        paper_noise_intensity = st.slider("Paper noise intensity", 0.0, 0.4, 0.06)
        scan_blur_radius = st.slider("Scan blur radius", 0.0, 2.0, 0.35)
        scan_rotate_deg = st.slider("Whole-page skew (deg)", -3.0, 3.0, 0.0)
        jpeg_quality = st.slider("Re-compression (0 = off)", 0, 95, 0)

# --------------------------------------------------------------- main area --
text = st.text_area(
    "Note text", height=280,
    placeholder="Anant Kumar Sinha\n00719051923\nAIDS - B1\nEcommerce Assignment\n\n"
                "Question 1. Blah blah blah\nAnswer -> Blah blah blah",
    help="Typed exactly as-is -- spaces, blank lines and line breaks are all preserved.",
)

generate = st.button("Generate", type="primary", disabled=not active_profile)
if not active_profile:
    st.info("Set up a handwriting profile in the sidebar first.")

if generate and active_profile:
    library = get_library(active_profile)
    params = RenderParams(
        ink_color_name=ink_color_name,
        font_px=font_px,
        rotation_jitter_deg=rotation_jitter_deg,
        scale_jitter_pct=scale_jitter_pct,
        x_jitter_px=x_jitter_px,
        y_jitter_px=y_jitter_px,
        baseline_wobble_amp_px=baseline_wobble_amp_px,
        baseline_wobble_freq=baseline_wobble_freq,
        letter_spacing_px=letter_spacing_px,
        spacing_jitter_pct=spacing_jitter_pct,
        word_space_factor=word_space_factor,
        line_height_factor=line_height_factor,
        margin_left_in=margin_left_in,
        margin_top_in=margin_top_in,
        auto_wrap=auto_wrap,
        justify_lines=justify_lines,
        overlap_probability=overlap_probability,
        overlap_strength_px=overlap_strength_px,
        opacity_min=opacity_range[0],
        opacity_max=opacity_range[1],
        paper_tone=paper_tone,
        paper_noise_intensity=paper_noise_intensity,
        paper_seed=st.session_state.seed,
        scan_blur_radius=scan_blur_radius,
        scan_rotate_deg=scan_rotate_deg,
        jpeg_quality=jpeg_quality,
    )
    rng = random.Random(st.session_state.seed)
    pages, missing, overflow_lines = render_pages(text, library, params, rng)

    if missing:
        st.warning("No handwritten variant for: " + " ".join(repr(m) for m in missing)
                   + " -- these were skipped (just spaced over).")

    if overflow_lines:
        if auto_wrap:
            st.warning(
                f"Line(s) {', '.join(str(n) for n in overflow_lines)} still run past the right "
                f"margin even after wrapping -- likely one long unspaced run (a URL, a number) with "
                f"nowhere to break. Reduce letter size/spacing or add a space to break it up."
            )
        else:
            st.warning(
                f"Line(s) {', '.join(str(n) for n in overflow_lines)} run past the right margin -- "
                f"\"Wrap long lines automatically\" is off, so anything past the edge is clipped in "
                f"the image below."
            )

    st.caption(f"~{estimate_lines_per_page(params)} lines fit per A4 page at the current letter "
               f"size/spacing -- longer notes automatically continue onto additional pages.")

    for i, page in enumerate(pages):
        st.image(page, caption=f"Page {i + 1}", width="stretch")
        st.download_button(
            f"Download page {i + 1} (PNG)", data=to_png_bytes(page),
            file_name=f"note_page_{i + 1}.png", mime="image/png", key=f"dl_png_{i}",
        )

    if len(pages) > 1:
        st.download_button(
            "Download all pages (PDF)", data=to_pdf_bytes(pages),
            file_name="note.pdf", mime="application/pdf",
        )
