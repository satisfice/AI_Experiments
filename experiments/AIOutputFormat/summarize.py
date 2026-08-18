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
from utils import format_error, detect_preamble_leak, format_timestamp, is_standard_filename
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
        (quality_issues_output, quality_issues_examples) — nested dicts for tracking
        quality issues and their example source files.
        Structure: model -> temperature -> file_type -> prompt -> issue_type -> {items|examples}
    """
    quality_issues_output = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(
                    lambda: {k: set() for k in issue_types}
                )
            )
        )
    )
    quality_issues_examples = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(
                    lambda: {k: {} for k in issue_types}
                )
            )
        )
    )
    return quality_issues_output, quality_issues_examples


def _make_format_style_counts():
    """Create the format style counts tracking dict.

    Returns:
        format_style_counts — nested dict for counting how many files use each format style.
        Structure: model -> temperature -> file_type -> prompt -> formatStyle -> count
    """
    return defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(
                    lambda: defaultdict(int)
                )
            )
        )
    )


def _make_cleanup_rules_agg():
    """Create the cleanup rules aggregation dict.

    Returns:
        cleanup_rules_agg — nested Counter dict tracking rule invocation across trials.
        Structure: model -> temperature -> file_type -> prompt -> Counter(rule_name -> count)
        Each count is the number of trials in the set that triggered that rule.
    """
    return defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(Counter)
            )
        )
    )


def _make_format_aggregation_dicts():
    """Create the format-specific cleanup rule aggregation dicts (case, markdown, HTML, JSON, YAML, CSV, txt1).

    Returns:
        (case_values_agg, md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg,
         csv_cleanup_agg, txt1_cleanup_agg)
        Used for detecting cross-trial inconsistencies in formatting/casing.
    """
    case_values_agg = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(list)
            )
        )
    )
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


def _flag_case_inconsistencies(case_values_agg, quality_issues_output, quality_issues_examples):
    """Flag trial sets whose case type differs across files (more than one distinct
    case type observed for the same model/temperature/file_type/prompt). Mutates
    quality_issues_output and quality_issues_examples in place."""
    for model_name in case_values_agg:
        for temp_value in case_values_agg[model_name]:
            for file_type in case_values_agg[model_name][temp_value]:
                for prompt_name in case_values_agg[model_name][temp_value][file_type]:
                    entries = case_values_agg[model_name][temp_value][file_type][prompt_name]
                    distinct = set(v for v, _ in entries)
                    if len(distinct) <= 1:
                        continue
                    for case_val, fname in entries:
                        quality_issues_output[model_name][temp_value][file_type][prompt_name]["inconsistent_case"].add(case_val)
                        if case_val not in quality_issues_examples[model_name][temp_value][file_type][prompt_name]["inconsistent_case"]:
                            quality_issues_examples[model_name][temp_value][file_type][prompt_name]["inconsistent_case"][case_val] = fname


def _flag_format_inconsistencies(cleanup_agg, quality_issues_output, quality_issues_examples,
                                  file_type_label, issue_key):
    """Flag trial sets whose cleanup-rule sets differ across files for one file type
    (markdown/HTML/JSON/YAML), recording which rules varied and an example filename
    for each. Mutates quality_issues_output and quality_issues_examples in place."""
    for model_name in cleanup_agg:
        for temp_value in cleanup_agg[model_name]:
            for prompt_name in cleanup_agg[model_name][temp_value]:
                entries = cleanup_agg[model_name][temp_value][prompt_name]
                rule_sets = [rules for rules, _ in entries]
                if len(set(rule_sets)) <= 1:
                    continue
                all_rules = set().union(*rule_sets)
                common_rules = set.intersection(*[set(r) for r in rule_sets])
                varying_rules = all_rules - common_rules
                for rule in varying_rules:
                    quality_issues_output[model_name][temp_value][file_type_label][prompt_name][issue_key].add(rule)
                    if rule not in quality_issues_examples[model_name][temp_value][file_type_label][prompt_name][issue_key]:
                        example_fname = next((fname for rules, fname in entries if rule in rules), "unknown")
                        quality_issues_examples[model_name][temp_value][file_type_label][prompt_name][issue_key][rule] = example_fname


class TrialKey(NamedTuple):
    """Identifies one (model, temperature, file_type, prompt) combination --
    matches the tuple key already used for format_consistency lookups. A
    drop-in replacement for that raw tuple, with named field access."""
    model: str
    temperature: str
    file_type: str
    prompt: str


def _print_prompt_analysis(pd, key, format_consistency, treatment_fields, quality_issues_examples, safe_write):
    """Print one prompt's quality-issue breakdown within the analysis report."""
    model_name, temp_value, file_type, prompt_name = key
    safe_write(f"      {prompt_name}:")

    # Format consistency
    is_consistent = pd.get("consistentFormat", True)
    if is_consistent:
        safe_write(f"        Format: ✓ consistent")
    else:
        fc = format_consistency.get((model_name, str(temp_value), file_type, prompt_name), {})
        varying = [f for f in treatment_fields if len(set(fc.get(f, []))) > 1]

        # Build human-readable description of what varies.
        # treatment_fields is ["formatStyle", "codeblock"]; list
        # all distinct formatStyle values when that field varies.
        parts = []
        if "formatStyle" in varying:
            parts.extend(sorted(set(fc.get("formatStyle", []))))
        if "codeblock" in varying:
            parts.append("codeblock")
        safe_write(f"        Format: ✗ inconsistent ({', '.join(parts)})")

    # Format issues breakdown
    format_issue_counts = pd.get("formatIssues", {})
    if format_issue_counts:
        issues_str = ", ".join(f"{i}: {c}" for i, c in sorted(format_issue_counts.items()))
        safe_write(f"        Format Issues: {issues_str}")

    # Per-issue-type breakdown (punctuation types show an example filename; the rest don't)
    issue_display = [
        ("leading_punctuation", "Leading punctuation", True),
        ("trailing_punctuation", "Trailing punctuation", True),
        ("internal_punctuation", "Internal punctuation", True),
        ("exceeds_max_length", "Exceeds max length", False),
        ("preamble_leak", "Preamble leaks", False),
        ("markup_artifact", "Markup artifacts", False),
        ("repeated_chars", "Repeated characters", False),
    ]
    for issue_key, label, with_example in issue_display:
        items = [e["instance"] for e in pd.get(issue_key, [])]
        if not items:
            continue
        safe_write(f"        {label} ({len(items)} unique):")
        for item in items[:5]:
            suffix = ""
            if with_example:
                example_file = quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][issue_key].get(item)
                suffix = f" Example: {example_file}" if example_file else ""
            safe_write(f"          - {ascii(item)}{suffix}")
        if len(items) > 5:
            safe_write(f"          ... and {len(items) - 5} more")


