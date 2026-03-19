#!/usr/bin/env python3

import csv
import html as html_mod
import json
import sys
import textwrap
import click
from pathlib import Path
from collections import Counter, defaultdict
import plotly.graph_objects as go
from config import abbreviate_model_name, get_model_color, model_supports_temperature
from utils import format_error, print_error

# Color palette for formats (normalized to lowercase for matching)
FORMAT_COLORS = {
    'text': '#1f77b4',         # blue
    'json': '#ff7f0e',         # orange
    'numberedtext': '#2ca02c', # green
    'markdown': '#d62728',     # red
    'yaml': '#9467bd',         # purple
    'html': '#8c564b',         # brown
    'csv': '#e377c2',          # pink
}

# Map from data format names to filename extensions
FORMAT_TO_EXTENSION = {
    'text': 'txt',
    'numberedtext': 'txt1',
    'markdown': 'md',
    'json': 'json',
    'yaml': 'yml',
    'csv': 'csv',
    'html': 'html',
    # Case-insensitive variants
    'Text': 'txt',
    'NumberedText': 'txt1',
    'Markdown': 'md',
    'JSON': 'json',
    'YAML': 'yml',
    'CSV': 'csv',
    'HTML': 'html',
}


def get_file_extension(format_name):
    """Convert data format name to filename extension."""
    # Try exact match first, then try lowercase, then return lowercase as fallback
    format_lower = format_name.lower()
    return FORMAT_TO_EXTENSION.get(format_name, FORMAT_TO_EXTENSION.get(format_lower, format_lower))


def is_preamble(item):
    """
    Filter preamble text patterns.
    Returns True if the item should be filtered out.
    """
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

    return False


def hsl_to_rgb(h, s, l):
    """
    Convert HSL (hue [0-360], saturation [0-100], lightness [0-100]) to RGB hex color.
    """
    s = s / 100.0
    l = l / 100.0

    c = (1 - abs(2 * l - 1)) * s
    h_prime = h / 60.0
    x = c * (1 - abs(h_prime % 2 - 1))

    if h_prime < 1:
        r, g, b = c, x, 0
    elif h_prime < 2:
        r, g, b = x, c, 0
    elif h_prime < 3:
        r, g, b = 0, c, x
    elif h_prime < 4:
        r, g, b = 0, x, c
    elif h_prime < 5:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x

    m = l - c / 2
    r = int((r + m) * 255)
    g = int((g + m) * 255)
    b = int((b + m) * 255)

    return f'#{r:02x}{g:02x}{b:02x}'




def adjust_color_by_temperature(base_color_hex, temperature_str, model_name=None, models_list=None, max_temperature=2.0):
    """
    Adjust color based on temperature if model supports it.
    For models that support temperature: adjust from soft to vivid based on temperature
    For models that don't support temperature: return base color (more saturated)

    Args:
        base_color_hex: color in #rrggbb format
        temperature_str: temperature as string (e.g., "0.0", "1.0", "2.0")
        model_name: name of the model (for checking temperature support)
        models_list: list of all models (for context)
        max_temperature: temperature value that gives maximum vividness
    """
    # Check if model supports temperature
    supports_temp = True
    if model_name:
        try:
            supports_temp = model_supports_temperature(model_name)
        except:
            supports_temp = True  # default to adjusting if we can't determine

    # If model doesn't support temperature, increase saturation for the base color
    if not supports_temp:
        # Increase saturation of the base color
        from color_picker import increase_saturation
        return increase_saturation(base_color_hex, factor=1.4)

    try:
        # Handle "None" string from JSON serialization of None values
        if temperature_str == "None" or temperature_str is None:
            temp_value = 1.0
        else:
            temp_value = float(temperature_str)
    except (ValueError, TypeError):
        temp_value = 1.0

    # Normalize temperature (0.0 to 1.0 scale)
    temp_factor = min(temp_value / max_temperature, 1.0)

    # Parse hex color to RGB
    hex_color = base_color_hex.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # Convert RGB to HSL
    r_norm = r / 255.0
    g_norm = g / 255.0
    b_norm = b / 255.0

    max_c = max(r_norm, g_norm, b_norm)
    min_c = min(r_norm, g_norm, b_norm)
    l = (max_c + min_c) / 2

    if max_c == min_c:
        h = s = 0
    else:
        d = max_c - min_c
        s = d / (2 - max_c - min_c) if l > 0.5 else d / (max_c + min_c)

        if max_c == r_norm:
            h = (60 * ((g_norm - b_norm) / d) + 360) % 360
        elif max_c == g_norm:
            h = (60 * ((b_norm - r_norm) / d) + 120) % 360
        else:
            h = (60 * ((r_norm - g_norm) / d) + 240) % 360

    # Adjust saturation and lightness based on temperature
    # Low temp: soft (low saturation, higher lightness)
    # High temp: vivid (high saturation, medium lightness)
    new_saturation = 30 + (temp_factor * 70)   # 30-100% (soft to vivid)
    new_lightness = 70 - (temp_factor * 25)    # 70-45% (soft to darker)

    return hsl_to_rgb(h, new_saturation, new_lightness)


