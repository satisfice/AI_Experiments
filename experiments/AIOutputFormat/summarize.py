#!/usr/bin/env python3

import json
import re
import sys
import click
from dataclasses import dataclass
from typing import NamedTuple, Optional
from pathlib import Path
from collections import defaultdict, Counter
from fnmatch import fnmatch
from config import abbreviate_model_name
from utils import format_error, is_standard_filename
from process_single_file import (
    trim_items, is_alphabetical_order, process_and_track,
    extract_code_block, parse_filename_metadata, parse_cleanup_keys,
    detect_format_style, reorder_metadata, FORMAT_MAP, PARSERS,
    extract_first_alpha_string
)

RESULTS_DIR = Path("results")
META_DIR = RESULTS_DIR / "meta"
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


# Matches a numbered list prefix: digits followed by . ) : or -
_NUMBERED_LINE_RE = re.compile(r'^\d+[.):\-]')

# Formats whose content may be wrapped in markdown codefences (``` ... ```)
_CODEFENCED_FORMATS = frozenset({'JSON', 'HTML', 'CSV', 'YAML'})


# ── Shared low-level helpers ──────────────────────────────────────────────────

def _make_four_level_defaultdict(innermost_factory):
    """Create a 4-level nested defaultdict: model -> temperature -> file_type -> prompt.
    innermost_factory: callable that returns the innermost value."""
    return defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(innermost_factory)
            )
        )
    )


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


def _make_issue_output_dicts(issue_types):
    """Create the quality issues output and examples tracking dicts.

    Returns:
        (quality_issues_output, quality_issues_instances) — nested dicts for tracking
        quality issues and their example source files.
        Structure: model -> temperature -> file_type -> prompt -> issue_type -> {items|examples}
    """
    quality_issues_output = _make_four_level_defaultdict(lambda: {k: set() for k in issue_types})
    quality_issues_instances = _make_four_level_defaultdict(lambda: {k: {} for k in issue_types})
    return quality_issues_output, quality_issues_instances


def _make_format_style_counts():
    """Create the format style counts tracking dict.

    Returns:
        format_style_counts — nested dict for counting how many files use each format style.
        Structure: model -> temperature -> file_type -> prompt -> formatStyle -> count
    """
    return _make_four_level_defaultdict(lambda: defaultdict(int))


def _make_cleanup_rules_agg():
    """Create the cleanup rules aggregation dict.

    Returns:
        cleanup_rules_agg — nested Counter dict tracking rule invocation across trials.
        Structure: model -> temperature -> file_type -> prompt -> Counter(rule_name -> count)
        Each count is the number of trials in the set that triggered that rule.
    """
    return _make_four_level_defaultdict(Counter)


def _make_format_aggregation_dicts():
    """Create the format-specific cleanup rule aggregation dicts (case, markdown, HTML, JSON, YAML, CSV, txt1).

    Returns:
        (case_values_agg, md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg,
         csv_cleanup_agg, txt1_cleanup_agg)
        Used for detecting cross-trial inconsistencies in formatting/casing.
    """
    case_values_agg = _make_four_level_defaultdict(list)

    cleanup_agg_template = lambda: defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(list)
        )
    )
    md_cleanup_agg = cleanup_agg_template()
    html_cleanup_agg = cleanup_agg_template()
    json_cleanup_agg = cleanup_agg_template()
    yaml_cleanup_agg = cleanup_agg_template()
    csv_cleanup_agg = cleanup_agg_template()
    txt1_cleanup_agg = cleanup_agg_template()

    return case_values_agg, md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg, csv_cleanup_agg, txt1_cleanup_agg


def _record_case_inconsistencies_for_set(trial_set, inconsistencies, quality_ctx):
    """Record case inconsistencies detected in a trial set into aggregation dicts."""
    if not inconsistencies:
        return

    model = trial_set.model
    temp = trial_set.temperature
    file_type = trial_set.file_type
    prompt = trial_set.prompt

    for case_val, example_filename in inconsistencies.items():
        quality_ctx.output[model][temp][file_type][prompt]["inconsistent_case"].add(case_val)
        if case_val not in quality_ctx.instances[model][temp][file_type][prompt]["inconsistent_case"]:
            quality_ctx.instances[model][temp][file_type][prompt]["inconsistent_case"][case_val] = example_filename


def _flag_case_inconsistencies(trial_sets, quality_ctx):
    """Detect and record case inconsistencies across all trial sets."""
    for trial_set in trial_sets.values():
        inconsistencies = trial_set.detect_case_inconsistencies()
        _record_case_inconsistencies_for_set(trial_set, inconsistencies, quality_ctx)


def _record_format_rule_inconsistencies_for_set(trial_set, inconsistencies, quality_ctx, issue_key):
    """Record format rule inconsistencies detected in a trial set into aggregation dicts."""
    if not inconsistencies:
        return

    model = trial_set.model
    temp = trial_set.temperature
    file_type = trial_set.file_type
    prompt = trial_set.prompt

    for rule, example_filename in inconsistencies.items():
        quality_ctx.output[model][temp][file_type][prompt][issue_key].add(rule)
        if rule not in quality_ctx.instances[model][temp][file_type][prompt][issue_key]:
            quality_ctx.instances[model][temp][file_type][prompt][issue_key][rule] = example_filename


