#!/usr/bin/env python3
"""General item cleanup functions: each takes a list of strings, applies one
transformation, and returns (modified_items, cleanup_str_or_none). A cleanup
string is emitted only when at least one item was actually changed.

These apply to all formats -- process_single_file._apply_cleanup_pipeline runs
most of them on every parsed item regardless of source format. csv_parser and
text_parser also call several of them directly (clean_strip_leading_bullets,
clean_strip_leading_numbers, clean_strip_quotes)."""

import re

from item_quality import _LEADING_PUNCT_RE, _TRAILING_PUNCT_RE


def _punct_cleanup_str(rule, changed, chars_stripped):
    """Return a cleanup string with optional stripped-char annotation, or None if nothing changed."""
    if not changed:
        return None
    chars_str = ''.join(sorted(chars_stripped))
    suffix = f" ({chars_str})" if chars_str else ''
    return f"{rule}: {changed} items{suffix}"


def _clean_with_transform(items, rule, transform):
    """Apply `transform` to each item, tracking how many items actually changed.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = transform(item)
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"{rule}: {changed} items" if changed else None
    return result, cleanup


def _clean_with_char_tracking(items, rule, pattern):
    """Like _clean_with_transform, but for anchored regexes where the specific
    characters stripped are worth reporting (via _punct_cleanup_str)."""
    result = []
    changed = 0
    chars_stripped = set()
    for item in items:
        m = pattern.search(item)
        if m:
            changed += 1
            chars_stripped.update(c for c in m.group(0) if not c.isspace())
        result.append(pattern.sub('', item).strip())
    return result, _punct_cleanup_str(rule, changed, chars_stripped)


def clean_strip_leading_format(items):
    """Strip leading formatting characters (* - + : ; , . ! ? { } [ ] etc.).
    Rule: Strip-Leading-Formatting.
    Returns (items, cleanup_str_or_none). Cleanup string includes which chars were stripped."""
    return _clean_with_char_tracking(items, "Strip-Leading-Formatting", _LEADING_PUNCT_RE)


def clean_strip_number_word_prefix(items):
    """Strip a numeric prefix attached directly to a word (e.g. '48eagle' -> 'eagle').
    Rule: Strip-Number-Word-Prefix.
    Returns (items, cleanup_str_or_none)."""
    return _clean_with_transform(items, "Strip-Number-Word-Prefix",
                                  lambda item: re.sub(r'^[0-9]+([a-zA-Z])', r'\1', item).strip())


def clean_remove_parenthetical(items):
    """Remove parenthetical content (e.g. 'camelopard (giraffe)' -> 'camelopard').
    Rule: Remove-Parenthetical.
    Returns (items, cleanup_str_or_none)."""
    return _clean_with_transform(items, "Remove-Parenthetical",
                                  lambda item: re.sub(r'\s*\([^)]*\)', '', item).strip())


def clean_strip_trailing_punct(items):
    """Strip trailing formatting characters (. , ; : ! ? etc.).
    Rule: Strip-Trailing-Punctuation.
    Returns (items, cleanup_str_or_none). Cleanup string includes which chars were stripped."""
    return _clean_with_char_tracking(items, "Strip-Trailing-Punctuation", _TRAILING_PUNCT_RE)


def clean_strip_doubled_punct(items):
    """Remove doubled or tripled internal punctuation (e.g. 'ti::ger' -> 'tiger').
    Rule: Strip-Doubled-Punctuation.
    Returns (items, cleanup_str_or_none)."""
    return _clean_with_transform(
        items, "Strip-Doubled-Punctuation",
        lambda item: re.sub(r'([a-zA-Z])([:.;,\-_/\\|])\2+([a-zA-Z])', r'\1\3', item))


def clean_strip_leading_hyphens(items):
    """Strip any remaining leading hyphens and spaces (final normalization).
    Rule: Strip-Leading-Hyphens.
    Returns (items, cleanup_str_or_none)."""
    return _clean_with_transform(items, "Strip-Leading-Hyphens",
                                  lambda item: item.lstrip('- ').strip())


def clean_lowercase(items):
    """Lowercase all items.
    Rule: Lowercase.
    Returns (items, cleanup_str)."""
    result = [item.lower() for item in items]
    return result, "Lowercase"


def clean_strip_quotes(items):
    """Remove outer quote wrapping (single or double) from items.
    Rule: Strip-Quotes.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        stripped = item.strip()
        while stripped and stripped[0] in ('"', "'") and stripped[-1] == stripped[0]:
            stripped = stripped[1:-1].strip()
        if stripped != item:
            changed += 1
        result.append(stripped)
    return result, (f"Strip-Quotes: {changed} items" if changed else None)


def clean_strip_leading_bullets(items):
    """Remove leading bullet list markers (* - +) from items.
    Rule: Strip-Leading-Bullets.
    Returns (items, cleanup_str_or_none)."""
    return _clean_with_transform(items, "Strip-Leading-Bullets",
                                  lambda item: re.sub(r'^[\s*\-+]+', '', item).strip())


def clean_strip_leading_numbers(items):
    """Remove leading number prefixes with punctuation (e.g. '1. ', '2) ', '3: ') from items.
    Rule: Strip-Leading-Numbers.
    Returns (items, cleanup_str_or_none)."""
    return _clean_with_transform(items, "Strip-Leading-Numbers",
                                  lambda item: re.sub(r'^\d+[\.\):\-\s]+', '', item).strip())
