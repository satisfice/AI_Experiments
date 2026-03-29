#!/usr/bin/env python3

import json
import csv
import yaml
import re
import sys
import click
from html.parser import HTMLParser
from pathlib import Path
from collections import defaultdict, Counter
from io import StringIO
from fnmatch import fnmatch
from config import abbreviate_model_name
from utils import format_error, print_error

RESULTS_DIR = Path("results")
RESULTS_FILE = RESULTS_DIR / "results.json"
QUALITY_FILE = RESULTS_DIR / "quality.json"
UNIQUE_ITEMS_FILE = RESULTS_DIR / "unique_items.txt"
UNIQUE_SOURCE_ITEMS_FILE = RESULTS_DIR / "unique_source_items.txt"
SKIP_EXTENSIONS = {".xlsx", ".log"}
SKIP_PATTERNS = {"results.json", "quality.json", "unique_items.txt", "unique_source_items.txt", "spreadsheet.csv"}

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


def detect_format_style(content, ext):
    """Detect the formatting style of the content based on extension and content structure."""
    content_stripped = content.strip()

    if ext == '.json':
        has_backticks = content_stripped.startswith('```') and content_stripped.endswith('```')
        # Check for newlines inside the backticks (if present) to detect format style
        inner_content = content_stripped
        if has_backticks:
            start = content_stripped.find('\n')
            end = content_stripped.rfind('\n')
            if start != -1 and end != -1 and start != end:
                inner_content = content_stripped[start:end]
        has_newlines = '\n' in inner_content

        if has_backticks and has_newlines:
            return "format and markdown backticks"
        elif has_backticks:
            return "markdown backticks"
        elif has_newlines:
            return "multiple lines"
        else:
            return "single lines"

    elif ext == '.html':
        has_backticks = content_stripped.startswith('```') and content_stripped.endswith('```')
        has_newlines = '\n' in content_stripped

        if has_backticks:
            return "markdown backticks"
        elif has_newlines:
            return "multiple lines"
        else:
            return "single line"

    elif ext == '.csv':
        has_backticks = content_stripped.startswith('```') and content_stripped.endswith('```')
        if has_backticks:
            return "markdown backticks"

        non_empty_lines = [l for l in content_stripped.split('\n') if l.strip()]
        if len(non_empty_lines) <= 1:
            return "single row"
        # Multiple lines: check whether any line contains commas
        has_commas = any(',' in line for line in non_empty_lines)
        if has_commas:
            return "multiple rows"
        else:
            return "one per line"

    elif ext == '.yml':
        has_backticks = content_stripped.startswith('```') and content_stripped.endswith('```')
        if has_backticks:
            return "markdown backticks"

        non_empty_lines = [l for l in content_stripped.split('\n') if l.strip()]
        # "leading hyphen" if any line uses YAML list syntax (- item)
        if any(l.strip().startswith('- ') or l.strip() == '-' for l in non_empty_lines):
            return "leading hyphen"
        return "plain text"

    elif ext in ('.txt', '.txt1'):
        non_empty_lines = [l for l in content_stripped.split('\n') if l.strip()]
        if not non_empty_lines:
            return "plain text"
        # A line is "numbered" if it starts with digits followed by a period, parenthesis, colon, or dash
        is_numbered = lambda l: bool(re.match(r'^\d+[.):\-]', l.strip()))
        if ext == '.txt':
            # Plain text is the expected style; any numbered line makes it "numbered text"
            return "numbered text" if any(is_numbered(l) for l in non_empty_lines) else "plain text"
        else:  # .txt1
            # Numbered text is the expected style; any un-numbered line makes it "plain text"
            return "plain text" if any(not is_numbered(l) for l in non_empty_lines) else "numbered text"

    else:
        return "unknown"


def parse_txt(content):
    """Parse text file: each line is an item.
    Returns (items, cleanups)."""
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    cleanups = []
    return items, cleanups