def load_results_json(json_path):
    """Load and parse results.json with UTF-8 encoding and error handling"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except UnicodeDecodeError as e:
        # Find line number by counting newlines up to error position
        with open(json_path, 'rb') as f:
            content_before = f.read(e.start)
            line_num = content_before.count(b'\n') + 1
        raise ValueError(
            f"Character encoding error in {json_path} at line {line_num}: {e.reason}"
        )
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in {json_path} at line {e.lineno}, column {e.colno}: {e.msg}"
        )


def aggregate_items_by_format_and_model(data):
    """
    For each unique combination of (experiment, format, prompt, model, temperature),
    count items (filtered) and track number of trials (files).
    Returns: dict[tuple] -> dict with Counter, trial_count, per_trial_counts, and metadata
    where tuple is (experiment, format, prompt, model, temperature)
    """
    result = defaultdict(lambda: {"counter": Counter(), "trial_count": 0, "per_trial_counts": [], "filenames": [], "metadata": {}})

    for file_type, entries in data.items():
        # Skip non-file-type entries (like malformedOutput metadata)
        if not isinstance(entries, list):
            continue

        for entry in entries:
            metadata = entry['metadata']
            model = metadata['model']
            format_type = metadata['format']
            experiment = metadata.get('experiment', 'unknown')
            prompt = metadata.get('prompt', 'unknown')
            temperature = metadata.get('temperature', 'default')
            items = entry.get('items', [])

            # Filter out preamble text
            filtered_items = [item for item in items if not is_preamble(item)]

            # Create unique key for this combination
            key = (experiment, format_type, prompt, model, temperature)

            # Count items for this combination
            result[key]["counter"].update(filtered_items)
            # Track items per trial
            result[key]["per_trial_counts"].append(len(filtered_items))
            # Increment trial count for each file/entry
            result[key]["trial_count"] += 1
            if "filename" in entry:
                result[key]["filenames"].append(entry["filename"])

            # Store metadata
            result[key]["metadata"] = {
                "experiment": experiment,
                "format": format_type,
                "prompt": prompt,
                "model": model,
                "temperature": temperature
            }

    return result


def _trial_numbers_str(instances):
    """Given a list of {"instance": ..., "source": filename} dicts, return a
    parenthesised sorted list of trial numbers extracted from the source filenames,
    e.g. '(3, 11, 17)'.  Returns an empty string if no sources are present."""
    nums = set()
    for entry in instances:
        src = entry.get("source", "")
        if src:
            try:
                nums.add(int(Path(src).stem.split('-')[-1]))
            except (ValueError, IndexError):
                pass
    if not nums:
        return ""
    return "(" + ", ".join(str(n) for n in sorted(nums)) + ")"


def get_cleanup_data_for_combo(quality_data, model, temperature, format_type, prompt):
    """
    Return (quality_issues, cleanup_rules) for a specific combo.
    quality_issues: list of human-readable issue strings (empty list if none).
    cleanup_rules: list of "rule (N)" strings showing trial counts (empty list if none).
    quality_data is keyed by abbreviated model name.
    """
    if not quality_data:
        return [], []

    abbrev_model = abbreviate_model_name(model)
    prompt_data = quality_data.get(abbrev_model, {}).get(str(temperature), {}).get(format_type, {}).get(prompt, {})
    if not prompt_data:
        return [], []

    issues = []

    # Prepend inconsistent format warning if flagged
    if not prompt_data.get("consistentFormat", True):
        issues.append("Inconsistent output format")

    # Non-empty issue lists (skip metadata keys)
    NON_ISSUE_KEYS = {"consistentFormat", "formatStyles", "cleanupRules"}
    for key, value in prompt_data.items():
        if key in NON_ISSUE_KEYS:
            continue
        if not value:
            continue
        if key == "parse-failed":
            # List each failed file individually instead of just the category label.
            for entry in sorted(value, key=lambda e: e.get("instance", "")):
                issues.append(f"Parsing failed completely for {entry['instance']}")
        elif key.startswith("inconsistent_"):
            issues.append(key.replace('_', ' ').title())
        else:
            label = key.replace('_', ' ').title()
            trial_str = _trial_numbers_str(value)
            issues.append(f"{label} {trial_str}".strip() if trial_str else label)

    # cleanupRules is a dict {rule: trial_count} in the current format, or a list in the old format.
    raw = prompt_data.get("cleanupRules", {})
    if isinstance(raw, dict):
        cleanup_rules = [f"{rule} ({count})" for rule, count in sorted(raw.items())]
    else:
        cleanup_rules = list(raw)
    return issues, cleanup_rules


def get_unique_items_sorted(data):
    """
    Get all unique items (filtered) sorted by total count descending.
    Returns: list of (item, total_count) tuples
    """
    all_items = Counter()

    for file_type, entries in data.items():
        # Skip non-file-type entries (like malformedOutput metadata)
        if not isinstance(entries, list):
            continue

        for entry in entries:
            items = entry.get('items', [])
            filtered_items = [item for item in items if not is_preamble(item)]
            all_items.update(filtered_items)

    # Sort by count descending, then alphabetically
    sorted_items = sorted(all_items.items(), key=lambda x: (-x[1], x[0]))
    return sorted_items


def generate_html_report_with_filters(items_by_format_model, all_items_sorted, formats, models, temperatures, experiments, prompts, data, output_path, quality_data=None, prompt_texts=None, format_prompts=None):
    """
    Generate individual charts for each (experiment, format, prompt, model, temperature) combination.
    Wrap each in divs with CSS classes for filtering based on experiment/prompt/format/model/temperature.
    Uses CSS display:none to show/hide based on checkbox selections.
    data: raw data for counting trials per combination
    items_by_format_model: dict with 5-tuple keys (experiment, format, prompt, model, temperature)
    quality_data: dict with quality issues by model/temperature/format (from quality.json)
    """
    if quality_data is None:
        quality_data = {}
    # Build mapping from 5-tuple keys to metadata and counters
    combo_info = {}  # (fmt, model, temp, exp, prompt) -> {"counter": Counter, ...}

    for key_tuple, value_dict in items_by_format_model.items():
        experiment, format_type, prompt, model, temperature = key_tuple
        counter = value_dict["counter"] if isinstance(value_dict, dict) else value_dict
        trial_count = value_dict.get("trial_count", 0) if isinstance(value_dict, dict) else 0
        per_trial_counts = value_dict.get("per_trial_counts", []) if isinstance(value_dict, dict) else []

        # Calculate max, min, and average items per trial
        max_items = max(per_trial_counts) if per_trial_counts else 0
        min_items = min(per_trial_counts) if per_trial_counts else 0

        combo_key = (format_type, model, str(temperature), experiment, prompt)
        combo_info[combo_key] = {
            "counter": counter,
            "trial_count": trial_count,
            "per_trial_counts": per_trial_counts,
            "max_items": max_items,
            "min_items": min_items,
            "experiment": experiment,
            "format": format_type,
            "prompt": prompt,
            "model": model,
            "temperature": str(temperature)
        }

    # First pass: collect all y values to find the maximum range for individual plots
    all_y_values = []
    x_items = [item for item, _ in all_items_sorted]
    # Create display version with truncated names (limit to 15 characters)
    x_items_display = [item[:15] if len(item) > 15 else item for item in x_items]

    for combo_key, info in combo_info.items():
        y_values = [info["counter"].get(item, 0) for item in x_items]
        all_y_values.extend(y_values)

    # Calculate max Y value for individual plots
    max_y = max(all_y_values) if all_y_values else 1

# Generate a figure for each (format, model, temperature, experiment, prompt) combination
    # The aggregated plot is rendered entirely by JavaScript after filters are applied.
    figures_html = {}
    figures_metadata = {}  # Store metadata for each plot
    plot_configs = []  # Plot data for deferred JavaScript rendering

    # Store per-combo y_values for dynamic aggregation in JavaScript
    combo_y_values = {}  # {combo_key_str: [y_values]}

    for combo_key, info in combo_info.items():
        fmt, model, temp, exp, prompt = combo_key
        counter = info["counter"]
        y_values = [counter.get(item, 0) for item in x_items]

        # Store for JavaScript dynamic aggregation
        combo_key_str = f"{fmt}|{model}|{temp}|{exp}|{prompt}"
        combo_y_values[combo_key_str] = y_values

        # Build a simple bar chart with fixed Y axis range
        fig = go.Figure(
            data=[go.Bar(
                x=x_items_display,
                y=y_values,
                marker=dict(color=FORMAT_COLORS.get(fmt.lower(), '#636363')),
                hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>',
            )]
        )

        fig.update_layout(
            height=400,
            showlegend=False,
            hovermode='x unified',
            margin=dict(l=10, r=10, t=40, b=30),
            xaxis=dict(tickangle=90),
            yaxis=dict(range=[0, max_y]),
            autosize=True,
            bargap=0.02,
            hoverlabel=dict(font=dict(size=16)),
        )

        chart_id = f"{fmt}-{model}-{temp}-{exp}-{prompt}".replace('.', '-').replace('+', '-').replace(' ', '-').replace('_', '-')
        div_id = f"graph-{chart_id}"

        # Collect plot config for deferred JavaScript rendering; embed a bare placeholder div.
        fig_dict = json.loads(fig.to_json())
        plot_configs.append({"divId": div_id, "data": fig_dict["data"], "layout": fig_dict["layout"]})
        figures_html[combo_key] = f'<div id="{div_id}" class="plotly-graph-div" style="height:400px; width:100%;"></div>'
        figures_metadata[combo_key] = {
            "format": fmt,
            "model": model,
            "temperature": temp,
            "experiment": exp,
            "prompt": prompt
        }

    # Build HTML with filters
    html_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Item Frequency Distribution Report</title>
    <script src="https://cdn.plot.ly/plotly-3.3.1.min.js" integrity="sha256-4rD3fugVb/nVJYUv5Ky3v+fYXoouHaBSP20WIJuEiWg=" crossorigin="anonymous"></script>
    <style>
        :root {
            --plot-min-width: 600px;
        }
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .header {
            background-color: #fff;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .header-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        h1 {
            margin: 0;
            color: #333;
        }
        .filter-section {
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
        }
        .filter-section h3 {
            margin: 0;
            color: #555;
            font-size: 14px;
            font-weight: 600;
            text-transform: uppercase;
            white-space: nowrap;
        }
        .filter-row {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
        }
        .filter-item {
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .filter-item input {
            cursor: pointer;
        }
        .filter-item label {
            cursor: pointer;
            font-size: 14px;
            color: #333;
        }
        /* Format and model filter checkboxes with color coding */
        .format-filter,
        .model-filter {
            appearance: none;
            -webkit-appearance: none;
            width: 18px;
            height: 18px;
            border: 2px solid;
            border-radius: 3px;
            cursor: pointer;
            background-color: white;
            transition: all 0.2s ease;
            position: relative;
        }
        .format-filter:checked,
        .model-filter:checked {
            background-color: currentColor;
        }
        #plots-container {
            margin-top: 20px;
            display: flex;
            flex-direction: column;
            gap: 30px;
            padding: 20px;
            min-width: 0;
            box-sizing: border-box;
        }
        .aggregated-section {
            padding: 20px;
            background-color: #fff;
            border-radius: 5px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            transition: background-color 0.3s ease, min-height 0.3s ease;
        }
        .aggregated-section.loading {
            background-color: #d3d3d3;
            min-height: 600px;
        }
        .aggregated-section.loading .plot-wrapper {
            opacity: 0;
            transition: opacity 0.3s ease;
        }
        .aggregated-section:not(.loading) .plot-wrapper {
            opacity: 1;
            transition: opacity 0.3s ease;
        }
        .prompt-column {
            display: contents;
        }
        .prompt-column-header {
            display: none;
        }
        .column-toggle-btn {
            padding: 8px 16px;
            font-size: 14px;
            color: #fff;
            background-color: #007bff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            transition: background-color 0.2s ease;
            font-weight: 600;
            white-space: nowrap;
        }
        .column-toggle-btn:hover {
            background-color: #0056b3;
        }
        .column-toggle-btn:active {
            background-color: #004085;
        }
        .column-btn-group {
            display: flex;
            flex-direction: column;
            align-items: stretch;
        }
        .progress-section {
            display: none;
            margin-top: 4px;
        }
        .progress-section.visible {
            display: block;
        }
        .progress-bar-container {
            width: 100%;
            height: 4px;
            background-color: #e0e0e0;
            border-radius: 2px;
            overflow: hidden;
        }
        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #4CAF50, #45a049);
            width: 0%;
            transition: width 0.1s ease;
        }
        /* Hide button in header when in dual-column mode */
        body.columns-2 #column-toggle-btn {
            display: none;
        }
        /* Button in right column header (dual-column mode only) */
        .right-column-toggle-btn {
            padding: 8px 16px;
            font-size: 14px;
            color: #fff;
            background-color: #007bff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-weight: 600;
            white-space: nowrap;
        }
        .right-column-toggle-btn:hover {
            background-color: #0056b3;
        }
        .right-column-toggle-btn:active {
            background-color: #004085;
        }
        .plot-title {
            font-weight: 600;
            font-size: 14px;
            color: #333;
            margin-bottom: 10px;
        }
        .plot-section {
            display: flex;
            flex-direction: column;
        }
        .plot-section.hidden {
            display: none;
        }
        .plot-wrapper {
            background-color: #fff;
            border-radius: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            border: 4px solid #ddd;
            padding: 10px;
            display: block;
            flex: 1;
            width: 100%;
            overflow: hidden;
        }
        .plot-wrapper > div {
            width: 100% !important;
            height: 100% !important;
        }
        .plot-wrapper .plotly-graphdiv {
            width: 100% !important;
            height: 100% !important;
        }
        .aggregated-section .plot-wrapper {
            border: 4px solid #000;
        }
        .plot-wrapper.hidden {
            display: none;
        }
        .plot-section.plot-loading .plot-wrapper {
            background-color: #e0e0e0;
            min-height: 400px;
        }
        /* Cleanup tooltip */
        #cleanup-tooltip {
            display: none;
            position: fixed;
            background: #fff;
            border: 1px solid #bbb;
            border-radius: 4px;
            padding: 8px 12px;
            max-width: 640px;
            z-index: 9999;
            font-size: 13px;
            line-height: 1.6;
            box-shadow: 2px 4px 10px rgba(0,0,0,0.18);
            pointer-events: none;
            white-space: normal;
        }
        /* Dual-column layout */
        .column {
            display: none;  /* Hidden by default, shown only in dual-column mode */
        }
        body.columns-2 {
            margin: 0;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }
        body.columns-2 .column {
            flex: 1;
            min-width: 0;
            display: flex;
            flex-direction: column;
            overflow-y: scroll;
            overflow-x: hidden;
            scrollbar-gutter: stable;
            padding: 0 10px;
            box-sizing: border-box;
        }
        body.columns-2 .column > * {
            flex-shrink: 0;
            width: 100%;
            min-width: 0;
            box-sizing: border-box;
        }
    </style>
</head>
<body>
    <div id="main-content">
    <div class="header">
        <div class="header-top">
            <h1>Item Frequency Distribution Report</h1>
            <div class="column-btn-group">
                <button id="column-toggle-btn" class="column-toggle-btn">Two Columns</button>
                <div class="progress-section" id="progress-section">
                    <div class="progress-bar-container">
                        <div class="progress-bar-fill" id="progress-bar-fill"></div>
                    </div>
                </div>
            </div>
        </div>

        <div class="filter-section">
            <h3>Experiment</h3>
            <div class="filter-row" id="experiment-filters">
"""

    # Add experiment checkboxes
    for exp in experiments:
        safe_id = exp.replace('.', '-').replace('+', '-').replace(' ', '-').replace('_', '-')
        html_content += f'                <div class="filter-item"><input type="checkbox" id="filter-exp-{safe_id}" class="exp-filter" value="{exp}" checked><label for="filter-exp-{safe_id}">{exp}</label></div>\n'

    html_content += """            </div>
        </div>

        <div class="filter-section">
            <h3>Prompt</h3>
            <div class="filter-row" id="prompt-filters">
"""

    # Add prompt checkboxes
    for prompt in prompts:
        safe_id = prompt.replace('.', '-').replace('+', '-').replace(' ', '-').replace('_', '-')
        html_content += f'                <div class="filter-item"><input type="checkbox" id="filter-prompt-{safe_id}" class="prompt-filter" value="{prompt}" checked><label for="filter-prompt-{safe_id}">{prompt}</label></div>\n'

    html_content += """            </div>
        </div>

        <div class="filter-section">
            <h3>Format</h3>
            <div class="filter-row" id="format-filters">
"""

    # Add format checkboxes with format-specific colors
    for fmt in formats:
        format_color = FORMAT_COLORS.get(fmt.lower(), '#636363')
        html_content += f'                <div class="filter-item"><input type="checkbox" id="filter-format-{fmt}" class="format-filter" value="{fmt}" checked style="border-color: {format_color};" data-format="{fmt}" data-color="{format_color}"><label for="filter-format-{fmt}">{fmt}</label></div>\n'

    # Add Aggregated as a format option
    html_content += '                <div class="filter-item"><input type="checkbox" id="filter-format-aggregated" class="format-filter agg-toggle" value="aggregated" checked data-format="aggregated"><label for="filter-format-aggregated">Aggregated</label></div>\n'

    html_content += """            </div>
        </div>

        <div class="filter-section">
            <h3>Model</h3>
            <div class="filter-row" id="model-filters">
"""

    # Add model checkboxes with model-specific colors
    for model in models:
        safe_id = model.replace('.', '-').replace('+', '-').replace(' ', '-')
        model_color = get_model_color(model)
        html_content += f'                <div class="filter-item"><input type="checkbox" id="filter-model-{safe_id}" class="model-filter" value="{model}" checked style="border-color: {model_color};" data-model="{model}" data-color="{model_color}"><label for="filter-model-{safe_id}">{abbreviate_model_name(model)}</label></div>\n'

    html_content += """            </div>
        </div>

        <div class="filter-section">
            <h3>Temperature</h3>
            <div class="filter-row" id="temperature-filters">
"""

    # Add temperature checkboxes
    for temp in temperatures:
        safe_id = temp.replace('.', '-').replace(' ', '-')
        html_content += f'                <div class="filter-item"><input type="checkbox" id="filter-temp-{safe_id}" class="temp-filter" value="{temp}" checked><label for="filter-temp-{safe_id}">{temp}</label></div>\n'

    html_content += """            </div>
        </div>
    </div>

    <div id="plots-container">
"""

    # Add aggregated plot at the top (spans all columns)
    # Title and plot data are computed by JavaScript after filters are applied.
    html_content += f'    <div class="aggregated-section loading" id="aggregated-section">\n'
    html_content += f'        <div class="plot-title" id="aggregated-title">Aggregated Results</div>\n'
    html_content += f'        <div class="plot-wrapper" id="aggregated-plot">\n'
    html_content += '            <div id="graph-aggregated" style="height:600px;"></div>\n'
    html_content += '        </div>\n'
    html_content += '    </div>\n\n'

    # Group plots by prompt
    plots_by_prompt = defaultdict(list)
    for combo_key, metadata in figures_metadata.items():
        prompt = metadata["prompt"]
        plots_by_prompt[prompt].append((combo_key, metadata))

    # Get sorted list of unique prompts
    sorted_prompts = sorted(plots_by_prompt.keys())

    # Single column layout - no dynamic CSS needed

    # Add each prompt column with its plots
    for prompt in sorted_prompts:
        html_content += f'    <div class="prompt-column" data-prompt="{prompt}">\n'
        html_content += f'        <div class="prompt-column-header">{prompt}</div>\n'

        # Add all plots for this prompt
        for combo_key, metadata in plots_by_prompt[prompt]:
            fmt = metadata["format"]
            model = metadata["model"]
            temp = metadata["temperature"]
            exp = metadata["experiment"]

            # Get item counts for this specific combination
            counter = combo_info[combo_key]["counter"]
            total_items = sum(counter.values())
            unique_items = len(counter)
            trial_count = combo_info[combo_key]["trial_count"]
            max_items = combo_info[combo_key]["max_items"]
            min_items = combo_info[combo_key]["min_items"]

            # Get colors
            format_color = FORMAT_COLORS.get(fmt.lower(), '#636363')
            model_base_color = get_model_color(model)
            background_color = adjust_color_by_temperature(model_base_color, temp, model, models)

            # Calculate percentage of unique items and average per trial
            unique_pct = (unique_items / total_items * 100) if total_items > 0 else 0
            avg_per_trial = total_items / trial_count if trial_count > 0 else 0

            # Build cleanup indicator with rich tooltip (quality issues in bold + cleanup rules).
            # Always emitted: red if quality problems, #555 if cleanup only, #aaa if nothing to clean.
            quality_issues, cleanup_rules = get_cleanup_data_for_combo(quality_data, model, temp, fmt, prompt)
            if quality_issues or cleanup_rules:
                # Build HTML tooltip content: quality issues in bold first, then cleanup rules.
                # Use raw strings in tooltip_lines (no pre-escaping); html_mod.escape() is
                # applied once to the assembled HTML for safe embedding in the attribute value.
                # JavaScript's getAttribute() decodes the entities, then innerHTML renders the HTML.
                tooltip_lines = []
                for issue in quality_issues:
                    tooltip_lines.append(f'<b>{issue}</b>')
                if cleanup_rules:
                    if quality_issues:
                        tooltip_lines.append('<span style="display:block;margin-top:4px"></span>')
                    for rule in cleanup_rules:
                        tooltip_lines.append(rule)
                tooltip_html = html_mod.escape('<br>'.join(tooltip_lines))
                color = "red" if quality_issues else "#555"
                quality_indicator = (
                    f' | <span class="cleanup-indicator" data-tooltip-html="{tooltip_html}"'
                    f' style="color: {color}; cursor: default;">Cleanup</span>'
                )
            else:
                # No cleanup or quality issues: show grayed-out indicator with no tooltip.
                quality_indicator = (
                    ' | <span style="color: #aaa; cursor: default;">Cleanup</span>'
                )

            # Build clipboard string for "Load set" button using actual filenames in the set
            set_filenames = sorted(combo_info[combo_key].get("filenames", []))
            if set_filenames:
                load_set_str = "np " + " ".join(set_filenames)
            else:
                # Fallback to wildcard if filenames were not recorded
                abbr_model = abbreviate_model_name(model)
                temp_code = "txx" if temp in ("None", None) else f"t{int(float(temp) * 10):02d}"
                file_ext = get_file_extension(fmt)
                load_set_str = f"np *{exp}-{prompt}-{abbr_model}-{temp_code}*.{file_ext}"

            # Build button HTML with onclick handler for clipboard copy
            load_set_button = (
                f' | <button '
                f'style="font-size: 0.85em; padding: 1px 6px; cursor: pointer;" '
                f'title="{load_set_str}" '
                f'onclick="var b=this; navigator.clipboard.writeText(\'{load_set_str}\').then(function(){{'
                f'b.textContent=\'Copied!\'; setTimeout(function(){{b.textContent=\'Load set\';}},1000);}});"'
                f'>Load set</button>'
            )

            # Build prompt tooltip: prompt text + double line break + format instruction.
            # Text is wrapped at 80 chars; lines separated by <br> for HTML rendering.
            _prompt_body = (prompt_texts or {}).get(prompt, '')
            _fmt_ext = get_file_extension(fmt)
            _fmt_instruction = (format_prompts or {}).get(_fmt_ext, '')
            _tooltip_parts = []
            if _prompt_body:
                _tooltip_parts.append('<br>'.join(textwrap.wrap(_prompt_body, width=80)))
            if _fmt_instruction:
                if _tooltip_parts:
                    _tooltip_parts.append('<br><br>')
                _tooltip_parts.append('<br>'.join(textwrap.wrap(_fmt_instruction, width=80)))
            _prompt_tooltip_attr = ''
            if _tooltip_parts:
                _escaped = html_mod.escape(''.join(_tooltip_parts))
                _prompt_tooltip_attr = f' class="prompt-indicator" data-tooltip-html="{_escaped}"'

            # Build HTML title with colored text (two lines, second line starts with Model)
            title_html = (
                f'Experiment: <span style="color: #333;">{exp}</span> | '
                f'Format: <span style="color: {format_color}; font-weight: bold;">{fmt}</span> | '
                f'Prompt: <span style="color: #333;"{_prompt_tooltip_attr}>{prompt}</span> | <br>'
                f'Model: <span style="color: {model_base_color}; font-weight: bold;">{abbreviate_model_name(model)}</span> | '
                f'Temperature: <span style="color: {background_color}; font-weight: bold;">{temp}</span> | '
                f'Trials: {trial_count} | Min: {min_items} | Max: {max_items} | Average: {avg_per_trial:.1f} | '
                f'Total: {total_items} | Unique: {unique_items} ({unique_pct:.1f}%){quality_indicator}{load_set_button}'
            )

            html_content += f'        <div class="plot-section" data-format="{fmt}" data-model="{model}" data-temperature="{temp}" data-prompt="{prompt}" data-experiment="{exp}">\n'
            html_content += f'            <div class="plot-title">{title_html}</div>\n'
            html_content += f'            <div class="plot-wrapper" style="background-color: {background_color}; border-color: {model_base_color};">\n'
            html_content += figures_html[combo_key]
            html_content += '            </div>\n'
            html_content += '        </div>\n\n'

        html_content += '    </div>\n\n'

    html_content += """    </div>
    </div>

    <div id="cleanup-tooltip"></div>

    <script>
        // Shared tooltip for .cleanup-indicator and .prompt-indicator
        (function() {
            var tooltip = document.getElementById('cleanup-tooltip');
            var SELECTOR = '.cleanup-indicator, .prompt-indicator';
            document.addEventListener('mouseover', function(e) {
                var el = e.target.closest(SELECTOR);
                if (!el) return;
                var raw = el.getAttribute('data-tooltip-html') || '';
                tooltip.innerHTML = raw;
                tooltip.style.display = 'block';
            });
            document.addEventListener('mousemove', function(e) {
                if (tooltip.style.display === 'none') return;
                var x = e.clientX + 14;
                var y = e.clientY + 14;
                // Keep tooltip within viewport
                var tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
                if (x + tw > window.innerWidth)  x = e.clientX - tw - 6;
                if (y + th > window.innerHeight) y = e.clientY - th - 6;
                tooltip.style.left = x + 'px';
                tooltip.style.top  = y + 'px';
            });
            document.addEventListener('mouseout', function(e) {
                var el = e.target.closest(SELECTOR);
                if (!el) return;
                tooltip.style.display = 'none';
            });
        })();

        // Store data for dynamic aggregation
        const aggregationData = {
            items: """ + json.dumps(x_items_display) + """,
            comboData: """ + json.dumps(combo_y_values) + """,
            globalMaxY: """ + str(int(max_y)) + """
        };
        const plotConfigs = """ + json.dumps(plot_configs) + """;

        // Snapshot of main-content HTML before any Plotly rendering — only bare <div>
        // placeholders, no SVG. Used by initializeDualColumns instead of cloneNode(true).
        const mainContentTemplate = document.getElementById('main-content').innerHTML;

        // Build lookup map for O(1) access by divId, pre-compute per-plot max Y
        const plotConfigMap = {};
        plotConfigs.forEach(function(cfg) {
            plotConfigMap[cfg.divId] = cfg;
            cfg.maxY = Math.max.apply(null, cfg.data[0].y);
        });

        // Dynamic Y-axis max for individual plots (updated as filters change)
        var currentMaxY = aggregationData.globalMaxY;

        // Lazy rendering: IntersectionObserver renders plots as they scroll into view
        var activeObservers = [];

        function observePlots(container) {
            // In two-column mode, columns are the scroll containers; otherwise use viewport
            var scrollRoot = (container.classList && container.classList.contains('column')) ? container : null;

            var observer = new IntersectionObserver(function(entries) {
                entries.forEach(function(entry) {
                    if (entry.isIntersecting) {
                        var div = entry.target;
                        var cfg = plotConfigMap[div.id];
                        if (cfg) {
                            var layout = Object.assign({}, cfg.layout);
                            layout.yaxis = Object.assign({}, cfg.layout.yaxis, {range: [0, currentMaxY]});
                            Plotly.newPlot(div, cfg.data, layout);
                            var section = div.closest('.plot-section');
                            if (section) section.classList.remove('plot-loading');
                        }
                        observer.unobserve(div);
                    }
                });
            }, { root: scrollRoot, rootMargin: '500px' });

            container.querySelectorAll('.plotly-graph-div').forEach(function(div) {
                if (!div.classList.contains('js-plotly-plot')) {
                    var section = div.closest('.plot-section');
                    if (section) section.classList.add('plot-loading');
                    observer.observe(div);
                }
            });

            activeObservers.push(observer);
            return observer;
        }

        function cleanupObservers() {
            activeObservers.forEach(function(obs) { obs.disconnect(); });
            activeObservers = [];
        }

        // Update Y-axis scale for all individual plots based on visible plot maximums
        var yAxisScaleTimeout;
        function updatePlotYAxisScale() {
            // Find all active containers
            var containers = [];
            if (isInTwoColumnMode && savedColumns) {
                containers = [savedColumns.column1, savedColumns.column2];
            } else {
                var mc = document.getElementById('main-content');
                if (mc) containers = [mc];
            }

            // Find max Y across all visible plots in all containers
            var maxY = 0;
            containers.forEach(function(container) {
                container.querySelectorAll('.plot-section:not(.hidden)').forEach(function(section) {
                    var plotDiv = section.querySelector('.plotly-graph-div');
                    if (plotDiv) {
                        var cfg = plotConfigMap[plotDiv.id];
                        if (cfg && cfg.maxY > maxY) maxY = cfg.maxY;
                    }
                });
            });

            if (maxY === 0) maxY = 1;
            if (maxY === currentMaxY) return;  // Skip relayout if scale unchanged
            currentMaxY = maxY;

            // Update only visible rendered individual plots' Y-axis ranges
            containers.forEach(function(container) {
                container.querySelectorAll('.plot-section:not(.hidden) .js-plotly-plot').forEach(function(plotDiv) {
                    if (plotDiv.id === 'graph-aggregated') return;
                    Plotly.relayout(plotDiv, {'yaxis.range': [0, maxY]});
                });
            });
        }

        // Recalculate aggregated plot based on visible plots, scoped to one container
        function updateAggregatedPlot(primaryContainer) {
            const selectedExps = new Set(Array.from(primaryContainer.querySelectorAll('.exp-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));
            const selectedPrompts = new Set(Array.from(primaryContainer.querySelectorAll('.prompt-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));
            const selectedFormats = new Set(Array.from(primaryContainer.querySelectorAll('.format-filter'))
                .filter(function(cb) { return cb.checked && !cb.classList.contains('agg-toggle'); }).map(function(cb) { return cb.value; }));
            const selectedModels = new Set(Array.from(primaryContainer.querySelectorAll('.model-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));
            const selectedTemps = new Set(Array.from(primaryContainer.querySelectorAll('.temp-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));

            // Sum y_values from all matching combos
            const aggregatedY = new Array(aggregationData.items.length).fill(0);
            const itemLen = aggregationData.items.length;

            // Pre-build filter check for efficiency
            const hasFilters = selectedExps.size > 0 && selectedPrompts.size > 0 &&
                               selectedFormats.size > 0 && selectedModels.size > 0 && selectedTemps.size > 0;

            if (hasFilters) {
                const combos = Object.keys(aggregationData.comboData);
                for (let i = 0; i < combos.length; i++) {
                    const comboKeyStr = combos[i];
                    const parts = comboKeyStr.split('|');
                    const fmt = parts[0];
                    const model = parts[1];
                    const temp = parts[2];
                    const exp = parts[3];
                    const prompt = parts[4];

                    // Check if this combo matches all selected filters
                    if (selectedExps.has(exp) && selectedPrompts.has(prompt) &&
                        selectedFormats.has(fmt) && selectedModels.has(model) && selectedTemps.has(temp)) {
                        // Sum y values across all matching combos
                        const yValues = aggregationData.comboData[comboKeyStr];
                        for (let j = 0; j < itemLen; j++) {
                            aggregatedY[j] += yValues[j];
                        }
                    }
                }
            }

            // Render aggregated plot scoped to this container (full item set on X-axis)
            // Y-axis max is the sum of the most frequently appearing item in selected filters
            var aggMaxY = Math.max.apply(null, aggregatedY) || 1;
            var aggPlot = primaryContainer.querySelector('[id="graph-aggregated"]');
            if (aggPlot) {
                Plotly.react(aggPlot, [{
                    type: 'bar',
                    x: aggregationData.items,
                    y: aggregatedY,
                    marker: {color: '#636363'},
                    hovertemplate: '<b>%{x}</b><br>Count: %{y}<extra></extra>'
                }], {
                    height: 600,
                    showlegend: false,
                    hovermode: 'x unified',
                    margin: {l: 50, r: 10, t: 40, b: 200},
                    xaxis: {tickangle: 90},
                    yaxis: {range: [0, aggMaxY]},
                    autosize: true,
                    bargap: 0.02,
                    hoverlabel: {font: {size: 16}}
                });
            }

            // Calculate and update aggregated plot header with statistics
            const totalItems = aggregatedY.reduce(function(sum, val) { return sum + val; }, 0);
            const uniqueItems = aggregatedY.filter(function(val) { return val > 0; }).length;
            const newTitle = 'Aggregated Results (' + totalItems + ' items; ' + uniqueItems + ' unique)';

            var titleDiv = primaryContainer.querySelector('[id="aggregated-title"]');
            if (titleDiv) {
                titleDiv.textContent = newTitle;
            }

            // Remove loading state from aggregated section
            var aggSection = primaryContainer.querySelector('[id="aggregated-section"]');
            if (aggSection) {
                aggSection.classList.remove('loading');
            }
        }

        // Handle dual-column layout if requested
        function initializeDualColumns() {
            const mainContent = document.getElementById('main-content');

            // Build columns from the pre-render template (bare <div> placeholders, no SVG).
            // This is nearly instant compared to cloneNode(true) on a rendered Plotly document.
            const clone1 = document.createElement('div');
            clone1.innerHTML = mainContentTemplate;
            const clone2 = document.createElement('div');
            clone2.innerHTML = mainContentTemplate;

            const column1 = document.createElement('div');
            column1.className = 'column';
            column1.appendChild(clone1);

            const column2 = document.createElement('div');
            column2.className = 'column';
            column2.appendChild(clone2);

            mainContent.style.display = 'none';
            document.body.classList.add('columns-2');
            document.body.appendChild(column1);
            document.body.appendChild(column2);

            savedColumns = { column1: column1, column2: column2, mainContent: mainContent };

            // Add toggle button to right column header
            const rightHeader = column2.querySelector('.header');
            if (rightHeader) {
                const rightHeaderTop = rightHeader.querySelector('.header-top');
                if (rightHeaderTop) {
                    const rightToggleBtn = document.createElement('button');
                    rightToggleBtn.id = 'right-column-toggle-btn';
                    rightToggleBtn.className = 'right-column-toggle-btn';
                    rightToggleBtn.textContent = 'Single Column';
                    rightToggleBtn.addEventListener('click', function() {
                        document.getElementById('column-toggle-btn').click();
                    });
                    rightHeaderTop.appendChild(rightToggleBtn);
                }
            }

            column1.addEventListener('scroll', function() { column2.scrollTop = column1.scrollTop; });
            column2.addEventListener('scroll', function() { column1.scrollTop = column2.scrollTop; });

            // Set up event listeners and lazy-render plots via IntersectionObserver
            cleanupObservers();
            setupColumnEventListeners(column1);
            setupColumnEventListeners(column2);
            observePlots(column1);
            observePlots(column2);
            updatePlotYAxisScale();
        }

        // Don't initialize dual columns on page load - start in single column mode
        // User can toggle to dual columns with the button

        // Column-scoped visibility: each column's checkboxes control only
        // that column's plots. In single-column mode, scope is the document.
        var columnContainers = document.querySelectorAll('.column');
        var primaryContainer;  // The container used for aggregated plot calculation
        if (columnContainers.length === 0) {
            columnContainers = [document];
            primaryContainer = document;
        } else {
            primaryContainer = columnContainers[0];  // Left column in dual-column mode
        }

        var aggregatedPlotTimeouts = new WeakMap();  // Per-container debounce timers

        function updateColumnVisibility(container) {
            var selectedExps = new Set(Array.from(container.querySelectorAll('.exp-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));
            var selectedPrompts = new Set(Array.from(container.querySelectorAll('.prompt-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));
            var selectedFormats = new Set(Array.from(container.querySelectorAll('.format-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));
            var selectedModels = new Set(Array.from(container.querySelectorAll('.model-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));
            var selectedTemps = new Set(Array.from(container.querySelectorAll('.temp-filter'))
                .filter(function(cb) { return cb.checked; }).map(function(cb) { return cb.value; }));

            container.querySelectorAll('.plot-section').forEach(function(section) {
                var shouldShow = selectedExps.has(section.getAttribute('data-experiment')) &&
                                 selectedPrompts.has(section.getAttribute('data-prompt')) &&
                                 selectedFormats.has(section.getAttribute('data-format')) &&
                                 selectedModels.has(section.getAttribute('data-model')) &&
                                 selectedTemps.has(section.getAttribute('data-temperature'));
                section.classList.toggle('hidden', !shouldShow);
            });

            // Debounce aggregated plot update per container
            var existingTimeout = aggregatedPlotTimeouts.get(container);
            if (existingTimeout) clearTimeout(existingTimeout);
            aggregatedPlotTimeouts.set(container, setTimeout(function() {
                updateAggregatedPlot(container);
            }, 100));

            // Debounce global Y-axis scale update (cross-column)
            clearTimeout(yAxisScaleTimeout);
            yAxisScaleTimeout = setTimeout(updatePlotYAxisScale, 100);
        }

        function resizeColumnPlots(container) {
            container.querySelectorAll('.js-plotly-plot').forEach(function(plotDiv) {
                var plotSection = plotDiv.closest('.plot-section');
                if (!plotSection || !plotSection.classList.contains('hidden')) {
                    Plotly.Plots.resize(plotDiv);
                }
            });
            // Also resize aggregated plot within this container
            container.querySelectorAll('[id="graph-aggregated"]').forEach(function(aggPlot) {
                var aggSection = aggPlot.closest('.aggregated-section');
                if (aggSection && !aggSection.classList.contains('hidden')) {
                    Plotly.Plots.resize(aggPlot);
                }
            });
        }

        function setupColumnEventListeners(container) {
            // This function sets up all event listeners for a column
            // It needs to be called both on page load and after creating dual columns

            // Style format checkboxes: outline with format color, fill on check
            container.querySelectorAll('.format-filter').forEach(function(checkbox) {
                var color = checkbox.getAttribute('data-color');
                if (color) {
                    checkbox.style.borderColor = color;
                    checkbox.style.color = color;
                    checkbox.addEventListener('change', function() {
                        this.style.backgroundColor = this.checked ? color : 'white';
                    });
                    if (checkbox.checked) {
                        checkbox.style.backgroundColor = color;
                    }
                }
            });

            // Style model checkboxes: outline with model color, fill on check
            container.querySelectorAll('.model-filter').forEach(function(checkbox) {
                var color = checkbox.getAttribute('data-color');
                if (color) {
                    checkbox.style.borderColor = color;
                    checkbox.style.color = color;
                    checkbox.addEventListener('change', function() {
                        this.style.backgroundColor = this.checked ? color : 'white';
                    });
                    if (checkbox.checked) {
                        checkbox.style.backgroundColor = color;
                    }
                }
            });

            // Handle aggregated plot toggle
            container.querySelectorAll('.agg-toggle').forEach(function(aggToggle) {
                var aggregatedTitle = container.querySelector('[id="aggregated-title"]');
                var aggregatedPlot = container.querySelector('[id="aggregated-plot"]');
                if (aggregatedTitle && aggregatedPlot) {
                    aggToggle.addEventListener('change', function() {
                        aggregatedTitle.style.display = this.checked ? 'block' : 'none';
                        aggregatedPlot.style.display = this.checked ? 'block' : 'none';
                        resizeColumnPlots(container);
                    });
                }
            });

            // Attach change listeners to all filter checkboxes in this column
            container.querySelectorAll('.exp-filter, .prompt-filter, .format-filter, .model-filter, .temp-filter')
                .forEach(function(checkbox) {
                    checkbox.addEventListener('change', function() {
                        updateColumnVisibility(container);
                    });
                });

            // Initialize visibility for this column
            updateColumnVisibility(container);
        }

        // Set up event listeners for initial column containers
        columnContainers.forEach(function(container) {
            setupColumnEventListeners(container);
        });

        // Lazy-render individual plots via IntersectionObserver
        if (columnContainers[0] === document) {
            observePlots(document.getElementById('main-content') || document.body);
        } else {
            columnContainers.forEach(function(container) {
                observePlots(container);
            });
        }

        // Update aggregated plot immediately (always visible at the top)
        columnContainers.forEach(function(container) {
            updateAggregatedPlot(container);
        });

        // Initialize Y-axis scale for individual plots
        updatePlotYAxisScale();

        // Column mode toggle
        var isInTwoColumnMode = false;
        var columnToggleBtn = document.getElementById('column-toggle-btn');
        var savedColumns = null;  // Store column references for cleanup

        function updateColumnModeButton() {
            if (isInTwoColumnMode) {
                columnToggleBtn.textContent = 'Single Column';
            } else {
                columnToggleBtn.textContent = 'Two Columns';
            }
        }

        columnToggleBtn.addEventListener('click', function() {
            if (isInTwoColumnMode) {
                // Switch to single column mode
                isInTwoColumnMode = false;
                document.body.classList.remove('columns-2');

                // Remove columns from DOM and show main content
                cleanupObservers();
                if (savedColumns) {
                    savedColumns.column1.remove();
                    savedColumns.column2.remove();
                    savedColumns.mainContent.style.display = 'block';
                    savedColumns = null;
                }

                // Re-setup listeners and re-observe any unrendered plots
                var mainContent = document.getElementById('main-content');
                if (mainContent) {
                    setupColumnEventListeners(mainContent);
                    observePlots(mainContent);
                    updatePlotYAxisScale();
                    // Resize already-rendered plots after layout reflow
                    setTimeout(function() {
                        mainContent.querySelectorAll('.js-plotly-plot').forEach(function(plotDiv) {
                            Plotly.Plots.resize(plotDiv);
                        });
                    }, 50);
                }
            } else {
                // Switch to two column mode
                isInTwoColumnMode = true;
                initializeDualColumns();
            }
            updateColumnModeButton();
        });

        updateColumnModeButton();
    </script>

</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)


def write_spreadsheet_csv(items_by_format_model, output_path):
    """Write summary spreadsheet CSV with one row per (experiment, prompt, model, temperature, format).

    Columns: experiment, prompt, model, temperature, format, trials,
             min items, max items, average items, variance, total items,
             unique items, percent unique.

    'unique items' counts distinct items across all trials in the group.
    'total items' is the sum of item counts across all trials.
    'percent unique' is unique / total * 100.
    Variance is sample variance (n-1 denominator); 0 when trials <= 1.
    """
    rows = []
    for (experiment, format_type, prompt, model, temperature), group in items_by_format_model.items():
        counts = group["per_trial_counts"]
        counter = group["counter"]
        trials = group["trial_count"]

        if counts:
            min_items = min(counts)
            max_items = max(counts)
            avg_items = sum(counts) / len(counts)
            variance = (sum((x - avg_items) ** 2 for x in counts) / (len(counts) - 1)
                        if len(counts) > 1 else 0.0)
        else:
            min_items = max_items = avg_items = variance = 0.0

        total_items = sum(counter.values())
        unique_items = len(counter)
        percent_unique = round(unique_items / total_items * 100, 1) if total_items > 0 else 0.0

        rows.append({
            "experiment": experiment,
            "prompt": prompt,
            "model": model,
            "temperature": temperature,
            "format": format_type,
            "trials": trials,
            "min items": min_items,
            "max items": max_items,
            "average items": round(avg_items, 2),
            "variance": round(variance, 2),
            "total items": total_items,
            "unique items": unique_items,
            "percent unique": percent_unique,
        })

    rows.sort(key=lambda r: (r["experiment"], r["prompt"], r["model"], str(r["temperature"]), r["format"]))

    fieldnames = [
        "experiment", "prompt", "model", "temperature", "format",
        "trials", "min items", "max items", "average items", "variance",
        "total items", "unique items", "percent unique",
    ]
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@click.command()
@click.option('--experiment', type=str, default=None,
              help='Experiment name to filter (optional). Output: results/report_{experiment}.html')
@click.option('-i', '--input', type=str, default=None,
              help='Override input file path (default: results/results.json)')
@click.option('-o', '--output', type=str, default=None,
              help='Override output file path (default: results/report.html or results/report_{experiment}.html if --experiment is set)')
def main(experiment, input, output):
    """Generate HTML report with bar charts from experiment results"""

    # Construct paths from experiment name if not explicitly provided
    if input is None:
        input = 'results/results.json'
    if output is None:
        if experiment:
            output = f'results/report_{experiment}.html'
        else:
            output = 'results/report.html'

    input_path = Path(input)
    output_path = Path(output)

    if not input_path.exists():
        click.echo(format_error("generate_report", f"Input file not found: {input_path}"), err=True)
        sys.exit(1)

    try:
        # Load data
        click.echo("Loading results.json...")
        data = load_results_json(input_path)

        # Load quality data if available
        quality_data = {}
        quality_path = input_path.parent / 'quality.json'
        if quality_path.exists():
            click.echo("Loading quality data...")
            quality_data = load_results_json(quality_path)
        else:
            click.echo("Quality data not found (run summarize --analysis to generate)")

        # Extract unique values for filtering
        formats = set()
        models = set()
        temperatures = set()
        experiments = set()
        prompts = set()
        for file_type, entries in data.items():
            # Skip non-file-type entries (like malformedOutput metadata)
            if not isinstance(entries, list):
                continue

            for entry in entries:
                metadata = entry.get('metadata', {})
                formats.add(metadata.get('format', 'unknown'))
                models.add(metadata.get('model', 'unknown'))
                # Temperature might be in metadata or filename
                temp = metadata.get('temperature', 'default')
                temperatures.add(str(temp))
                exp = metadata.get('experiment', 'unknown')
                experiments.add(exp)
                prompt = metadata.get('prompt', 'unknown')
                prompts.add(prompt)

        # Sort for consistent ordering
        formats = sorted(formats)
        models = sorted(models)
        temperatures = sorted(temperatures)
        experiments = sorted(experiments)
        prompts = sorted(prompts)

        click.echo(f"Found formats: {', '.join(formats)}")
        click.echo(f"Found models: {', '.join(models)}")
        click.echo(f"Found temperatures: {', '.join(temperatures)}")
        click.echo(f"Found experiments: {', '.join(experiments)}")
        click.echo(f"Found prompts: {', '.join(prompts)}")

        # Filter data by experiment if specified
        if experiment:
            click.echo(f"Filtering for experiment: {experiment}")
            filtered_data = {}
            for file_type, entries in data.items():
                if not isinstance(entries, list):
                    filtered_data[file_type] = entries
                    continue
                filtered_entries = [
                    entry for entry in entries
                    if entry.get('metadata', {}).get('experiment', '') == experiment
                ]
                if filtered_entries:
                    filtered_data[file_type] = filtered_entries
            data = filtered_data

        # Load formats.json for format instructions (keyed by extension, e.g. "html", "json")
        format_prompts = {}
        formats_json_path = Path(__file__).parent / 'formats.json'
        if formats_json_path.exists():
            formats_data = json.loads(formats_json_path.read_text(encoding='utf-8'))
            format_prompts = {k: v.get('prompt', '') for k, v in formats_data.items()}

        # Load .prompt files for each discovered prompt name
        script_dir = Path(__file__).parent
        prompt_texts = {}
        for prompt_name in prompts:
            prompt_file = script_dir / f'{prompt_name}.prompt'
            if prompt_file.exists():
                prompt_texts[prompt_name] = prompt_file.read_text(encoding='utf-8').strip()

        # Aggregate items
        click.echo("Aggregating items by format and model...")
        items_by_format_model = aggregate_items_by_format_and_model(data)

        # Get unique items sorted
        click.echo("Sorting items...")
        all_items_sorted = get_unique_items_sorted(data)
        click.echo(f"Found {len(all_items_sorted)} unique items (after filtering preambles)")

        # Export to HTML with filters (generates separate charts for each format)
        click.echo(f"Writing report to {output_path}...")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        generate_html_report_with_filters(items_by_format_model, all_items_sorted, formats, models, temperatures, experiments, prompts, data, str(output_path), quality_data, prompt_texts=prompt_texts, format_prompts=format_prompts)

        click.echo(f"Success. Report generated at {output_path}")

        # Write summary spreadsheet alongside the report
        spreadsheet_path = output_path.parent / "spreadsheet.csv"
        write_spreadsheet_csv(items_by_format_model, spreadsheet_path)
        click.echo(f"Wrote spreadsheet to {spreadsheet_path}")

    except Exception as e:
        click.echo(format_error("generate_report", str(e)), err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
