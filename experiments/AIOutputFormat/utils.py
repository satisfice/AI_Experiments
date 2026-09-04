"""Utility functions for the AIOutputFormat project."""

import re
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter


def format_error(program_name: str, message: str) -> str:
    """
    Format an error message with program name and timestamp.

    Args:
        program_name: Name of the program producing the error (e.g., 'experiment', 'summarize')
        message: The error message

    Returns:
        Formatted error string: "[ProgramName] [TIMESTAMP] ERROR: message"
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"[{program_name}] [{timestamp}] ERROR: {message}"


def print_error(program_name: str, message: str, exit_code: int = None):
    """
    Print a formatted error message to stderr.

    Args:
        program_name: Name of the program producing the error
        message: The error message
        exit_code: Optional exit code (if provided, calls sys.exit)
    """
    formatted_msg = format_error(program_name, message)
    sys.stderr.write(formatted_msg + "\n")
    sys.stderr.flush()
    if exit_code is not None:
        sys.exit(exit_code)


def detect_preamble_leak(item):
    """Check if item looks like LLM preamble/instruction text.
    Matches common LLM response prefixes, items with 6+ words, and various preamble patterns."""
    # Non-string items are filtered out
    if not isinstance(item, str):
        return True

    item_lower = item.lower().strip()

    # Empty or very short
    if len(item_lower) < 2:
        return True

    # Markdown headers (lines starting with #)
    if item.lstrip().startswith('#'):
        return True

    # Starts with common preamble phrases
    if any(item_lower.startswith(prefix) for prefix in [
        "here's", "here are", "here is", "sure", "certainly",
        "here's a", "here are the", "here is the",
        "here are some", "here's some"
    ]):
        return True

    # Contains list indicators
    if any(phrase in item_lower for phrase in [
        "list of", "the following", "are the", "is a list"
    ]):
        return True

    # Ends with colon or ellipsis (likely intro text)
    if item_lower.endswith(":") or item_lower.endswith("..."):
        return True

    # Items with 6+ words are suspicious (item names are typically 1-3 words)
    if len(item_lower.split()) >= 6:
        return True

    # Additional regex patterns for preamble indicators
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
    for pattern in preamble_patterns:
        if re.search(pattern, item_lower):
            return True

    return False


def format_timestamp(timestamp_str):
    """Convert timestamp to YYYY-MM-DD HH:MM:SS format.
    Supports both old 12-digit (YYYYMMDDhhmm) and new 14-digit (YYYYMMDDhhmmss) formats."""
    if not timestamp_str.isdigit():
        return timestamp_str

    if len(timestamp_str) == 12:
        return f"{timestamp_str[0:4]}-{timestamp_str[4:6]}-{timestamp_str[6:8]} {timestamp_str[8:10]}:{timestamp_str[10:12]}:00"
    elif len(timestamp_str) == 14:
        return f"{timestamp_str[0:4]}-{timestamp_str[4:6]}-{timestamp_str[6:8]} {timestamp_str[8:10]}:{timestamp_str[10:12]}:{timestamp_str[12:14]}"
    else:
        return timestamp_str


def is_standard_filename(filename):
    """Check if filename follows standard naming convention.
    Format: YYYYMMDDHHMMSS-EXPERIMENT-PROMPT-HARDNESS-MODEL-TEMP-ITERATION.EXT
    Requires 14-digit timestamp and 7+ parts (hardness code is 'fs' or 'fh')."""
    name_without_ext = Path(filename).stem
    parts = name_without_ext.split('-')

    if not parts[0].isdigit() or len(parts[0]) != 14:
        return False
    if len(parts) < 7:
        return False
    if not parts[-1].isdigit() or len(parts[-1]) != 2:
        return False
    if not parts[-2].startswith('t'):
        return False
    if parts[3] not in ('fs', 'fh'):
        return False

    return True


def calculate_statistics(counts):
    """Calculate statistics for a list of item counts.

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

    if len(counts) > 1:
        variance = sum((x - avg_count) ** 2 for x in counts) / (len(counts) - 1)
    else:
        variance = 0

    count_freq = Counter(counts)
    mode_count = count_freq.most_common(1)[0][0]

    return {
        "max": max_count,
        "min": min_count,
        "avg": round(avg_count, 2),
        "var": round(variance, 2),
        "mode": mode_count
    }
