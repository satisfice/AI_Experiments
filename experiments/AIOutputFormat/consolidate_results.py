#!/usr/bin/env python3

import json
import csv
import yaml
import re
import click
from pathlib import Path
from collections import defaultdict
from io import StringIO

RESULTS_DIR = Path("results")
OUTPUT_FILE = RESULTS_DIR / "consolidated.json"
SKIP_EXTENSIONS = {".xlsx", ".log"}
SKIP_PATTERNS = {"consolidated.json", "consolidated_items.json"}

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
    """Parse text file: each line is an item."""
    return [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]


def parse_json(content):
    """Parse JSON file: if array, each element; if object, each value. Flatten any list items."""
    try:
        data = json.loads(content)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = list(data.values())
        else:
            items = [data]

        # Flatten any list items
        flattened = []
        for item in items:
            if isinstance(item, list):
                flattened.extend(item)
            else:
                flattened.append(item)
        return flattened

    except json.JSONDecodeError:
        return []


def parse_csv(content):
    """Parse CSV: single row = each item; multiple rows = first column values."""
    try:
        reader = csv.reader(StringIO(content))
        rows = list(reader)

        if not rows:
            return []

        if len(rows) == 1:
            # Single row: each item in the row is a value
            return rows[0]
        else:
            # Multiple rows: extract first column
            return [row[0] for row in rows if row]
    except Exception:
        return []


def parse_md(content):
    """Parse Markdown file: each line is an item."""
    return [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]


def parse_yaml(content):
    """Parse YAML: extract items from structure. If single item is a list, flatten it."""
    try:
        data = yaml.safe_load(content)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = list(data.values())
        else:
            items = [data] if data is not None else []

        # If only one item and it's a list, flatten it
        if len(items) == 1 and isinstance(items[0], list):
            items = items[0]

        return items

    except yaml.YAMLError:
        return []


def parse_html(content):
    """Parse HTML: extract items from <li> tags, removing all <li> tags."""
    pattern = r'<li[^>]*>(.*?)</li>'
    matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
    # Clean up HTML entities and tags
    items = []
    for match in matches:
        # Remove all HTML tags including <li> tags
        text = re.sub(r'<[^>]+>', '', match)
        # Remove any remaining <li> or </li> tags
        text = re.sub(r'</?li[^>]*>', '', text, flags=re.IGNORECASE)
        # Unescape HTML entities
        text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        text = text.strip()
        if text:
            items.append(text)
    return items


def parse_txt1(content):
    """Parse .txt1 file: each line is an item, removing leading numbers/punctuation."""
    items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    # Remove leading numbers and punctuation
    cleaned = []
    for item in items:
        # Remove leading digits, dots, parentheses, hyphens, colons, etc.
        cleaned_item = re.sub(r'^[0-9\.\)\-:]+\s*', '', item)
        cleaned.append(cleaned_item)
    return cleaned


