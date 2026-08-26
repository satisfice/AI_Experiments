#!/usr/bin/env python3
"""Single-file processing: parsing, format detection, quality checking, and cleaning."""

import json
import csv
import yaml
import re
from html.parser import HTMLParser
from io import StringIO
from collections import Counter, defaultdict
from pathlib import Path

from config import abbreviate_model_name
from utils import detect_preamble_leak, format_timestamp, is_standard_filename

# Map file extensions to format types
FORMAT_MAP = {
    '.txt': 'text',
    '.txt1': 'numberedText',
    '.json': 'JSON',
    '.yml': 'YAML',
    '.yaml': 'YAML',
    '.html': 'HTML',
    '.csv': 'CSV',
    '.md': 'markdown',
}

# Matches a numbered list prefix: digits followed by . ) : or -
_NUMBERED_LINE_RE = re.compile(r'^\d+[.):\-]')

# Formats whose content may be wrapped in markdown codefences (``` ... ```)
_CODEFENCED_FORMATS = frozenset({'JSON', 'HTML', 'CSV', 'YAML'})


def _non_empty_lines(content_stripped):
    return [l for l in content_stripped.split('\n') if l.strip()]


def _is_codefenced(content_stripped):
    # True when the whole response is wrapped in ``` ... ```
    return content_stripped.startswith('```') and content_stripped.endswith('```')


def _codefence_inner(content_stripped):
    """Return the content between the opening and closing fence lines.

    The opening fence line is e.g. ```json — find its trailing newline (start).
    The closing fence line is ``` — find its leading newline (end).
    Slice [start+1 : end] gives everything in between.
    Returns '' if the fence has no inner newlines (single-line fence)."""
    start = content_stripped.find('\n')   # newline after opening ``` line
    end   = content_stripped.rfind('\n')  # newline before closing ``` line
    if start != -1 and end != -1 and start != end:
        return content_stripped[start + 1:end]
    return ''  # fence is a single line with no inner content


# ── Public entry point ────────────────────────────────────────────────────────

def detect_intended_format(ext):
    """Return the expected format name for the given extension (from FORMAT_MAP)."""
    return FORMAT_MAP.get(ext, 'unknown')


# ── Per-format style detectors ────────────────────────────────────────────────
# Called only for non-codefenced content — codefencing is resolved upstream in
# validate_format before any of these are reached.

def _detect_json_style(content_stripped):
    # JSON style is determined solely by whether the output spans multiple lines
    return "multiple lines" if '\n' in content_stripped else "single lines"


def _detect_html_style(content_stripped):
    # HTML style: multi-line means the model emitted real markup; single line is unusual
    return "multiple lines" if '\n' in content_stripped else "single line"


def _detect_csv_style(content_stripped):
    lines = _non_empty_lines(content_stripped)
    if len(lines) <= 1:
        # Everything on one row — comma-separated columns, or a single value
        return "single row"
    # Multiple lines: commas present → proper CSV rows; no commas → one item per line
    return "multiple rows" if any(',' in l for l in lines) else "one per line"


def _detect_yaml_style(content_stripped):
    lines = _non_empty_lines(content_stripped)
    # YAML list syntax uses "- item" or a bare "-" as a list marker
    if any(l.strip().startswith('- ') or l.strip() == '-' for l in lines):
        return "leading hyphen"
    # No list markers — the model wrote plain text into a YAML file
    return "plain text"


# ── Text format validators ────────────────────────────────────────────────────
# Unlike structured-format detectors, these can return None to signal a mismatch
# between the content's actual style and what the format expects.

def _validate_text_style(content_stripped, expect_numbered):
    """Common implementation for plain-text and numbered-text validation.

    expect_numbered=False: ANY numbered line is a mismatch → returns None.
    expect_numbered=True:  ANY un-numbered line is a mismatch → returns None."""
    lines = _non_empty_lines(content_stripped)
    if not lines:
        # Empty content: trivially matches plain-text; fails numbered-text
        return "plain text" if not expect_numbered else None
    # Partition into numbered and un-numbered lines
    numbered = [l for l in lines if _NUMBERED_LINE_RE.match(l.strip())]
    if expect_numbered:
        # Every line must carry a number prefix
        return "numbered text" if len(numbered) == len(lines) else None
    # No line should carry a number prefix
    return None if numbered else "plain text"


def _validate_text_plain(content_stripped):
    """Plain-text format (.txt): expects no numbered lines."""
    return _validate_text_style(content_stripped, expect_numbered=False)


def _validate_text_numbered(content_stripped):
    """Numbered-text format (.txt1): expects all lines numbered."""
    return _validate_text_style(content_stripped, expect_numbered=True)


# ── Dispatch table and pipeline ───────────────────────────────────────────────

# Maps each intended format to its style validator.
# Structured-format validators (JSON/HTML/CSV/YAML) always return a style string.
# Text validators return None when content doesn't match the expected style.
_FORMAT_VALIDATORS = {
    'JSON':         _detect_json_style,
    'HTML':         _detect_html_style,
    'CSV':          _detect_csv_style,
    'YAML':         _detect_yaml_style,
    'text':         _validate_text_plain,
    'numberedText': _validate_text_numbered,
}