def parse_json(content):
    """Parse JSON file: if array, each element; if object, each value. Flatten any list items.
    Handles multiple JSON formats:
    - Standard arrays: ["item1", "item2"]
    - Standard objects: {"key": "item"}
    - Set-like format: {"item1", "item2"} (converted to array)
    - JSON with single quotes (Python dict syntax)
    - JSON wrapped in triple backticks (markdown code fence format)
    Extracts values from list of dicts with common keys.
    Returns (items, cleanups)."""
    cleanups = []

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
        cleanups.append("Extract-from-JSON-Codefence-Markdown")

    # Try to detect and convert set-like format {item1, item2, ...} to array format
    # Set-like format has { } with strings separated by commas, but no colons (no key-value pairs)
    if content_to_parse.startswith('{') and content_to_parse.rstrip().endswith('}'):
        # Check if this looks like a set-like format (no colons outside of strings)
        # Count colons not in quotes
        in_quotes = False
        colon_count = 0
        for char in content_to_parse:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ':' and not in_quotes:
                colon_count += 1

        if colon_count == 0:
            # Looks like a set-like format - convert to array format
            converted = content_to_parse.replace('{', '[').replace('}', ']')
            # Remove trailing commas before closing bracket
            converted = converted.replace(',\n]', '\n]').replace(', ]', ']').replace(',]', ']')
            content_to_parse = converted
            cleanups.append("JSON-Set-Format-Conversion")

    # Detect repeated-key JSON objects: {"key":"v1","key":"v2",...}
    # Standard json.loads silently drops duplicates; use object_pairs_hook to preserve them.
    if content_to_parse.startswith('{'):
        _pairs_by_level = []
        try:
            json.loads(content_to_parse, object_pairs_hook=lambda p: _pairs_by_level.append(p) or dict(p))
        except json.JSONDecodeError:
            pass
        else:
            if _pairs_by_level:
                top_pairs = _pairs_by_level[-1]   # last call = top-level object
                top_keys = [k for k, v in top_pairs]
                if len(top_keys) > len(set(top_keys)):
                    items = [v for k, v in top_pairs]
                    # Flatten any list values (consistent with rest of parse_json)
                    flattened = []
                    for item in items:
                        if isinstance(item, list):
                            flattened.extend(item)
                        else:
                            flattened.append(item)
                    cleanups.append("QUALITY: Repeated JSON object keys (same key used for multiple values)")
                    return flattened, cleanups

    try:
        data = json.loads(content_to_parse)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = list(data.values())
            cleanups.append("JSON-Dict-Value-Extraction")
        else:
            items = [data]

        # Check if items are dicts with a common key that should be extracted
        items = _extract_from_dict_list(items, cleanups)

        # Flatten any list items
        flattened = []
        had_nested_lists = False
        for item in items:
            if isinstance(item, list):
                had_nested_lists = True
                flattened.extend(item)
            else:
                flattened.append(item)

        if had_nested_lists:
            cleanups.append("JSON-Nested-List-Flattening")

        return flattened, cleanups

    except json.JSONDecodeError as e:
        # Try other conversions before giving up
        fixed_content = None

        # First, try to fix set-like format if not already converted
        if content_to_parse.startswith('{') and content_to_parse.rstrip().endswith('}'):
            # Check if no colons (set-like format)
            in_quotes = False
            colon_count = 0
            for char in content_to_parse:
                if char == '"':
                    in_quotes = not in_quotes
                elif char == ':' and not in_quotes:
                    colon_count += 1

            if colon_count == 0:
                try:
                    converted = content_to_parse.replace('{', '[').replace('}', ']')
                    converted = converted.replace(',\n]', '\n]').replace(', ]', ']').replace(',]', ']')
                    data = json.loads(converted)
                    cleanups.append("JSON-Set-Format-Conversion")
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = list(data.values())
                    else:
                        items = [data]
                    items = _extract_from_dict_list(items, cleanups)
                    flattened = []
                    for item in items:
                        if isinstance(item, list):
                            flattened.extend(item)
                        else:
                            flattened.append(item)
                    return flattened, cleanups
                except json.JSONDecodeError:
                    pass  # Try next method

        # Try to fix non-standard JSON (Python dict syntax) (single quotes)
        if "'" in content_to_parse and "{" in content_to_parse:
            try:
                # Replace single quotes with double quotes for dict/list syntax
                # This is a heuristic that works for simple Python dicts in JSON
                fixed_content = content_to_parse.replace("'", '"')
                data = json.loads(fixed_content)

                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = list(data.values())
                else:
                    items = [data]

                # Check if items are dicts with a common key that should be extracted
                items = _extract_from_dict_list(items, cleanups)

                # Flatten any list items
                flattened = []
                for item in items:
                    if isinstance(item, list):
                        flattened.extend(item)
                    else:
                        flattened.append(item)

                cleanups.append("JSON-Python-Syntax-Repair")
                return flattened, cleanups
            except json.JSONDecodeError:
                # If the fix didn't work, return empty list with error note
                cleanups.append("QUALITY: Parse-Failed")
                return [], cleanups
        else:
            cleanups.append("QUALITY: Parse-Failed")
            return [], cleanups


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
    Returns (items, cleanups)."""
    cleanups = []
    try:
        items, cleanup = csv_parse_rows(content)
        if cleanup:
            cleanups.append(cleanup)

        items, cleanup = clean_strip_quotes(items)
        if cleanup:
            cleanups.append(cleanup)

        return items, cleanups

    except Exception as e:
        cleanups.append("QUALITY: Parse-Failed")
        return [], cleanups


def parse_md(content):
    """Parse Markdown file: each line is an item, with markdown bullets and headers removed.
    Returns (items, cleanups) where cleanups is a list of cleanup operations performed."""
    items = []
    cleanups = []
    header_count = 0
    bullet_count = 0

    for line in content.split('\n'):
        if line.strip():
            # Skip markdown headers (lines starting with #)
            if line.lstrip().startswith('#'):
                header_count += 1
                continue
            # Remove leading markdown bullets (*, -, +) and following whitespace
            original = line
            cleaned = re.sub(r'^[\s*\-+]+', '', line).rstrip('\n\r').strip()
            if cleaned:
                if cleaned != original.strip():
                    bullet_count += 1
                items.append(cleaned)

    if header_count > 0:
        cleanups.append("MD-Header-Removal")
    if bullet_count > 0:
        cleanups.append("Bullet-Removal")

    return items, cleanups


def parse_yaml(content):
    """Parse YAML: extract items from structure. If single item is a list, flatten it.
    Falls back to text parsing if content looks like plain text list.
    Returns (items, cleanups)."""
    cleanups = []
    try:
        # Check for a YAML end-of-directives / document-separator marker (---) on a non-first
        # line; strip everything from that line onward so it doesn't corrupt the parse.
        all_lines = content.strip().split('\n')
        for i, line in enumerate(all_lines):
            if i > 0 and line.strip() == '---':
                cleanups.append("YAML-Directive-Marker-Handling")
                content = '\n'.join(all_lines[:i])
                break

        # Check if content looks like plain text list (newline-separated, no YAML syntax)
        lines = content.strip().split('\n')
        # If content has multiple lines AND none start with YAML list markers or numbers+period,
        # treat it as a plain text list
        is_plain_text_list = (
            len(lines) > 1 and
            all(not line.strip().startswith(('-', '*', '•')) and
                not re.match(r'^\d+\.\s', line.strip())
                for line in lines if line.strip())
        )

        if is_plain_text_list:
            # Plain text list: one item per line
            items = [line.strip() for line in lines if line.strip()]
            cleanups.append("YAML-Plain-Text-Detection")
        else:
            # Parse as YAML
            data = yaml.safe_load(content)
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = list(data.values())
                cleanups.append("YAML-Dict-Value-Extraction")
            elif isinstance(data, str):
                # YAML parsed as a plain string (likely non-standard YAML with numbered/bulleted lines)
                # Try to parse as text with numbered items (1. item 2. item 3. item, etc.)
                # Look for pattern: digit(s) followed by period and space
                numbered_items = re.findall(r'\d+\.\s+([^0-9]+?)(?=\d+\.\s|$)', data)
                if numbered_items:
                    # Found numbered items - use them, stripping whitespace
                    items = [item.strip() for item in numbered_items if item.strip()]
                    cleanups.append("YAML-Numbered-Item-Extraction")
                else:
                    # No numbered items found, treat as single item
                    items = [data] if data else []
            else:
                items = [data] if data is not None else []

        # If only one item and it's a list, flatten it
        if len(items) == 1 and isinstance(items[0], list):
            items = items[0]
            cleanups.append("YAML-Single-List-Flattening")

        return items, cleanups

    except yaml.YAMLError as e:
        # YAML parse error - fall back to plain text parsing with YAML list markers
        cleanups.append("YAML-Parse-Error-Fallback")

        # Try to extract items with YAML list markers (-, *, •) as fallback
        try:
            lines = content.strip().split('\n')
            items = []
            for line in lines:
                stripped = line.strip()
                if stripped and stripped[0] in ('-', '*', '•'):
                    # Remove the marker and following whitespace
                    item = re.sub(r'^[-*•]\s*', '', stripped).strip()
                    items.append(item)

            if items:
                return items, cleanups
        except Exception:
            pass

        return [], cleanups


# ── HTML Tree-Building Helpers ────────────────────────────────────────────────

# Maps lowercase HTML tag names to their display form used in cleanup key names.
_HTML_TAG_DISPLAY = {
    'html': 'HTML', 'body': 'Body', 'head': 'Head',
    'ul': 'UL', 'ol': 'OL', 'li': 'LI',
    'p': 'P', 'div': 'Div', 'span': 'Span',
    'article': 'Article', 'section': 'Section', 'br': 'BR',
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


def parse_html(content):
    """Parse HTML using a DOM tree. Extracts items from <li> tags (primary) or falls back
    to <p>/<span>/<div>/<article>/<section>. Each tag in the ancestor path emits its own
    cleanup key. Returns (items, cleanups)."""
    cleanups = []
    items = []

    # ── Codefence fallback (unchanged logic) ────────────────────────────────
    if content.strip().startswith('```'):
        code_block_pattern = r'^```[\w]*\n(.*?)\n```\s*$'
        match = re.search(code_block_pattern, content.strip(), re.DOTALL)
        if match:
            extracted_content = match.group(1)
            cleanups.append("Extract-from-HTML-Codefence-Markdown")
            cleanups.append("QUALITY: Invalid HTML format (content wrapped in markdown code fence instead of proper HTML markup)")

            # If inner content is an all-numbered list, return it immediately
            numbered_pattern = r'^\d+\.\s+(.+)$'
            numbered_items = [
                re.match(numbered_pattern, line.strip()).group(1)
                for line in extracted_content.split('\n')
                if re.match(numbered_pattern, line.strip())
            ]
            if numbered_items:
                items = numbered_items
                return items, cleanups

            # Otherwise fall through with extracted content (drop the fence)
            content = extracted_content

    # ── Build DOM tree ───────────────────────────────────────────────────────
    root = _parse_html_tree(content)

    # ── Primary path: <li> tags ──────────────────────────────────────────────
    li_nodes = _find_leaf_tag_nodes(root, 'li')
    if li_nodes:
        items, had_br, _ = _items_from_nodes(li_nodes)
        cleanups.extend(_path_tag_cleanups(li_nodes, 'li'))
        if had_br:
            cleanups.append("Remove-BR-Tags")
        return items, cleanups

    # ── Fallback path: try each tag in priority order ────────────────────────
    for tag in _HTML_FALLBACK_TAGS:
        tag_nodes = _find_leaf_tag_nodes(root, tag)
        if not tag_nodes:
            continue
        raw_items, had_br, was_line_split = _items_from_nodes(tag_nodes)
        if not raw_items:
            continue

        # Check if every item is a numbered entry (e.g. "<span>1. Lion</span>")
        num_matches = [re.match(r'^\d+\.\s+(.+)$', it) for it in raw_items]
        if raw_items and all(num_matches):
            items = [m.group(1) for m in num_matches]
            cleanups.extend(_path_tag_cleanups(tag_nodes, tag))
            if had_br:
                cleanups.append("Remove-BR-Tags")
            cleanups.append("HTML-Numbered-Items-In-Tags")
            cleanups.append("QUALITY: Numbered items in separate tags format (each item in its own <span>/<p> tag with number prefix instead of proper <li> list)")
            break

        # Per-item comma-split check for inline comma-separated values
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

        items = final_items
        cleanups.extend(_path_tag_cleanups(tag_nodes, tag))
        if had_br:
            cleanups.append("Remove-BR-Tags")
        if comma_split_used:
            cleanups.append(f"QUALITY: Comma-separated items in single <{tag}> tag (items should be in separate tags or list markers)")
        if not was_line_split:
            cleanups.append("QUALITY: Single-span tag format (items in separate <p>/<span> tags instead of <li> list)")
        break

    # ── Invalid tag fallback: tag names rendered as items ───────────────────
    if not items:
        known_tags = set(_HTML_TAG_DISPLAY.keys()) | _HtmlTreeBuilder._VOID_TAGS
        invalid_nodes = _find_nodes_with_unknown_tags(root, known_tags)
        if invalid_nodes:
            items = [node.tag for node in invalid_nodes]
            cleanups.append("Extract-From-Invalid-HTML-Tags")
            cleanups.append("QUALITY: Invalid HTML (item text rendered as tags)")

    # ── Plain text fallback: no HTML structure detected ──────────────────────
    if not items:
        plain_items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
        if plain_items:
            # Strip HTML tags from each line before classification.  Files that mix an
            # HTML-tagged title line (e.g. <u>Animal Names</u>) with plain numbered items
            # need the tags removed first so the numbered-list check works correctly.
            clean_lines = [re.sub(r'<[^>]+>', '', line).strip() for line in plain_items]
            clean_lines = [l for l in clean_lines if l]

            numbered_pattern = r'^\d+\.\s+(.+)$'
            num_matches = [re.match(numbered_pattern, l) for l in clean_lines]
            numbered_items = [m.group(1) for m in num_matches if m]

            if numbered_items and all(num_matches):
                # Every line is a numbered item — clean numbered list in plain text.
                items = numbered_items
                cleanups.append("HTML-Numbered-List-Stripping")
                cleanups.append("QUALITY: Invalid HTML (contains plain numbered list instead of HTML markup)")
            elif numbered_items:
                # Mix of numbered items and non-numbered lines (e.g. a title).
                # If the non-numbered lines are all short (≤5 words), treat them as
                # header/title noise and extract only the numbered items.
                non_numbered = [clean_lines[i] for i, m in enumerate(num_matches) if not m]
                if all(len(l.split()) <= 5 for l in non_numbered):
                    items = numbered_items
                    cleanups.append("HTML-Numbered-List-Stripping")
                    cleanups.append("QUALITY: Invalid HTML (contains plain numbered list instead of HTML markup)")
                else:
                    items = clean_lines
                    cleanups.append("HTML-PlainText-Fallback")
                    cleanups.append("QUALITY: Requested HTML; response was plain text")
            else:
                items = clean_lines
                cleanups.append("HTML-PlainText-Fallback")
                cleanups.append("QUALITY: Requested HTML; response was plain text")

    return items, cleanups


def parse_txt1(content):
    """Parse .txt1 file: each line is an item, removing leading numbers.
    Delegates to clean_strip_leading_numbers for consistent cleanup and logging.
    Emits a QUALITY flag when no leading numbers are present (unexpected for this format).
    Returns (items, cleanups)."""
    cleanups = []
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    cleaned, cleanup = clean_strip_leading_numbers(items)
    if cleanup:
        cleanups.append(cleanup)
    elif cleaned:
        # numberedText format expected leading numbers; their absence is a quality issue
        cleanups.append("QUALITY: TXT1-No-Numbers (file has no leading numbers; expected numbered format)")
    return cleaned, cleanups


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
    Cleanup key emitted is named by the language specifier of the first block:
      ```json  -> Extract-from-JSON-Codefence-Markdown
      ```yaml / ```yml -> Extract-from-YAML-Codefence-Markdown
      ```html  -> Extract-from-HTML-Codefence-Markdown
      ```csv   -> Extract-from-CSV-Codefence-Markdown
      ``` (no specifier or unrecognized) -> Extract-from-Generic-Codefence-Markdown
    """
    _LANG_CLEANUP_MAP = {
        'json': 'Extract-from-JSON-Codefence-Markdown',
        'yaml': 'Extract-from-YAML-Codefence-Markdown',
        'yml':  'Extract-from-YAML-Codefence-Markdown',
        'html': 'Extract-from-HTML-Codefence-Markdown',
        'csv':  'Extract-from-CSV-Codefence-Markdown',
    }
    cleanups = []
    # Capture language specifier and block content separately
    code_block_pattern = r'```([\w]*)\n(.*?)\n```'
    matches = re.findall(code_block_pattern, content, re.DOTALL)

    if matches:
        # Found code blocks - concatenate content from all blocks
        extracted = '\n'.join(m[1] for m in matches)
        # Name the cleanup from the language specifier of the first block
        lang_spec = matches[0][0].lower()
        cleanups.append(_LANG_CLEANUP_MAP.get(lang_spec, 'Extract-from-Generic-Codefence-Markdown'))
        return extracted, True, cleanups
    else:
        # No code blocks found
        return content, False, cleanups