def _print_analysis_report(item_count_stats, quality_issues_dict, quality_issues_examples,
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
                    _print_prompt_analysis(prompts_data[prompt_name], TrialKey(model_name, temp_value, file_type, prompt_name),
                                            format_consistency, treatment_fields, quality_issues_examples, safe_write)


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
    quality_issues_examples: dict
    format_consistency: dict
    format_style_counts: dict
    cleanup_rules_agg: dict
    issue_types: list
    treatment_fields: list


def _gather_all_combos(format_consistency, format_style_counts, quality_issues_output):
    """Collect all (model, temp, file_type, prompt) tuples from all data sources."""
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
    return sorted(all_combos, key=lambda x: (x[0], x[1], x[2].casefold(), x[3]))


def _build_prompt_data_section(model_name, temp_value, file_type, prompt_name, ctx):
    """Build quality issues, consistency, formatIssues, and cleanupRules for one prompt."""
    prompt_data = {}

    issues = ctx.quality_issues_output.get(model_name, {}).get(temp_value, {}).get(file_type, {}).get(prompt_name, {})
    for issue_type in ctx.issue_types:
        raw_items = issues.get(issue_type, set())
        if raw_items:
            items_with_source = []
            for item in raw_items:
                source = ctx.quality_issues_examples[model_name][temp_value][file_type][prompt_name][issue_type].get(item, "unknown")
                items_with_source.append({"instance": item, "source": source})
            items_with_source.sort(key=lambda x: x["instance"].lower())
            prompt_data[issue_type] = items_with_source

    fc = ctx.format_consistency.get((model_name, temp_value, file_type, prompt_name), {})
    has_format_inconsistency = False
    inconsistency_issue_types = {
        "markdown": "inconsistent_md_format",
        "HTML": "inconsistent_html_format",
        "JSON": "inconsistent_json_format",
        "YAML": "inconsistent_yaml_format",
    }
    if file_type in inconsistency_issue_types:
        inconsistency_type = inconsistency_issue_types[file_type]
        has_format_inconsistency = inconsistency_type in prompt_data and bool(prompt_data[inconsistency_type])

    if fc:
        varying = [f for f in ctx.treatment_fields if len(set(fc[f])) > 1]
        prompt_data["consistentFormat"] = len(varying) == 0 and not has_format_inconsistency
    else:
        prompt_data["consistentFormat"] = not has_format_inconsistency

    style_counts = ctx.format_style_counts.get(model_name, {}).get(temp_value, {}).get(file_type, {}).get(prompt_name, {})
    if style_counts:
        prompt_data["formatIssues"] = dict(style_counts)

    rules_counter = ctx.cleanup_rules_agg.get(model_name, {}).get(temp_value, {}).get(file_type, {}).get(prompt_name, {})
    if rules_counter:
        prompt_data["cleanupRules"] = dict(sorted(rules_counter.items()))

    return prompt_data


def _build_quality_issues_dict(format_consistency, format_style_counts, quality_issues_output,
                                quality_issues_examples, cleanup_rules_agg, issue_types, treatment_fields):
    """Build quality_issues_dict with hierarchy: model -> temperature -> file_type -> prompt."""
    ctx = _QualityDataContext(
        quality_issues_output=quality_issues_output,
        quality_issues_examples=quality_issues_examples,
        format_consistency=format_consistency,
        format_style_counts=format_style_counts,
        cleanup_rules_agg=cleanup_rules_agg,
        issue_types=issue_types,
        treatment_fields=treatment_fields
    )

    quality_issues_dict = {}
    all_combos = _gather_all_combos(format_consistency, format_style_counts, quality_issues_output)

    for model_name, temp_value, file_type, prompt_name in all_combos:
        prompt_data = _build_prompt_data_section(model_name, temp_value, file_type, prompt_name, ctx)
        quality_issues_dict \
            .setdefault(model_name, {}) \
            .setdefault(temp_value, {}) \
            .setdefault(file_type, {})[prompt_name] = prompt_data

    return quality_issues_dict


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
    if format_type and ext != f".{format_type}" and ext != format_type:
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


def _passes_metadata_filters(filename_metadata, file_name, experiment, model, exclude_model, temperature, timestamp):
    """Check whether a file's parsed filename metadata passes all the active
    experiment/model/exclude-model/temperature/timestamp filters."""
    if experiment and filename_metadata.get("experiment") != experiment:
        return False

    # Model filtering: include only specific model if specified, or exclude specific models
    file_model = filename_metadata.get("model")
    if model and file_model != model:
        return False
    # Exclude models by pattern (supports wildcards like gpt*, *llama*, etc.)
    if exclude_model and any(matches_model_pattern(file_model, pattern) for pattern in exclude_model):
        return False

    if temperature is not None:
        file_temp = filename_metadata.get("temperature")
        try:
            temp_filter = float(temperature)
        except (ValueError, TypeError):
            return False
        if file_temp != temp_filter:
            return False

    if timestamp:
        file_timestamp = Path(file_name).stem.split('-')[0]
        if file_timestamp != timestamp:
            return False

    return True


def _track_item_quality_issues(metadata, key, ext, quality_issues_output, quality_issues_examples, filename):
    """Track item-level and format-level quality issues from one file's metadata
    into quality_issues_output/examples. Mutates both in place."""
    model_name, temp_value, file_type, prompt_name = key
    if "itemIssues" in metadata:
        item_issues = metadata["itemIssues"]
        for issue_type in ["leading_punctuation", "trailing_punctuation", "internal_punctuation",
                           "exceeds_max_length", "preamble_leak",
                           "markup_artifact", "repeated_chars"]:
            example = item_issues.get(issue_type)
            if not example:
                continue
            # txt1 exception: leading-number items are expected format, not quality issues
            if issue_type == "leading_punctuation" and ext == '.txt1' \
                    and re.match(r'^\d+[\.\)\-\s]', example):
                continue
            quality_issues_output[model_name][str(temp_value)][file_type][prompt_name][issue_type].add(example)
            if example not in quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][issue_type]:
                quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][issue_type][example] = filename

        # repeated_sequence: use filename as instance so every affected trial
        # accumulates independently (items may repeat the same value across trials,
        # which would deduplicate if stored as instance strings).
        if item_issues.get("repeated_sequence"):
            quality_issues_output[model_name][str(temp_value)][file_type][prompt_name]["repeated_sequence"].add(filename)
            if filename not in quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name]["repeated_sequence"]:
                quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name]["repeated_sequence"][filename] = filename

    # Track format-level issues from metadata["formatIssues"]
    # (populated by parser_quality_issues and processingQualityIssues)
    if "formatIssues" in metadata:
        for fs_label in metadata["formatIssues"]:
            # For format-style quality issues, track the filename as the instance so
            # _trial_numbers_str can extract trial numbers and show abbreviated ranges
            quality_issues_output[model_name][str(temp_value)][file_type][prompt_name][fs_label].add(filename)
            if filename not in quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][fs_label]:
                quality_issues_examples[model_name][str(temp_value)][file_type][prompt_name][fs_label][filename] = filename


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


