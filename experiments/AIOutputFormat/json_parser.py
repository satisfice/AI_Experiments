#!/usr/bin/env python3
"""JSON parsing: extract items from JSON-formatted model output, with repair
strategies for common malformations (set-like syntax, Python dict quoting,
truncated output)."""

import json
import re

from parsing_common import _flatten_nested_items


def _repair_truncated_json(content):
    """Attempt to close a truncated JSON string by stripping a dangling trailing
    comma and appending the missing closing brackets/braces.
    Returns (repaired_content, was_repaired).  was_repaired is False when the
    content appeared complete (stack empty) or already invalid beyond repair."""
    # Strip trailing comma that would have been followed by more items
    stripped = re.sub(r',\s*$', '', content.rstrip())
    # Count unmatched opening brackets to determine what's missing
    stack = []
    in_string = False
    escape = False
    for char in stripped:
        if escape:
            escape = False
            continue
        if char == '\\' and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in '{[':
            stack.append(']' if char == '[' else '}')
        elif char in ']}' and stack and stack[-1] == char:
            stack.pop()
    if not stack:
        return content, False
    return stripped + '\n' + ''.join(reversed(stack)), True


def _looks_like_json_set_format(content):
    """True if content is object-shaped ({...}) but contains no colons outside
    quoted strings -- i.e. it's actually a set-like list, not key-value pairs."""
    if not (content.startswith('{') and content.rstrip().endswith('}')):
        return False
    in_quotes = False
    colon_count = 0
    for char in content:
        if char == '"':
            in_quotes = not in_quotes
        elif char == ':' and not in_quotes:
            colon_count += 1
    return colon_count == 0


def _convert_json_set_to_array(content):
    """Convert a set-like {a, b, c} string to array syntax [a, b, c]."""
    converted = content.replace('{', '[').replace('}', ']')
    # Remove trailing commas before closing bracket
    return converted.replace(',\n]', '\n]').replace(', ]', ']').replace(',]', ']')


def _extract_from_dict_list(items, cleanups):
    """
    If items is a list of dicts with a common key, extract values from that key.
    For example: [{"name": "lion"}, {"name": "tiger"}] -> ["lion", "tiger"]
    Returns modified items list and updates cleanups list.
    """
    if not items or not isinstance(items, list):
        return items

    # Check if all items are dicts
    if not all(isinstance(item, dict) for item in items):
        return items

    # Find common keys across all dicts
    if items:
        common_keys = set(items[0].keys())
        for item in items[1:]:
            common_keys &= set(item.keys())

        # If there's exactly one common key, extract values from it
        if len(common_keys) == 1:
            key = list(common_keys)[0]
            extracted = []
            for item in items:
                value = item[key]
                extracted.append(value)
            cleanups.append("JSON-Dict-List-Key-Extraction")
            return extracted

    return items


def _json_data_to_items(data, cleanups, note_dict_extraction=False, note_nested_flatten=False):
    """Convert parsed JSON data to a flat list of items: array -> as-is, object ->
    its values, scalar -> single-item list. Extracts a common key from dict-list
    items and flattens any nested lists. Mutates cleanups."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = list(data.values())
        if note_dict_extraction:
            cleanups.append("JSON-Dict-Value-Extraction")
    else:
        items = [data]

    # Check if items are dicts with a common key that should be extracted
    items = _extract_from_dict_list(items, cleanups)

    # Flatten any list items
    flattened, had_nested_lists = _flatten_nested_items(items)
    if had_nested_lists and note_nested_flatten:
        cleanups.append("JSON-Nested-List-Flattening")
    return flattened


def _parse_json_direct(content_to_parse, cleanups, quality_issues, unwrap_pairs, repeated_keys_found):
    """Parse content directly as JSON and convert to a flat item list.
    Raises json.JSONDecodeError if content_to_parse isn't valid JSON."""
    data = json.loads(content_to_parse, object_pairs_hook=unwrap_pairs)
    if repeated_keys_found[0]:
        quality_issues.append("repeated-json-keys")
        cleanups.append("JSON-Repeated-Key-Unwinding")
    return _json_data_to_items(data, cleanups, note_dict_extraction=True, note_nested_flatten=True)


def _try_json_set_like_repair(content_to_parse, cleanups, quality_issues):
    """If content looks like a set-like {a, b, c} object, convert and parse it.
    Returns items on success, else None."""
    if not _looks_like_json_set_format(content_to_parse):
        return None
    try:
        converted = _convert_json_set_to_array(content_to_parse)
        data = json.loads(converted)
        cleanups.append("JSON-Set-Format-Conversion")
        return _json_data_to_items(data, cleanups)
    except json.JSONDecodeError:
        return None


