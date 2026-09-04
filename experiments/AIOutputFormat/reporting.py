#!/usr/bin/env python3
"""Analysis and quality reporting: formatted output for summarize results.

Handles printing analysis reports, quality issue breakdowns, and format consistency status."""

import sys
from typing import List, Dict, Any, Tuple

from summarize import TrialKey, QualityContext, calculate_statistics


def _safe_write(text: str) -> None:
    """Write text to stdout with error handling for encoding issues."""
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


def print_format_consistency_status(
    prompt_data: dict,
    trial_key: TrialKey,
    format_consistency: dict,
    treatment_fields: List[str]
) -> None:
    """Print format consistency status for a prompt."""
    is_consistent = prompt_data.get("consistentFormat", True)
    if is_consistent:
        _safe_write(f"        Format: ✓ consistent")
    else:
        fc = format_consistency.get((trial_key.model, str(trial_key.temperature), trial_key.file_type, trial_key.prompt), {})
        varying = [f for f in treatment_fields if len(set(fc.get(f, []))) > 1]
        parts = []
        if "formatStyle" in varying:
            parts.extend(sorted(set(fc.get("formatStyle", []))))
        if "codeblock" in varying:
            parts.append("codeblock")
        _safe_write(f"        Format: ✗ inconsistent ({', '.join(parts)})")


def print_format_issues_breakdown(prompt_data: dict) -> None:
    """Print format issues summary for a prompt."""
    format_issue_counts = prompt_data.get("formatIssues", {})
    if format_issue_counts:
        issues_str = ", ".join(f"{i}: {c}" for i, c in sorted(format_issue_counts.items()))
        _safe_write(f"        Format Issues: {issues_str}")


def print_issue_instance_items(
    items: List[str],
    issue_key: str,
    with_instance: bool,
    trial_key: TrialKey,
    quality_ctx: QualityContext
) -> None:
    """Print instance items for an issue type."""
    for item in items[:5]:
        suffix = ""
        if with_instance:
            instance_file = quality_ctx.instances[trial_key.model][str(trial_key.temperature)][trial_key.file_type][trial_key.prompt][issue_key].get(item)
            suffix = f" Instance: {instance_file}" if instance_file else ""
        _safe_write(f"          - {ascii(item)}{suffix}")


def print_single_issue_type(
    issue_key: str,
    label: str,
    with_instance: bool,
    prompt_data: dict,
    trial_key: TrialKey,
    quality_ctx: QualityContext
) -> None:
    """Print breakdown for a single issue type."""
    items = [e["instance"] for e in prompt_data.get(issue_key, [])]
    if not items:
        return
    _safe_write(f"        {label} ({len(items)} unique):")
    print_issue_instance_items(items, issue_key, with_instance, trial_key, quality_ctx)
    if len(items) > 5:
        _safe_write(f"          ... and {len(items) - 5} more")


def print_issue_type_breakdown(
    prompt_data: dict,
    trial_key: TrialKey,
    quality_ctx: QualityContext
) -> None:
    """Print per-issue-type breakdown for a prompt."""
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
        print_single_issue_type(issue_key, label, with_instance, prompt_data, trial_key, quality_ctx)


def print_prompt_analysis(
    prompt_data: dict,
    trial_key: TrialKey,
    format_consistency: dict,
    treatment_fields: List[str],
    quality_ctx: QualityContext
) -> None:
    """Print one prompt's quality-issue breakdown within the analysis report."""
    _safe_write(f"      {trial_key.prompt}:")
    print_format_consistency_status(prompt_data, trial_key, format_consistency, treatment_fields)
    print_format_issues_breakdown(prompt_data)
    print_issue_type_breakdown(prompt_data, trial_key, quality_ctx)


def print_analysis_report(
    item_count_stats: Dict[str, Dict[Any, Dict[str, List[int]]]],
    quality_issues_dict: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
    quality_ctx: QualityContext,
    format_consistency: dict,
    treatment_fields: List[str]
) -> None:
    """Print the verbose per-model/temperature/file-type analysis report."""
    _safe_write("\n" + "="*70)
    _safe_write("DATA ANALYSIS REPORT BY MODEL, TEMPERATURE, AND FILE TYPE")
    _safe_write("="*70)

    for model_name in sorted(item_count_stats.keys()):
        _safe_write(f"\n{model_name}:")
        for temp_value in sorted(item_count_stats[model_name].keys(), key=lambda x: (x == "unknown", x)):
            _safe_write(f"  Temperature {temp_value}:")
            for file_type in sorted(item_count_stats[model_name][temp_value].keys(), key=str.casefold):
                counts = item_count_stats[model_name][temp_value][file_type]
                stats = calculate_statistics(counts)
                _safe_write(f"    {file_type} ({len(counts)} files):")
                _safe_write(f"      Items: max={stats['max']}, min={stats['min']}, avg={stats['avg']}, var={stats['var']}, mode={stats['mode']}")

                prompts_data = quality_issues_dict.get(model_name, {}).get(str(temp_value), {}).get(file_type, {})
                for prompt_name in sorted(prompts_data.keys()):
                    tk = TrialKey(model_name, temp_value, file_type, prompt_name)
                    print_prompt_analysis(prompts_data[prompt_name], tk, format_consistency, treatment_fields, quality_ctx)
