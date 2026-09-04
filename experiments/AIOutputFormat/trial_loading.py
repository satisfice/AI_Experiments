#!/usr/bin/env python3
"""Trial loading and metadata parsing: reading and processing result files.

Handles file loading, parsing, metadata extraction, and trial aggregation."""

import json
import click
import traceback
from pathlib import Path
from collections import Counter, defaultdict
from typing import Optional, Tuple, List

from config import abbreviate_model_name
from process_single_file import (
    extract_code_block, parse_filename_metadata, parse_cleanup_keys,
    detect_format_style, reorder_metadata, FORMAT_MAP, PARSERS, process_and_track
)
from summarize import (
    Trial, QualityContext, _track_item_quality_issues,
    _make_issue_output_dicts, _make_format_style_counts, _make_cleanup_rules_agg,
    _make_four_level_defaultdict, _make_format_aggregation_dicts,
    AggregationState, SKIP_EXTENSIONS, _read_result_file_content,
    _should_attempt_result_file, _passes_metadata_filters, META_DIR
)

RESULTS_DIR = Path("results")


def create_trial_from_file(
    file_path: Path,
    max_item_length: int,
    options
) -> Tuple[Optional[Trial], Optional[str]]:
    """Attempt to create a Trial from a file.

    Returns (Trial, skip_reason) where skip_reason is None if successful,
    or a string explaining why the file was skipped."""
    ext = _should_attempt_result_file(file_path, options.filename_filter, options.format_type)
    if ext is None:
        return None, None  # Silent skip (doesn't match filters)

    if ext in SKIP_EXTENSIONS:
        return None, f"extension {ext} skipped"

    content = _read_result_file_content(file_path)
    if content is None:
        return None, f"encoding error"

    if ext not in PARSERS:
        return None, f"no parser for {ext}"

    items, metadata = _parse_and_build_file_metadata(file_path, content, ext, max_item_length, options)
    if items is None:
        return None, None  # Filtered by metadata

    file_type = FORMAT_MAP.get(ext, ext)
    trial = Trial(
        filename=file_path.name,
        file_type=file_type,
        extension=ext,
        items=items,
        metadata=metadata
    )
    return trial, None


def _merge_cleanup_metadata(metadata: dict, codeblock_cleanups: List[str], parser_cleanups: List[str]) -> None:
    """Merge cleanup information from multiple sources into metadata."""
    all_cleanups = codeblock_cleanups + parser_cleanups
    if metadata.get("processingCleanups"):
        all_cleanups.extend(metadata.pop("processingCleanups"))
    if all_cleanups:
        cleanup_dict = parse_cleanup_keys(all_cleanups)
        if cleanup_dict:
            metadata["cleanup"] = cleanup_dict


def _merge_format_issues(metadata: dict, parser_quality_issues: List[str]) -> None:
    """Merge format-level issues from multiple sources into metadata."""
    format_issues = []
    if parser_quality_issues:
        format_issues.extend(parser_quality_issues)
    if metadata.get("processingQualityIssues"):
        format_issues.extend(metadata.pop("processingQualityIssues"))
    if format_issues:
        metadata["formatIssues"] = format_issues


def _merge_sidecar_metadata(file_path: Path, metadata: dict) -> None:
    """Load and merge metadata from sidecar .meta.json file if it exists."""
    meta_path = META_DIR / (file_path.stem + ".meta.json")
    if not meta_path.exists():
        return

    try:
        with open(meta_path, 'r', encoding='utf-8') as mf:
            file_meta = json.load(mf)
        if "responseComplete" in file_meta:
            metadata["responseComplete"] = file_meta["responseComplete"]
        if "incompleteReason" in file_meta:
            metadata["incompleteReason"] = file_meta["incompleteReason"]
    except Exception:
        pass


def _parse_and_build_file_metadata(
    file_path: Path,
    content: str,
    ext: str,
    max_item_length: int,
    options
) -> Tuple[Optional[List[str]], Optional[dict]]:
    """Parse file content and build complete metadata.

    Returns (items, metadata) or (None, None) if filtered out."""
    parser = PARSERS[ext]
    cleaned_content, had_codeblock, codeblock_cleanups = extract_code_block(content)
    items, parser_cleanups, parser_quality_issues = parser(cleaned_content)

    filename_metadata = parse_filename_metadata(file_path.name)

    if not _passes_metadata_filters(filename_metadata, file_path.name, options.experiment,
                                     options.model, options.exclude_model, options.temperature, options.timestamp):
        return None, None

    # Process and track normalization
    items, processing, metadata = process_and_track(items, ext, max_item_length)

    # Merge all metadata sources
    metadata.update(processing)
    if had_codeblock:
        metadata["codeblock"] = True
    metadata["format"] = FORMAT_MAP.get(ext, "unknown")
    metadata["formatStyle"] = detect_format_style(content, ext)

    # Merge cleanup and quality issue metadata
    _merge_cleanup_metadata(metadata, codeblock_cleanups, parser_cleanups)
    _merge_format_issues(metadata, parser_quality_issues)

    # Merge filename and sidecar metadata
    metadata.update(filename_metadata)
    _merge_sidecar_metadata(file_path, metadata)

    # Track duplicates
    item_counts = Counter(items)
    metadata["duplicates"] = sum(1 for count in item_counts.values() if count > 1)

    metadata = reorder_metadata(metadata)
    return items, metadata


