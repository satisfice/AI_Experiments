#!/usr/bin/env python3
"""Single-file processing: parsing, format detection, quality checking, and cleaning.

Thin orchestrator over the format-specific parser modules (json_parser,
csv_parser, yaml_parser, html_parser, text_parser, markdown_parser) and the
functionality shared across two or more of them (parsing_common, item_quality,
item_cleanup, format_detection). Re-exports the names other modules import
from here (cli_helpers, file_io, summarize, trial_loading, summarize_server)
so this split has no effect on callers."""

import re
from pathlib import Path

from utils import detect_preamble_leak, format_timestamp, is_standard_filename

from format_detection import FORMAT_MAP, detect_intended_format, detect_format_style
from item_quality import (
    extract_first_alpha_string,
    detect_leading_punctuation,
    detect_trailing_punctuation,
    detect_internal_punctuation,
    detect_markup_artifact,
    detect_repeated_chars,
    detect_case,
)
from item_cleanup import (
    clean_strip_leading_format,
    clean_strip_number_word_prefix,
    clean_remove_parenthetical,
    clean_strip_trailing_punct,
    clean_strip_doubled_punct,
    clean_strip_leading_hyphens,
    clean_lowercase,
)
from json_parser import parse_json
from csv_parser import parse_csv, csv_strip_leading_markers
from yaml_parser import parse_yaml
from html_parser import parse_html
from text_parser import parse_txt, parse_txt1
from markdown_parser import parse_md


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