# Maps substrings found in QUALITY cleanup strings to short canonical format-style labels.
# Used by fixups_to_cleanup() to route cleanup strings to metadata["formatStyles"].
QUALITY_FORMAT_STYLE_MAP = {
    "Single-span tag format":             "single-span-tag",
    "Requested HTML; response was plain": "html_no_markup",
    "Invalid HTML":                       "html_no_markup",
    "Invalid HTML (item text rendered as tags)": "invalid-html-tags",
    "YAML end-of-directives marker":      "end-of-directives",
    "Numbered items in separate tags":    "numbered-items-in-tags",
    "Repeated JSON object keys":          "repeated-json-keys",
    "Non-Western characters":             "non-western-characters",
    "Comma-separated items in single":    "comma-separated",
    "TXT1-No-Numbers":                    "txt1-no-numbers",
    "parsing failed completely":          "parse-failed",
    "HTML markup found in items":         "stray-html-markup",
    "Blockquote markers in items":        "blockquote-markup",
}


def fixups_to_cleanup(cleanup_keys):
    """Convert a cleanup string list to (cleanup_dict, format_style_labels).

    'Rule: N items' → {Rule: N}
    Other strings    → {string: True}
    QUALITY strings  → excluded from cleanup; returned as short labels list instead.
    """
    cleanup = {}
    format_styles = []
    for cleanup_key in cleanup_keys:
        matched = False
        for substr, label in QUALITY_FORMAT_STYLE_MAP.items():
            if substr in cleanup_key:
                format_styles.append(label)
                matched = True
                break
        if matched:
            continue
        m = re.match(r'^(.+?):\s+(\d+)\s+items?$', cleanup_key)
        if m:
            cleanup[m.group(1)] = int(m.group(2))
        else:
            cleanup[cleanup_key] = True
    return cleanup, format_styles


def reorder_metadata(metadata):
    """Reorder metadata keys: time, experiment, prompt, model, temperature, format,
    formatStyle, formatStyles, iteration, codeblock, cleanup, then others.
    Note: prompt is optional for old format files. codeblock, formatStyles, and cleanup are optional."""
    key_order = ["time", "experiment", "prompt", "model", "temperature", "format",
                 "formatStyle", "formatStyles", "iteration", "codeblock", "cleanup"]
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
    Supports both old and new formats:
    - Old: YYYYMMDDHHMM-EXPERIMENT-MODEL-TEMP-ITERATION.EXT (12-digit timestamp, 5+ parts)
    - New: YYYYMMDDHHMMSS-EXPERIMENT-PROMPT-MODEL-TEMP-ITERATION.EXT (14-digit timestamp, 6+ parts)
    """
    name_without_ext = Path(filename).stem
    parts = name_without_ext.split('-')

    # Check timestamp and determine format
    if not parts[0].isdigit():
        return False

    timestamp_len = len(parts[0])

    if timestamp_len == 12:
        # Old format: need at least 5 parts (timestamp, experiment, model, temp, iteration)
        if len(parts) < 5:
            return False
    elif timestamp_len == 14:
        # New format: need at least 6 parts (timestamp, experiment, prompt, model, temp, iteration)
        if len(parts) < 6:
            return False
    else:
        # Invalid timestamp length
        return False

    # Check iteration is 2 digits
    if not parts[-1].isdigit() or len(parts[-1]) != 2:
        return False

    # Check temperature starts with 't'
    if not parts[-2].startswith('t'):
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
    Extract timestamp, prompt, experiment, model, and temperature from filename.
    Supports both old and new formats:
    - Old: TIMESTAMP-EXPERIMENT-MODEL-TEMP-ITERATION.EXT (12-digit timestamp, no prompt)
      Example: 202602161623-animals_plain-gpt4-t10-01.md
    - New: TIMESTAMP-EXPERIMENT-PROMPT-MODEL-TEMP-ITERATION.EXT (14-digit timestamp)
      Example: 20250216160215-test1-animals-gpt4-t10-01.md
    Returns dict with time, prompt, experiment, model, temperature, and iteration.
    """
    # Remove extension
    name_without_ext = Path(filename).stem

    # Split by hyphen
    parts = name_without_ext.split('-')

    if len(parts) < 5:
        return {}

    # First part is timestamp
    timestamp_raw = parts[0]
    timestamp = format_timestamp(timestamp_raw)

    # Work backwards: find temperature and iteration
    iteration = parts[-1]
    temp_part = parts[-2]

    # Verify we have a valid temperature part (starts with 't')
    if not temp_part.startswith('t'):
        # No valid temperature found
        return {
            "time": timestamp,
        }

    # Determine format based on timestamp length
    is_new_format = len(timestamp_raw) == 14
    is_old_format = len(timestamp_raw) == 12

    if is_new_format:
        # New format: TIMESTAMP-EXPERIMENT-PROMPT-MODEL-TEMP-ITERATION
        if len(parts) < 6:
            return {"time": timestamp}

        experiment = parts[1]
        prompt = parts[2]
        model = '-'.join(parts[3:-2])

    elif is_old_format:
        # Old format: TIMESTAMP-EXPERIMENT-[PROMPT/VARIANT]-MODEL-TEMP-ITERATION
        # Note: Some files have PROMPT/VARIANT field, some don't
        if len(parts) < 5:
            return {"time": timestamp}

        prompt = None
        experiment = parts[1]

        # Determine if there's a prompt/variant field
        # Temperature is parts[-2] and iteration is parts[-1]
        # If we have 6+ parts, then parts[2] is the prompt/variant and parts[3] is the model
        # If we have exactly 5 parts, then parts[2] is the model
        if len(parts) >= 6:
            # Has prompt/variant: TIMESTAMP-EXPERIMENT-PROMPT-MODEL-TEMP-ITERATION
            prompt = parts[2]
            model = '-'.join(parts[3:-2])
        else:
            # No prompt: TIMESTAMP-EXPERIMENT-MODEL-TEMP-ITERATION
            model = '-'.join(parts[2:-2])

    else:
        return {"time": timestamp}

    # Parse temperature
    temperature = None
    if temp_part.startswith('t'):
        temp_str = temp_part[1:]  # Remove 't' prefix
        if temp_str != 'xx':
            try:
                temp_int = int(temp_str)
                temperature = temp_int / 10.0
            except ValueError:
                pass

    metadata = {
        "time": timestamp,
        "experiment": experiment,
        "model": model,
        "temperature": temperature,
        "iteration": int(iteration) if iteration.isdigit() else None,
    }

    # Only add prompt if it exists (new format only)
    if prompt is not None:
        metadata["prompt"] = prompt

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


def detect_leading_punctuation(item):
    """Return True if item begins with a punctuation/formatting character."""
    return bool(_LEADING_PUNCT_RE.match(item))


def detect_trailing_punctuation(item):
    """Return True if item ends with a punctuation/formatting character."""
    return bool(_TRAILING_PUNCT_RE.search(item))


