#!/usr/bin/env python3
"""Parsing helpers shared by more than one format-specific parser.

_flatten_nested_items is used by both json_parser and yaml_parser.
parse_single_row / parse_one_per_line operate on already-tokenized rows
(not CSV syntax itself) and are written to be reusable by any parser that
produces one-sequence-of-values or one-value-per-line output; csv_parser
is their only caller today, but they hold no CSV-specific logic."""


def _flatten_nested_items(items):
    """Flatten any nested list items into a single list.
    Returns (flattened_items, had_nested_lists)."""
    flattened = []
    had_nested_lists = False
    for item in items:
        if isinstance(item, list):
            had_nested_lists = True
            flattened.extend(item)
        else:
            flattened.append(item)
    return flattened, had_nested_lists


def parse_single_row(row):
    """Return items from a single sequence (one row of already-parsed values).
    Rule: Parse-Single-Row.
    Returns (items, cleanup_str)."""
    return list(row), "Parse-Single-Row"


def parse_one_per_line(lines):
    """Return items from a sequence where each element is one item (one per line).
    Strips whitespace and drops empty entries.
    Rule: Parse-One-Per-Line.
    Returns (items, cleanup_str)."""
    return [str(line).strip() for line in lines if str(line).strip()], "Parse-One-Per-Line"
