#!/usr/bin/env python3
"""CSV parsing: extract items from CSV-formatted model output, dispatching on
row/column shape (single row, one-per-line, or a proper multi-row/multi-column
grid)."""

import csv
from io import StringIO

from parsing_common import parse_single_row, parse_one_per_line
from item_cleanup import clean_strip_leading_bullets, clean_strip_leading_numbers, clean_strip_quotes


def csv_parse_single_row(rows):
    """Extract items from a single-row CSV where each column is one item.
    Rule: CSV-Parse-Single-Row. Delegates to parse_single_row.
    Returns (items, cleanup_str)."""
    items, _ = parse_single_row(rows[0])
    return items, "CSV-Parse-Single-Row"


def csv_parse_one_per_line(rows):
    """Extract items from a CSV where each row contains exactly one item (no commas).
    Rule: CSV-Parse-One-Per-Line. Delegates to parse_one_per_line.
    Returns (items, cleanup_str)."""
    items, _ = parse_one_per_line([row[0] for row in rows])
    return items, "CSV-Parse-One-Per-Line"


def csv_parse_multi_row(rows):
    """Extract items from a multi-row CSV where each row contains multiple
    comma-separated items; all items from all rows are collected.
    Rule: CSV-Parse-Multi-Row.
    Returns (items, cleanup_str)."""
    return [item for row in rows for item in row], "CSV-Parse-Multi-Row"


def csv_parse_rows(content):
    """Read CSV content via csv.reader, detect format style, and dispatch to the
    appropriate per-format parser: csv_parse_single_row, csv_parse_one_per_line,
    or csv_parse_multi_row.
    Returns (items, cleanup_str_or_none)."""
    reader = csv.reader(StringIO(content))
    rows = [row for row in reader if row]

    if not rows:
        return [], None

    if len(rows) == 1:
        return csv_parse_single_row(rows)

    max_cols = max(len(row) for row in rows)
    if max_cols == 1:
        return csv_parse_one_per_line(rows)

    return csv_parse_multi_row(rows)


def csv_strip_leading_markers(items):
    """Remove leading bullet markers and number prefixes from CSV items.
    Delegates to clean_strip_leading_bullets then clean_strip_leading_numbers.
    Returns (items, cleanups_list)."""
    cleanups = []
    items, cleanup = clean_strip_leading_bullets(items)
    if cleanup:
        cleanups.append(cleanup)
    items, cleanup = clean_strip_leading_numbers(items)
    if cleanup:
        cleanups.append(cleanup)
    return items, cleanups


def parse_csv(content):
    """Parse CSV content. Orchestrates csv_parse_rows -> clean_strip_quotes.
    Returns (items, cleanups, quality_issues)."""
    cleanups = []
    quality_issues = []
    try:
        items, cleanup = csv_parse_rows(content)
        if cleanup:
            cleanups.append(cleanup)

        items, cleanup = clean_strip_quotes(items)
        if cleanup:
            cleanups.append(cleanup)

        return items, cleanups, quality_issues

    except Exception as e:
        quality_issues.append("parse-failed")
        return [], cleanups, quality_issues
