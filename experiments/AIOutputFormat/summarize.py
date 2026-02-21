#!/usr/bin/env python3

import json
import csv
import yaml
import re
import sys
import click
from pathlib import Path
from collections import defaultdict, Counter
from io import StringIO
from config import abbreviate_model_name

RESULTS_DIR = Path("results")
RESULTS_FILE = RESULTS_DIR / "results.json"
QUALITY_FILE = RESULTS_DIR / "quality.json"
UNIQUE_ITEMS_FILE = RESULTS_DIR / "unique_items.txt"
UNIQUE_SOURCE_ITEMS_FILE = RESULTS_DIR / "unique_source_items.txt"
SKIP_EXTENSIONS = {".xlsx", ".log"}
SKIP_PATTERNS = {"results.json", "quality.json", "unique_items.txt", "unique_source_items.txt", "consolidated.json", "consolidated_items.json"}

# Map file extensions to format types
FORMAT_MAP = {
    '.txt': 'text',
    '.txt1': 'numberedText',
    '.json': 'JSON',
    '.yml': 'YAML',
    '.html': 'HTML',
    '.csv': 'CSV',
    '.md': 'markdown',
}


def parse_txt(content):
    """Parse text file: each line is an item.
    Returns (items, fixups)."""
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    fixups = []
    return items, fixups


def parse_json(content):
    """Parse JSON file: if array, each element; if object, each value. Flatten any list items.
    Handles JSON with Python dict syntax (non-standard format) (single quotes instead of double quotes).
    Extracts values from list of dicts with common keys.
    Returns (items, fixups)."""
    fixups = []
    try:
        data = json.loads(content)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = list(data.values())
            fixups.append("Extracted values from JSON object")
        else:
            items = [data]

        # Check if items are dicts with a common key that should be extracted
        items = _extract_from_dict_list(items, fixups)

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
            fixups.append("Flattened nested list items")

        return flattened, fixups

    except json.JSONDecodeError as e:
        # Try to fix non-standard JSON (Python dict syntax) (single quotes)
        if "'" in content and "{" in content:
            try:
                # Replace single quotes with double quotes for dict/list syntax
                # This is a heuristic that works for simple Python dicts in JSON
                fixed_content = content.replace("'", '"')
                data = json.loads(fixed_content)

                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = list(data.values())
                else:
                    items = [data]

                # Check if items are dicts with a common key that should be extracted
                items = _extract_from_dict_list(items, fixups)

                # Flatten any list items
                flattened = []
                for item in items:
                    if isinstance(item, list):
                        flattened.extend(item)
                    else:
                        flattened.append(item)

                fixups.append("Converted non-standard JSON (Python dict syntax) to valid JSON")
                return flattened, fixups
            except json.JSONDecodeError:
                # If the fix didn't work, return empty list with error note
                fixups.append("Non-standard JSON with Python dict syntax - could not repair")
                return [], fixups
        else:
            fixups.append("JSON parse error - returned empty list")
            return [], fixups


