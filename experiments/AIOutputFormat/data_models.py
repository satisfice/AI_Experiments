#!/usr/bin/env python3
"""Shared data models and dataclasses for trial processing and analysis.

Separated to avoid circular imports."""

from dataclasses import dataclass
from typing import NamedTuple, Optional, List


class TrialKey(NamedTuple):
    """Identifies one (model, temperature, file_type, format_hardness, experiment,
    prompt) combination -- the sole identity type for a trial, used everywhere in
    the codebase (including generate_report.py, which formerly kept its own
    separate Combo type omitting format_hardness while TrialKey omitted
    experiment -- the two gaps caused two different silent-collision bugs before
    being unified here). format_hardness sits next to file_type since it qualifies
    the format request; experiment sits next to prompt since an experiment is a
    named batch of prompts. Order matches the nesting used throughout the
    aggregation dicts keyed by these same fields."""
    model: str
    temperature: str
    file_type: str
    format_hardness: str
    experiment: str
    prompt: str


@dataclass
class QualityContext:
    """Bundles quality issues output and instances for cohesive passing to functions."""
    output: dict
    instances: dict


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
    """Represents a set of trials that share one TrialKey, varying only in
    iteration number. Example: 3 trials with the same model/temp/format/
    hardness/experiment/prompt but iterations 01, 02, 03.
    """
    key: TrialKey           # Identity shared by every trial in this set
    trials: list            # List of Trial objects in this set

    @property
    def model(self) -> str:
        return self.key.model

    @property
    def temperature(self) -> str:
        return self.key.temperature

    @property
    def file_type(self) -> str:
        return self.key.file_type

    @property
    def format_hardness(self) -> str:
        return self.key.format_hardness

    @property
    def experiment(self) -> str:
        return self.key.experiment

    @property
    def prompt(self) -> str:
        return self.key.prompt

    def extract_case_values(self):
        """Extract (case_value, filename) pairs from all trials in this set."""
        return [(trial.metadata.get("case", "lower"), trial.filename) for trial in self.trials]

    def detect_case_inconsistencies(self):
        """Detect if trials in this set have inconsistent case handling.

        Returns: {case_value: instance_filename} or {} if consistent.
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

        Returns: {varying_rule: instance_filename} or {} if consistent.
        """
        rule_sets = [frozenset(trial.metadata.get("cleanup", {}).keys()) for trial in self.trials]

        if len(set(rule_sets)) <= 1:
            return {}  # All trials have same rule set

        all_rules = set().union(*rule_sets)
        common_rules = set.intersection(*[set(r) for r in rule_sets]) if rule_sets else set()
        varying_rules = all_rules - common_rules

        # Find instance filename for each varying rule
        issues = {}
        for rule in varying_rules:
            for trial in self.trials:
                trial_rules = frozenset(trial.metadata.get("cleanup", {}).keys())
                if rule in trial_rules:
                    issues[rule] = trial.filename
                    break
        return issues


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