def validate_format(content_stripped, intended_format):
    """Detect the content's style for the intended format.

    Step 1 — look up the validator; if none registered, return 'unknown' immediately.
    Step 2 — for structured formats, check for codefencing before anything else.
             JSON also inspects the inner content to distinguish pretty-printed
             JSON-in-a-fence from compact JSON-in-a-fence.
    Step 3 — delegate to the format-specific validator for non-codefenced content.

    Returns the style string; None if content mismatches the expected format;
    'unknown' if no validator is registered (e.g. markdown)."""
    # Step 1: is this a format we know how to detect?
    validator = _FORMAT_VALIDATORS.get(intended_format)
    if validator is None:
        return "unknown"

    # Step 2: codefence gate — applies only to structured formats, not text
    if intended_format in _CODEFENCED_FORMATS and _is_codefenced(content_stripped):
        if intended_format == 'JSON':
            # JSON codefences can still be pretty-printed (newlines inside) or compact
            return "format and markdown backticks" if '\n' in _codefence_inner(content_stripped) else "markdown backticks"
        # All other structured formats: codefenced is the only distinction needed
        return "markdown backticks"

    # Step 3: content is not codefenced — delegate to the format-specific detector
    return validator(content_stripped)


def detect_actual_format(content_stripped):
    """Fallback called when validate_format returns None (format mismatch).
    Determines what the content actually looks like regardless of what was expected.
    Returns 'numbered text' if any line is numbered, 'plain text' otherwise."""
    lines = _non_empty_lines(content_stripped)
    if not lines:
        return "plain text"
    return "numbered text" if any(_NUMBERED_LINE_RE.match(l.strip()) for l in lines) else "plain text"


def detect_format_style(content, ext):
    """Detect the formatting style of the content based on extension and content structure."""
    content_stripped = content.strip()
    # Determine what style we expect to find, based on the file extension
    intended = detect_intended_format(ext)
    # Attempt to validate that the content matches the expected style
    style = validate_format(content_stripped, intended)
    # None means the content doesn't match — detect what it actually is instead
    return style if style is not None else detect_actual_format(content_stripped)


def parse_txt(content):
    """Parse text file: each line is an item.
    Returns (items, cleanups, quality_issues)."""
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    cleanups = []
    quality_issues = []
    return items, cleanups, quality_issues


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


# ── General Parsing Functions ─────────────────────────────────────────────────
# Format-agnostic helpers that operate on already-separated data. CSV uses them
# now; other format parsers can call them in future passes.

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


# ── CSV Parser Functions ──────────────────────────────────────────────────────

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


def _strip_yaml_directive_marker(content, cleanups):
    """Strip a YAML end-of-directives / document-separator marker (---) found on a
    non-first line, and everything from that line onward, so it doesn't corrupt
    the parse."""
    all_lines = content.strip().split('\n')
    for i, line in enumerate(all_lines):
        if i > 0 and line.strip() == '---':
            cleanups.append("YAML-Directive-Marker-Handling")
            return '\n'.join(all_lines[:i])
    return content


def _is_yaml_plain_text_list(lines):
    """True if content looks like a plain text list: multiple lines, none of which
    start with a YAML list marker (-, *, •) or a numbered prefix."""
    return (
        len(lines) > 1 and
        all(not line.strip().startswith(('-', '*', '•')) and
            not re.match(r'^\d+\.\s', line.strip())
            for line in lines if line.strip())
    )


def _yaml_data_to_items(data, cleanups):
    """Convert parsed YAML data to a list of items: a list (possibly a single
    dict-wrapped inner list), a dict's values, a plain string (possibly containing
    numbered items), or any other scalar."""
    if isinstance(data, list):
        items = data
        # Handle [{key: [item1, item2, ...]}] — model wrapped the list in a named key.
        # Extract the inner list so downstream flattening can normalise it.
        if len(items) == 1 and isinstance(items[0], dict):
            values = list(items[0].values())
            if len(values) == 1 and isinstance(values[0], list):
                items = values[0]
                cleanups.append("YAML-Wrapped-List-Extraction")
        # Use shared flattening (though rarely needed for YAML lists)
        items, had_nested = _flatten_nested_items(items)
        return items

    if isinstance(data, dict):
        cleanups.append("YAML-Dict-Value-Extraction")
        items = list(data.values())
        # Flatten any nested lists in the dict values
        items, had_nested = _flatten_nested_items(items)
        return items

    if isinstance(data, str):
        # YAML parsed as a plain string (likely non-standard YAML with numbered/bulleted lines)
        # Try to parse as text with numbered items (1. item 2. item 3. item, etc.)
        # Look for pattern: digit(s) followed by period and space
        numbered_items = re.findall(r'\d+\.\s+([^0-9]+?)(?=\d+\.\s|$)', data)
        if numbered_items:
            cleanups.append("YAML-Numbered-Item-Extraction")
            return [item.strip() for item in numbered_items if item.strip()]
        return [data] if data else []

    return [data] if data is not None else []


def _try_yaml_marker_fallback(content):
    """Fallback for unparseable YAML: extract lines that start with a YAML list
    marker (-, *, •). Returns items, or [] if none found."""
    items = []
    for line in content.strip().split('\n'):
        stripped = line.strip()
        if stripped and stripped[0] in ('-', '*', '•'):
            # Remove the marker and following whitespace
            item = re.sub(r'^[-*•]\s*', '', stripped).strip()
            items.append(item)
    return items


