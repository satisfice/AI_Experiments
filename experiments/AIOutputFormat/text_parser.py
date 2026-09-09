#!/usr/bin/env python3
"""Plain-text and numbered-text parsing: .txt (one item per line) and .txt1
(one numbered item per line)."""

from item_cleanup import clean_strip_leading_numbers


def parse_txt(content):
    """Parse text file: each line is an item.
    Returns (items, cleanups, quality_issues)."""
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    cleanups = []
    quality_issues = []
    return items, cleanups, quality_issues


def parse_txt1(content):
    """Parse .txt1 file: each line is an item, removing leading numbers.
    Delegates to clean_strip_leading_numbers for consistent cleanup and logging.
    Emits a QUALITY flag when no leading numbers are present (unexpected for this format).
    Returns (items, cleanups, quality_issues)."""
    cleanups = []
    quality_issues = []
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    cleaned, cleanup = clean_strip_leading_numbers(items)
    if cleanup:
        cleanups.append(cleanup)
    elif cleaned:
        # numberedText format expected leading numbers; their absence is a quality issue
        quality_issues.append("txt1-no-numbers")
    return cleaned, cleanups, quality_issues
