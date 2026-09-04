#!/usr/bin/env python3
"""User interaction helpers: data collection and selection logic (platform-agnostic).

Designed for both CLI and HTML frontends. Pure functions return data structures;
IO (click, HTML rendering) is kept in the presentation layer."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Set, Optional
from fnmatch import fnmatch

from process_single_file import parse_filename_metadata, is_standard_filename

# Default results directory; can be overridden for testing or alternate paths
DEFAULT_RESULTS_DIR = Path("results")

# Skip patterns when scanning for metadata
SKIP_PATTERNS = {"results.json", "quality.json", "unique_items.txt", "unique_source_items.txt", "spreadsheet.csv"}


@dataclass
class SelectionRequest:
    """Platform-agnostic representation of a selection prompt.
    Can be rendered by CLI (via click.prompt) or HTML (via form)."""
    title: str
    choices: List[str]
    allow_none: bool = True
    allow_multiple: bool = False
    description: Optional[str] = None


def matches_model_pattern(model_name: str, pattern: str) -> bool:
    """Check if model name matches pattern.
    Supports:
    - Exact matches: haiku matches claudehaiku4520251001
    - Wildcards: gpt*, *llama*, t0*
    - Case-insensitive matching"""
    model_lower = model_name.lower()
    pattern_lower = pattern.lower()

    if '*' in pattern_lower or '?' in pattern_lower:
        return fnmatch(model_lower, pattern_lower)

    return pattern_lower in model_lower


def parse_selection_input(selection_input: str, num_choices: int) -> Tuple[bool, List[int]]:
    """Parse user input into selected indices.
    Returns (success, indices_or_empty_list).
    Input '0' or '' means no selection."""
    if selection_input == '0' or selection_input == '':
        return True, []
    try:
        indices = [int(x) - 1 for x in selection_input.split()]
        return True, indices
    except ValueError:
        return False, []


def validate_selection_indices(indices: List[int], num_choices: int) -> Tuple[bool, Optional[str]]:
    """Validate selected indices are within range.
    Returns (valid, error_message_or_None)."""
    if any(idx < 0 or idx >= num_choices for idx in indices):
        return False, "Invalid selection. Please enter valid numbers."
    return True, None


def extract_selection_from_indices(choices: List[str], indices: List[int]) -> List[str]:
    """Extract selected items from choices by indices.
    Assumes indices are already validated."""
    return [choices[idx] for idx in indices]


def extract_metadata_values(
    metadata: dict,
    experiments: Set[str],
    models: Set[str],
    temperatures: Set[float]
) -> None:
    """Extract experiment, model, and temperature values from parsed filename metadata.
    Mutates the three sets in place."""
    if metadata.get("experiment"):
        experiments.add(metadata["experiment"])
    if metadata.get("model"):
        models.add(metadata["model"])
    if metadata.get("temperature") is not None:
        temperatures.add(metadata["temperature"])


def should_collect_from_file(file_path: Path) -> bool:
    """Check if file should be processed for metadata value collection."""
    if not file_path.is_file():
        return False
    if file_path.name in SKIP_PATTERNS:
        return False
    if not is_standard_filename(file_path.name):
        return False
    return True


def collect_available_values(results_dir: Path = None) -> Tuple[List[str], List[str], List[float]]:
    """Scan results directory and collect available experiments, models, temperatures.

    Args:
        results_dir: Path to results directory. Defaults to DEFAULT_RESULTS_DIR.

    Returns:
        (sorted_experiments, sorted_models, sorted_temperatures)"""
    if results_dir is None:
        results_dir = DEFAULT_RESULTS_DIR

    experiments = set()
    models = set()
    temperatures = set()

    if not results_dir.exists():
        return [], [], []

    for file_path in results_dir.iterdir():
        if not should_collect_from_file(file_path):
            continue

        try:
            metadata = parse_filename_metadata(file_path.name)
            extract_metadata_values(metadata, experiments, models, temperatures)
        except Exception:
            pass

    return sorted(experiments), sorted(models), sorted(temperatures)


def build_selection_requests(
    experiments: List[str],
    models: List[str],
    temperatures: List[float]
) -> List[SelectionRequest]:
    """Build SelectionRequest objects for interactive mode.

    Returns list of requests (for experiments, models-to-exclude, temperatures) in that order."""
    requests = []

    if experiments:
        requests.append(SelectionRequest(
            title="Available Experiments",
            choices=experiments,
            allow_none=True,
            allow_multiple=True,
            description="Select experiments to include"
        ))

    if models:
        requests.append(SelectionRequest(
            title="Available Models to Exclude",
            choices=models,
            allow_none=True,
            allow_multiple=True,
            description="Select models to exclude from analysis"
        ))

    if temperatures:
        temp_strs = [str(t) for t in temperatures]
        requests.append(SelectionRequest(
            title="Available Temperatures",
            choices=temp_strs,
            allow_none=True,
            allow_multiple=True,
            description="Select temperatures to include"
        ))

    return requests