def _flag_format_inconsistencies(trial_sets, quality_ctx, file_type_label, issue_key):
    """Detect and record format rule inconsistencies for a specific file type across all trial sets."""
    for trial_set in trial_sets.values():
        if trial_set.file_type != file_type_label:
            continue
        inconsistencies = trial_set.detect_format_rule_inconsistencies()
        _record_format_rule_inconsistencies_for_set(trial_set, inconsistencies, quality_ctx, issue_key)


class TrialKey(NamedTuple):
    """Identifies one (model, temperature, file_type, prompt) combination --
    matches the tuple key already used for format_consistency lookups. A
    drop-in replacement for that raw tuple, with named field access."""
    model: str
    temperature: str
    file_type: str
    prompt: str


@dataclass
class QualityContext:
    """Bundles quality issues output and instances for cohesive passing to functions."""
    output: dict
    instances: dict


def _print_format_consistency_status(pd, key, format_consistency, treatment_fields, safe_write):
    """Print format consistency status for a prompt."""
    model_name, temp_value, file_type, prompt_name = key
    is_consistent = pd.get("consistentFormat", True)
    if is_consistent:
        safe_write(f"        Format: ✓ consistent")
    else:
        fc = format_consistency.get((model_name, str(temp_value), file_type, prompt_name), {})
        varying = [f for f in treatment_fields if len(set(fc.get(f, []))) > 1]
        parts = []
        if "formatStyle" in varying:
            parts.extend(sorted(set(fc.get("formatStyle", []))))
        if "codeblock" in varying:
            parts.append("codeblock")
        safe_write(f"        Format: ✗ inconsistent ({', '.join(parts)})")


def _print_format_issues_breakdown(pd, safe_write):
    """Print format issues summary for a prompt."""
    format_issue_counts = pd.get("formatIssues", {})
    if format_issue_counts:
        issues_str = ", ".join(f"{i}: {c}" for i, c in sorted(format_issue_counts.items()))
        safe_write(f"        Format Issues: {issues_str}")


def _print_issue_instance_items(items, issue_key, with_instance, trial_key, quality_ctx, safe_write):
    """Print instance items for an issue type."""
    for item in items[:5]:
        suffix = ""
        if with_instance:
            instance_file = quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][issue_key].get(item)
            suffix = f" Instance: {instance_file}" if instance_file else ""
        safe_write(f"          - {ascii(item)}{suffix}")


def _print_single_issue_type(issue_key, label, with_instance, pd, trial_key, quality_ctx, safe_write):
    """Print breakdown for a single issue type."""
    items = [e["instance"] for e in pd.get(issue_key, [])]
    if not items:
        return
    safe_write(f"        {label} ({len(items)} unique):")
    _print_issue_instance_items(items, issue_key, with_instance, trial_key, quality_ctx, safe_write)
    if len(items) > 5:
        safe_write(f"          ... and {len(items) - 5} more")


def _print_issue_type_breakdown(pd, key, quality_ctx, safe_write):
    """Print per-issue-type breakdown for a prompt."""
    trial_key = key
    issue_display = [
        ("leading_punctuation", "Leading punctuation", True),
        ("trailing_punctuation", "Trailing punctuation", True),
        ("internal_punctuation", "Internal punctuation", True),
        ("exceeds_max_length", "Exceeds max length", False),
        ("preamble_leak", "Preamble leaks", False),
        ("markup_artifact", "Markup artifacts", False),
        ("repeated_chars", "Repeated characters", False),
    ]
    for issue_key, label, with_instance in issue_display:
        _print_single_issue_type(issue_key, label, with_instance, pd, trial_key, quality_ctx, safe_write)


def _print_prompt_analysis(pd, key, format_consistency, treatment_fields, quality_ctx, safe_write):
    """Print one prompt's quality-issue breakdown within the analysis report."""
    safe_write(f"      {key.prompt_name}:")
    _print_format_consistency_status(pd, key, format_consistency, treatment_fields, safe_write)
    _print_format_issues_breakdown(pd, safe_write)
    _print_issue_type_breakdown(pd, key, quality_ctx, safe_write)


def _print_analysis_report(item_count_stats, quality_issues_dict, quality_ctx,
                            format_consistency, treatment_fields):
    """Print the verbose per-model/temperature/file-type analysis report
    (the `analysis and verbose` report, extracted out of summarize_results)."""
    def safe_write(text):
        """Write text to stdout with error handling for encoding issues"""
        sys.stdout.write(text + '\n')
        sys.stdout.flush()

    safe_write("\n" + "="*70)
    safe_write("DATA ANALYSIS REPORT BY MODEL, TEMPERATURE, AND FILE TYPE")
    safe_write("="*70)

    for model_name in sorted(item_count_stats.keys()):
        safe_write(f"\n{model_name}:")
        for temp_value in sorted(item_count_stats[model_name].keys(), key=lambda x: (x == "unknown", x)):
            safe_write(f"  Temperature {temp_value}:")
            for file_type in sorted(item_count_stats[model_name][temp_value].keys(), key=str.casefold):
                counts = item_count_stats[model_name][temp_value][file_type]
                stats = calculate_statistics(counts)
                safe_write(f"    {file_type} ({len(counts)} files):")
                safe_write(f"      Items: max={stats['max']}, min={stats['min']}, avg={stats['avg']}, var={stats['var']}, mode={stats['mode']}")

                prompts_data = quality_issues_dict.get(model_name, {}).get(str(temp_value), {}).get(file_type, {})
                for prompt_name in sorted(prompts_data.keys()):
                    tk = TrialKey(model_name, temp_value, file_type, prompt_name)
                    _print_prompt_analysis(prompts_data[prompt_name], tk,
                                            format_consistency, treatment_fields, quality_issues_instances, safe_write)