def parse_yaml(content):
    """Parse YAML: extract items from structure. If single item is a list, flatten it.
    Falls back to text parsing if content looks like plain text list, and to
    marker-based line extraction if content isn't valid YAML at all.
    Returns (items, cleanups, quality_issues)."""
    cleanups = []
    quality_issues = []
    try:
        content = _strip_yaml_directive_marker(content, cleanups)

        # Check if content looks like plain text list (newline-separated, no YAML syntax)
        lines = content.strip().split('\n')
        if _is_yaml_plain_text_list(lines):
            # Plain text list: one item per line
            items = [line.strip() for line in lines if line.strip()]
            cleanups.append("YAML-Plain-Text-Detection")
        else:
            data = yaml.safe_load(content)
            items = _yaml_data_to_items(data, cleanups)

        # If only one item and it's a list, flatten it
        if len(items) == 1 and isinstance(items[0], list):
            items = items[0]
            cleanups.append("YAML-Single-List-Flattening")

        return items, cleanups, quality_issues

    except yaml.YAMLError:
        # YAML parse error - fall back to plain text parsing with YAML list markers
        cleanups.append("YAML-Parse-Error-Fallback")
        try:
            items = _try_yaml_marker_fallback(content)
            if items:
                return items, cleanups, quality_issues
        except Exception:
            pass

        return [], cleanups, quality_issues


# ── HTML Tree-Building Helpers ────────────────────────────────────────────────

# Maps lowercase HTML tag names to their display form used in cleanup key names.
_HTML_TAG_DISPLAY = {
    'html': 'HTML', 'body': 'Body', 'head': 'Head',
    'ul': 'UL', 'ol': 'OL', 'li': 'LI',
    'p': 'P', 'div': 'Div', 'span': 'Span',
    'article': 'Article', 'section': 'Section', 'br': 'BR',
    'b': 'Bold', 'i': 'Italic', 'strong': 'Strong', 'em': 'Emphasis', 'u': 'Underline',
    'pre': 'Pre',
}

def _tag_cleanup_name(tag):
    """Return the cleanup key name for a given HTML tag, e.g. 'li' -> 'Extract-From-LI-Tags'."""
    display = _HTML_TAG_DISPLAY.get(tag.lower(), tag.capitalize())
    return f"Extract-From-{display}-Tags"


class _HtmlNode:
    """Minimal DOM tree node for HTML parsing."""
    __slots__ = ('tag', 'children', 'parent')

    def __init__(self, tag):
        self.tag = tag          # string tag name, or None for virtual root / text nodes
        self.children = []      # list of _HtmlNode or str
        self.parent = None

    def append(self, child):
        """Append a child node or text string, setting parent if it's a node."""
        self.children.append(child)
        if isinstance(child, _HtmlNode):
            child.parent = self

    def iter_tag(self, tag):
        """Depth-first generator of descendant nodes with the given tag name."""
        for child in self.children:
            if isinstance(child, _HtmlNode):
                if child.tag == tag:
                    yield child
                yield from child.iter_tag(tag)

    def ancestor_tags(self):
        """Return list of ancestor tag names from outermost to parent (excludes virtual root)."""
        tags = []
        node = self.parent
        while node is not None and node.tag is not None:
            tags.append(node.tag)
            node = node.parent
        tags.reverse()
        return tags

    def text_content(self, br_as_newline=False):
        """Recursively collect text, optionally replacing <br> children with newline.
        Returns (text, had_br) where had_br is True if any <br> was encountered."""
        parts = []
        had_br = False
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif isinstance(child, _HtmlNode):
                if child.tag == 'br':
                    had_br = True
                    if br_as_newline:
                        parts.append('\n')
                else:
                    sub_text, sub_br = child.text_content(br_as_newline)
                    parts.append(sub_text)
                    if sub_br:
                        had_br = True
        return ''.join(parts), had_br