def _write_unique_items_file(consolidated_dict, verbose):
    """Write the always-on unique-items file (all non-empty items across every
    consolidated entry, deduplicated and sorted)."""
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

    # Collect all cleanup strings
    all_cleanups = codeblock_cleanups + parser_cleanups
    if metadata.get("processingCleanups"):
        all_cleanups.extend(metadata.pop("processingCleanups"))
    if all_cleanups:
        cleanup_dict = parse_cleanup_keys(all_cleanups)
        if cleanup_dict:
            metadata["cleanup"] = cleanup_dict

    # Collect format-level issues
    format_issues = []
    if parser_quality_issues:
        format_issues.extend(parser_quality_issues)
    if metadata.get("processingQualityIssues"):
        format_issues.extend(metadata.pop("processingQualityIssues"))
    if format_issues:
        metadata["formatIssues"] = format_issues

    # Merge filename metadata
    metadata.update(filename_metadata)

    # Track duplicates
    item_counts = Counter(items)
    metadata["duplicates"] = sum(1 for count in item_counts.values() if count > 1)

    # Merge sidecar metadata
    meta_path = META_DIR / (file_path.stem + ".meta.json")
    if meta_path.exists():
        try:
            with open(meta_path, 'r', encoding='utf-8') as mf:
                file_meta = json.load(mf)
            if "responseComplete" in file_meta:
                metadata["responseComplete"] = file_meta["responseComplete"]
            if "incompleteReason" in file_meta:
                metadata["incompleteReason"] = file_meta["incompleteReason"]
        except Exception:
            pass

    metadata = reorder_metadata(metadata)
    return items, metadata


