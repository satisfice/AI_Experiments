#!/usr/bin/env python3
"""File I/O operations: reading, writing, and managing result files.

Handles JSON output, unique items files, and skip summaries."""

import json
import click
from pathlib import Path
from typing import Dict, List, Set

from process_single_file import extract_first_alpha_string

# Result file paths
RESULTS_DIR = Path("results")
RESULTS_FILE = RESULTS_DIR / "results.json"
QUALITY_FILE = RESULTS_DIR / "quality.json"
UNIQUE_ITEMS_FILE = RESULTS_DIR / "unique_items.txt"
UNIQUE_SOURCE_ITEMS_FILE = RESULTS_DIR / "unique_source_items.txt"


def write_results_and_quality_json(
    consolidated_dict: Dict,
    quality_issues_dict: Dict,
    file_count: int,
    verbose: bool
) -> None:
    """Write results.json (always) and quality.json (if any issues exist).
    Print verbose summary stats if requested."""
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(consolidated_dict, f, indent=2, ensure_ascii=False)

    if verbose:
        click.echo(f"\nConsolidated {file_count} files into {RESULTS_FILE}")
        file_types = sorted(consolidated_dict.keys())
        click.echo(f"File types: {', '.join(file_types)}")

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
        if quality_issues_dict:
            with open(QUALITY_FILE, 'w', encoding='utf-8') as f:
                json.dump(quality_issues_dict, f, indent=2, ensure_ascii=False)


def print_skip_summary(skipped_trials: List[str], zero_item_files: List[str]) -> None:
    """Print the skipped-trials and zero-item-files summaries, if any."""
    if skipped_trials:
        click.echo(f"Skipped {len(skipped_trials)} trials:")
        for trial_name in sorted(skipped_trials):
            click.echo(f"  {trial_name}")

    if zero_item_files:
        click.echo(f"Files with 0 items ({len(zero_item_files)}):")
        for filename in sorted(zero_item_files):
            click.echo(f"  {filename}")


def collect_unique_items_from_consolidated(consolidated_dict: Dict) -> Set[str]:
    """Collect all unique non-empty items from consolidated dict."""
    unique_set = set()
    for ext_key in consolidated_dict:
        for entry in consolidated_dict[ext_key]:
            for item in entry.get("items", []):
                if item:
                    unique_set.add(item)
    return unique_set


def write_items_to_file(sorted_items: List[str], file_path: Path) -> None:
    """Write sorted items to file, one per line."""
    with open(file_path, 'w', encoding='utf-8') as f:
        for item in sorted_items:
            f.write(item + '\n')


def write_unique_items_file(consolidated_dict: Dict, verbose: bool) -> None:
    """Write the unique-items file: all non-empty items across entries,
    deduplicated and sorted."""
    unique_set = collect_unique_items_from_consolidated(consolidated_dict)
    sorted_items = sorted(unique_set)
    write_items_to_file(sorted_items, UNIQUE_ITEMS_FILE)
    if verbose:
        click.echo(f"Wrote {len(sorted_items)} unique items to {UNIQUE_ITEMS_FILE}")


def write_unique_source_items_file(source_items: Set[str], verbose: bool) -> None:
    """Write raw parsed source items (before processing), sorted by first
    alphabetical string (case-insensitive), preserving original case."""
    sorted_source_items = sorted(source_items, key=extract_first_alpha_string)
    with open(UNIQUE_SOURCE_ITEMS_FILE, 'w', encoding='utf-8') as f:
        for item in sorted_source_items:
            f.write(item + '\n')
    if verbose:
        click.echo(f"Wrote {len(sorted_source_items)} unique source items to {UNIQUE_SOURCE_ITEMS_FILE}")