@dataclass
class SummarizeFilters:
    """Filter and behavior options for summarize_results(), bundled to avoid a
    10-argument function signature."""
    filename_filter: Optional[str] = None    # Legacy filter: substring match on filename (e.g., "experiment1")
    model: Optional[str] = None              # Include only this model (e.g., "gpt4"); None includes all
    format_type: Optional[str] = None        # Filter by file format/extension (e.g., "json", "txt")
    experiment: Optional[str] = None         # Filter by experiment name (e.g., "animals5")
    timestamp: Optional[str] = None          # Filter by timestamp (e.g., "202602061922")
    temperature: Optional[float] = None      # Filter by temperature (e.g., 1.0)
    max_item_length: int = 25                # Items longer than this are flagged
    analysis: bool = False                   # Whether to generate the analysis report
    exclude_model: Optional[tuple] = None    # Model name patterns to EXCLUDE (e.g., ("gpt4", "llama*"))
    verbose: bool = False                    # Show detailed summary output; skipped files always shown


def _describe_active_filters(options):
    """Build a list of human-readable descriptions of which filters are active,
    for the startup echo line."""
    filters_applied = []
    if options.filename_filter:
        filters_applied.append(f"filename: {options.filename_filter}")
    if options.experiment:
        filters_applied.append(f"experiment: {options.experiment}")
    if options.model:
        filters_applied.append(f"model: {options.model} (include only)")
    if options.exclude_model:
        filters_applied.append(f"exclude-model: {', '.join(options.exclude_model)}")
    if options.format_type:
        filters_applied.append(f"format: {options.format_type}")
    if options.timestamp:
        filters_applied.append(f"timestamp: {options.timestamp}")
    if options.temperature:
        filters_applied.append(f"temperature: {options.temperature}")
    return filters_applied


def _compute_format_consistency(consolidated_dict, treatment_fields):
    """Detect treatment consistency for each trial set: tracks all fields that
    indicate how output was structured or cleaned up, grouped by
    (abbreviated_model, str(temperature), file_type, prompt).
    Returns {(model, temp, file_type, prompt): {field: [values]}}."""
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
                format_consistency[key] = {field: [] for field in treatment_fields}

            for field in treatment_fields:
                # codeblock is absent when False; treat absence as False
                format_consistency[key][field].append(metadata.get(field, False))
    return format_consistency


@dataclass
class _QualityDataContext:
    """Context for building prompt data sections."""
    quality_issues_output: dict
    quality_issues_instances: dict
    format_consistency: dict
    format_style_counts: dict
    cleanup_rules_agg: dict
    issue_types: list
    treatment_fields: list


def _extract_quality_issues_for_prompt(trial_key, ctx):
    """Extract quality issues with sources for a prompt."""
    prompt_data = {}
    issues = ctx.quality_issues_output.get(trial_key.model, {}).get(trial_key.temperature, {}).get(trial_key.file_type, {}).get(trial_key.prompt, {})
    for issue_type in ctx.issue_types:
        raw_items = issues.get(issue_type, set())
        if raw_items:
            items_with_source = []
            for item in raw_items:
                source = ctx.quality_issues_instances[trial_key.model][trial_key.temperature][trial_key.file_type][trial_key.prompt][issue_type].get(item, "unknown")
                items_with_source.append({"instance": item, "source": source})
            items_with_source.sort(key=lambda x: x["instance"].lower())
            prompt_data[issue_type] = items_with_source
    return prompt_data


def _compute_format_consistency_for_prompt(trial_key, prompt_data, ctx):
    """Compute format consistency for a prompt."""
    fc = ctx.format_consistency.get((trial_key.model, trial_key.temperature, trial_key.file_type, trial_key.prompt), {})
    has_format_inconsistency = False

    inconsistency_issue_types = {
        "markdown": "inconsistent_md_format",
        "HTML": "inconsistent_html_format",
        "JSON": "inconsistent_json_format",
        "YAML": "inconsistent_yaml_format",
    }
    if trial_key.file_type in inconsistency_issue_types:
        inconsistency_type = inconsistency_issue_types[trial_key.file_type]
        has_format_inconsistency = inconsistency_type in prompt_data and bool(prompt_data[inconsistency_type])

    if fc:
        varying = [f for f in ctx.treatment_fields if len(set(fc[f])) > 1]
        prompt_data["consistentFormat"] = len(varying) == 0 and not has_format_inconsistency
    else:
        prompt_data["consistentFormat"] = not has_format_inconsistency


def _extract_format_issues_for_prompt(trial_key, prompt_data, ctx):
    """Extract format style issues for a prompt."""
    style_counts = ctx.format_style_counts.get(trial_key.model, {}).get(trial_key.temperature, {}).get(trial_key.file_type, {}).get(trial_key.prompt, {})
    if style_counts:
        prompt_data["formatIssues"] = dict(style_counts)


def _extract_cleanup_rules_for_prompt(trial_key, prompt_data, ctx):
    """Extract cleanup rules for a prompt."""
    rules_counter = ctx.cleanup_rules_agg.get(trial_key.model, {}).get(trial_key.temperature, {}).get(trial_key.file_type, {}).get(trial_key.prompt, {})
    if rules_counter:
        prompt_data["cleanupRules"] = dict(sorted(rules_counter.items()))