def _extract_from_dict_list(items, fixups):
    """
    If items is a list of dicts with a common key, extract values from that key.
    For example: [{"name": "lion"}, {"name": "tiger"}] -> ["lion", "tiger"]
    Returns modified items list and updates fixups list.
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
            fixups.append(f"Extracted '{key}' values from JSON dict list")
            return extracted

    return items


def parse_csv(content):
    """Parse CSV: single row = each item; multiple rows = first column values.
    Removes quote characters that are part of CSV formatting.
    Returns (items, fixups)."""
    fixups = []
    try:
        reader = csv.reader(StringIO(content))
        rows = list(reader)

        if not rows:
            return [], fixups

        if len(rows) == 1:
            # Single row: each item in the row is a value
            items = rows[0]
            fixups.append("Parsed single-row CSV - each column as item")
        else:
            # Multiple rows: extract first column
            items = [row[0] for row in rows if row]
            fixups.append("Parsed multi-row CSV - extracted first column")

        # Clean up items: remove leading/trailing quotes and spaces if they're CSV formatting artifacts
        cleaned_items = []
        removed_quotes_count = 0
        for item in items:
            original = item
            # Strip whitespace first, then remove quotes
            cleaned = item.strip()  # Remove leading/trailing spaces
            # Remove outer quotes (handle both single and double quotes)
            while cleaned and cleaned[0] in ('"', "'") and cleaned[-1] == cleaned[0]:
                cleaned = cleaned[1:-1].strip()
            if cleaned != original:
                removed_quotes_count += 1
            cleaned_items.append(cleaned)

        if removed_quotes_count > 0:
            fixups.append(f"Removed CSV quote formatting from {removed_quotes_count} items")

        return cleaned_items, fixups

    except Exception as e:
        fixups.append(f"CSV parse error: {type(e).__name__}")
        return [], fixups


def parse_md(content):
    """Parse Markdown file: each line is an item, with markdown bullets and headers removed.
    Returns (items, fixups) where fixups is a list of cleanup operations performed."""
    items = []
    fixups = []
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
        fixups.append(f"Skipped {header_count} markdown header(s)")
    if bullet_count > 0:
        fixups.append(f"Removed markdown bullets from {bullet_count} line(s)")

    return items, fixups


def parse_yaml(content):
    """Parse YAML: extract items from structure. If single item is a list, flatten it.
    Falls back to text parsing if content looks like plain text list.
    Returns (items, fixups)."""
    fixups = []
    try:
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
            fixups.append("Parsed as plain text list (no YAML syntax detected)")
        else:
            # Parse as YAML
            data = yaml.safe_load(content)
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = list(data.values())
                fixups.append("Extracted values from YAML object")
            elif isinstance(data, str):
                # YAML parsed as a plain string (likely non-standard YAML with numbered/bulleted lines)
                # Try to parse as text with numbered items (1. item 2. item 3. item, etc.)
                # Look for pattern: digit(s) followed by period and space
                numbered_items = re.findall(r'\d+\.\s+([^0-9]+?)(?=\d+\.\s|$)', data)
                if numbered_items:
                    # Found numbered items - use them, stripping whitespace
                    items = [item.strip() for item in numbered_items if item.strip()]
                    fixups.append("Extracted numbered items from YAML string")
                else:
                    # No numbered items found, treat as single item
                    items = [data] if data else []
            else:
                items = [data] if data is not None else []

        # If only one item and it's a list, flatten it
        if len(items) == 1 and isinstance(items[0], list):
            items = items[0]
            fixups.append("Flattened single-item list")

        return items, fixups

    except yaml.YAMLError as e:
        fixups.append(f"YAML parse error - {type(e).__name__}")
        return [], fixups


def parse_html(content):
    """Parse HTML: extract items from <li> tags, removing all <li> tags.
    Returns (items, fixups)."""
    fixups = []
    pattern = r'<li[^>]*>(.*?)</li>'
    matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
    # Clean up HTML entities and tags
    items = []
    html_tag_count = 0
    entity_count = 0

    for match in matches:
        original = match
        # Remove all HTML tags including <li> tags
        text = re.sub(r'<[^>]+>', '', match)
        text = re.sub(r'</?li[^>]*>', '', text, flags=re.IGNORECASE)
        if text != original:
            html_tag_count += 1
        # Unescape HTML entities
        entity_refs = len(re.findall(r'&(?:lt|gt|amp);', text))
        if '&' in text:
            text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            entity_count += entity_refs
        text = text.strip()
        if text:
            items.append(text)

    if matches:
        fixups.append(f"Extracted {len(matches)} items from <li> tags")
    if html_tag_count > 0:
        fixups.append("Removed HTML tags")
    if entity_count > 0:
        fixups.append(f"Unescaped {entity_count} HTML entities")

    return items, fixups


def parse_txt1(content):
    """Parse .txt1 file: each line is an item, removing leading numbers/punctuation.
    Returns (items, fixups)."""
    fixups = []
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    # Remove leading numbers and punctuation
    cleaned = []
    removed_count = 0
    for item in items:
        # Remove leading digits, dots, parentheses, hyphens, colons, etc.
        cleaned_item = re.sub(r'^[0-9\.\)\-:]+\s*', '', item)
        if cleaned_item != item:
            removed_count += 1
        cleaned.append(cleaned_item)

    if removed_count > 0:
        fixups.append(f"Removed leading numbers/punctuation from {removed_count} items")

    return cleaned, fixups


PARSERS = {
    '.txt': parse_txt,
    '.json': parse_json,
    '.csv': parse_csv,
    '.md': parse_md,
    '.yml': parse_yaml,
    '.html': parse_html,
    '.txt1': parse_txt1,
}


def extract_code_block(content):
    """
    Extract content from markdown code blocks (```...```).
    Looks for code blocks with optional language specifier (```json, ```yaml, etc).
    Returns (extracted_content, had_codeblock, fixups) tuple.
    If code blocks found, returns the content inside them.
    If no code blocks found, returns original content.
    """
    fixups = []
    # Look for markdown code blocks with ``` delimiters
    code_block_pattern = r'```[\w]*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, content, re.DOTALL)

    if matches:
        # Found code blocks - concatenate content from all blocks
        extracted = '\n'.join(matches)
        fixups.append(f"Extracted {len(matches)} code block(s)")
        return extracted, True, fixups
    else:
        # No code blocks found
        return content, False, fixups


def reorder_metadata(metadata):
    """Reorder metadata keys: time, experiment, prompt, model, temperature, format, iteration, codeblock, fixups, then others.
    Note: prompt is optional for old format files. codeblock and fixups are optional."""
    key_order = ["time", "experiment", "prompt", "model", "temperature", "format", "iteration", "codeblock", "fixups"]
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
    - New: YYYYMMDDHHMMSS-PROMPT-EXPERIMENT-MODEL-TEMP-ITERATION.EXT (14-digit timestamp, 6+ parts)
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
        # New format: need at least 6 parts (timestamp, prompt, experiment, model, temp, iteration)
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
    - New: TIMESTAMP-PROMPT-EXPERIMENT-MODEL-TEMP-ITERATION.EXT (14-digit timestamp)
      Example: 20250216160215-animals-test1-gpt4-t10-01.md
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
        # New format: TIMESTAMP-PROMPT-EXPERIMENT-MODEL-TEMP-ITERATION
        if len(parts) < 6:
            return {"time": timestamp}

        prompt = parts[1]
        experiment = parts[2]
        model = '-'.join(parts[3:-2])

    elif is_old_format:
        # Old format: TIMESTAMP-EXPERIMENT-MODEL-TEMP-ITERATION (no prompt)
        if len(parts) < 5:
            return {"time": timestamp}

        prompt = None
        experiment = parts[1]
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


def has_inappropriate_punctuation(item):
    """
    Check if item has inappropriate or excessive punctuation.
    After format-specific parsing is complete, any remaining punctuation is problematic.

    Allows: letters (including diacriticals), digits, spaces, apostrophes, hyphens
    Disallows: other punctuation marks like }, ::, **, etc.

    Examples:
    - '10. Crocodile' in CSV with quotes: excessive (redundant formatting)
    - 'Animals**': inappropriate (markdown bold markers left over)
    """
    for char in item:
        # Allow: letters (including diacriticals via isalpha), digits, spaces, apostrophe, hyphen
        if not (char.isalpha() or char.isdigit() or char in " '-"):
            return True
    return False


def classify_inappropriate_pattern(item):
    """
    Classify the type of inappropriate punctuation pattern.
    Returns a class identifier (e.g., 'asterisk', 'parenthesis', 'colon', etc.)
    """
    # Check for specific patterns
    if '**' in item or '*' in item:
        return 'asterisk'
    if '::' in item:
        return 'double_colon'
    if '}' in item:
        return 'brace'
    if '<' in item or '>' in item:
        return 'angle_bracket'
    if '(' in item or ')' in item:
        return 'parenthesis'
    if '{' in item:
        return 'curly_brace'
    if '/' in item:
        return 'slash'
    if ',' in item:
        return 'comma'
    if '.' in item:
        return 'period'
    if '!' in item:
        return 'exclamation'
    if '?' in item:
        return 'question'
    # Default for any other punctuation
    return 'other_punctuation'


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


def clean_format_specific(items, ext):
    """
    Clean up format-specific formatting FIRST (before quality checks).
    Removes bullets, numbers, HTML tags appropriate to the file format.
    Returns (cleaned_items, fixups_list).
    """
    fixups = []
    cleaned = []
    removed_leading_numbers = False
    removed_leading_punctuation = False
    removed_html_tags = False

    for item in items:
        original = item
        cleaned_item = item

        # Remove HTML tags from HTML and markdown files
        if ext in ['.html', '.md']:
            before_html = cleaned_item
            cleaned_item = re.sub(r'</?[a-zA-Z][^>]*>?', '', cleaned_item)
            cleaned_item = re.sub(r'^>+|>+$', '', cleaned_item).strip()
            if cleaned_item != before_html:
                removed_html_tags = True

        # For markdown, text, and CSV files, remove leading bullets and numbers (format-specific)
        if ext in ['.md', '.txt', '.csv']:
            before_list = cleaned_item
            # Remove leading bullets/list markers (* - +) and whitespace
            cleaned_item = re.sub(r'^[\s*\-+]+', '', cleaned_item).strip()
            # Remove leading numbers with punctuation (1. 2) 3: etc.)
            cleaned_item = re.sub(r'^\d+[\.\):\-\s]+', '', cleaned_item).strip()
            if cleaned_item != before_list:
                if re.match(r'^\d+[\.\):\-\s]', before_list):
                    removed_leading_numbers = True
                else:
                    removed_leading_punctuation = True

        # Check if remaining text is in quotes (markdown issue)
        if cleaned_item and re.match(r'^["\'].*["\']$', cleaned_item):
            # Text is embedded in quotes - this is inappropriate punctuation for markdown
            pass  # Will be caught as inappropriate_punctuation later

        cleaned.append(cleaned_item)

    if removed_leading_numbers:
        if removed_leading_punctuation:
            fixups.append("Removed leading numbers and punctuation")
        else:
            fixups.append("Removed leading numbers")
    elif removed_leading_punctuation:
        fixups.append("Removed leading punctuation")

    if removed_html_tags:
        fixups.append("Removed stray HTML tags from items")

    return cleaned, fixups


def process_and_track(items, ext, max_item_length=25):
    """
    Process items in the correct order:
    1. Trim items
    2. Detect leading numbers/bullets in original items
    3. Clean format-specific formatting
    4. Check quality issues on cleaned items
    5. Continue with other processing

    Args:
        items: List of items to process
        ext: File extension
        max_item_length: Maximum allowed item length (items longer are flagged in fixups)
    Returns (processed_items, processing_metadata, metadata).
    """
    processing = {
        "leadingBullets": False,
        "leadingNumbers": False,
        "listItemTags": False,
        "consistentCase": True,
        "case": "lower",
    }
    quality_issues = {
        "inappropriate_punctuation": [],
        "exceeds_max_length": [],
        "preamble_leak": [],
        "markup_artifact": [],
        "repeated_chars": [],
    }
    processing_fixups = []

    if not items:
        metadata = {
            "itemCount": 0,
            "alphabeticalOrder": True
        }
        return items, processing, metadata

    # Step 1: Trim items
    trimmed = trim_items(items)

    # Step 2: Detect leading numbers and bullets in ORIGINAL items (before cleaning)
    has_leading_bullets = False
    has_leading_numbers = False
    for item in trimmed:
        # Bullets: asterisks, hyphens, plus signs at the start (possibly with spaces/dots)
        if re.match(r'^[\s*\-+]+', item):
            has_leading_bullets = True
        # Numbers: digits followed by punctuation (. ) : - space) at the start
        # For markdown, "1. Dog" should be flagged as leading numbers
        if re.match(r'^\d+[\.\):\-\s]', item):
            has_leading_numbers = True

    processing["leadingBullets"] = has_leading_bullets
    processing["leadingNumbers"] = has_leading_numbers

    # Step 3: Clean format-specific formatting FIRST
    cleaned_items, format_fixups = clean_format_specific(trimmed, ext)
    if format_fixups:
        processing_fixups.extend(format_fixups)

    # After format-specific cleaning, check for HTML tags in HTML files
    if ext == '.html':
        processing["listItemTags"] = True

    # Check for quality issues with leading digits attached to letters (e.g., "48eagle")
    quality_problem_items = []
    for item in cleaned_items:
        if re.match(r'^\d+[a-zA-Z]', item):
            quality_problem_items.append(item)

    if quality_problem_items:
        processing["qualityIssueNumberWord"] = True

    # Check alphabetical order of original items
    alphabetical = is_alphabetical_order(trimmed)

    # Detect case pattern in original items
    case_type, consistent_case = detect_case(trimmed)
    processing["case"] = case_type
    processing["consistentCase"] = consistent_case

    # Step 4: Check for quality issues on CLEANED items (after format-specific removal)
    # For markdown, "1. Dog" won't be in cleaned_items anymore, so we won't flag it as inappropriate
    for item in cleaned_items:
        # Check for inappropriate punctuation
        # Allowed: letters (including diacriticals), digits, spaces, apostrophes, hyphens
        # Disallowed: other punctuation marks
        if has_inappropriate_punctuation(item):
            quality_issues["inappropriate_punctuation"].append(item)

        # Check for items exceeding maximum length
        if len(item) > max_item_length:
            quality_issues["exceeds_max_length"].append(item)

        # Check for LLM preamble leaks
        if detect_preamble_leak(item):
            quality_issues["preamble_leak"].append(item)

        # Check for residual markup
        if detect_markup_artifact(item):
            quality_issues["markup_artifact"].append(item)

        # Check for repeated characters (likely typos)
        if detect_repeated_chars(item):
            quality_issues["repeated_chars"].append(item)

    # Note: misspelling detection is deferred to a second pass in summarize_results()
    # after the corpus word frequency table is built across all files.

    # Step 5: Process cleaned items further (additional cleanup, lowercase)
    # First, filter out items with quality issues
    processed = []

    for item in cleaned_items:
        # Skip items with inappropriate punctuation, preamble leaks, or markup artifacts
        if item in quality_issues["inappropriate_punctuation"]:
            continue
        if item in quality_issues["preamble_leak"]:
            continue
        if item in quality_issues["markup_artifact"]:
            continue

        processed_item = item

        # Continue with additional cleanup for all items
        # Handle numbers directly attached to letters (e.g., "48eagle" -> "eagle")
        processed_item = re.sub(r'^[0-9]+([a-zA-Z])', r'\1', processed_item)

        processed_item = processed_item.strip()  # Strip any remaining whitespace

        # Remove parentheses and their contents (e.g., "camelopard (giraffe)" -> "camelopard")
        processed_item = re.sub(r'\s*\([^)]*\)', '', processed_item)
        processed_item = processed_item.strip()

        # Strip trailing punctuation (but keep alphanumeric at end)
        processed_item = re.sub(r'[\s\*\-+:;,\.!?\}\[\]{}()\[\]]+$', '', processed_item)
        processed_item = processed_item.strip()

        # Remove doubled and tripled punctuation that appears between letters (e.g., ti::ger -> tiger, do--g -> dog)
        # This catches cases where punctuation is doubled within words
        processed_item = re.sub(r'([a-zA-Z])([:.;,\-_/\\|])\2+([a-zA-Z])', r'\1\3', processed_item)

        # Lowercase the item
        processed_item = processed_item.lower()

        processed.append(processed_item)

    # Create metadata
    metadata = {
        "itemCount": len(processed),
        "alphabeticalOrder": alphabetical
    }

    # Store quality issue flags if any found
    if any(quality_issues[k] for k in quality_issues):
        metadata["qualityIssues"] = quality_issues

    # Store processing fixups
    if processing_fixups:
        metadata["processingFixups"] = processing_fixups

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


def summarize_results(filename_filter=None, model=None, format_type=None, experiment=None, timestamp=None, temperature=None, max_item_length=25, analysis=False):
    """
    Read all result files by type, parse items, and summarize into a single JSON.
    Structure: {filetype: [{filename: str, items: [...]}, ...], ...}

    Args:
        filename_filter: Optional string to filter files by name (legacy, e.g., "experiment1")
        model: Optional model name to filter by (e.g., "gpt4")
        format_type: Optional file format/extension to filter by (e.g., "json", "txt")
        experiment: Optional experiment name to filter by (e.g., "animals5")
        timestamp: Optional timestamp to filter by (e.g., "202602061922")
        temperature: Optional temperature to filter by (e.g., "1.0", "10" as int/10)
        max_item_length: Maximum allowed item length in characters (default 25, items longer are flagged)
    """
    if not RESULTS_DIR.exists():
        click.echo(f"Error: {RESULTS_DIR} directory not found", err=True)
        return False

    consolidated = defaultdict(list)
    # All tracked issue types
    ISSUE_TYPES = ["inappropriate_punctuation", "exceeds_max_length", "preamble_leak", "markup_artifact", "repeated_chars"]
    # Track quality issues: model -> temperature -> file_type -> issue_type -> set of items
    quality_issues_output = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {k: set() for k in ISSUE_TYPES})))
    # Track example filenames: model -> temperature -> file_type -> issue_type -> {item: filename}
    quality_issues_examples = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {k: {} for k in ISSUE_TYPES})))
    # Track item counts for statistics: model -> temperature -> file_type -> [list of item counts]
    item_count_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # Track leading bullets/numbers prevalence: model -> temperature -> file_type -> {leading_bullets: [], leading_numbers: []}
    leading_chars_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"leading_bullets": [], "leading_numbers": []})))
    file_count = 0
    skip_count = 0
    source_items = set()  # Track unique items from raw parsed data

    # Display filter parameters
    filters_applied = []
    if filename_filter:
        filters_applied.append(f"filename: {filename_filter}")
    if model:
        filters_applied.append(f"model: {model}")
    if format_type:
        filters_applied.append(f"format: {format_type}")
    if experiment:
        filters_applied.append(f"experiment: {experiment}")
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

        # Skip consolidated.json itself
        if file_path.name in SKIP_PATTERNS:
            continue

        # Skip files that don't follow standard naming convention
        if not is_standard_filename(file_path.name):
            skip_count += 1
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
            skip_count += 1
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
                skip_count += 1
                continue

        # Parse content based on file type
        if ext not in PARSERS:
            click.echo(f"Skipping (no parser): {file_path.name}")
            skip_count += 1
            continue

        try:
            parser = PARSERS[ext]

            # Extract content from code blocks if present
            cleaned_content, had_codeblock, codeblock_fixups = extract_code_block(content)

            # Parse the (possibly cleaned) content
            items, parser_fixups = parser(cleaned_content)

            # Collect source items (raw parsed items before processing)
            for item in items:
                if item:  # Only track non-empty items
                    source_items.add(item)

            # Process and track normalization
            items, processing, metadata = process_and_track(items, ext, max_item_length)

            # Merge processing into metadata
            metadata.update(processing)

            # Add codeblock flag if code blocks were found and processed
            if had_codeblock:
                metadata["codeblock"] = True

            # Add format from extension
            metadata["format"] = FORMAT_MAP.get(ext, "unknown")

            # Collect all fixups
            all_fixups = codeblock_fixups + parser_fixups

            # Add processing fixups (format-specific cleanup)
            if metadata.get("processingFixups"):
                all_fixups.extend(metadata.pop("processingFixups"))

            # Add HTML tag removal fixup if it occurred
            if processing.get("strayHTMLTags"):
                all_fixups.append("Removed stray HTML tags from items")

            if all_fixups:
                metadata["fixups"] = all_fixups

            # Parse filename for experiment, model, temperature
            filename_metadata = parse_filename_metadata(file_path.name)
            metadata.update(filename_metadata)

            # Apply metadata filters
            if model and metadata.get("model") != model:
                continue
            if experiment and metadata.get("experiment") != experiment:
                continue
            if timestamp and metadata.get("experiment", "").startswith(timestamp):
                # Check if timestamp matches (first 12 chars of timestamp part)
                file_timestamp = Path(file_path.name).stem.split('-')[0]
                if file_timestamp != timestamp:
                    continue
            if temperature is not None:
                file_temp = metadata.get("temperature")
                # Handle temperature as either float or string
                try:
                    temp_filter = float(temperature)
                    if file_temp != temp_filter:
                        continue
                except (ValueError, TypeError):
                    continue

            # Count duplicate items (items appearing more than once)
            item_counts = Counter(items)
            duplicate_count = sum(1 for count in item_counts.values() if count > 1)

            # Add duplicates to metadata before reordering
            metadata["duplicates"] = duplicate_count

            # Reorder metadata keys
            metadata = reorder_metadata(metadata)

            # Get model, temperature, file type for tracking
            model_name = abbreviate_model_name(metadata.get("model", "unknown"))
            temp_value = metadata.get("temperature", "unknown")
            file_type = FORMAT_MAP.get(ext, ext)

            # Track quality issues by model, temperature, and file type
            if "qualityIssues" in metadata:
                quality_issues = metadata["qualityIssues"]
                filename = file_path.name

                for inappropriate_item in quality_issues.get("inappropriate_punctuation", []):
                    # Skip leading numbers for txt1 files (they're expected in numbered text)
                    if ext == '.txt1' and re.match(r'^\d+[\.\)\-\s]', inappropriate_item):
                        continue
                    quality_issues_output[model_name][temp_value][file_type]["inappropriate_punctuation"].add(inappropriate_item)
                    # Store example filename (keep first occurrence)
                    # Use str(temp_value) to match the keys used in quality_issues_dict for reporting
                    if inappropriate_item not in quality_issues_examples[model_name][str(temp_value)][file_type]["inappropriate_punctuation"]:
                        quality_issues_examples[model_name][str(temp_value)][file_type]["inappropriate_punctuation"][inappropriate_item] = filename
                for long_item in quality_issues.get("exceeds_max_length", []):
                    quality_issues_output[model_name][temp_value][file_type]["exceeds_max_length"].add(long_item)
                    if long_item not in quality_issues_examples[model_name][str(temp_value)][file_type]["exceeds_max_length"]:
                        quality_issues_examples[model_name][str(temp_value)][file_type]["exceeds_max_length"][long_item] = filename
                for preamble_item in quality_issues.get("preamble_leak", []):
                    quality_issues_output[model_name][temp_value][file_type]["preamble_leak"].add(preamble_item)
                    if preamble_item not in quality_issues_examples[model_name][str(temp_value)][file_type]["preamble_leak"]:
                        quality_issues_examples[model_name][str(temp_value)][file_type]["preamble_leak"][preamble_item] = filename
                for markup_item in quality_issues.get("markup_artifact", []):
                    quality_issues_output[model_name][temp_value][file_type]["markup_artifact"].add(markup_item)
                    if markup_item not in quality_issues_examples[model_name][str(temp_value)][file_type]["markup_artifact"]:
                        quality_issues_examples[model_name][str(temp_value)][file_type]["markup_artifact"][markup_item] = filename
                for repeated_item in quality_issues.get("repeated_chars", []):
                    quality_issues_output[model_name][temp_value][file_type]["repeated_chars"].add(repeated_item)
                    if repeated_item not in quality_issues_examples[model_name][str(temp_value)][file_type]["repeated_chars"]:
                        quality_issues_examples[model_name][str(temp_value)][file_type]["repeated_chars"][repeated_item] = filename

            # Count duplicate items (items appearing more than once)
            item_counts = Counter(items)
            duplicate_count = sum(1 for count in item_counts.values() if count > 1)

            # Track item count for statistics
            item_count_stats[model_name][str(temp_value)][file_type].append(len(items))

            # Track leading bullets and numbers prevalence
            leading_chars_stats[model_name][str(temp_value)][file_type]["leading_bullets"].append(metadata.get("leadingBullets", False))
            leading_chars_stats[model_name][str(temp_value)][file_type]["leading_numbers"].append(metadata.get("leadingNumbers", False))

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
            skip_count += 1
            continue

    # Convert defaultdict to regular dict for JSON serialization
    consolidated_dict = dict(consolidated)

    # Convert quality_issues_output sets to sorted lists with source info for JSON serialization
    # Structure: model -> temperature -> file_type -> issue_type -> [{"instance": item, "source": filename}, ...]
    quality_issues_dict = {}
    if quality_issues_output or analysis:
        for model_name in sorted(quality_issues_output.keys()):
            quality_issues_dict[model_name] = {}
            for temp_value in sorted(quality_issues_output[model_name].keys(), key=lambda x: (x == "unknown", x)):
                quality_issues_dict[model_name][str(temp_value)] = {}
                for file_type in sorted(quality_issues_output[model_name][temp_value].keys()):
                    quality_issues_dict[model_name][str(temp_value)][file_type] = {}
                    for issue_type in ISSUE_TYPES:
                        # Create list of objects with instance and source
                        items_with_source = []
                        for item in quality_issues_output[model_name][temp_value][file_type][issue_type]:
                            source = quality_issues_examples[model_name][str(temp_value)][file_type][issue_type].get(item, "unknown")
                            items_with_source.append({
                                "instance": item,
                                "source": source
                            })
                        # Sort by instance (case-insensitive)
                        items_with_source.sort(key=lambda x: x["instance"].lower())
                        quality_issues_dict[model_name][str(temp_value)][file_type][issue_type] = items_with_source

    # Write results JSON (without quality issues)
    try:
        with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(consolidated_dict, f, indent=2, ensure_ascii=False)

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

        if skip_count > 0:
            click.echo(f"Skipped {skip_count} files")

        # Print analysis report for all file types per model and temperature
        if analysis:
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
                        for file_type in sorted(item_count_stats[model_name][temp_value].keys()):
                            counts = item_count_stats[model_name][temp_value][file_type]
                            stats = calculate_statistics(counts)
                            safe_write(f"    {file_type} ({len(counts)} files):")
                            safe_write(f"      Items: max={stats['max']}, min={stats['min']}, avg={stats['avg']}, var={stats['var']}, mode={stats['mode']}")

                            # Leading bullets and numbers prevalence
                            if file_type in leading_chars_stats[model_name][temp_value]:
                                bullets_data = leading_chars_stats[model_name][temp_value][file_type]["leading_bullets"]
                                numbers_data = leading_chars_stats[model_name][temp_value][file_type]["leading_numbers"]
                                if bullets_data and numbers_data:
                                    bullets_prevalence = classify_prevalence(sum(bullets_data), len(bullets_data))
                                    numbers_prevalence = classify_prevalence(sum(numbers_data), len(numbers_data))
                                    safe_write(f"      Leading bullets: {bullets_prevalence}; Leading numbers: {numbers_prevalence}")

                            # Quality issues (only if present for this combination)
                            issues = quality_issues_dict.get(model_name, {}).get(str(temp_value), {}).get(file_type, {})
                            inapp_items = issues.get("inappropriate_punctuation", [])
                            exceed_items = issues.get("exceeds_max_length", [])

                            if inapp_items:
                                pattern_groups = {}
                                for item in inapp_items:
                                    pattern = classify_inappropriate_pattern(item)
                                    if pattern not in pattern_groups:
                                        pattern_groups[pattern] = item

                                safe_write(f"      Inappropriate punctuation ({len(pattern_groups)} classes):")
                                for pattern in sorted(pattern_groups.keys())[:5]:
                                    item = pattern_groups[pattern]
                                    item_repr = ascii(item)
                                    example_file = quality_issues_examples[model_name][temp_value][file_type]["inappropriate_punctuation"].get(item)
                                    if example_file:
                                        safe_write(f"        - {item_repr} Example: {example_file}")
                                    else:
                                        safe_write(f"        - {item_repr}")
                                if len(pattern_groups) > 5:
                                    remaining_patterns = sorted(pattern_groups.keys())[5:]
                                    item = pattern_groups[remaining_patterns[0]]
                                    example_file = quality_issues_examples[model_name][temp_value][file_type]["inappropriate_punctuation"].get(item)
                                    if example_file:
                                        safe_write(f"        ... and {len(pattern_groups) - 5} more classes Example: {example_file}")
                                    else:
                                        safe_write(f"        ... and {len(pattern_groups) - 5} more classes")

                            if exceed_items:
                                safe_write(f"      Exceeds max length ({len(exceed_items)} unique):")
                                for item in exceed_items[:5]:
                                    item_repr = ascii(item)
                                    example_file = quality_issues_examples[model_name][temp_value][file_type]["exceeds_max_length"].get(item)
                                    if example_file:
                                        safe_write(f"        - {item_repr} Example: {example_file}")
                                    else:
                                        safe_write(f"        - {item_repr}")
                                if len(exceed_items) > 5:
                                    remaining_items = exceed_items[5:]
                                    example_file = quality_issues_examples[model_name][temp_value][file_type]["exceeds_max_length"].get(remaining_items[0])
                                    if example_file:
                                        safe_write(f"        ... and {len(exceed_items) - 5} more Example: {example_file}")
                                    else:
                                        safe_write(f"        ... and {len(exceed_items) - 5} more")

                            # Preamble leaks
                            preamble_items = issues.get("preamble_leak", [])
                            if preamble_items:
                                safe_write(f"      Preamble leaks ({len(preamble_items)} unique):")
                                for item in preamble_items[:5]:
                                    safe_write(f"        - {ascii(item)}")
                                if len(preamble_items) > 5:
                                    safe_write(f"        ... and {len(preamble_items) - 5} more")

                            # Markup artifacts
                            markup_items = issues.get("markup_artifact", [])
                            if markup_items:
                                safe_write(f"      Markup artifacts ({len(markup_items)} unique):")
                                for item in markup_items[:5]:
                                    safe_write(f"        - {ascii(item)}")
                                if len(markup_items) > 5:
                                    safe_write(f"        ... and {len(markup_items) - 5} more")

                            # Repeated characters
                            repeated_items = issues.get("repeated_chars", [])
                            if repeated_items:
                                safe_write(f"      Repeated characters ({len(repeated_items)} unique):")
                                for item in repeated_items[:5]:
                                    safe_write(f"        - {ascii(item)}")
                                if len(repeated_items) > 5:
                                    safe_write(f"        ... and {len(repeated_items) - 5} more")

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
        click.echo(f"Wrote {len(sorted_items)} unique items to {UNIQUE_ITEMS_FILE}")

        # Write unique source items file (raw parsed items before processing)
        # Sort by first alphabetical string (case-insensitive), preserving original case in output
        sorted_source_items = sorted(source_items, key=extract_first_alpha_string)
        with open(UNIQUE_SOURCE_ITEMS_FILE, 'w', encoding='utf-8') as f:
            for item in sorted_source_items:
                f.write(item + '\n')
        click.echo(f"Wrote {len(sorted_source_items)} unique source items to {UNIQUE_SOURCE_ITEMS_FILE}")

        return True

    except Exception as e:
        click.echo(f"Error writing consolidated JSON: {e}", err=True)
        return False


@click.command()
@click.option('--filter', type=str, default=None,
              help='Filter files by string in filename (legacy, e.g., "experiment1")')
@click.option('--model', type=str, default=None,
              help='Filter by model name (e.g., "gpt4", "llama318b")')
@click.option('--format', 'format_type', type=str, default=None,
              help='Filter by file format (e.g., "json", "txt", "md")')
@click.option('--experiment', type=str, default=None,
              help='Filter by experiment name (e.g., "animals5")')
@click.option('--timestamp', type=str, default=None,
              help='Filter by timestamp (e.g., "202602061922")')
@click.option('--temperature', type=float, default=None,
              help='Filter by temperature (e.g., "1.0", "0.7")')
@click.option('--max-item-length', type=int, default=25,
              help='Maximum allowed item length in characters (items longer are flagged)')
@click.option('-a', '--analysis', is_flag=True, default=False,
              help='Generate data analysis report by model and temperature')
def main(filter, model, format_type, experiment, timestamp, temperature, max_item_length, analysis):
    """Summarize result files into a single JSON by type and parsed items."""
    success = summarize_results(filter, model, format_type, experiment, timestamp, temperature, max_item_length, analysis)
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
