#!/usr/bin/env python3
"""Format detection and style validation: what format a file's extension
implies, and whether its content's actual style matches that expectation.

Distinct from the format parsers (json_parser.py, csv_parser.py, etc.), which
extract items FROM content once its format is known. The per-format style
detectors here (_detect_json_style, _detect_html_style, _detect_csv_style,
_detect_yaml_style) are used only via the _FORMAT_VALIDATORS dispatch table
in this module, not by the parsers themselves -- detecting a style and
extracting items are different responsibilities."""

import re

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