def _build_prompt_data_section(trial_key, ctx):
    """Build quality issues, consistency, formatIssues, and cleanupRules for one prompt."""
    prompt_data = _extract_quality_issues_for_prompt(trial_key, ctx)
    _compute_format_consistency_for_prompt(trial_key, prompt_data, ctx)
    _extract_format_issues_for_prompt(trial_key, prompt_data, ctx)
    _extract_cleanup_rules_for_prompt(trial_key, prompt_data, ctx)
    return prompt_data


def _build_quality_issues_dict(trial_sets, format_consistency, format_style_counts, quality_issues_output,
                                quality_issues_instances, cleanup_rules_agg, issue_types, treatment_fields):
    """Build quality_issues_dict from trial sets."""
    ctx = _QualityDataContext(
        quality_issues_output=quality_issues_output,
        quality_issues_instances=quality_issues_instances,
        format_consistency=format_consistency,
        format_style_counts=format_style_counts,
        cleanup_rules_agg=cleanup_rules_agg,
        issue_types=issue_types,
        treatment_fields=treatment_fields
    )

    quality_issues_dict = {}

    # Iterate through trial sets instead of nested dict combos
    for trial_set in trial_sets.values():
        tk = TrialKey(trial_set.model, trial_set.temperature, trial_set.file_type, trial_set.prompt)
        prompt_data = _build_prompt_data_section(tk, ctx)
        quality_issues_dict \
            .setdefault(trial_set.model, {}) \
            .setdefault(trial_set.temperature, {}) \
            .setdefault(trial_set.file_type, {})[trial_set.prompt] = prompt_data

    return quality_issues_dict


def _matches_format_type(ext, format_type):
    """Check if extension matches the requested format type."""
    if not format_type:
        return True
    return ext == f".{format_type}" or ext == format_type


def _should_attempt_result_file(file_path, filename_filter, format_type):
    """Pre-filter for the result-file scan: decide whether to even attempt parsing
    this file. Returns the lowercase extension if the file should be attempted, or
    None if it should be silently skipped. (Extension-based skip-list handling is
    left to the caller, since that case needs to be recorded in skipped_trials.)"""
    if not file_path.is_file():
        return None
    if file_path.name in SKIP_PATTERNS:
        return None
    if not is_standard_filename(file_path.name):
        return None
    if filename_filter and filename_filter not in file_path.name:
        return None
    ext = file_path.suffix.lower()
    if not ext:
        return None
    if not _matches_format_type(ext, format_type):
        return None
    return ext


def _read_result_file_content(file_path):
    """Read a result file's content, trying utf-8 then utf-16.
    Returns None if both encodings fail."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='utf-16') as f:
                return f.read()
        except UnicodeDecodeError:
            return None


def _check_experiment_filter(filename_metadata, experiment):
    """Check if file's experiment matches filter."""
    if experiment and filename_metadata.get("experiment") != experiment:
        return False
    return True


def _check_model_filters(filename_metadata, model, exclude_model):
    """Check if file's model passes inclusion and exclusion filters."""
    file_model = filename_metadata.get("model")
    if model and file_model != model:
        return False
    if exclude_model and any(matches_model_pattern(file_model, pattern) for pattern in exclude_model):
        return False
    return True


def _check_temperature_filter(filename_metadata, temperature):
    """Check if file's temperature matches filter."""
    if temperature is None:
        return True
    file_temp = filename_metadata.get("temperature")
    try:
        temp_filter = float(temperature)
    except (ValueError, TypeError):
        return False
    return file_temp == temp_filter


def _check_timestamp_filter(file_name, timestamp):
    """Check if file's timestamp matches filter."""
    if not timestamp:
        return True
    file_timestamp = Path(file_name).stem.split('-')[0]
    return file_timestamp == timestamp


def _passes_metadata_filters(filename_metadata, file_name, experiment, model, exclude_model, temperature, timestamp):
    """Check whether a file's parsed filename metadata passes all active filters."""
    return (
        _check_experiment_filter(filename_metadata, experiment) and
        _check_model_filters(filename_metadata, model, exclude_model) and
        _check_temperature_filter(filename_metadata, temperature) and
        _check_timestamp_filter(file_name, timestamp)
    )


def _is_txt1_leading_number_exception(issue_type, ext, example):
    """Check if this is a txt1 file with leading number (expected format, not a quality issue)."""
    if issue_type != "leading_punctuation" or ext != '.txt1':
        return False
    return bool(re.match(r'^\d+[\.\)\-\s]', example))


def _track_item_level_issues(item_issues, issue_type, ext, trial_key, quality_ctx, filename):
    """Track a single item-level quality issue."""
    example = item_issues.get(issue_type)
    if not example:
        return
    if _is_txt1_leading_number_exception(issue_type, ext, example):
        return
    quality_ctx.output[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][issue_type].add(example)
    if example not in quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][issue_type]:
        quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][issue_type][example] = filename


def _track_repeated_sequence_issue(item_issues, trial_key, quality_ctx, filename):
    """Track repeated_sequence issue using filename as instance."""
    if item_issues.get("repeated_sequence"):
        quality_ctx.output[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt]["repeated_sequence"].add(filename)
        if filename not in quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt]["repeated_sequence"]:
            quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt]["repeated_sequence"][filename] = filename


def _track_format_level_issues(format_issues, trial_key, quality_ctx, filename):
    """Track format-level quality issues from metadata."""
    for fs_label in format_issues:
        quality_ctx.output[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][fs_label].add(filename)
        if filename not in quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][fs_label]:
            quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][fs_label][filename] = filename