def _update_aggregations_from_parsed_file(file_path, items, metadata, ext, state):
    """Update all aggregation dicts from parsed file data."""
    model_name = abbreviate_model_name(metadata.get("model", "unknown"))
    temp_value = metadata.get("temperature", "unknown")
    file_type = FORMAT_MAP.get(ext, ext)
    prompt_name = metadata.get("prompt", "unknown")

    # Track cleanup rules
    for rule_name in metadata.get("cleanup", {}).keys():
        state.cleanup_rules_agg[model_name][str(temp_value)][file_type][prompt_name][rule_name] += 1

    # Track case values
    case_value = metadata.pop("case", "lower")
    metadata.pop("consistentCase", None)
    state.case_values_agg[model_name][str(temp_value)][file_type][prompt_name].append((case_value, file_path.name))

    # Track cleanup rule sets per format
    rule_set = frozenset(metadata.get("cleanup", {}).keys())
    if ext in state.format_aggs:
        state.format_aggs[ext]['agg'][model_name][str(temp_value)][prompt_name].append((rule_set, file_path.name))

    # Track format styles
    state.format_style_counts[model_name][str(temp_value)][file_type][prompt_name][metadata.get("formatStyle", "unknown")] += 1
    for fs_label in metadata.get("formatIssues", []):
        state.format_style_counts[model_name][str(temp_value)][file_type][prompt_name][fs_label] += 1

    # Track quality issues
    _track_item_quality_issues(metadata, TrialKey(model_name, temp_value, file_type, prompt_name),
                                ext, state.quality_issues_output, state.quality_issues_instances, file_path.name)

    # Track item counts
    state.item_count_stats[model_name][str(temp_value)][file_type].append(len(items))

    # Add to consolidated
    state.consolidated[ext].append({
        "filename": file_path.name,
        "metadata": metadata,
        "items": items
    })


