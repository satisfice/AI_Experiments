#!/usr/bin/env python3
"""Markdown parsing: extract items from lines, skip headers, and detect/strip
bold/italic formatting. Fully self-contained -- no other parser shares this
logic."""

import re
from collections import defaultdict


def parse_md(content):
    """Parse Markdown file: extract items from lines, skip headers, detect formatting.
    Lines that are entirely bold (**text**) or italic (*text* or _text_) have formatting stripped
    and are kept as items, recorded as Markdown-Strip-Bold-Tags or Markdown-Strip-Italic-Tags.
    Lines that are both bold and italic (***text*** or **_text_** etc.) are recorded as Markdown-Both-Bold-And-Italic.
    Partially bold/italic lines are recorded as quality issues (not cleanup tasks).
    Lines that are headings (#) are skipped.
    Bullet markers and numbered prefixes are left in items for the cleanup pipeline to handle.
    Returns (items, cleanups, quality_issues)."""
    items = []
    cleanups = []
    quality_issues = []
    counts = defaultdict(int)

    # Both bold and italic: exactly three asterisks on each end (***text***)
    _BOTH_BOLD_ITALIC_RE = re.compile(r'^\*{3}[^*].*[^*]\*{3}$')
    # Entirely bold: exactly two asterisks on each end (**text**), not three
    _ENTIRELY_BOLD_RE = re.compile(r'^\*{2}[^*].*[^*]\*{2}(?!\*)$')
    # Entirely italic with *: single asterisk on each end (*text*), not double or triple
    _ENTIRELY_ITALIC_STAR_RE = re.compile(r'^\*[^*\s].*[^*\s]\*(?!\*)$')
    # Entirely italic with _: _text_
    _ENTIRELY_ITALIC_UNDER_RE = re.compile(r'^_[^_]+_$')
    # Partially bold: contains ** but not entirely wrapped
    _PARTIAL_BOLD_RE = re.compile(r'(\*{2}[^*].*[^*]\*{2}(?!\*)|\*{2}[^*]+$|^[^*]\*{2})')
    # Partially italic with *: contains single * but not entirely wrapped (excluding entirely italic and bold)
    _PARTIAL_ITALIC_STAR_RE = re.compile(r'(\*[^*\s][^*]*\*(?!\*)|[^*]\*[^*\s]|^\*[^*\s]|[^*\s]\*$)')
    # Partially italic with _: contains _ but not entirely wrapped
    _PARTIAL_ITALIC_UNDER_RE = re.compile(r'([^_]_[^_]+_|_[^_]+_[^_]|^_[^_]|[^_]_$)')

    for line in content.split('\n'):
        if not line.strip():
            continue

        stripped = line.strip()

        # Skip markdown headers (lines starting with #)
        if stripped.startswith('#'):
            counts["header"] += 1
            continue

        # Check for formatting patterns on the full line (including bullets/numbers)
        is_both = bool(_BOTH_BOLD_ITALIC_RE.match(stripped))
        is_entirely_bold = bool(_ENTIRELY_BOLD_RE.match(stripped))
        is_entirely_italic_star = bool(_ENTIRELY_ITALIC_STAR_RE.match(stripped))
        is_entirely_italic_under = bool(_ENTIRELY_ITALIC_UNDER_RE.match(stripped))

        # Check for partial formatting (but not if entirely bold/italic or both)
        has_partial_bold = bool(_PARTIAL_BOLD_RE.search(stripped)) and not is_entirely_bold and not is_both
        has_partial_italic_star = bool(_PARTIAL_ITALIC_STAR_RE.search(stripped)) and not is_entirely_italic_star and not is_both
        has_partial_italic_under = bool(_PARTIAL_ITALIC_UNDER_RE.search(stripped)) and not is_entirely_italic_under and not is_both

        # First matching category wins (mirrors the original if/elif priority order).
        # Entries with a transform strip formatting from content_part; partial matches
        # only record a quality issue and leave content_part untouched.
        categories = [
            (is_both, lambda s: re.sub(r'^\*{3}|^\*_{2}|^_\*{2}|^_{3}|\*{3}$|_\*{2}$|\*_{2}$|_{3}$', '', s), "both"),
            (is_entirely_bold, lambda s: s[2:-2], "entirely_bold"),
            (is_entirely_italic_star, lambda s: s[1:-1], "entirely_italic"),
            (is_entirely_italic_under, lambda s: s[1:-1], "entirely_italic"),
            (has_partial_bold, None, "partial_bold"),
            (has_partial_italic_star, None, "partial_italic_star"),
            (has_partial_italic_under, None, "partial_italic_under"),
        ]
        content_part = stripped
        for matched, transform, counter_key in categories:
            if matched:
                if transform:
                    content_part = transform(content_part)
                counts[counter_key] += 1
                break

        if content_part:
            items.append(content_part)

    cleanup_rules = [
        (counts["header"], cleanups, "MD-Header-Removal"),
        (counts["entirely_bold"], cleanups, "Markdown-Strip-Bold-Tags"),
        (counts["entirely_italic"], cleanups, "Markdown-Strip-Italic-Tags"),
        (counts["both"], cleanups, "Markdown-Both-Bold-And-Italic"),
        (counts["partial_bold"], quality_issues, "Markdown-Cleanup-Partially-Bold-Line"),
        (counts["partial_italic_star"], quality_issues, "Markdown-Cleanup-Partially-Italic-Star-Line"),
        (counts["partial_italic_under"], quality_issues, "Markdown-Cleanup-Partially-Italic-Underscore-Line"),
    ]
    for count, target_list, label in cleanup_rules:
        if count > 0:
            target_list.append(label)

    return items, cleanups, quality_issues