def update_aggregations_from_trial(trial: Trial, state: AggregationState) -> None:
    """Update all aggregation dicts from a Trial."""
    model_name = abbreviate_model_name(trial.metadata.get("model", "unknown"))
    temp_value = trial.metadata.get("temperature", "unknown")
    prompt_name = trial.metadata.get("prompt", "unknown")

    # Track cleanup rules
    for rule_name in trial.metadata.get("cleanup", {}).keys():
        state.cleanup_rules_agg[model_name][str(temp_value)][trial.file_type][prompt_name][rule_name] += 1

    # Track case values
    case_value = trial.metadata.pop("case", "lower")
    trial.metadata.pop("consistentCase", None)
    state.case_values_agg[model_name][str(temp_value)][trial.file_type][prompt_name].append((case_value, trial.filename))

    # Track cleanup rule sets per format
    rule_set = frozenset(trial.metadata.get("cleanup", {}).keys())
    if trial.extension in state.format_aggs:
        state.format_aggs[trial.extension]['agg'][model_name][str(temp_value)][prompt_name].append((rule_set, trial.filename))

    # Track format styles
    state.format_style_counts[model_name][str(temp_value)][trial.file_type][prompt_name][trial.metadata.get("formatStyle", "unknown")] += 1
    for fs_label in trial.metadata.get("formatIssues", []):
        state.format_style_counts[model_name][str(temp_value)][trial.file_type][prompt_name][fs_label] += 1

    # Track quality issues
    quality_ctx = QualityContext(output=state.quality_issues_output, instances=state.quality_issues_instances)
    _track_item_quality_issues(trial, quality_ctx)

    # Track item counts
    state.item_count_stats[model_name][str(temp_value)][trial.file_type].append(len(trial.items))

    # Add to consolidated
    state.consolidated[trial.extension].append({
        "filename": trial.filename,
        "metadata": trial.metadata,
        "items": trial.items
    })


def _track_trial_items(trial: Trial, state: AggregationState) -> None:
    """Track source items from a trial."""
    for item in trial.items:
        if item:
            state.source_items.add(item)


def _track_zero_item_file(trial: Trial, state: AggregationState) -> None:
    """Track files with zero items."""
    if trial.metadata.get("itemCount") == 0:
        state.zero_item_files.append(trial.filename)


def _handle_trial_load_error(file_path: Path, error: Exception, verbose: bool, state: AggregationState) -> None:
    """Handle errors during trial loading."""
    click.echo(f"Error loading {file_path.name}: {error}")
    if verbose:
        click.echo(traceback.format_exc())
    state.skipped_trials.append(file_path.name)


def _process_loaded_trial(trial: Trial, state: AggregationState) -> None:
    """Process a successfully loaded trial."""
    _track_trial_items(trial, state)
    _track_zero_item_file(trial, state)
    click.echo(f"Loaded: {trial.filename} ({len(trial.items)} items)")


def collect_trials(
    max_item_length: int,
    verbose: bool,
    options,
    state: AggregationState
) -> Tuple[List[Trial], int]:
    """Scan result files and load them as Trial objects.

    Returns (trials, file_count).
    Tracks skipped files and reports progress."""
    trials = []
    file_count = 0

    for file_path in sorted(RESULTS_DIR.iterdir()):
        try:
            trial, skip_reason = create_trial_from_file(file_path, max_item_length, options)

            if trial is None:
                if skip_reason:
                    click.echo(f"Skipping ({skip_reason}): {file_path.name}")
                    state.skipped_trials.append(file_path.name)
                continue

            _process_loaded_trial(trial, state)
            trials.append(trial)
            file_count += 1

        except Exception as e:
            _handle_trial_load_error(file_path, e, verbose, state)
            continue

    return trials, file_count


def process_trial_set(trial_set, state: AggregationState) -> None:
    """Process all trials in a set: update consolidated data and aggregations."""
    for trial in trial_set.trials:
        update_aggregations_from_trial(trial, state)