def _track_item_quality_issues(metadata, key, ext, quality_ctx, filename):
    """Track item-level and format-level quality issues from one file's metadata
    into quality_ctx. Mutates in place."""
    if "itemIssues" in metadata:
        item_issues = metadata["itemIssues"]
        for issue_type in ["leading_punctuation", "trailing_punctuation", "internal_punctuation",
                           "exceeds_max_length", "preamble_leak",
                           "markup_artifact", "repeated_chars"]:
            _track_item_level_issues(item_issues, issue_type, ext, key, quality_ctx, filename)
        _track_repeated_sequence_issue(item_issues, key, quality_ctx, filename)
    if "formatIssues" in metadata:
        _track_format_level_issues(metadata["formatIssues"], key, quality_ctx, filename)


def _write_results_and_quality_json(consolidated_dict, quality_issues_dict, file_count, verbose):
    """Write results.json (always) and quality.json (if there are any quality
    issues), printing verbose summary stats if requested."""
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
        # Still write quality JSON even if not verbose
        if quality_issues_dict:
            with open(QUALITY_FILE, 'w', encoding='utf-8') as f:
                json.dump(quality_issues_dict, f, indent=2, ensure_ascii=False)


def _print_skip_summary(skipped_trials, zero_item_files):
    """Print the skipped-trials and zero-item-files summaries, if any."""
    if skipped_trials:
        click.echo(f"Skipped {len(skipped_trials)} trials:")
        for trial_name in sorted(skipped_trials):
            click.echo(f"  {trial_name}")

    if zero_item_files:
        click.echo(f"Files with 0 items ({len(zero_item_files)}):")
        for filename in sorted(zero_item_files):
            click.echo(f"  {filename}")


def _collect_unique_items_from_consolidated(consolidated_dict):
    """Collect all unique non-empty items from consolidated dict."""
    unique_set = set()
    for ext_key in consolidated_dict:
        for entry in consolidated_dict[ext_key]:
            for item in entry.get("items", []):
                if item:
                    unique_set.add(item)
    return unique_set


def _write_items_to_file(sorted_items, file_path):
    """Write sorted items to file, one per line."""
    with open(file_path, 'w', encoding='utf-8') as f:
        for item in sorted_items:
            f.write(item + '\n')


def _write_unique_items_file(consolidated_dict, verbose):
    """Write the always-on unique-items file (all non-empty items across every
    consolidated entry, deduplicated and sorted)."""
    unique_set = _collect_unique_items_from_consolidated(consolidated_dict)
    sorted_items = sorted(unique_set)
    _write_items_to_file(sorted_items, UNIQUE_ITEMS_FILE)
    if verbose:
        click.echo(f"Wrote {len(sorted_items)} unique items to {UNIQUE_ITEMS_FILE}")


def _write_unique_source_items_file(source_items, verbose):
    """Write the raw parsed source items (before processing), sorted by first
    alphabetical string (case-insensitive), preserving original case in output."""
    sorted_source_items = sorted(source_items, key=extract_first_alpha_string)
    with open(UNIQUE_SOURCE_ITEMS_FILE, 'w', encoding='utf-8') as f:
        for item in sorted_source_items:
            f.write(item + '\n')
    if verbose:
        click.echo(f"Wrote {len(sorted_source_items)} unique source items to {UNIQUE_SOURCE_ITEMS_FILE}")


@dataclass
class AggregationState:
    """Bundles all mutable aggregation structures to avoid parameter explosion."""
    consolidated: dict
    quality_issues_output: dict
    quality_issues_instances: dict
    format_style_counts: dict
    item_count_stats: dict
    cleanup_rules_agg: dict
    case_values_agg: dict
    format_aggs: dict
    skipped_trials: list
    zero_item_files: list
    source_items: set


@dataclass
class QualityAnalysisResults:
    """Bundles computed quality analysis results to reduce function arguments."""
    quality_issues_dict: dict
    format_consistency: dict


@dataclass
class ReportOptions:
    """Bundles report control flags and metadata."""
    file_count: int
    analysis: bool
    verbose: bool


@dataclass
class Trial:
    """Represents a single trial (result file) with its parsed content and metadata."""
    filename: str           # e.g., "202602061922-animals-animals_hard-gpt4-0.7-01.json"
    file_type: str          # e.g., "JSON" (from FORMAT_MAP)
    extension: str          # e.g., ".json"
    items: list             # Parsed items from file
    metadata: dict          # All metadata including model, temperature, prompt, etc.


