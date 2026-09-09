#!/usr/bin/env python3
"""YAML parsing: extract items from YAML-formatted model output, with fallbacks
for plain-text-list content and marker-based recovery from invalid YAML."""

import re

import yaml

from parsing_common import _flatten_nested_items


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