class _HtmlTreeBuilder(HTMLParser):
    """Builds an _HtmlNode tree from HTML content."""

    _VOID_TAGS = frozenset({
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr',
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode(None)   # virtual root
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _HtmlNode(tag)
        self.stack[-1].append(node)
        if tag not in self._VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        # Pop back to the matching open tag (handles malformed/unclosed HTML)
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return

    def handle_data(self, data):
        self.stack[-1].append(data)


def _parse_html_tree(content):
    """Parse HTML content into an _HtmlNode tree. Returns virtual root node.
    Swallows all exceptions so callers never crash on malformed HTML."""
    builder = _HtmlTreeBuilder()
    try:
        builder.feed(content)
    except Exception:
        pass
    return builder.root


def _find_leaf_tag_nodes(root, tag):
    """Return all nodes with the given tag that have no descendant of the same tag.
    This prevents double-counting from nested same-tag containers (e.g. <div><div>)."""
    results = []
    for node in root.iter_tag(tag):
        # Check if this node contains any descendant with the same tag
        has_nested = any(True for _ in node.iter_tag(tag))
        if not has_nested:
            results.append(node)
    return results


def _items_from_nodes(nodes):
    """Extract text items from a list of _HtmlNode objects.
    For each node: if the text content (after br-splitting) contains newlines,
    splits into multiple items. Otherwise yields a single item.
    Returns (items, had_br, was_line_split)."""
    items = []
    had_br = False
    was_line_split = False
    for node in nodes:
        text, node_had_br = node.text_content(br_as_newline=True)
        if node_had_br:
            had_br = True
        stripped = text.strip()
        if '\n' in stripped:
            # Split by newlines and treat each non-empty line as an item
            lines = [ln.strip() for ln in stripped.split('\n') if ln.strip()]
            items.extend(lines)
            was_line_split = True
        elif stripped:
            items.append(stripped)
    return items, had_br, was_line_split


def _path_tag_cleanups(item_nodes, item_tag):
    """Return an ordered list of Extract-From-X-Tags cleanup keys representing the
    DOM path from root to item_tag.  Uses item_nodes[0]'s ancestor chain as the
    representative path.  Deduplicates while preserving outermost-to-innermost order,
    then appends item_tag at the end."""
    if not item_nodes:
        return [_tag_cleanup_name(item_tag)]
    ancestor_tags = item_nodes[0].ancestor_tags()
    # Deduplicate while preserving order
    seen = set()
    ordered = []
    for t in ancestor_tags + [item_tag]:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return [_tag_cleanup_name(t) for t in ordered]


def _find_nodes_with_unknown_tags(root, known_tags):
    """Depth-first search for nodes whose tag name is not in known_tags."""
    result = []
    for child in root.children:
        if isinstance(child, _HtmlNode) and child.tag is not None:
            if child.tag not in known_tags:
                result.append(child)
            result.extend(_find_nodes_with_unknown_tags(child, known_tags))
    return result


# Tags tried as fallbacks when no <li> tags are found, in priority order.
_HTML_FALLBACK_TAGS = ['p', 'span', 'div', 'article', 'section']

# Structural tags that don't indicate formatted content (allowed in any HTML)
_HTML_STRUCTURAL_TAGS = frozenset(['html', 'body', 'head', 'br', 'hr'])

# Tags that indicate formatted content or containers
_HTML_NON_STRUCTURAL_TAGS = frozenset([
    'li', 'p', 'span', 'div', 'article', 'section', 'ol', 'ul',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'b', 'i', 'strong', 'em', 'u', 's', 'code', 'pre',
])


def _all_tags_in_tree(root):
    """Return set of all tag names in the tree (excluding virtual root and text nodes)."""
    tags = set()
    for child in root.children:
        if isinstance(child, _HtmlNode) and child.tag is not None:
            tags.add(child.tag)
            tags.update(_all_tags_in_tree(child))
    return tags


def _only_has_tag(root, target_tag):
    """Return True if the tree contains target_tag and no other non-structural tags.
    Only structural tags (html, body, head, br, hr) are allowed besides the target."""
    all_tags = _all_tags_in_tree(root)
    target_nodes = list(root.iter_tag(target_tag))
    if not target_nodes:
        return False
    # Check if any non-structural tags exist other than the target
    other_tags = (all_tags & _HTML_NON_STRUCTURAL_TAGS) - {target_tag}
    return len(other_tags) == 0


def _detect_br_tags(tag_nodes):
    """Check if any BR tags are present in the tag nodes.
    Returns cleanup list: ["Remove-BR-Tags"] if BR tags found, else []."""
    for node in tag_nodes:
        _, had_br = node.text_content(br_as_newline=True)
        if had_br:
            return ["Remove-BR-Tags"]
    return []


def _extract_from_html_formatting_tag(tag_nodes, tag):
    """Extract items that are wrapped in the specified formatting tag.
    Returns (items, cleanups) where cleanups refers to the extraction cleanup task only."""
    if not tag_nodes:
        return [], []

    items, _, _ = _items_from_nodes(tag_nodes)
    cleanups = [_tag_cleanup_name(tag)]
    return items, cleanups


def _try_numbered_items_in_tag(raw_items, tag_nodes, tag, had_br, cleanups, quality_issues):
    """If every item in raw_items is a numbered entry (e.g. "<span>1. Lion</span>"),
    strip the numbers and return the items. Returns None if not all are numbered."""
    num_matches = [re.match(r'^\d+\.\s+(.+)$', it) for it in raw_items]
    if not (raw_items and all(num_matches)):
        return None
    items = [m.group(1) for m in num_matches]
    cleanups.extend(_path_tag_cleanups(tag_nodes, tag))
    if had_br:
        cleanups.append("Remove-BR-Tags")
    cleanups.append("HTML-Numbered-Items-In-Tags")
    quality_issues.append("numbered-items-in-tags")
    return items


def _split_comma_separated_items(raw_items, tag):
    """Split inline comma-separated values within items of p/span/div tags.
    Returns (final_items, comma_split_used)."""
    final_items = []
    comma_split_used = False
    for item in raw_items:
        if ',' in item and tag in ['p', 'span', 'div']:
            parts = [p.strip() for p in item.split(',') if p.strip()]
            if len(parts) > 1 and all(len(p) < 100 for p in parts):
                final_items.extend(parts)
                comma_split_used = True
                continue
        final_items.append(item)
    return final_items, comma_split_used


def _try_fallback_tags(root, cleanups, quality_issues):
    """Try each tag in _HTML_FALLBACK_TAGS priority order; return raw items for the
    first tag that yields any (mutating cleanups/quality_issues along the way), or
    [] if none do. Extracted from parse_html's fallback-tag loop."""
    for tag in _HTML_FALLBACK_TAGS:
        tag_nodes = _find_leaf_tag_nodes(root, tag)
        if not tag_nodes:
            continue
        raw_items, had_br, was_line_split = _items_from_nodes(tag_nodes)
        if not raw_items:
            continue

        numbered_items = _try_numbered_items_in_tag(raw_items, tag_nodes, tag, had_br, cleanups, quality_issues)
        if numbered_items is not None:
            return numbered_items

        final_items, comma_split_used = _split_comma_separated_items(raw_items, tag)
        cleanups.extend(_path_tag_cleanups(tag_nodes, tag))
        if had_br:
            cleanups.append("Remove-BR-Tags")
        if comma_split_used:
            quality_issues.append("comma-separated")
        if not was_line_split:
            quality_issues.append("single-span-tag")
        return final_items

    return []


def _try_html_codefence_fallback(content, cleanups, quality_issues):
    """If content is a fenced code block, extract numbered items from inside it.
    If it's fenced but not all-numbered, drop the fence and return the inner content
    to continue parsing as HTML. Returns (items_or_None, content_to_use_next)."""
    if not content.strip().startswith('```'):
        return None, content

    code_block_pattern = r'^```[\w]*\n(.*?)\n```\s*$'
    match = re.search(code_block_pattern, content.strip(), re.DOTALL)
    if not match:
        return None, content

    extracted_content = match.group(1)
    cleanups.append("Extract-from-Codefence-Markdown")
    quality_issues.append("html_no_markup")

    # If inner content is an all-numbered list, return it immediately
    numbered_pattern = r'^\d+\.\s+(.+)$'
    numbered_items = [
        re.match(numbered_pattern, line.strip()).group(1)
        for line in extracted_content.split('\n')
        if re.match(numbered_pattern, line.strip())
    ]
    if numbered_items:
        return numbered_items, content

    # Otherwise fall through with extracted content (drop the fence)
    return None, extracted_content


def _try_formatting_tag_only(root, cleanups, quality_issues):
    """Quality-issue path: if the document contains only one specific formatting
    tag (b/i/em/u/pre), extract items from it. Returns items if successful, else None."""
    for tag, quality_issue in [('b', 'HTML_Only_Bold_Tags'), ('i', 'HTML_Only_Italic_Tags'), ('em', 'HTML_Only_Emphasis_Tags'), ('u', 'HTML_Only_Underline_Tags'), ('pre', 'HTML_Only_Pre_Tags')]:
        if _only_has_tag(root, tag):
            tag_nodes = _find_leaf_tag_nodes(root, tag)
            items, extraction_cleanups = _extract_from_html_formatting_tag(tag_nodes, tag)
            cleanups.extend(extraction_cleanups)
            cleanups.extend(_detect_br_tags(tag_nodes))
            quality_issues.append(quality_issue)
            if items:
                return items
    return None


def _try_li_primary_path(root, cleanups):
    """Primary path: extract items from <li> tags. Returns items (even if empty)
    once any <li> is found, or None if there are no <li> tags at all."""
    li_nodes = _find_leaf_tag_nodes(root, 'li')
    if not li_nodes:
        return None
    items, had_br, _ = _items_from_nodes(li_nodes)
    cleanups.extend(_path_tag_cleanups(li_nodes, 'li'))
    if had_br:
        cleanups.append("Remove-BR-Tags")
    return items


def _try_bare_list_path(root, cleanups, quality_issues):
    """UL/OL with unwrapped text: items directly in the list tag, with no <li>
    children. Returns items if found, else None."""
    for list_tag in ['ul', 'ol']:
        list_nodes = list(root.iter_tag(list_tag))
        if not list_nodes:
            continue
        # Only process nodes that have bare text children but no <li> children
        bare_nodes = [
            n for n in list_nodes
            if any(isinstance(c, str) and c.strip() for c in n.children)
            and not any(isinstance(c, _HtmlNode) and c.tag == 'li' for c in n.children)
        ]
        if not bare_nodes:
            continue
        raw_items, had_br, _ = _items_from_nodes(bare_nodes)
        if raw_items:
            cleanups.extend(_path_tag_cleanups(bare_nodes, list_tag))
            if had_br:
                cleanups.append("Remove-BR-Tags")
            quality_issues.append("items-not-in-li")
            return raw_items
    return None


def _try_invalid_tag_fallback(root, cleanups, quality_issues):
    """Extract text content from unrecognized/invalid tags, or the tag name
    itself for hollow tags (e.g. <tiger>). Returns items (possibly empty)."""
    known_tags = set(_HTML_TAG_DISPLAY.keys()) | _HtmlTreeBuilder._VOID_TAGS
    invalid_nodes = _find_nodes_with_unknown_tags(root, known_tags)
    if not invalid_nodes:
        return []

    items = []
    has_hollow_tags = False
    for node in invalid_nodes:
        text, _ = node.text_content()
        text = text.strip()
        if text:
            items.append(text)
        elif node.tag:
            # Hollow tag: tag name IS the content (e.g. <tiger>)
            items.append(node.tag)
            has_hollow_tags = True
    if items:
        cleanups.append("Extract-From-Invalid-HTML-Tags")
        quality_issues.append("invalid-html-tags")
        if has_hollow_tags:
            quality_issues.append("pointy-bracket-wrapping")
    return items


def _try_plain_text_fallback(content, cleanups, quality_issues):
    """No HTML structure detected: treat each non-blank line as an item, stripping
    any stray tags and detecting numbered lists. Returns items (possibly empty)."""
    plain_items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    if not plain_items:
        return []

    # Strip HTML tags from each line before classification. Files that mix an
    # HTML-tagged title line (e.g. <u>Animal Names</u>) with plain numbered items
    # need the tags removed first so the numbered-list check works correctly.
    clean_lines = [re.sub(r'<[^>]+>', '', line).strip() for line in plain_items]
    clean_lines = [l for l in clean_lines if l]

    numbered_pattern = r'^\d+\.\s+(.+)$'
    num_matches = [re.match(numbered_pattern, l) for l in clean_lines]
    numbered_items = [m.group(1) for m in num_matches if m]

    if numbered_items and all(num_matches):
        # Every line is a numbered item — clean numbered list in plain text.
        cleanups.append("HTML-Numbered-List-Stripping")
        quality_issues.append("html_no_markup")
        return numbered_items

    if numbered_items:
        # Mix of numbered items and non-numbered lines (e.g. a title).
        # If the non-numbered lines are all short (≤5 words), treat them as
        # header/title noise and extract only the numbered items.
        non_numbered = [clean_lines[i] for i, m in enumerate(num_matches) if not m]
        if all(len(l.split()) <= 5 for l in non_numbered):
            cleanups.append("HTML-Numbered-List-Stripping")
            quality_issues.append("html_no_markup")
            return numbered_items
        cleanups.append("HTML-PlainText-Fallback")
        quality_issues.append("html_no_markup")
        return clean_lines

    cleanups.append("HTML-PlainText-Fallback")
    quality_issues.append("html_no_markup")
    return clean_lines


def parse_html(content):
    """Parse HTML using a DOM tree. Tries each strategy in turn: codefence fallback,
    formatting-tag-only quality path, <li> primary path, bare UL/OL text, fallback
    tags in priority order (<p>/<span>/<div>/<article>/<section>), invalid-tag
    extraction, and finally plain-text. Returns (items, cleanups, quality_issues)."""
    cleanups = []
    quality_issues = []

    # Try codefence extraction first (takes content, may modify it)
    codefence_items, content = _try_html_codefence_fallback(content, cleanups, quality_issues)
    if codefence_items is not None:
        return codefence_items, cleanups, quality_issues

    root = _parse_html_tree(content)

    # Define strategies that operate on parsed root. Each returns items (may be None or empty).
    # Try each in order; first one that returns non-empty items succeeds.
    strategies = [
        _try_formatting_tag_only,
        _try_li_primary_path,
        _try_bare_list_path,
        _try_fallback_tags,
        _try_invalid_tag_fallback,
    ]

    for strategy in strategies:
        # Most strategies take (root, cleanups, quality_issues); _try_li_primary_path only takes (root, cleanups)
        if strategy == _try_li_primary_path:
            items = strategy(root, cleanups)
        else:
            items = strategy(root, cleanups, quality_issues)
        if items:
            return items, cleanups, quality_issues

    # Final fallback: try as plain text
    items = _try_plain_text_fallback(content, cleanups, quality_issues)
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


PARSERS = {
    '.txt': parse_txt,
    '.json': parse_json,
    '.csv': parse_csv,
    '.md': parse_md,
    '.yml': parse_yaml,
    '.yaml': parse_yaml,
    '.html': parse_html,
    '.txt1': parse_txt1,
}


def extract_code_block(content):
    """
    Extract content from markdown code blocks (```...```).
    Looks for code blocks with optional language specifier (```json, ```yaml, etc).
    Returns (extracted_content, had_codeblock, cleanups) tuple.
    If code blocks found, returns the content inside them.
    If no code blocks found, returns original content.
    Cleanup emitted is always "Extract-from-Codefence-Markdown" (language specifier irrelevant).
    """
    cleanups = []
    # Capture language specifier and block content separately
    code_block_pattern = r'```([\w]*)\n(.*?)\n```'
    matches = re.findall(code_block_pattern, content, re.DOTALL)

    if matches:
        # Found code blocks - concatenate content from all blocks
        extracted = '\n'.join(m[1] for m in matches)
        cleanups.append('Extract-from-Codefence-Markdown')
        return extracted, True, cleanups
    else:
        # No code blocks found
        return content, False, cleanups


def parse_cleanup_keys(cleanup_keys):
    """Convert a cleanup task name list to a structured cleanup dict.

    'Rule: N items' → {Rule: N}
    'Rule: N items (chars)' → {Rule (chars): N}
    Other strings    → {string: True}
    """
    cleanup = {}
    for cleanup_key in cleanup_keys:
        m = re.match(r'^(.+?):\s+(\d+)\s+items?(?:\s+(\([^)]+\)))?$', cleanup_key)
        if m:
            rule_name = m.group(1)
            count = int(m.group(2))
            chars_annotation = m.group(3)  # e.g. "(<)" or None
            key = f"{rule_name} {chars_annotation}" if chars_annotation else rule_name
            cleanup[key] = count
        else:
            cleanup[cleanup_key] = True
    return cleanup


def reorder_metadata(metadata):
    """Reorder metadata keys: time, experiment, prompt, formatHardness, model, temperature, format,
    formatStyle, itemIssues, formatIssues, iteration, codeblock, cleanup, then others.
    Note: itemIssues, formatIssues, codeblock, and cleanup are optional."""
    key_order = ["time", "experiment", "prompt", "formatHardness", "model", "temperature", "format",
                 "formatStyle", "responseComplete", "incompleteReason", "itemIssues", "formatIssues",
                 "iteration", "codeblock", "cleanup"]
    ordered = {}

    # Add keys in specified order (if they exist)
    for key in key_order:
        if key in metadata:
            ordered[key] = metadata[key]

    # Add remaining keys in alphabetical order
    remaining_keys = sorted(set(metadata.keys()) - set(key_order))
    for key in remaining_keys:
        ordered[key] = metadata[key]

    return ordered


def trim_items(items):
    """Trim leading and trailing spaces from all items, converting to string if needed."""
    trimmed = []
    for item in items:
        # Convert to string and trim
        item_str = str(item).strip() if item is not None else ""
        if item_str:
            trimmed.append(item_str)
    return trimmed


def is_alphabetical_order(items):
    """Check if items are in alphabetical order (case-insensitive)."""
    if not items or len(items) < 2:
        return True

    lowercase_items = [str(item).lower() for item in items]
    return lowercase_items == sorted(lowercase_items)


def is_standard_filename(filename):
    """
    Check if filename follows standard naming convention.
    Format: YYYYMMDDHHMMSS-EXPERIMENT-PROMPT-HARDNESS-MODEL-TEMP-ITERATION.EXT
    Requires 14-digit timestamp and 7+ parts (hardness code is 'fs' or 'fh').
    """
    name_without_ext = Path(filename).stem
    parts = name_without_ext.split('-')

    # Must have 14-digit timestamp
    if not parts[0].isdigit() or len(parts[0]) != 14:
        return False

    # Need at least 7 parts (timestamp, experiment, prompt, hardness, model, temp, iteration)
    if len(parts) < 7:
        return False

    # Check iteration is 2 digits
    if not parts[-1].isdigit() or len(parts[-1]) != 2:
        return False

    # Check temperature starts with 't'
    if not parts[-2].startswith('t'):
        return False

    # Check hardness code is 'fs' or 'fh'
    if parts[3] not in ('fs', 'fh'):
        return False

    return True


def format_timestamp(timestamp_str):
    """
    Convert timestamp to YYYY-MM-DD HH:MM:SS format.
    Supports both old 12-digit (YYYYMMDDhhmm) and new 14-digit (YYYYMMDDhhmmss) formats.
    """
    if not timestamp_str.isdigit():
        return timestamp_str

    if len(timestamp_str) == 12:
        # Old format: YYYYMMDDhhmm -> YYYY-MM-DD HH:MM:SS
        return f"{timestamp_str[0:4]}-{timestamp_str[4:6]}-{timestamp_str[6:8]} {timestamp_str[8:10]}:{timestamp_str[10:12]}:00"
    elif len(timestamp_str) == 14:
        # New format: YYYYMMDDhhmmss -> YYYY-MM-DD HH:MM:SS
        return f"{timestamp_str[0:4]}-{timestamp_str[4:6]}-{timestamp_str[6:8]} {timestamp_str[8:10]}:{timestamp_str[10:12]}:{timestamp_str[12:14]}"
    else:
        return timestamp_str


def parse_filename_metadata(filename):
    """
    Extract metadata from filename.
    Format: YYYYMMDDHHMMSS-EXPERIMENT-PROMPT-HARDNESS-MODEL-TEMP-ITERATION.EXT
    Where HARDNESS is 'fs' (soft) or 'fh' (hard).
    Returns dict with time, experiment, prompt, formatHardness, model, temperature, and iteration.
    """
    # Remove extension
    name_without_ext = Path(filename).stem

    # Split by hyphen
    parts = name_without_ext.split('-')

    if len(parts) < 7:
        return {}

    # First part is timestamp
    timestamp_raw = parts[0]

    # Verify 14-digit timestamp
    if not timestamp_raw.isdigit() or len(timestamp_raw) != 14:
        return {}

    timestamp = format_timestamp(timestamp_raw)

    # Extract fields by position
    experiment = parts[1]
    prompt = parts[2]
    hardness_code = parts[3]
    # Model may contain hyphens, so join parts[4:-2]
    model = '-'.join(parts[4:-2])
    temp_part = parts[-2]
    iteration = parts[-1]

    # Verify temperature part starts with 't'
    if not temp_part.startswith('t'):
        return {"time": timestamp}

    # Parse temperature
    temperature = None
    temp_str = temp_part[1:]  # Remove 't' prefix
    if temp_str != 'xx':
        try:
            temp_int = int(temp_str)
            temperature = temp_int / 10.0
        except ValueError:
            pass

    # Convert hardness code to format hardness value
    hardness_map = {'fs': 'soft', 'fh': 'hard'}
    format_hardness = hardness_map.get(hardness_code)

    metadata = {
        "time": timestamp,
        "experiment": experiment,
        "prompt": prompt,
        "model": model,
        "temperature": temperature,
        "iteration": int(iteration) if iteration.isdigit() else None,
    }

    # Add format hardness if valid
    if format_hardness is not None:
        metadata["formatHardness"] = format_hardness

    return metadata


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


# Shared punctuation character class used by both detection and cleanup functions.
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


# ── General Item Cleanup Functions ───────────────────────────────────────────
# Each function takes a list of strings, applies one transformation, and returns
# (modified_items, cleanup_str_or_none). A cleanup string is emitted only when at
# least one item was actually changed. These apply to all formats; CSV uses them
# as part of its pipeline established in this pass.

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


def clean_format_specific(items, ext):
    """
    Apply format-specific cleanup before quality checks.
    - Strips stray HTML tags and blockquote markers from all formats (both flagged as quality issues).
    - Strips leading bullet/number markers from CSV, Markdown, and plain text.
    Returns (cleaned_items, cleanups_list, quality_issues_list).
    """
    cleanups = []
    quality_issues = []

    # Pass 1: strip stray HTML tags and blockquote markers from every item regardless of format.
    # Residual HTML tags or blockquote '>' markers in structured output indicate model leakage.
    html_tag_count = 0
    blockquote_count = 0
    result = []
    for item in items:
        no_tags = re.sub(r'</?[a-zA-Z][^>]*>?', '', item)
        if no_tags != item:
            html_tag_count += 1
        no_bq = re.sub(r'^>+|>+$', '', no_tags).strip()
        if no_bq != no_tags:
            blockquote_count += 1
        result.append(no_bq)

    if html_tag_count:
        cleanups.append("HTML-Stray-Tag-Cleanup")
        quality_issues.append("stray-html-markup")
    if blockquote_count:
        cleanups.append("Blockquote-Marker-Cleanup")
        quality_issues.append("blockquote-markup")

    # Pass 2: strip leading bullet/number markers from formats where they serve as list structure.
    if ext in ('.csv', '.md', '.txt'):
        result, marker_cleanups = csv_strip_leading_markers(result)
        cleanups.extend(marker_cleanups)

    return result, cleanups, quality_issues


def _check_item_quality_issues(cleaned_items, max_item_length):
    """Check for quality issues in cleaned items. Returns (quality_issues, preamble_set, processing_quality_issues)."""
    quality_issues = {}
    preamble_set = set()
    processing_quality_issues = []

    item_quality_checks = [
        ("leading_punctuation", detect_leading_punctuation),
        ("trailing_punctuation", detect_trailing_punctuation),
        ("internal_punctuation", detect_internal_punctuation),
        ("exceeds_max_length", lambda it: len(it) > max_item_length),
        ("markup_artifact", detect_markup_artifact),
        ("repeated_chars", detect_repeated_chars),
    ]
    for item in cleaned_items:
        for key, detector in item_quality_checks:
            if detector(item) and key not in quality_issues:
                quality_issues[key] = item

        if detect_preamble_leak(item):
            preamble_set.add(item)
            if "preamble_leak" not in quality_issues:
                quality_issues["preamble_leak"] = item

    if any(ord(c) > 127 for item in cleaned_items for c in item):
        processing_quality_issues.append("non-western-characters")

    return quality_issues, preamble_set, processing_quality_issues


def _apply_cleanup_pipeline(items):
    """Apply cleanup pipeline to items. Returns (processed_items, processing_cleanups)."""
    cleanup_pipeline = [
        clean_strip_leading_format,
        clean_strip_number_word_prefix,
        clean_remove_parenthetical,
        clean_strip_trailing_punct,
        clean_strip_doubled_punct,
        clean_strip_leading_hyphens,
    ]

    processed = items
    processing_cleanups = []
    for step in cleanup_pipeline:
        processed, cleanup = step(processed)
        if cleanup:
            processing_cleanups.append(cleanup)

    if any(any(c.isupper() for c in item) for item in processed):
        processed, cleanup = clean_lowercase(processed)
        if cleanup:
            processing_cleanups.append(cleanup)

    return processed, processing_cleanups


def _detect_repeated_sequence(items):
    """Detect if items have 3+ consecutive identical entries. Returns issue_key or None."""
    for i in range(len(items) - 2):
        if items[i] == items[i + 1] == items[i + 2]:
            return items[i]
    return None


def process_and_track(items, ext, max_item_length=25):
    """Process items: trim → format-clean → check quality → apply cleanup pipeline."""
    if not items:
        return items, {"consistentCase": True, "case": "lower"}, {"itemCount": 0, "alphabeticalOrder": True}

    processing = {"consistentCase": True, "case": "lower"}
    processing_cleanups = []

    trimmed = trim_items(items)
    cleaned_items, format_cleanups, format_quality_issues = clean_format_specific(trimmed, ext)
    if format_cleanups:
        processing_cleanups.extend(format_cleanups)

    alphabetical = is_alphabetical_order(trimmed)
    case_type, consistent_case = detect_case(trimmed)
    processing["case"] = case_type
    processing["consistentCase"] = consistent_case

    quality_issues, preamble_set, processing_quality_issues = _check_item_quality_issues(cleaned_items, max_item_length)
    if format_quality_issues:
        processing_quality_issues.extend(format_quality_issues)

    filtered = [item for item in cleaned_items if item not in preamble_set]
    processed, cleanup_results = _apply_cleanup_pipeline(filtered)
    processing_cleanups.extend(cleanup_results)

    repeated = _detect_repeated_sequence(processed)
    if repeated:
        quality_issues["repeated_sequence"] = repeated

    metadata = {
        "itemCount": len(processed),
        "alphabeticalOrder": alphabetical
    }
    if quality_issues:
        metadata["itemIssues"] = quality_issues
    if processing_cleanups:
        metadata["processingCleanups"] = processing_cleanups
    if processing_quality_issues:
        metadata["processingQualityIssues"] = processing_quality_issues

    return processed, processing, metadata