def _try_json_python_syntax_repair(content_to_parse, cleanups, quality_issues):
    """If content has single quotes (Python dict/list syntax), retry with them
    converted to double quotes. Returns items on success, else None."""
    if "'" not in content_to_parse or "{" not in content_to_parse:
        return None
    try:
        # This is a heuristic that works for simple Python dicts in JSON
        fixed_content = content_to_parse.replace("'", '"')
        data = json.loads(fixed_content)
        flattened = _json_data_to_items(data, cleanups)
        cleanups.append("JSON-Python-Syntax-Repair")
        return flattened
    except json.JSONDecodeError:
        return None


def _try_json_truncated_repair(content_to_parse, cleanups, quality_issues, unwrap_pairs):
    """If content looks like model output truncated before closing brackets,
    attempt to repair and reparse it. Returns items on success, else None."""
    repaired, was_repaired = _repair_truncated_json(content_to_parse)
    if not was_repaired:
        return None
    try:
        data = json.loads(repaired, object_pairs_hook=unwrap_pairs)
        flattened = _json_data_to_items(data, cleanups, note_dict_extraction=True)
        quality_issues.append("json-truncated")
        cleanups.append("JSON-Truncated-Recovery")
        return flattened
    except json.JSONDecodeError:
        return None


def parse_json(content):
    """Parse JSON file: if array, each element; if object, each value. Flatten any list items.
    Handles multiple JSON formats:
    - Standard arrays: ["item1", "item2"]
    - Standard objects: {"key": "item"}
    - Set-like format: {"item1", "item2"} (converted to array)
    - JSON with single quotes (Python dict syntax)
    - JSON wrapped in triple backticks (markdown code fence format)
    Extracts values from list of dicts with common keys.

    On failure, tries three repairs in turn (set-like format, Python-dict-syntax,
    truncated output) before giving up. Returns (items, cleanups, quality_issues)."""
    cleanups = []
    quality_issues = []

    # Strip triple backticks if present (markdown code fence format)
    content_to_parse = content.strip()
    if content_to_parse.startswith('```') and content_to_parse.endswith('```'):
        # Remove opening backticks and everything up to and including the first newline
        start_idx = content_to_parse.find('\n')
        if start_idx != -1:
            content_to_parse = content_to_parse[start_idx+1:]
        else:
            # No newline, just remove the opening backticks
            content_to_parse = content_to_parse[3:]
        # Remove closing backticks
        content_to_parse = content_to_parse[:-3].strip()
        cleanups.append("Extract-from-Codefence-Markdown")

    # Try to detect and convert set-like format {item1, item2, ...} to array format
    if _looks_like_json_set_format(content_to_parse):
        content_to_parse = _convert_json_set_to_array(content_to_parse)
        cleanups.append("JSON-Set-Format-Conversion")

    # Remove trailing commas before ] or } (invalid JSON but common model output)
    stripped, n_subs = re.subn(r',(\s*[}\]])', r'\1', content_to_parse)
    if n_subs:
        content_to_parse = stripped
        cleanups.append("JSON-Trailing-Comma-Removal")

    # Detect repeated-key JSON objects at any nesting level: {"k":"v1","k":"v2",...}
    # Standard json.loads silently drops duplicates; object_pairs_hook replaces such
    # objects with a flat list of their values so downstream flattening picks them up.
    repeated_keys_found = [False]
    def unwrap_pairs(pairs):
        keys = [k for k, v in pairs]
        if len(keys) > len(set(keys)):
            repeated_keys_found[0] = True
            return [v for k, v in pairs]
        return dict(pairs)

    try:
        flattened = _parse_json_direct(content_to_parse, cleanups, quality_issues, unwrap_pairs, repeated_keys_found)
        return flattened, cleanups, quality_issues
    except json.JSONDecodeError:
        # Try repairs in turn before giving up
        items = _try_json_set_like_repair(content_to_parse, cleanups, quality_issues)
        if items is not None:
            return items, cleanups, quality_issues

        items = _try_json_python_syntax_repair(content_to_parse, cleanups, quality_issues)
        if items is not None:
            return items, cleanups, quality_issues

        repeated_keys_found[0] = False
        items = _try_json_truncated_repair(content_to_parse, cleanups, quality_issues, unwrap_pairs)
        if items is not None:
            return items, cleanups, quality_issues

        quality_issues.append("parse-failed")
        return [], cleanups, quality_issues