@dataclass
class TrialSet:
    """Represents a set of trials with identical model/temperature/file_type/prompt.

    A trial set is all trials that vary only in iteration number.
    Example: 3 trials with same model/temp/prompt but iterations 01, 02, 03.
    """
    model: str              # Abbreviated model name
    temperature: str        # Temperature value (as string)
    file_type: str          # File format type (e.g., "JSON", "markdown")
    prompt: str             # Prompt name
    trials: list            # List of Trial objects in this set

    def extract_case_values(self):
        """Extract (case_value, filename) pairs from all trials in this set."""
        return [(trial.metadata.get("case", "lower"), trial.filename) for trial in self.trials]

    def detect_case_inconsistencies(self):
        """Detect if trials in this set have inconsistent case handling.

        Returns: {case_value: example_filename} or {} if consistent.
        """
        case_values = self.extract_case_values()
        distinct_cases = set(v for v, _ in case_values)

        if len(distinct_cases) <= 1:
            return {}  # Consistent

        # Group filenames by case value
        issues = {}
        for case_val, fname in case_values:
            if case_val not in issues:
                issues[case_val] = fname
        return issues

    def detect_format_rule_inconsistencies(self):
        """Detect if trials in this set have inconsistent cleanup rule sets.

        Returns: {varying_rule: example_filename} or {} if consistent.
        """
        rule_sets = [frozenset(trial.metadata.get("cleanup", {}).keys()) for trial in self.trials]

        if len(set(rule_sets)) <= 1:
            return {}  # All trials have same rule set

        all_rules = set().union(*rule_sets)
        common_rules = set.intersection(*[set(r) for r in rule_sets]) if rule_sets else set()
        varying_rules = all_rules - common_rules

        # Find example filename for each varying rule
        issues = {}
        for rule in varying_rules:
            for trial in self.trials:
                trial_rules = frozenset(trial.metadata.get("cleanup", {}).keys())
                if rule in trial_rules:
                    issues[rule] = trial.filename
                    break
        return issues


def _group_trials_into_sets(trials):
    """Group trials by (model, temperature, file_type, prompt).

    Returns a dict: {(model, temp, file_type, prompt): TrialSet}
    """
    sets_dict = {}
    for trial in trials:
        model = abbreviate_model_name(trial.metadata.get("model", "unknown"))
        temp = str(trial.metadata.get("temperature", "unknown"))
        prompt = trial.metadata.get("prompt", "unknown")

        key = (model, temp, trial.file_type, prompt)
        if key not in sets_dict:
            sets_dict[key] = TrialSet(
                model=model,
                temperature=temp,
                file_type=trial.file_type,
                prompt=prompt,
                trials=[]
            )
        sets_dict[key].trials.append(trial)

    return sets_dict


def _create_trial_from_file(file_path, max_item_length, options):
    """Attempt to create a Trial from a file. Returns Trial or None if file should be skipped.

    Returns (Trial, skip_reason) where skip_reason is None if successful, or a string explaining why skipped.
    """
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


def _merge_cleanup_metadata(metadata, codeblock_cleanups, parser_cleanups):
    """Merge cleanup information from multiple sources into metadata."""
    all_cleanups = codeblock_cleanups + parser_cleanups
    if metadata.get("processingCleanups"):
        all_cleanups.extend(metadata.pop("processingCleanups"))
    if all_cleanups:
        cleanup_dict = parse_cleanup_keys(all_cleanups)
        if cleanup_dict:
            metadata["cleanup"] = cleanup_dict


def _merge_format_issues(metadata, parser_quality_issues):
    """Merge format-level issues from multiple sources into metadata."""
    format_issues = []
    if parser_quality_issues:
        format_issues.extend(parser_quality_issues)
    if metadata.get("processingQualityIssues"):
        format_issues.extend(metadata.pop("processingQualityIssues"))
    if format_issues:
        metadata["formatIssues"] = format_issues


def _merge_sidecar_metadata(file_path, metadata):
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


def _parse_and_build_file_metadata(file_path, content, ext, max_item_length, options):
    """Parse file content and build complete metadata. Returns (items, metadata) or (None, None)."""
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


def _update_aggregations_from_trial(trial, state):
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
    quality_ctx = QualityContext(output=state.quality_issues_output, examples=state.quality_issues_instances)
    _track_item_quality_issues(trial.metadata, TrialKey(model_name, temp_value, trial.file_type, prompt_name),
                                trial.extension, quality_ctx, trial.filename)

    # Track item counts
    state.item_count_stats[model_name][str(temp_value)][trial.file_type].append(len(trial.items))

    # Add to consolidated
    state.consolidated[trial.extension].append({
        "filename": trial.filename,
        "metadata": trial.metadata,
        "items": trial.items
    })


def _track_trial_items(trial, state):
    """Track source items from a trial."""
    for item in trial.items:
        if item:
            state.source_items.add(item)


def _track_zero_item_file(trial, state):
    """Track files with zero items."""
    if trial.metadata.get("itemCount") == 0:
        state.zero_item_files.append(trial.filename)


def _handle_trial_load_error(file_path, error, verbose, state):
    """Handle errors during trial loading."""
    import traceback
    click.echo(f"Error loading {file_path.name}: {error}")
    if verbose:
        click.echo(traceback.format_exc())
    state.skipped_trials.append(file_path.name)


def _process_loaded_trial(trial, state):
    """Process a successfully loaded trial."""
    _track_trial_items(trial, state)
    _track_zero_item_file(trial, state)
    click.echo(f"Loaded: {trial.filename} ({len(trial.items)} items)")


def _collect_trials(max_item_length, verbose, options, state):
    """Scan result files and load them as Trial objects.

    Returns (trials, file_count, zero_item_files).
    Tracks skipped files and reports progress.
    """
    trials = []
    file_count = 0

    for file_path in sorted(RESULTS_DIR.iterdir()):
        try:
            trial, skip_reason = _create_trial_from_file(file_path, max_item_length, options)

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


def _process_trial_set(trial_set, state):
    """Process all trials in a set: update consolidated data and aggregations."""
    for trial in trial_set.trials:
        _update_aggregations_from_trial(trial, state)


