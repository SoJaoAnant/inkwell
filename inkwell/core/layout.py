"""Turns raw input text into lines of tokens, preserving exactly the
whitespace and line breaks the user typed -- no auto-wrapping, no
stripping. Leading spaces (indentation) survive because a literal space is
just a cursor-advance token, never trimmed."""

from core.charset import SPECIAL_TOKENS

_TOKENS_BY_LEN = sorted(SPECIAL_TOKENS.keys(), key=len, reverse=True)


def tokenize_line(line):
    tokens = []
    i = 0
    n = len(line)
    while i < n:
        matched = None
        for tok in _TOKENS_BY_LEN:
            if line.startswith(tok, i):
                matched = tok
                break
        if matched:
            tokens.append(matched)
            i += len(matched)
        else:
            tokens.append(line[i])
            i += 1
    return tokens


def tokenize_text(text):
    """Returns a list of token-lists, one per line (split on '\\n' only)."""
    return [tokenize_line(line) for line in text.split("\n")]