def _scan_and_process_files(filename_filter, format_type, max_item_length, verbose, options, state):
    """Scan result files, parse them, and populate aggregation state.
    Returns file_count."""
    file_count = 0

    # Scan results directory
    for file_path in sorted(RESULTS_DIR.iterdir()):
        ext = _should_attempt_result_file(file_path, filename_filter, format_type)
        if ext is None:
            continue

        if ext in SKIP_EXTENSIONS:
            state.skipped_trials.append(file_path.name)
            continue

        content = _read_result_file_content(file_path)
        if content is None:
            click.echo(f"Skipping (encoding error): {file_path.name}")
            state.skipped_trials.append(file_path.name)
            continue

        if ext not in PARSERS:
            click.echo(f"Skipping (no parser): {file_path.name}")
            state.skipped_trials.append(file_path.name)
            continue

        try:
            items, metadata = _parse_and_build_file_metadata(file_path, content, ext, max_item_length, options)

            if items is None:
                continue

            # Collect source items (only after filtering passes)
            for item in items:
                if item:
                    state.source_items.add(item)

            if metadata.get("itemCount") == 0:
                state.zero_item_files.append(file_path.name)

            # Update all aggregations from this file
            _update_aggregations_from_parsed_file(file_path, items, metadata, ext, state)

            file_count += 1
            click.echo(f"Processed: {file_path.name} ({len(items)} items)")

        except Exception as e:
            import traceback
            click.echo(f"Error parsing {file_path.name}: {e}")
            if verbose:
                click.echo(traceback.format_exc())
            state.skipped_trials.append(file_path.name)
            continue

    return file_count


def summarize_results(options):
    """
    Read all result files by type, parse items, and summarize into a single JSON.
    Structure: {filetype: [{filename: str, items: [...]}, ...], ...}

    Args:
        options: SummarizeFilters instance (see its field comments for details).
    """
    filename_filter = options.filename_filter
    model = options.model
    format_type = options.format_type
    experiment = options.experiment
    timestamp = options.timestamp
    temperature = options.temperature
    max_item_length = options.max_item_length
    analysis = options.analysis
    exclude_model = options.exclude_model
    verbose = options.verbose

    if not RESULTS_DIR.exists():
        click.echo(format_error("summarize", f"{RESULTS_DIR} directory not found"), err=True)
        return False

    consolidated = defaultdict(list)
    # All tracked issue types (item-level first, then format-style-derived using dash-separated names)
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
    # Initialize quality issue tracking structures
    quality_issues_output, quality_issues_examples = _make_issue_output_dicts(ISSUE_TYPES)
    format_style_counts = _make_format_style_counts()
    item_count_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    cleanup_rules_agg = _make_cleanup_rules_agg()
    case_values_agg, md_cleanup_agg, html_cleanup_agg, json_cleanup_agg, yaml_cleanup_agg, csv_cleanup_agg, txt1_cleanup_agg = _make_format_aggregation_dicts()

    # Map format types to their aggregations and metadata for post-scan flagging.
    format_aggs = {
        '.md': {'agg': md_cleanup_agg, 'label': 'markdown', 'issue_key': 'inconsistent_md_format'},
        '.html': {'agg': html_cleanup_agg, 'label': 'HTML', 'issue_key': 'inconsistent_html_format'},
        '.json': {'agg': json_cleanup_agg, 'label': 'JSON', 'issue_key': 'inconsistent_json_format'},
        '.yml': {'agg': yaml_cleanup_agg, 'label': 'YAML', 'issue_key': 'inconsistent_yaml_format'},
        '.yaml': {'agg': yaml_cleanup_agg, 'label': 'YAML', 'issue_key': 'inconsistent_yaml_format'},
        '.csv': {'agg': csv_cleanup_agg, 'label': 'CSV', 'issue_key': 'inconsistent_csv_format'},
        '.txt1': {'agg': txt1_cleanup_agg, 'label': 'numberedText', 'issue_key': 'inconsistent_txt1_format'},
    }
    file_count = 0
    skipped_trials = []  # Track trial filenames that were skipped
    zero_item_files = []  # Track files that produced 0 items
    source_items = set()  # Track unique items from raw parsed data

    # Display filter parameters
    filters_applied = _describe_active_filters(options)
    if filters_applied:
        click.echo(f"Filters: {', '.join(filters_applied)}\n")

    # Build aggregation state object
    state = AggregationState(
        consolidated=consolidated,
        quality_issues_output=quality_issues_output,
        quality_issues_instances=quality_issues_examples,
        format_style_counts=format_style_counts,
        item_count_stats=item_count_stats,
        cleanup_rules_agg=cleanup_rules_agg,
        case_values_agg=case_values_agg,
        format_aggs=format_aggs,
        skipped_trials=skipped_trials,
        zero_item_files=zero_item_files,
        source_items=source_items
    )

    # Scan and process all result files
    file_count = _scan_and_process_files(filename_filter, format_type, max_item_length, verbose, options, state)

    # Convert defaultdict to regular dict for JSON serialization
    consolidated_dict = dict(consolidated)

    # Compute cross-trial consistency flags and quality issues
    format_consistency, quality_issues_dict = _compute_quality_and_consistency(
        consolidated_dict, state.case_values_agg, state.format_aggs, state.quality_issues_output,
        state.quality_issues_instances, state.format_style_counts, state.cleanup_rules_agg, ISSUE_TYPES)

    # Write results and reports
    return _write_results_and_reports(consolidated_dict, quality_issues_dict, file_count, analysis, verbose,
                                      state.item_count_stats, state.quality_issues_instances, format_consistency,
                                      state.source_items, state.skipped_trials, state.zero_item_files)