def detect_internal_punctuation(item):
    """Return True if item contains a special character in its interior
    (i.e. after stripping leading/trailing formatting chars the cleanup pipeline
    would remove). Catches things like slashes or colons inside words."""
    inner = _LEADING_PUNCT_RE.sub('', item)
    inner = _TRAILING_PUNCT_RE.sub('', inner)
    return any(not (c.isalpha() or c.isdigit() or c in " '-") for c in inner)


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


def detect_preamble_leak(item):
    """Check if item looks like LLM preamble/instruction text.
    Matches common LLM response prefixes and items with 6+ words."""
    preamble_patterns = [
        r'\bhere\s+(is|are)\b',
        r'\bsure\b',
        r'\bcertainly\b',
        r'\blist\s+of\b',
        r'\bfollowing\b',
        r'\bi\'?ll\b',
        r'\bi\s+can\b',
        r'\bbelow\b',
    ]
    lower = item.lower()
    # Items with 6+ words are suspicious (item names are typically 1-3 words)
    if len(lower.split()) >= 6:
        return True
    for pattern in preamble_patterns:
        if re.search(pattern, lower):
            return True
    return False


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

def clean_strip_leading_format(items):
    """Strip leading markdown/format characters (* - + : ; , . ! ? { } [ ] etc.).
    Rule: Strip-Leading-Formatting.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = _LEADING_PUNCT_RE.sub('', item).strip()
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Strip-Leading-Formatting: {changed} items" if changed else None
    return result, cleanup


def clean_strip_number_word_prefix(items):
    """Strip a numeric prefix attached directly to a word (e.g. '48eagle' -> 'eagle').
    Rule: Strip-Number-Word-Prefix.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = re.sub(r'^[0-9]+([a-zA-Z])', r'\1', item).strip()
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Strip-Number-Word-Prefix: {changed} items" if changed else None
    return result, cleanup


def clean_remove_parenthetical(items):
    """Remove parenthetical content (e.g. 'camelopard (giraffe)' -> 'camelopard').
    Rule: Remove-Parenthetical.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = re.sub(r'\s*\([^)]*\)', '', item).strip()
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Remove-Parenthetical: {changed} items" if changed else None
    return result, cleanup


def clean_strip_trailing_punct(items):
    """Strip trailing punctuation and format characters from items.
    Rule: Strip-Trailing-Punctuation.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = _TRAILING_PUNCT_RE.sub('', item).strip()
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Strip-Trailing-Punctuation: {changed} items" if changed else None
    return result, cleanup


def clean_strip_doubled_punct(items):
    """Remove doubled or tripled internal punctuation (e.g. 'ti::ger' -> 'tiger').
    Rule: Strip-Doubled-Punctuation.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = re.sub(r'([a-zA-Z])([:.;,\-_/\\|])\2+([a-zA-Z])', r'\1\3', item)
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Strip-Doubled-Punctuation: {changed} items" if changed else None
    return result, cleanup


def clean_strip_leading_hyphens(items):
    """Strip any remaining leading hyphens and spaces (final normalization).
    Rule: Strip-Leading-Hyphens.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = item.lstrip('- ').strip()
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Strip-Leading-Hyphens: {changed} items" if changed else None
    return result, cleanup


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
    cleaned = []
    changed = 0
    for item in items:
        original = item
        c = item.strip()
        while c and c[0] in ('"', "'") and c[-1] == c[0]:
            c = c[1:-1].strip()
        if c != original:
            changed += 1
        cleaned.append(c)
    cleanup = f"Strip-Quotes: {changed} items" if changed else None
    return cleaned, cleanup