def _initialize_issue_types_and_aggregations():
    """Initialize issue types and aggregation structures."""
    ISSUE_TYPES = [
        "leading_punctuation", "trailing_punctuation", "internal_punctuation",
        "exceeds_max_length", "preamble_leak",
        "markup_artifact", "repeated_chars", "repeated_sequence",
        "single-span-tag",
        "numbered-items-in-tags", "repeated-json-keys", "non-western-characters",
        "comma-separated", "txt1-no-numbers", "html_no_markup", "invalid-html-tags", "pointy-bracket-wrapping",
        "inconsistent_case", "inconsistent_md_format", "inconsistent_html_format",
        "inconsistent_json_format", "inconsistent_yaml_format", "inconsistent_csv_format", "inconsistent_txt1_format",
        "parse-failed", "json-truncated", "stray-html-markup", "blockquote-markup",
        "Markdown-Cleanup-Partially-Bold-Line", "Markdown-Cleanup-Partially-Italic-Star-Line",
        "Markdown-Cleanup-Partially-Italic-Underscore-Line",
        "HTML_Only_Bold_Tags", "HTML_Only_Italic_Tags", "HTML_Only_Emphasis_Tags", "HTML_Only_Underline_Tags",
        "HTML_Only_Pre_Tags", "items-not-in-li",
    ]
    quality_issues_output, quality_issues_instances = _make_issue_output_dicts(ISSUE_TYPES)
    format_style_counts = _make_format_style_counts()
    item_count_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    cleanup_rules_agg = _make_cleanup_rules_agg()
    case_values_agg, md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg, csv_cleanup_agg, txt1_cleanup_agg = _make_format_aggregation_dicts()
    return ISSUE_TYPES, quality_issues_output, quality_issues_instances, format_style_counts, item_count_stats, cleanup_rules_agg, case_values_agg, md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg, csv_cleanup_agg, txt1_cleanup_agg


def _build_format_aggregations(md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg, csv_cleanup_agg, txt1_cleanup_agg):
    """Build the format aggregations mapping."""
    return {
        '.md': {'agg': md_cleanup_agg, 'label': 'markdown', 'issue_key': 'inconsistent_md_format'},
        '.html': {'agg': html_cleanup_agg, 'label': 'HTML', 'issue_key': 'inconsistent_html_format'},
        '.json': {'agg': json_cleanup_agg, 'label': 'JSON', 'issue_key': 'inconsistent_json_format'},
        '.yml': {'agg': yaml_cleanup_agg, 'label': 'YAML', 'issue_key': 'inconsistent_yaml_format'},
        '.yaml': {'agg': yaml_cleanup_agg, 'label': 'YAML', 'issue_key': 'inconsistent_yaml_format'},
        '.csv': {'agg': csv_cleanup_agg, 'label': 'CSV', 'issue_key': 'inconsistent_csv_format'},
        '.txt1': {'agg': txt1_cleanup_agg, 'label': 'numberedText', 'issue_key': 'inconsistent_txt1_format'},
    }


def _create_aggregation_state(consolidated, quality_issues_output, quality_issues_instances, format_style_counts, item_count_stats, cleanup_rules_agg, case_values_agg, format_aggs):
    """Create aggregation state object."""
    return AggregationState(
        consolidated=consolidated,
        quality_issues_output=quality_issues_output,
        quality_issues_instances=quality_issues_instances,
        format_style_counts=format_style_counts,
        item_count_stats=item_count_stats,
        cleanup_rules_agg=cleanup_rules_agg,
        case_values_agg=case_values_agg,
        format_aggs=format_aggs,
        skipped_trials=[],
        zero_item_files=[],
        source_items=set()
    )


def summarize_results(options):
    """
    Read all result files by type, parse items, and summarize into a single JSON.
    Structure: {filetype: [{filename: str, items: [...]}, ...], ...}

    Args:
        options: SummarizeFilters instance (see its field comments for details).
    """
    if not RESULTS_DIR.exists():
        click.echo(format_error("summarize", f"{RESULTS_DIR} directory not found"), err=True)
        return False

    # Initialize data structures
    consolidated = defaultdict(list)
    ISSUE_TYPES, quality_issues_output, quality_issues_instances, format_style_counts, item_count_stats, cleanup_rules_agg, case_values_agg, md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg, csv_cleanup_agg, txt1_cleanup_agg = _initialize_issue_types_and_aggregations()
    format_aggs = _build_format_aggregations(md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg, csv_cleanup_agg, txt1_cleanup_agg)
    state = _create_aggregation_state(consolidated, quality_issues_output, quality_issues_instances, format_style_counts, item_count_stats, cleanup_rules_agg, case_values_agg, format_aggs)

    # Display filter parameters
    filters_applied = _describe_active_filters(options)
    if filters_applied:
        click.echo(f"Filters: {', '.join(filters_applied)}\n")

    # Collect and process trials
    trials, file_count = _collect_trials(options.max_item_length, options.verbose, options, state)
    trial_sets = _group_trials_into_sets(trials)
    for trial_set in trial_sets.values():
        _process_trial_set(trial_set, state)

    # Compute quality and consistency
    consolidated_dict = dict(consolidated)
    format_consistency, quality_issues_dict = _compute_quality_and_consistency(
        consolidated_dict, trial_sets, state.format_aggs, state.quality_issues_output,
        state.quality_issues_instances, state.format_style_counts, state.cleanup_rules_agg, ISSUE_TYPES)

    # Write results and reports
    quality_results = QualityAnalysisResults(
        quality_issues_dict=quality_issues_dict,
        format_consistency=format_consistency
    )
    report_options = ReportOptions(file_count=file_count, analysis=options.analysis, verbose=options.verbose)
    return _write_results_and_reports(state, quality_results, report_options)