def _compute_quality_and_consistency(consolidated_dict, case_values_agg, format_aggs,
                                     quality_issues_output, quality_issues_examples,
                                     format_style_counts, cleanup_rules_agg, ISSUE_TYPES):
    """Compute cross-trial consistency flags and build quality issues dict.
    Returns (format_consistency, quality_issues_dict)."""
    # Detect treatment consistency for each trial set.
    TREATMENT_FIELDS = ["formatStyle", "codeblock"]
    format_consistency = _compute_format_consistency(consolidated_dict, TREATMENT_FIELDS)

    # Compute cross-trial case inconsistency.
    _flag_case_inconsistencies(case_values_agg, quality_issues_output, quality_issues_examples)

    # Flag trial sets whose cleanup-rule sets differ across files, per file type.
    for ext, fmt_meta in format_aggs.items():
        _flag_format_inconsistencies(fmt_meta['agg'], quality_issues_output, quality_issues_examples,
                                      fmt_meta['label'], fmt_meta['issue_key'])

    # Build quality_issues_dict with hierarchy: model -> temperature -> file_type -> prompt
    quality_issues_dict = _build_quality_issues_dict(
        format_consistency, format_style_counts, quality_issues_output,
        quality_issues_examples, cleanup_rules_agg, ISSUE_TYPES, TREATMENT_FIELDS)

    return format_consistency, quality_issues_dict


def _write_results_and_reports(consolidated_dict, quality_issues_dict, file_count, analysis, verbose,
                               item_count_stats, quality_issues_examples, format_consistency,
                               source_items, skipped_trials, zero_item_files):
    """Write all result files and reports. Returns True on success, False on error."""
    TREATMENT_FIELDS = ["formatStyle", "codeblock"]
    try:
        _write_results_and_quality_json(consolidated_dict, quality_issues_dict, file_count, verbose)
        _print_skip_summary(skipped_trials, zero_item_files)

        # Print analysis report for all file types per model and temperature
        if analysis and verbose:
            try:
                _print_analysis_report(item_count_stats, quality_issues_dict,
                                        quality_issues_examples, format_consistency,
                                        TREATMENT_FIELDS)
            except Exception as report_err:
                click.echo(f"Warning: Could not generate full analysis report ({report_err})")

        _write_unique_items_file(consolidated_dict, verbose)
        _write_unique_source_items_file(source_items, verbose)

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

    success = summarize_results(SummarizeFilters(
        filename_filter=filter, model=model, format_type=format_type, experiment=experiment,
        timestamp=timestamp, temperature=temperature, max_item_length=max_item_length,
        analysis=analysis, exclude_model=exclude_model, verbose=verbose,
    ))
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