def clean_strip_leading_bullets(items):
    """Remove leading bullet list markers (* - +) from items.
    Rule: Strip-Leading-Bullets.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = re.sub(r'^[\s*\-+]+', '', item).strip()
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Strip-Leading-Bullets: {changed} items" if changed else None
    return result, cleanup


def clean_strip_leading_numbers(items):
    """Remove leading number prefixes with punctuation (e.g. '1. ', '2) ', '3: ') from items.
    Rule: Strip-Leading-Numbers.
    Returns (items, cleanup_str_or_none)."""
    result = []
    changed = 0
    for item in items:
        cleaned = re.sub(r'^\d+[\.\):\-\s]+', '', item).strip()
        if cleaned != item:
            changed += 1
        result.append(cleaned)
    cleanup = f"Strip-Leading-Numbers: {changed} items" if changed else None
    return result, cleanup


def clean_format_specific(items, ext):
    """
    Clean up format-specific formatting FIRST (before quality checks).
    Removes bullets, numbers, HTML tags, and blockquote markers from items.
    Applies to all formats; HTML markup or blockquote markers in any format are
    both cleaned and flagged as quality issues.
    For CSV, delegates leading-marker removal to csv_strip_leading_markers().
    Returns (cleaned_items, cleanups_list).
    """
    cleanups = []
    cleaned = list(items)

    # Strip HTML tags and blockquote markers from all formats.
    # Residual HTML tags in HTML files indicate malformed structure; in any other
    # format they indicate the model leaked markup into its response.
    # Blockquote '>' markers are similarly out of place in structured output.
    new_cleaned = []
    html_tag_count = 0
    blockquote_count = 0
    for item in cleaned:
        stripped_tags = re.sub(r'</?[a-zA-Z][^>]*>?', '', item)
        if stripped_tags != item:
            html_tag_count += 1
        item = stripped_tags
        stripped_bq = re.sub(r'^>+|>+$', '', item).strip()
        if stripped_bq != item:
            blockquote_count += 1
        item = stripped_bq
        new_cleaned.append(item)
    cleaned = new_cleaned
    if html_tag_count:
        cleanups.append("HTML-Stray-Tag-Cleanup")
        cleanups.append("QUALITY: HTML markup found in items")
    if blockquote_count:
        cleanups.append("Blockquote-Marker-Cleanup")
        cleanups.append("QUALITY: Blockquote markers in items")

    # CSV: delegate to named function that tracks bullets and numbers separately
    if ext == '.csv':
        cleaned, csv_cleanups = csv_strip_leading_markers(cleaned)
        cleanups.extend(csv_cleanups)

    # Markdown and plain text: delegate to shared cleanup functions
    elif ext in ['.md', '.txt']:
        cleaned, csv_cleanups = csv_strip_leading_markers(cleaned)
        cleanups.extend(csv_cleanups)

    return cleaned, cleanups


def process_and_track(items, ext, max_item_length=25):
    """
    Process items in the correct order:
    1. Trim items
    2. Clean format-specific formatting
    3. Check quality issues on cleaned items
    4. Apply general item cleanup pipeline

    Args:
        items: List of items to process
        ext: File extension
        max_item_length: Maximum allowed item length (items longer are flagged in cleanups)
    Returns (processed_items, processing_metadata, metadata).
    """
    processing = {
        "consistentCase": True,
        "case": "lower",
    }
    # quality_issues stores one example string per issue type (first occurrence only).
    # Keys are added only when an issue is found, so no empty keys are stored.
    quality_issues = {}
    # preamble_set collects ALL preamble items for filtering; quality_issues["preamble_leak"]
    # holds only the first example for reporting purposes.
    preamble_set = set()
    processing_cleanups = []

    if not items:
        metadata = {
            "itemCount": 0,
            "alphabeticalOrder": True
        }
        return items, processing, metadata

    # Step 1: Trim items
    trimmed = trim_items(items)

    # Step 2: Clean format-specific formatting FIRST
    cleaned_items, format_cleanups = clean_format_specific(trimmed, ext)
    if format_cleanups:
        processing_cleanups.extend(format_cleanups)

    # Check alphabetical order of original items
    alphabetical = is_alphabetical_order(trimmed)

    # Detect case pattern in original items
    case_type, consistent_case = detect_case(trimmed)
    processing["case"] = case_type
    processing["consistentCase"] = consistent_case

    # Step 3: Check for quality issues on CLEANED items (after format-specific removal).
    # For markdown, "1. Dog" won't be in cleaned_items anymore, so we won't flag it as inappropriate.
    # Only the first example of each issue type is stored (sufficient for reporting).
    for item in cleaned_items:
        # Check for specific punctuation issues
        if detect_leading_punctuation(item) and "leading_punctuation" not in quality_issues:
            quality_issues["leading_punctuation"] = item
        if detect_trailing_punctuation(item) and "trailing_punctuation" not in quality_issues:
            quality_issues["trailing_punctuation"] = item
        if detect_internal_punctuation(item) and "internal_punctuation" not in quality_issues:
            quality_issues["internal_punctuation"] = item

        # Check for items exceeding maximum length
        if len(item) > max_item_length and "exceeds_max_length" not in quality_issues:
            quality_issues["exceeds_max_length"] = item

        # Check for LLM preamble leaks — collect all for filtering; store only first as example
        if detect_preamble_leak(item):
            preamble_set.add(item)
            if "preamble_leak" not in quality_issues:
                quality_issues["preamble_leak"] = item

        # Check for residual markup
        if detect_markup_artifact(item) and "markup_artifact" not in quality_issues:
            quality_issues["markup_artifact"] = item

        # Check for repeated characters (likely typos)
        if detect_repeated_chars(item) and "repeated_chars" not in quality_issues:
            quality_issues["repeated_chars"] = item

    # Check for non-Western (non-ASCII) characters across all items; applies to any format.
    if any(ord(c) > 127 for item in cleaned_items for c in item):
        processing_cleanups.append("QUALITY: Non-Western characters detected in items")

    # Note: misspelling detection is deferred to a second pass in summarize_results()
    # after the corpus word frequency table is built across all files.

    # Step 4: Filter preamble items then apply general item cleanup pipeline.
    # Preamble items are LLM contamination and are excluded entirely.
    # All other items pass through each named cleanup function in order;
    # each function reports a cleanup only when it actually changed something.
    filtered = [item for item in cleaned_items if item not in preamble_set]

    cleanup_pipeline = [
        clean_strip_leading_format,
        clean_strip_number_word_prefix,
        clean_remove_parenthetical,
        clean_strip_trailing_punct,
        clean_strip_doubled_punct,
        clean_strip_leading_hyphens,
    ]

    processed = filtered
    for step in cleanup_pipeline:
        processed, cleanup = step(processed)
        if cleanup:
            processing_cleanups.append(cleanup)

    # Conditional lowercasing: only if uppercase characters are found
    if any(any(c.isupper() for c in item) for item in processed):
        processed, cleanup = clean_lowercase(processed)
        if cleanup:
            processing_cleanups.append(cleanup)

    # Create metadata
    metadata = {
        "itemCount": len(processed),
        "alphabeticalOrder": alphabetical
    }

    # Store quality issues (only non-empty; each value is a single example string)
    if quality_issues:
        metadata["qualityIssues"] = quality_issues

    # Store processing cleanups
    if processing_cleanups:
        metadata["processingCleanups"] = processing_cleanups

    return processed, processing, metadata


def classify_prevalence(true_count, total_count):
    """
    Classify prevalence of a boolean attribute across multiple files.
    Returns 'all', 'some', or 'none' based on the proportion of true values.
    """
    if total_count == 0:
        return "none"
    proportion = true_count / total_count
    if proportion == 1.0:
        return "all"
    elif proportion == 0.0:
        return "none"
    else:
        return "some"


def calculate_statistics(counts):
    """
    Calculate statistics for a list of item counts.

    Args:
        counts: List of integers (item counts)

    Returns:
        Dictionary with max, min, avg, var, and mode
    """
    if not counts:
        return {"max": 0, "min": 0, "avg": 0, "var": 0, "mode": 0}

    max_count = max(counts)
    min_count = min(counts)
    avg_count = sum(counts) / len(counts)

    # Calculate variance (sample variance if n > 1, else 0)
    if len(counts) > 1:
        variance = sum((x - avg_count) ** 2 for x in counts) / (len(counts) - 1)
    else:
        variance = 0

    # Calculate mode
    count_freq = Counter(counts)
    mode_count = count_freq.most_common(1)[0][0]

    return {
        "max": max_count,
        "min": min_count,
        "avg": round(avg_count, 2),
        "var": round(variance, 2),
        "mode": mode_count
    }


def summarize_results(filename_filter=None, model=None, format_type=None, experiment=None, timestamp=None, temperature=None, max_item_length=25, analysis=False, exclude_model=None, verbose=False):
    """
    Read all result files by type, parse items, and summarize into a single JSON.
    Structure: {filetype: [{filename: str, items: [...]}, ...], ...}

    Args:
        filename_filter: Optional string to filter files by name (legacy, e.g., "experiment1")
        model: Optional model name to INCLUDE (e.g., "gpt4"). If not specified, all models included.
        format_type: Optional file format/extension to filter by (e.g., "json", "txt")
        experiment: Optional experiment name to filter by (e.g., "animals5")
        timestamp: Optional timestamp to filter by (e.g., "202602061922")
        temperature: Optional temperature to filter by (e.g., "1.0", "10" as int/10)
        max_item_length: Maximum allowed item length in characters (default 25, items longer are flagged)
        analysis: Whether to generate analysis report by model and temperature
        exclude_model: Optional tuple of model names to EXCLUDE (e.g., ("gpt4", "llama318b"))
        verbose: If True, show detailed summary output; skipped files always shown regardless
    """
    if not RESULTS_DIR.exists():
        click.echo(format_error("summarize", f"{RESULTS_DIR} directory not found"), err=True)
        return False

    consolidated = defaultdict(list)
    # All tracked issue types (item-level first, then format-style-derived using dash-separated names)
    ISSUE_TYPES = [
        "leading_punctuation", "trailing_punctuation", "internal_punctuation",
        "exceeds_max_length", "preamble_leak",
        "markup_artifact", "repeated_chars",
        "single-span-tag", "plain-text", "end-of-directives",
        "numbered-items-in-tags", "repeated-json-keys", "non-western-characters",
        "comma-separated", "txt1-no-numbers", "html_no_markup", "invalid-html-tags",
        "inconsistent_case", "inconsistent_md_format", "inconsistent_html_format",
        "inconsistent_json_format",
        "parse-failed", "stray-html-markup", "blockquote-markup",
    ]
    # Track quality issues: model -> temperature -> file_type -> prompt -> issue_type -> set of items
    quality_issues_output = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {k: set() for k in ISSUE_TYPES}))))
    # Track example filenames: model -> temperature -> file_type -> prompt -> issue_type -> {item: filename}
    quality_issues_examples = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {k: {} for k in ISSUE_TYPES}))))
    # Track formatStyle counts per prompt: model -> temperature -> file_type -> prompt -> formatStyle -> count
    format_style_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))))
    # Track item counts for statistics: model -> temperature -> file_type -> [list of item counts]
    item_count_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # Count cleanup rule invocations per trial set: model -> temperature -> file_type -> prompt -> Counter
    # Each Counter maps rule_name -> number of trials in the set that triggered that rule.
    cleanup_rules_agg = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(Counter))))
    # Track case values per trial set for cross-trial inconsistency detection:
    # model -> temperature -> file_type -> prompt -> [(case_value, filename)]
    case_values_agg = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    # Track per-file cleanup rule sets for markdown trial sets (detects inconsistent formatting):
    # model -> temperature -> prompt -> [(frozenset(cleanup_keys), filename)]
    md_cleanup_agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # Track per-file cleanup rule sets for HTML trial sets (detects inconsistent formatting):
    # model -> temperature -> prompt -> [(frozenset(cleanup_keys), filename)]
    html_cleanup_agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # Track per-file cleanup rule sets for JSON trial sets (detects inconsistent formatting):
    # model -> temperature -> prompt -> [(frozenset(cleanup_keys), filename)]
    json_cleanup_agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    file_count = 0
    skipped_trials = []  # Track trial filenames that were skipped
    zero_item_files = []  # Track files that produced 0 items
    source_items = set()  # Track unique items from raw parsed data

    # Display filter parameters
    filters_applied = []
    if filename_filter:
        filters_applied.append(f"filename: {filename_filter}")
    if experiment:
        filters_applied.append(f"experiment: {experiment}")
    if model:
        filters_applied.append(f"model: {model} (include only)")
    if exclude_model:
        filters_applied.append(f"exclude-model: {', '.join(exclude_model)}")
    if format_type:
        filters_applied.append(f"format: {format_type}")
    if timestamp:
        filters_applied.append(f"timestamp: {timestamp}")
    if temperature:
        filters_applied.append(f"temperature: {temperature}")

    if filters_applied:
        click.echo(f"Filters: {', '.join(filters_applied)}\n")

    # Scan results directory
    for file_path in sorted(RESULTS_DIR.iterdir()):
        if not file_path.is_file():
            continue

        # Skip results.json and other generated output files
        if file_path.name in SKIP_PATTERNS:
            continue

        # Skip files that don't follow standard naming convention
        if not is_standard_filename(file_path.name):
            continue

        # Filter by filename if specified (legacy filter)
        if filename_filter and filename_filter not in file_path.name:
            continue

        # Get file extension
        ext = file_path.suffix.lower()
        if not ext:
            continue

        # Filter by format if specified
        if format_type and ext != f".{format_type}" and ext != format_type:
            continue

        # Skip certain extensions
        if ext in SKIP_EXTENSIONS:
            skipped_trials.append(file_path.name)
            continue

        # Read file content
        content = None
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='utf-16') as f:
                    content = f.read()
            except UnicodeDecodeError:
                click.echo(f"Skipping (encoding error): {file_path.name}")
                skipped_trials.append(file_path.name)
                continue

        # Parse content based on file type
        if ext not in PARSERS:
            click.echo(f"Skipping (no parser): {file_path.name}")
            skipped_trials.append(file_path.name)
            continue

        try:
            parser = PARSERS[ext]

            # Extract content from code blocks if present
            cleaned_content, had_codeblock, codeblock_cleanups = extract_code_block(content)

            # Parse the (possibly cleaned) content
            items, parser_cleanups = parser(cleaned_content)

            # Parse filename for experiment, model, temperature FIRST (needed for filtering)
            filename_metadata = parse_filename_metadata(file_path.name)

            # Apply metadata filters BEFORE collecting source items
            # This prevents filtered files from being included in unique items
            if experiment and filename_metadata.get("experiment") != experiment:
                continue
            # Model filtering: include only specific model if specified, or exclude specific models
            file_model = filename_metadata.get("model")
            if model and file_model != model:
                continue
            # Exclude models by pattern (supports wildcards like gpt*, *llama*, etc.)
            if exclude_model:
                if any(matches_model_pattern(file_model, pattern) for pattern in exclude_model):
                    continue
            # Temperature filtering
            if temperature is not None:
                file_temp = filename_metadata.get("temperature")
                try:
                    temp_filter = float(temperature)
                    if file_temp != temp_filter:
                        continue
                except (ValueError, TypeError):
                    continue
            # Timestamp filtering
            if timestamp:
                file_timestamp = Path(file_path.name).stem.split('-')[0]
                if file_timestamp != timestamp:
                    continue

            # NOW collect source items (only after filtering passes)
            for item in items:
                if item:  # Only track non-empty items
                    source_items.add(item)

            # Track files that produced 0 items (immediately after extraction, before processing)
            if len(items) == 0:
                zero_item_files.append(file_path.name)

            # Process and track normalization
            items, processing, metadata = process_and_track(items, ext, max_item_length)

            # Merge processing into metadata
            metadata.update(processing)

            # Add codeblock flag if code blocks were found and processed
            if had_codeblock:
                metadata["codeblock"] = True

            # Add format from extension
            metadata["format"] = FORMAT_MAP.get(ext, "unknown")

            # Add format style (how the data was structured in the file)
            metadata["formatStyle"] = detect_format_style(content, ext)

            # Collect all cleanup strings from parsers, codeblock processing, and cleanup pipeline
            all_cleanups = codeblock_cleanups + parser_cleanups
            # Expand parse-failure sentinel now that we have the filename
            all_cleanups = [
                f"QUALITY: parsing failed completely for {file_path.name}"
                if f == "QUALITY: Parse-Failed" else f
                for f in all_cleanups
            ]
            if metadata.get("processingCleanups"):
                all_cleanups.extend(metadata.pop("processingCleanups"))

            # Convert cleanup strings to structured cleanup dict and format-style label list.
            # QUALITY cleanup strings (format observations) go to metadata["formatStyles"];
            # all other cleanup strings go to metadata["cleanup"].
            if all_cleanups:
                cleanup_dict, format_style_labels = fixups_to_cleanup(all_cleanups)
                if cleanup_dict:
                    metadata["cleanup"] = cleanup_dict
                if format_style_labels:
                    metadata["formatStyles"] = format_style_labels

            # Merge filename metadata with processing metadata
            metadata.update(filename_metadata)

            # Count duplicate items (items appearing more than once)
            item_counts = Counter(items)
            duplicate_count = sum(1 for count in item_counts.values() if count > 1)

            # Add duplicates to metadata before reordering
            metadata["duplicates"] = duplicate_count

            # Reorder metadata keys
            metadata = reorder_metadata(metadata)

            # Get model, temperature, file type, and prompt for tracking
            model_name = abbreviate_model_name(metadata.get("model", "unknown"))
            temp_value = metadata.get("temperature", "unknown")
            file_type = FORMAT_MAP.get(ext, ext)
            prompt_name = metadata.get("prompt", "unknown")

            # Count how many trials in this set triggered each cleanup rule
            for rule_name in metadata.get("cleanup", {}).keys():
                cleanup_rules_agg[model_name][str(temp_value)][file_type][prompt_name][rule_name] += 1

            # Remove case keys from per-file metadata (case consistency is tracked cross-trial).
            case_value = metadata.pop("case", "lower")
            metadata.pop("consistentCase", None)
            case_values_agg[model_name][str(temp_value)][file_type][prompt_name].append((case_value, file_path.name))

            # For markdown files, track the full set of cleanup keys applied to each file.
            # After the file loop, trial sets where rule sets differ are flagged as inconsistent.
            if ext == '.md':
                md_rules = frozenset(metadata.get("cleanup", {}).keys())
                md_cleanup_agg[model_name][str(temp_value)][prompt_name].append((md_rules, file_path.name))

            # For HTML files, track the full set of cleanup keys applied to each file.
            # After the file loop, trial sets where rule sets differ are flagged as inconsistent.
            if ext == '.html':
                html_rules = frozenset(metadata.get("cleanup", {}).keys())
                html_cleanup_agg[model_name][str(temp_value)][prompt_name].append((html_rules, file_path.name))

            # For JSON files, track the full set of cleanup keys applied to each file.
            # After the file loop, trial sets where rule sets differ are flagged as inconsistent.
            if ext == '.json':
                json_rules = frozenset(metadata.get("cleanup", {}).keys())
                json_cleanup_agg[model_name][str(temp_value)][prompt_name].append((json_rules, file_path.name))

            # Track formatStyle counts per prompt (primary detect_format_style() value)
            format_style_counts[model_name][str(temp_value)][file_type][prompt_name][metadata.get("formatStyle", "unknown")] += 1
            # Also count QUALITY-derived format-style labels (from metadata["formatStyles"])
            for fs_label in metadata.get("formatStyles", []):
                format_style_counts[model_name][str(temp_value)][file_type][prompt_name][fs_label] += 1

            # Track quality issues by model, temperature, file type, and prompt
            # Track item-level quality issues (qualityIssues now stores one example per type)
            if "qualityIssues" in metadata:
                qi = metadata["qualityIssues"]
                filename = file_path.name
                for issue_type in ["leading_punctuation", "trailing_punctuation", "internal_punctuation",
                                   "exceeds_max_length", "preamble_leak",
                                   "markup_artifact", "repeated_chars"]:
                    example = qi.get(issue_type)
                    if not example:
                        continue
                    # txt1 exception: leading-number items are expected format, not quality issues
                    if issue_type == "leading_punctuation" and ext == '.txt1' \
                            and re.match(r'^\d+[\.\)\-\s]', example):
                        continue
                    quality_issues_output[model_name][str(temp_value)][file_type][prompt_name][issue_type].add(example)
                    if example not in quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][issue_type]:
                        quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][issue_type][example] = filename

            # Track format-style quality issues from metadata["formatStyles"]
            # (QUALITY cleanup strings were routed here by fixups_to_cleanup())
            if "formatStyles" in metadata:
                filename = file_path.name
                for fs_label in metadata["formatStyles"]:
                    if fs_label == "parse-failed":
                        # For parse failures, use the filename itself as the example so
                        # the report can show which specific files failed to parse.
                        quality_issues_output[model_name][str(temp_value)][file_type][prompt_name][fs_label].add(filename)
                        quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][fs_label][filename] = filename
                    else:
                        quality_issues_output[model_name][str(temp_value)][file_type][prompt_name][fs_label].add(fs_label)
                        if fs_label not in quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][fs_label]:
                            quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][fs_label][fs_label] = filename

            # Count duplicate items (items appearing more than once)
            item_counts = Counter(items)
            duplicate_count = sum(1 for count in item_counts.values() if count > 1)

            # Track item count for statistics
            item_count_stats[model_name][str(temp_value)][file_type].append(len(items))

            # Add to consolidated data
            consolidated[ext].append({
                "filename": file_path.name,
                "metadata": metadata,
                "items": items
            })
            file_count += 1
            click.echo(f"Processed: {file_path.name} ({len(items)} items)")

        except Exception as e:
            click.echo(f"Error parsing {file_path.name}: {e}")
            skipped_trials.append(file_path.name)
            continue

    # Convert defaultdict to regular dict for JSON serialization
    consolidated_dict = dict(consolidated)

    # Detect treatment consistency for each trial set.
    # Tracks all fields that indicate how output was structured or cleaned up.
    # Group by (abbreviated_model, str(temperature), file_type, prompt) — no experiment level.
    # Structure: {(model, temp, file_type, prompt): {field: [values]}}
    TREATMENT_FIELDS = ["formatStyle", "codeblock"]
    format_consistency = {}
    for ext, entries in consolidated_dict.items():
        file_type = FORMAT_MAP.get(ext, ext)
        for entry in entries:
            metadata = entry["metadata"]
            prompt = metadata.get("prompt", "unknown")
            model = abbreviate_model_name(metadata.get("model", "unknown"))
            temperature = str(metadata.get("temperature", "unknown"))

            key = (model, temperature, file_type, prompt)
            if key not in format_consistency:
                format_consistency[key] = {field: [] for field in TREATMENT_FIELDS}

            for field in TREATMENT_FIELDS:
                # codeblock is absent when False; treat absence as False
                format_consistency[key][field].append(metadata.get(field, False))

    # Compute cross-trial case inconsistency and populate quality_issues_output.
    # A trial set is flagged when more than one distinct case type is observed across files.
    for model_name in case_values_agg:
        for temp_value in case_values_agg[model_name]:
            for file_type in case_values_agg[model_name][temp_value]:
                for prompt_name in case_values_agg[model_name][temp_value][file_type]:
                    entries = case_values_agg[model_name][temp_value][file_type][prompt_name]
                    distinct = set(v for v, _ in entries)
                    if len(distinct) > 1:
                        for case_val, fname in entries:
                            quality_issues_output[model_name][temp_value][file_type][prompt_name]["inconsistent_case"].add(case_val)
                            if case_val not in quality_issues_examples[model_name][temp_value][file_type][prompt_name]["inconsistent_case"]:
                                quality_issues_examples[model_name][temp_value][file_type][prompt_name]["inconsistent_case"][case_val] = fname

    # Compute markdown format inconsistency across trials.
    # A markdown trial set is flagged when files have differing sets of cleanup rules applied,
    # indicating the model produced different structural formats across trials.
    # Instances are the rule names that appeared in some files but not all.
    for model_name in md_cleanup_agg:
        for temp_value in md_cleanup_agg[model_name]:
            for prompt_name in md_cleanup_agg[model_name][temp_value]:
                entries = md_cleanup_agg[model_name][temp_value][prompt_name]
                rule_sets = [rules for rules, _ in entries]
                if len(set(rule_sets)) > 1:
                    all_rules = set().union(*rule_sets)
                    common_rules = set.intersection(*[set(r) for r in rule_sets])
                    varying_rules = all_rules - common_rules
                    for rule in varying_rules:
                        quality_issues_output[model_name][temp_value]["markdown"][prompt_name]["inconsistent_md_format"].add(rule)
                        if rule not in quality_issues_examples[model_name][temp_value]["markdown"][prompt_name]["inconsistent_md_format"]:
                            example_fname = next((fname for rules, fname in entries if rule in rules), "unknown")
                            quality_issues_examples[model_name][temp_value]["markdown"][prompt_name]["inconsistent_md_format"][rule] = example_fname

    # Compute HTML format inconsistency across trials.
    # An HTML trial set is flagged when files have differing sets of cleanup rules applied,
    # indicating the model produced different structural formats across trials.
    # Instances are the rule names that appeared in some files but not all.
    for model_name in html_cleanup_agg:
        for temp_value in html_cleanup_agg[model_name]:
            for prompt_name in html_cleanup_agg[model_name][temp_value]:
                entries = html_cleanup_agg[model_name][temp_value][prompt_name]
                rule_sets = [rules for rules, _ in entries]
                if len(set(rule_sets)) > 1:
                    all_rules = set().union(*rule_sets)
                    common_rules = set.intersection(*[set(r) for r in rule_sets])
                    varying_rules = all_rules - common_rules
                    for rule in varying_rules:
                        quality_issues_output[model_name][temp_value]["HTML"][prompt_name]["inconsistent_html_format"].add(rule)
                        if rule not in quality_issues_examples[model_name][temp_value]["HTML"][prompt_name]["inconsistent_html_format"]:
                            example_fname = next((fname for rules, fname in entries if rule in rules), "unknown")
                            quality_issues_examples[model_name][temp_value]["HTML"][prompt_name]["inconsistent_html_format"][rule] = example_fname

    # Compute JSON format inconsistency across trials.
    # A JSON trial set is flagged when files have differing sets of cleanup rules applied,
    # indicating the model produced different structural formats across trials.
    # Instances are the rule names that appeared in some files but not all.
    for model_name in json_cleanup_agg:
        for temp_value in json_cleanup_agg[model_name]:
            for prompt_name in json_cleanup_agg[model_name][temp_value]:
                entries = json_cleanup_agg[model_name][temp_value][prompt_name]
                rule_sets = [rules for rules, _ in entries]
                if len(set(rule_sets)) > 1:
                    all_rules = set().union(*rule_sets)
                    common_rules = set.intersection(*[set(r) for r in rule_sets])
                    varying_rules = all_rules - common_rules
                    for rule in varying_rules:
                        quality_issues_output[model_name][temp_value]["JSON"][prompt_name]["inconsistent_json_format"].add(rule)
                        if rule not in quality_issues_examples[model_name][temp_value]["JSON"][prompt_name]["inconsistent_json_format"]:
                            example_fname = next((fname for rules, fname in entries if rule in rules), "unknown")
                            quality_issues_examples[model_name][temp_value]["JSON"][prompt_name]["inconsistent_json_format"][rule] = example_fname

    # Build quality_issues_dict with hierarchy: model -> temperature -> file_type -> prompt
    # Each prompt entry contains: issue lists, consistentFormat (bool), formatStyles (counts)
    quality_issues_dict = {}

    # Gather all (model, temp, file_type, prompt) combos from all sources
    all_combos = set(format_consistency.keys())
    for model_name in format_style_counts:
        for temp_value in format_style_counts[model_name]:
            for file_type in format_style_counts[model_name][temp_value]:
                for prompt_name in format_style_counts[model_name][temp_value][file_type]:
                    all_combos.add((model_name, temp_value, file_type, prompt_name))
    for model_name in quality_issues_output:
        for temp_value in quality_issues_output[model_name]:
            for file_type in quality_issues_output[model_name][temp_value]:
                for prompt_name in quality_issues_output[model_name][temp_value][file_type]:
                    all_combos.add((model_name, str(temp_value), file_type, prompt_name))

    for (model_name, temp_value, file_type, prompt_name) in sorted(all_combos, key=lambda x: (x[0], x[1], x[2].casefold(), x[3])):
        prompt_data = {}

        # Add quality issues (omit empty lists)
        issues = quality_issues_output.get(model_name, {}).get(temp_value, {}).get(file_type, {}).get(prompt_name, {})
        for issue_type in ISSUE_TYPES:
            raw_items = issues.get(issue_type, set())
            if raw_items:
                items_with_source = []
                for item in raw_items:
                    source = quality_issues_examples[model_name][temp_value][file_type][prompt_name][issue_type].get(item, "unknown")
                    items_with_source.append({"instance": item, "source": source})
                items_with_source.sort(key=lambda x: x["instance"].lower())
                prompt_data[issue_type] = items_with_source

        # Add consistentFormat: True if no treatment field varies across trials
        fc = format_consistency.get((model_name, temp_value, file_type, prompt_name), {})
        if fc:
            varying = [f for f in TREATMENT_FIELDS if len(set(fc[f])) > 1]
            prompt_data["consistentFormat"] = len(varying) == 0
        else:
            prompt_data["consistentFormat"] = True

        # Add formatStyles: count of each style seen for this prompt
        style_counts = format_style_counts.get(model_name, {}).get(temp_value, {}).get(file_type, {}).get(prompt_name, {})
        if style_counts:
            prompt_data["formatStyles"] = dict(style_counts)

        # Add cleanupRules: dict of {rule_name: trial_count} sorted by rule name
        rules_counter = cleanup_rules_agg.get(model_name, {}).get(temp_value, {}).get(file_type, {}).get(prompt_name, {})
        if rules_counter:
            prompt_data["cleanupRules"] = dict(sorted(rules_counter.items()))

        quality_issues_dict \
            .setdefault(model_name, {}) \
            .setdefault(temp_value, {}) \
            .setdefault(file_type, {})[prompt_name] = prompt_data

    # Write results JSON (without quality issues)
    try:
        with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(consolidated_dict, f, indent=2, ensure_ascii=False)

        if verbose:
            click.echo(f"\nConsolidated {file_count} files into {RESULTS_FILE}")
            file_types = sorted(consolidated_dict.keys())
            click.echo(f"File types: {', '.join(file_types)}")

            # Write quality JSON (quality issues by model)
            if quality_issues_dict:
                with open(QUALITY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(quality_issues_dict, f, indent=2, ensure_ascii=False)
                click.echo(f"Wrote quality issues to {QUALITY_FILE}")
            total_items = 0
            for ext in file_types:
                items = consolidated_dict[ext]
                item_count = sum(len(entry['items']) for entry in items)
                total_items += item_count
                click.echo(f"  {ext}: {len(items)} files, {item_count} items")

            click.echo(f"Total items: {total_items}")
        else:
            # Still write quality JSON even if not verbose
            if quality_issues_dict:
                with open(QUALITY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(quality_issues_dict, f, indent=2, ensure_ascii=False)

        if skipped_trials:
            click.echo(f"Skipped {len(skipped_trials)} trials:")
            for trial_name in sorted(skipped_trials):
                click.echo(f"  {trial_name}")

        if zero_item_files:
            click.echo(f"Files with 0 items ({len(zero_item_files)}):")
            for filename in sorted(zero_item_files):
                click.echo(f"  {filename}")

        # Print analysis report for all file types per model and temperature
        if analysis and verbose:
            try:
                def safe_write(text):
                    """Write text to stdout with error handling for encoding issues"""
                    sys.stdout.write(text + '\n')
                    sys.stdout.flush()

                safe_write("\n" + "="*70)
                safe_write("DATA ANALYSIS REPORT BY MODEL, TEMPERATURE, AND FILE TYPE")
                safe_write("="*70)

                # Iterate over item_count_stats which has entries for every
                # model/temperature/file_type combination that was processed
                for model_name in sorted(item_count_stats.keys()):
                    safe_write(f"\n{model_name}:")
                    for temp_value in sorted(item_count_stats[model_name].keys(), key=lambda x: (x == "unknown", x)):
                        safe_write(f"  Temperature {temp_value}:")
                        for file_type in sorted(item_count_stats[model_name][temp_value].keys(), key=str.casefold):
                            counts = item_count_stats[model_name][temp_value][file_type]
                            stats = calculate_statistics(counts)
                            safe_write(f"    {file_type} ({len(counts)} files):")
                            safe_write(f"      Items: max={stats['max']}, min={stats['min']}, avg={stats['avg']}, var={stats['var']}, mode={stats['mode']}")


                            # Per-prompt quality details
                            prompts_data = quality_issues_dict.get(model_name, {}).get(str(temp_value), {}).get(file_type, {})
                            for prompt_name in sorted(prompts_data.keys()):
                                pd = prompts_data[prompt_name]
                                safe_write(f"      {prompt_name}:")

                                # Format consistency
                                is_consistent = pd.get("consistentFormat", True)
                                if is_consistent:
                                    safe_write(f"        Format: ✓ consistent")
                                else:
                                    fc = format_consistency.get((model_name, str(temp_value), file_type, prompt_name), {})
                                    varying = [f for f in TREATMENT_FIELDS if len(set(fc.get(f, []))) > 1]

                                    # Build human-readable description of what varies.
                                    # TREATMENT_FIELDS is ["formatStyle", "codeblock"]; list
                                    # all distinct formatStyle values when that field varies.
                                    parts = []
                                    if "formatStyle" in varying:
                                        parts.extend(sorted(set(fc.get("formatStyle", []))))
                                    if "codeblock" in varying:
                                        parts.append("codeblock")
                                    safe_write(f"        Format: ✗ inconsistent ({', '.join(parts)})")

                                # Format styles breakdown
                                style_counts = pd.get("formatStyles", {})
                                if style_counts:
                                    styles_str = ", ".join(f"{s}: {c}" for s, c in sorted(style_counts.items()))
                                    safe_write(f"        Styles: {styles_str}")

                                # Punctuation issues (leading / trailing / internal)
                                for punct_type, label in [
                                    ("leading_punctuation",   "Leading punctuation"),
                                    ("trailing_punctuation",  "Trailing punctuation"),
                                    ("internal_punctuation",  "Internal punctuation"),
                                ]:
                                    punct_items = [e["instance"] for e in pd.get(punct_type, [])]
                                    if punct_items:
                                        safe_write(f"        {label} ({len(punct_items)} unique):")
                                        for item in punct_items[:5]:
                                            example_file = quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][punct_type].get(item)
                                            suffix = f" Example: {example_file}" if example_file else ""
                                            safe_write(f"          - {ascii(item)}{suffix}")
                                        if len(punct_items) > 5:
                                            safe_write(f"          ... and {len(punct_items) - 5} more")

                                # Exceeds max length
                                exceed_items = [e["instance"] for e in pd.get("exceeds_max_length", [])]
                                if exceed_items:
                                    safe_write(f"        Exceeds max length ({len(exceed_items)} unique):")
                                    for item in exceed_items[:5]:
                                        safe_write(f"          - {ascii(item)}")
                                    if len(exceed_items) > 5:
                                        safe_write(f"          ... and {len(exceed_items) - 5} more")

                                # Preamble leaks
                                preamble_items = [e["instance"] for e in pd.get("preamble_leak", [])]
                                if preamble_items:
                                    safe_write(f"        Preamble leaks ({len(preamble_items)} unique):")
                                    for item in preamble_items[:5]:
                                        safe_write(f"          - {ascii(item)}")
                                    if len(preamble_items) > 5:
                                        safe_write(f"          ... and {len(preamble_items) - 5} more")

                                # Markup artifacts
                                markup_items = [e["instance"] for e in pd.get("markup_artifact", [])]
                                if markup_items:
                                    safe_write(f"        Markup artifacts ({len(markup_items)} unique):")
                                    for item in markup_items[:5]:
                                        safe_write(f"          - {ascii(item)}")
                                    if len(markup_items) > 5:
                                        safe_write(f"          ... and {len(markup_items) - 5} more")

                                # Repeated characters
                                repeated_items = [e["instance"] for e in pd.get("repeated_chars", [])]
                                if repeated_items:
                                    safe_write(f"        Repeated characters ({len(repeated_items)} unique):")
                                    for item in repeated_items[:5]:
                                        safe_write(f"          - {ascii(item)}")
                                    if len(repeated_items) > 5:
                                        safe_write(f"          ... and {len(repeated_items) - 5} more")

            except Exception as report_err:
                click.echo(f"Warning: Could not generate full analysis report ({report_err})")

        # Write unique items file (always)
        unique_set = set()
        for ext_key in consolidated_dict:
            for entry in consolidated_dict[ext_key]:
                for item in entry.get("items", []):
                    if item:
                        unique_set.add(item)
        sorted_items = sorted(unique_set)
        with open(UNIQUE_ITEMS_FILE, 'w', encoding='utf-8') as f:
            for item in sorted_items:
                f.write(item + '\n')
        if verbose:
            click.echo(f"Wrote {len(sorted_items)} unique items to {UNIQUE_ITEMS_FILE}")

        # Write unique source items file (raw parsed items before processing)
        # Sort by first alphabetical string (case-insensitive), preserving original case in output
        sorted_source_items = sorted(source_items, key=extract_first_alpha_string)
        with open(UNIQUE_SOURCE_ITEMS_FILE, 'w', encoding='utf-8') as f:
            for item in sorted_source_items:
                f.write(item + '\n')
        if verbose:
            click.echo(f"Wrote {len(sorted_source_items)} unique source items to {UNIQUE_SOURCE_ITEMS_FILE}")

        return True

    except Exception as e:
        click.echo(format_error("summarize", f"Error writing results.json: {e}"), err=True)
        return False


def matches_model_pattern(model_name, pattern):
    """
    Check if model name matches pattern.
    Supports:
    - Exact matches: haiku matches claudehaiku4520251001
    - Wildcards: gpt*, *llama*, t0*
    - Case-insensitive matching
    """
    model_lower = model_name.lower()
    pattern_lower = pattern.lower()

    # If pattern contains wildcards, use fnmatch
    if '*' in pattern_lower or '?' in pattern_lower:
        return fnmatch(model_lower, pattern_lower)

    # Otherwise, check if pattern is contained in model name (case-insensitive substring match)
    # This allows "haiku" to match "claudehaiku4520251001"
    return pattern_lower in model_lower


def prompt_for_selections(title, choices):
    """
    Prompt user to select from a list of choices by number or space-separated numbers.
    Returns list of selected items.
    """
    if not choices:
        return []

    click.echo(f"\n{title}:")
    for idx, choice in enumerate(choices, 1):
        click.echo(f"  {idx:2d}. {choice}")
    click.echo(f"   0. (none/skip)")

    while True:
        selection = click.prompt("Enter number(s) separated by spaces", default='0').strip()
        if selection == '0' or selection == '':
            return []

        try:
            selected_indices = [int(x) - 1 for x in selection.split()]
            # Validate indices
            if any(idx < 0 or idx >= len(choices) for idx in selected_indices):
                click.echo("Invalid selection. Please enter valid numbers.")
                continue
            return [choices[idx] for idx in selected_indices]
        except ValueError:
            click.echo("Invalid input. Please enter space-separated numbers.")


def collect_available_values():
    """Scan results directory and collect available experiments, models, temperatures."""
    experiments = set()
    models = set()
    temperatures = set()

    for file_path in RESULTS_DIR.iterdir():
        if not file_path.is_file():
            continue
        if file_path.name in SKIP_PATTERNS:
            continue
        if not is_standard_filename(file_path.name):
            continue

        try:
            metadata = parse_filename_metadata(file_path.name)
            if metadata.get("experiment"):
                experiments.add(metadata["experiment"])
            if metadata.get("model"):
                models.add(metadata["model"])
            if metadata.get("temperature") is not None:
                temperatures.add(metadata["temperature"])
        except Exception:
            pass

    return sorted(experiments), sorted(models), sorted(temperatures)


@click.command()
@click.option('--filter', type=str, default=None,
              help='Filter files by string in filename (legacy, e.g., "experiment1")')
@click.option('-e', '--experiment', type=str, default=None,
              help='Filter by experiment name (e.g., "animals5")')
@click.option('-x', '--exclude-model', type=str, multiple=True, default=None,
              help='Exclude models by pattern (supports wildcards: gpt*, *llama*, etc.)')
@click.option('--model', type=str, default=None,
              help='Filter to include ONLY this model (e.g., "gpt4", "llama318b")')
@click.option('--format', 'format_type', type=str, default=None,
              help='Filter by file format (e.g., "json", "txt", "md")')
@click.option('--timestamp', type=str, default=None,
              help='Filter by timestamp (e.g., "202602061922")')
@click.option('--temperature', type=float, default=None,
              help='Filter by temperature (e.g., "1.0", "0.7")')
@click.option('--max-item-length', type=int, default=25,
              help='Maximum allowed item length in characters (items longer are flagged)')
@click.option('-a', '--analysis', is_flag=True, default=True,
              help='Generate data analysis report by model and temperature')
@click.option('--no-prompt', is_flag=True, default=False,
              help='Skip interactive prompting (use defaults or cli args only)')
@click.option('-v', '--verbose', is_flag=True, default=False,
              help='Show detailed summary output')
def main(filter, experiment, exclude_model, model, format_type, timestamp, temperature, max_item_length, analysis, no_prompt, verbose):
    """Summarize result files into a single JSON by type and parsed items."""
    # If no filters specified and not in --no-prompt mode, offer interactive selection
    if not no_prompt and not any([filter, experiment, exclude_model, model, format_type, timestamp, temperature is not None]):
        click.echo("No filters specified. Starting interactive mode...\n")
        experiments, models, temperatures = collect_available_values()

        # Prompt for experiment
        selected_experiments = prompt_for_selections("Available Experiments", experiments)
        if selected_experiments:
            experiment = selected_experiments[0] if len(selected_experiments) == 1 else None
            if len(selected_experiments) > 1:
                click.echo(f"Selected experiments: {', '.join(selected_experiments)}")
                click.echo("(Note: summarize currently supports filtering by one experiment at a time)")

        # Prompt for models to exclude
        exclude_models_list = prompt_for_selections("Available Models to Exclude", models)
        if exclude_models_list:
            exclude_model = tuple(exclude_models_list)

    success = summarize_results(filter, model, format_type, experiment, timestamp, temperature, max_item_length, analysis, exclude_model, verbose)
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