def _compute_quality_and_consistency(consolidated_dict, trial_sets, format_aggs,
                                     quality_issues_output, quality_issues_instances,
                                     format_style_counts, cleanup_rules_agg, ISSUE_TYPES):
    """Compute cross-trial consistency flags and build quality issues dict.
    Returns (format_consistency, quality_issues_dict)."""
    # Detect treatment consistency for each trial set.
    TREATMENT_FIELDS = ["formatStyle", "codeblock"]
    format_consistency = _compute_format_consistency(consolidated_dict, TREATMENT_FIELDS)

    # Compute cross-trial case inconsistency using trial sets.
    quality_ctx = QualityContext(output=quality_issues_output, examples=quality_issues_instances)
    _flag_case_inconsistencies(trial_sets, quality_ctx)

    # Detect format rule inconsistencies for each file type using trial sets.
    for ext, fmt_meta in format_aggs.items():
        _flag_format_inconsistencies(trial_sets, quality_ctx,
                                      fmt_meta['label'], fmt_meta['issue_key'])

    # Build quality_issues_dict from trial sets
    quality_issues_dict = _build_quality_issues_dict(
        trial_sets, format_consistency, format_style_counts, quality_issues_output,
        quality_issues_instances, cleanup_rules_agg, ISSUE_TYPES, TREATMENT_FIELDS)

    return format_consistency, quality_issues_dict


def _write_results_and_reports(state, quality_results, options):
    """Write all result files and reports. Returns True on success, False on error."""
    TREATMENT_FIELDS = ["formatStyle", "codeblock"]
    consolidated_dict = dict(state.consolidated)
    try:
        _write_results_and_quality_json(consolidated_dict, quality_results.quality_issues_dict,
                                        options.file_count, options.verbose)
        _print_skip_summary(state.skipped_trials, state.zero_item_files)

        # Print analysis report for all file types per model and temperature
        if options.analysis and options.verbose:
            try:
                quality_ctx = QualityContext(output=state.quality_issues_output, instances=state.quality_issues_instances)
                _print_analysis_report(state.item_count_stats, quality_results.quality_issues_dict,
                                        quality_ctx, quality_results.format_consistency,
                                        TREATMENT_FIELDS)
            except Exception as report_err:
                click.echo(f"Warning: Could not generate full analysis report ({report_err})")

        _write_unique_items_file(consolidated_dict, options.verbose)
        _write_unique_source_items_file(state.source_items, options.verbose)

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


def _display_choice_options(title, choices):
    """Display choice options for user selection."""
    click.echo(f"\n{title}:")
    for idx, choice in enumerate(choices, 1):
        click.echo(f"  {idx:2d}. {choice}")
    click.echo(f"   0. (none/skip)")


def _parse_selection_input(selection_input, num_choices):
    """Parse user input into selected indices. Returns tuple (success, indices_or_error_msg)."""
    if selection_input == '0' or selection_input == '':
        return True, []
    try:
        indices = [int(x) - 1 for x in selection_input.split()]
        return True, indices
    except ValueError:
        return False, "Invalid input. Please enter space-separated numbers."


def _validate_selection_indices(indices, num_choices):
    """Validate selected indices are within range. Returns tuple (valid, error_msg)."""
    if any(idx < 0 or idx >= num_choices for idx in indices):
        return False, "Invalid selection. Please enter valid numbers."
    return True, None


def prompt_for_selections(title, choices):
    """
    Prompt user to select from a list of choices by number or space-separated numbers.
    Returns list of selected items.
    """
    if not choices:
        return []

    _display_choice_options(title, choices)

    while True:
        selection = click.prompt("Enter number(s) separated by spaces", default='0').strip()
        success, result = _parse_selection_input(selection, len(choices))
        if not success:
            click.echo(result)
            continue

        if not result:
            return []

        valid, error_msg = _validate_selection_indices(result, len(choices))
        if not valid:
            click.echo(error_msg)
            continue

        return [choices[idx] for idx in result]


def _extract_metadata_values(metadata, experiments, models, temperatures):
    """Extract experiment, model, and temperature values from metadata."""
    if metadata.get("experiment"):
        experiments.add(metadata["experiment"])
    if metadata.get("model"):
        models.add(metadata["model"])
    if metadata.get("temperature") is not None:
        temperatures.add(metadata["temperature"])


def _should_collect_from_file(file_path):
    """Check if file should be processed for value collection."""
    if not file_path.is_file():
        return False
    if file_path.name in SKIP_PATTERNS:
        return False
    if not is_standard_filename(file_path.name):
        return False
    return True


def collect_available_values():
    """Scan results directory and collect available experiments, models, temperatures."""
    experiments = set()
    models = set()
    temperatures = set()

    for file_path in RESULTS_DIR.iterdir():
        if not _should_collect_from_file(file_path):
            continue

        try:
            metadata = parse_filename_metadata(file_path.name)
            _extract_metadata_values(metadata, experiments, models, temperatures)
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

    success = summarize_results(SummarizeFilters(
        filename_filter=filter, model=model, format_type=format_type, experiment=experiment,
        timestamp=timestamp, temperature=temperature, max_item_length=max_item_length,
        analysis=analysis, exclude_model=exclude_model, verbose=verbose,
    ))
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