PARSERS = {
    '.txt': parse_txt,
    '.json': parse_json,
    '.csv': parse_csv,
    '.md': parse_md,
    '.yml': parse_yaml,
    '.html': parse_html,
    '.txt1': parse_txt1,
}


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
    Format: TIMESTAMP-EXPERIMENT-MODEL-TEMP-ITERATION.EXT
    Example: 202602061922-animals5-gpt4-t10-01.md
    """
    name_without_ext = Path(filename).stem
    parts = name_without_ext.split('-')

    # Need at least: timestamp, experiment, model, temp, iteration
    if len(parts) < 5:
        return False

    # Check timestamp (12 digits)
    if not parts[0].isdigit() or len(parts[0]) != 12:
        return False

    # Check iteration is 2 digits
    if not parts[-1].isdigit() or len(parts[-1]) != 2:
        return False

    # Check temperature starts with 't'
    if not parts[-2].startswith('t'):
        return False

    return True


def parse_filename_metadata(filename):
    """
    Extract experiment, model, and temperature from filename.
    Filename format: TIMESTAMP-EXPERIMENT-MODEL-TEMP-ITERATION.EXT
    Examples:
      - 202602061922-animals5-gpt-4-t10-01.md (old format with hyphen in model)
      - 202602061922-animals5-gpt4-t10-01.md (new format without hyphen in model)
    Returns dict with experiment, model, temperature (as float or None), and iteration.
    """
    # Remove extension
    name_without_ext = Path(filename).stem

    # Split by hyphen
    parts = name_without_ext.split('-')

    if len(parts) < 4:
        return {}

    # First part is timestamp (12 chars)
    # Second part is experiment
    # Last 2 parts are temperature (starts with 't') and iteration (2 digits)
    # Everything in between is the model

    experiment = parts[1]

    # Work backwards: find temperature and iteration
    iteration = parts[-1]
    temp_part = parts[-2]

    # Verify we have a valid temperature part (starts with 't')
    if not temp_part.startswith('t'):
        # No valid temperature found, assume model might extend further
        return {
            "experiment": experiment,
        }

    # Everything from index 2 to -2 is the model
    model = '-'.join(parts[2:-2])

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

    return {
        "experiment": experiment,
        "model": model,
        "temperature": temperature,
        "iteration": int(iteration) if iteration.isdigit() else None,
    }


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


def process_and_track(items, ext):
    """
    Process items (trim, remove bullets/numbers, lowercase) and track what processing was applied.
    Returns (processed_items, processing_metadata, metadata).
    """
    processing = {
        "leadingBullets": "none",
        "leadingNumbers": "none",
        "listItemTags": "none",
        "consistentCase": True,
        "case": "lower",
    }

    if not items:
        metadata = {
            "itemCount": 0,
            "alphabeticalOrder": True
        }
        return items, processing, metadata

    # Trim items
    trimmed = trim_items(items)

    # Check alphabetical order of trimmed (original) items
    alphabetical = is_alphabetical_order(trimmed)

    # Detect case pattern in original items (before processing)
    case_type, consistent_case = detect_case(trimmed)
    processing["case"] = case_type
    processing["consistentCase"] = consistent_case

    # Process items based on file type
    processed = []

    for item in trimmed:
        processed_item = item

        # Remove leading bullets (dashes only, for markdown files)
        if ext == '.md':
            if re.match(r'^-\s+', processed_item):
                processing["leadingBullets"] = "removed"
                processed_item = re.sub(r'^-\s+', '', processed_item)

        # Remove leading numbers (for txt and md files)
        if ext in {'.txt', '.md'}:
            if re.match(r'^[0-9]+[\.\):\-]\s+', processed_item):
                processing["leadingNumbers"] = "removed"
                processed_item = re.sub(r'^[0-9]+[\.\):\-]\s+', '', processed_item)

        # Lowercase the item
        processed_item = processed_item.lower()

        processed.append(processed_item)

    # Track HTML tag removal (tags removed during parsing)
    if ext == '.html':
        processing["listItemTags"] = "removed"

    # Track txt1 leading number removal (removed during parsing)
    if ext == '.txt1':
        processing["leadingNumbers"] = "removed"

    # Create metadata
    metadata = {
        "itemCount": len(processed),
        "alphabeticalOrder": alphabetical
    }

    return processed, processing, metadata


def consolidate_results(filename_filter=None, model=None, format_type=None, experiment=None, timestamp=None, temperature=None):
    """
    Read all result files by type, parse items, and consolidate into a single JSON.
    Structure: {filetype: [{filename: str, items: [...]}, ...], ...}

    Args:
        filename_filter: Optional string to filter files by name (legacy, e.g., "experiment1")
        model: Optional model name to filter by (e.g., "gpt4")
        format_type: Optional file format/extension to filter by (e.g., "json", "txt")
        experiment: Optional experiment name to filter by (e.g., "animals5")
        timestamp: Optional timestamp to filter by (e.g., "202602061922")
        temperature: Optional temperature to filter by (e.g., "1.0", "10" as int/10)
    """
    if not RESULTS_DIR.exists():
        click.echo(f"Error: {RESULTS_DIR} directory not found", err=True)
        return False

    consolidated = defaultdict(list)
    file_count = 0
    skip_count = 0

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
            items = parser(content)
            # Process and track normalization
            items, processing, metadata = process_and_track(items, ext)

            # Merge processing into metadata
            metadata.update(processing)

            # Add format from extension
            metadata["format"] = FORMAT_MAP.get(ext, "unknown")

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

    # Write consolidated JSON
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(consolidated_dict, f, indent=2, ensure_ascii=False)

        click.echo(f"\nConsolidated {file_count} files into {OUTPUT_FILE}")
        click.echo(f"File types: {', '.join(sorted(consolidated_dict.keys()))}")
        total_items = 0
        for ext, items in sorted(consolidated_dict.items()):
            item_count = sum(len(entry['items']) for entry in items)
            total_items += item_count
            click.echo(f"  {ext}: {len(items)} files, {item_count} items")

        click.echo(f"Total items: {total_items}")

        if skip_count > 0:
            click.echo(f"Skipped {skip_count} files")

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
def main(filter, model, format_type, experiment, timestamp, temperature):
    """Consolidate result files into a single JSON by type and parsed items."""
    success = consolidate_results(filter, model, format_type, experiment, timestamp, temperature)
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
