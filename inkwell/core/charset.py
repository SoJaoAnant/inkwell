"""Defines every glyph the handwriting template asks the user to fill in.

Each entry is one "row" on the template: a character (or a short literal
token like "->"), a human-readable label, a filesystem-safe name (used for
the saved PNG), and a vertical anchor class -- because the renderer crops
each glyph tightly to its ink and re-anchors it, wherever it lands in the
box doesn't matter, but *which* anchor it uses does:
  - "baseline" (default): bottom of the ink sits on the writing line.
  - "descender": hangs partway below the line (g/j/p/q/y/comma/semicolon).
  - "mid": floats around x-height, like a hyphen or equals sign.
  - "high": floats up near cap-height, like an apostrophe or quote mark.
"""

DESCENDER_CHARS = set("gjpqy,;")
HIGH_CHARS = set("'\"")
MID_CHARS = set("-=")

UPPERCASE = [(c, f"upper_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
LOWERCASE = [(c, f"lower_{c}") for c in "abcdefghijklmnopqrstuvwxyz"]
DIGITS = [(c, f"digit_{c}") for c in "0123456789"]

PUNCTUATION_NAMES = {
    ".": "period", ",": "comma", ":": "colon", ";": "semicolon",
    "'": "apostrophe", '"': "dquote", "(": "lparen", ")": "rparen",
    "?": "question", "!": "exclaim", "-": "hyphen", "_": "underscore",
    "&": "amp", "@": "at", "#": "hash", "/": "slash", "\\": "backslash",
    "+": "plus", "=": "equals", "*": "asterisk", "%": "percent",
}
PUNCTUATION = [(c, name) for c, name in PUNCTUATION_NAMES.items()]

# Multi-character literal tokens that get their own glyph instead of being
# built from individual characters (an arrow drawn as "-" then ">" looks
# nothing like a real hand-drawn arrow).
SPECIAL_TOKENS = {
    "->": "arrow",
    "=>": "fatarrow",
}
SPECIAL = [(tok, name) for tok, name in SPECIAL_TOKENS.items()]

SECTIONS = [
    ("Uppercase", UPPERCASE),
    ("Lowercase", LOWERCASE),
    ("Digits", DIGITS),
    ("Punctuation & Symbols", PUNCTUATION),
    ("Special tokens", SPECIAL),
]

VARIANTS_PER_GLYPH = 4


class CharEntry:
    __slots__ = ("char", "safe_name", "section", "anchor")

    def __init__(self, char, safe_name, section):
        self.char = char
        self.safe_name = safe_name
        self.section = section
        if len(char) != 1:
            self.anchor = "baseline"
        elif char in DESCENDER_CHARS:
            self.anchor = "descender"
        elif char in HIGH_CHARS:
            self.anchor = "high"
        elif char in MID_CHARS:
            self.anchor = "mid"
        else:
            self.anchor = "baseline"

    @property
    def is_descender(self):
        return self.anchor == "descender"

    def __repr__(self):
        return f"CharEntry({self.char!r}, {self.safe_name!r})"


def all_entries():
    entries = []
    for section_name, chars in SECTIONS:
        for char, safe_name in chars:
            entries.append(CharEntry(char, safe_name, section_name))
    return entries


def token_lookup():
    """Longest-match-first list of (token_string, safe_name) for layout parsing."""
    entries = all_entries()
    return sorted(((e.char, e.safe_name) for e in entries), key=lambda t: -len(t[0]))
