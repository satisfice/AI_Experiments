#!/usr/bin/env python3
"""Item-level quality detectors: format-agnostic checks applied uniformly to
every parsed item regardless of which parser produced it (see
process_single_file._check_item_quality_issues)."""

import re

# Shared punctuation character class used by both this module's detectors and
# item_cleanup's transforms -- the two must agree on what counts as punctuation.
_PUNCT_CHARS = r'\s\*\-+:;,\.!?\}\[\]{}()\[\]'
_LEADING_PUNCT_RE = re.compile(r'^[' + _PUNCT_CHARS + r']+')
_TRAILING_PUNCT_RE = re.compile(r'[' + _PUNCT_CHARS + r']+$')


def _detect_pattern_in_item(item, pattern, location='full'):
    """Check if item matches a pattern at a specific location.
    location: 'start' (match), 'end' (search), 'full' (search), or 'interior' (after stripping edges).
    """
    if location == 'start':
        return bool(pattern.match(item))
    elif location == 'end':
        return bool(pattern.search(item))
    elif location == 'interior':
        inner = _LEADING_PUNCT_RE.sub('', item)
        inner = _TRAILING_PUNCT_RE.sub('', inner)
        # For interior, check if any character doesn't fit the whitelist
        return any(not (c.isalpha() or c.isdigit() or c in " '-") for c in inner)
    else:  # 'full'
        return bool(pattern.search(item))


def detect_leading_punctuation(item):
    """Return True if item begins with a punctuation/formatting character."""
    return _detect_pattern_in_item(item, _LEADING_PUNCT_RE, location='start')


def detect_trailing_punctuation(item):
    """Return True if item ends with a punctuation/formatting character."""
    return _detect_pattern_in_item(item, _TRAILING_PUNCT_RE, location='end')


def detect_internal_punctuation(item):
    """Return True if item contains a special character in its interior
    (i.e. after stripping leading/trailing formatting chars the cleanup pipeline
    would remove). Catches things like slashes or colons inside words."""
    return _detect_pattern_in_item(item, None, location='interior')


def extract_first_alpha_string(item):
    """
    Extract the first contiguous string of alphabetical characters from an item.
    Returns the extracted string in lowercase for case-insensitive sorting,
    or the full item (lowercased) if no alphabetical characters are found.

    Examples:
    - "123dog" -> "dog"
    - "_shrew" -> "shrew"
    - "cat42mouse" -> "cat"
    - "123" -> "123" (fallback to full item)
    """
    match = re.search(r'[a-zA-Z]+', item)
    if match:
        return match.group(0).lower()
    return item.lower()


def detect_markup_artifact(item):
    """Check if item contains residual HTML/XML/markdown markup."""
    markup_patterns = [
        r'</?[a-zA-Z]',    # HTML/XML tags
        r'\*\*',            # Markdown bold
        r'##',              # Markdown headers
        r'\[\[|\]\]',       # Wiki-style links
    ]
    for pattern in markup_patterns:
        if re.search(pattern, item):
            return True
    return False


def detect_repeated_chars(item):
    """Check if item has 3+ consecutive identical characters."""
    return bool(re.search(r'(.)\1{2,}', item, re.IGNORECASE))


def detect_case(items):
    """
    Detect the case pattern of items (before lowercasing).
    Returns (case, consistent) where:
    - case: "upper", "lower", or "mixed"
    - consistent: true if all items have same case, false if mixed
    """
    if not items:
        return "lower", True

    # Collect case info for items that have alphabetic characters
    item_cases = []
    for item in items:
        item_str = str(item)
        # Skip items with no alphabetic characters
        if not any(c.isalpha() for c in item_str):
            continue

        if item_str.isupper():
            item_cases.append("upper")
        elif item_str.islower():
            item_cases.append("lower")
        else:
            item_cases.append("mixed")

    if not item_cases:
        return "lower", True

    # Check consistency
    unique_cases = set(item_cases)

    if "mixed" in unique_cases:
        return "mixed", False
    elif len(unique_cases) == 1:
        return unique_cases.pop(), True
    else:
        return "mixed", False
