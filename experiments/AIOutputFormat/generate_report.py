#!/usr/bin/env python3

import json
import sys
import click
from pathlib import Path
from collections import Counter, defaultdict
import plotly.graph_objects as go
from config import abbreviate_model_name

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


def get_model_color(model_name, models_list):
    """
    Get a primary color base for a model.
    Assigns evenly spaced hues to each model.
    Will be adjusted by temperature from soft to vivid.
    """
    if not models_list:
        return '#6666cc'  # default blue

    model_index = sorted(models_list).index(model_name)
    hue = (model_index * 360 / len(models_list)) % 360

    # Base saturation/lightness for low temperature (soft)
    # Will be adjusted based on temperature
    return hsl_to_rgb(hue, 60, 60)


def adjust_color_by_temperature(base_color_hex, temperature_str, max_temperature=2.0):
    """
    Adjust color from soft to vivid based on temperature.
    Low temperature: soft, muted version of the color
    High temperature: vivid, saturated primary version
    base_color_hex: color in #rrggbb format
    temperature_str: temperature as string (e.g., "0.0", "1.0", "2.0")
    max_temperature: temperature value that gives maximum vividness
    """
    try:
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


def load_consolidated_data(json_path):
    """Load and parse consolidated.json"""
    with open(json_path, 'r') as f:
        return json.load(f)


def aggregate_items_by_format_and_model(data):
    """
    For each unique combination of (experiment, format, prompt, model, temperature),
    count items (filtered) and track number of trials (files).
    Returns: dict[tuple] -> dict with Counter, trial_count, per_trial_counts, and metadata
    where tuple is (experiment, format, prompt, model, temperature)
    """
    result = defaultdict(lambda: {"counter": Counter(), "trial_count": 0, "per_trial_counts": [], "metadata": {}})

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

            # Store metadata
            result[key]["metadata"] = {
                "experiment": experiment,
                "format": format_type,
                "prompt": prompt,
                "model": model,
                "temperature": temperature
            }

    return result


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


def generate_html_report_with_filters(items_by_format_model, all_items_sorted, formats, models, temperatures, experiments, prompts, data, output_path):
    """
    Generate individual charts for each (experiment, format, prompt, model, temperature) combination.
    Wrap each in divs with CSS classes for filtering based on experiment/prompt/format/model/temperature.
    Uses CSS display:none to show/hide based on checkbox selections.
    data: raw data for counting trials per combination
    items_by_format_model: dict with 5-tuple keys (experiment, format, prompt, model, temperature)
    """
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

    # Create aggregated plot first
    aggregated_counter = Counter()
    for info in combo_info.values():
        aggregated_counter.update(info["counter"])

    aggregated_y = [aggregated_counter.get(item, 0) for item in x_items]

    agg_fig = go.Figure(
        data=[go.Bar(
            x=x_items_display,
            y=aggregated_y,
            marker=dict(color='#636363'),
            hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>',
        )]
    )

    agg_fig.update_layout(
        height=400,
        showlegend=False,
        hovermode='x unified',
        margin=dict(l=10, r=10, t=40, b=30),
        xaxis=dict(tickangle=90),
        autosize=True,
    )

    aggregated_plotly_html = agg_fig.to_html(include_plotlyjs=False, div_id="graph-aggregated")
    agg_div_start = aggregated_plotly_html.find('<div id="graph-aggregated"')
    agg_script_start = aggregated_plotly_html.find('<script', agg_div_start)
    agg_script_end = aggregated_plotly_html.find('</script>', agg_script_start) + 9

    if agg_div_start != -1 and agg_script_start != -1 and agg_script_end > agg_script_start:
        aggregated_html = aggregated_plotly_html[agg_div_start:agg_script_end]
    else:
        aggregated_html = aggregated_plotly_html

    # Generate a figure for each (format, model, temperature, experiment, prompt) combination
    figures_html = {}
    figures_metadata = {}  # Store metadata for each plot
    figures_html['aggregated'] = aggregated_html

    for combo_key, info in combo_info.items():
        fmt, model, temp, exp, prompt = combo_key
        counter = info["counter"]
        y_values = [counter.get(item, 0) for item in x_items]

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
        )

        # Get Plotly HTML
        chart_id = f"{fmt}-{model}-{temp}-{exp}-{prompt}".replace('.', '-').replace('+', '-').replace(' ', '-').replace('_', '-')
        plotly_html = fig.to_html(include_plotlyjs=False, div_id=f"graph-{chart_id}")

        # Extract graph div and script
        div_start = plotly_html.find(f'<div id="graph-{chart_id}"')
        script_start = plotly_html.find('<script', div_start)
        script_end = plotly_html.find('</script>', script_start) + 9

        if div_start != -1 and script_start != -1 and script_end > script_start:
            chart_html = plotly_html[div_start:script_end]
        else:
            chart_html = plotly_html

        figures_html[combo_key] = chart_html
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
        }
        .prompt-column {
            display: contents;
        }
        .prompt-column-header {
            display: none;
        }
        .zoom-indicator {
            display: inline-block;
            margin-left: 20px;
            font-size: 14px;
            color: #666;
            background-color: #f0f0f0;
            padding: 5px 10px;
            border-radius: 3px;
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
        /* Dual-column layout */
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
            <div class="zoom-indicator">Zoom: <span id="zoom-level">100%</span> (Use +/- keys)</div>
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
        model_color = get_model_color(model, models)
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
    # Calculate total items across all combinations
    aggregated_total_items = sum(aggregated_counter.values())
    aggregated_unique_items = len(aggregated_counter)
    agg_title = f"Aggregated Results ({aggregated_total_items} items; {aggregated_unique_items} unique)"
    html_content += f'    <div class="aggregated-section">\n'
    html_content += f'        <div class="plot-title" id="aggregated-title">{agg_title}</div>\n'
    html_content += f'        <div class="plot-wrapper" id="aggregated-plot">\n'
    html_content += figures_html['aggregated']
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
            model_base_color = get_model_color(model, models)
            background_color = adjust_color_by_temperature(model_base_color, temp)

            # Calculate percentage of unique items and average per trial
            unique_pct = (unique_items / total_items * 100) if total_items > 0 else 0
            avg_per_trial = total_items / trial_count if trial_count > 0 else 0

            # Build HTML title with colored text (two lines, second line starts with Model)
            title_html = (
                f'Experiment: <span style="color: #333;">{exp}</span> | '
                f'Format: <span style="color: {format_color}; font-weight: bold;">{fmt}</span> | '
                f'Prompt: <span style="color: #333;">{prompt}</span> | <br>'
                f'Model: <span style="color: {model_base_color}; font-weight: bold;">{abbreviate_model_name(model)}</span> | '
                f'Temperature: <span style="color: {background_color}; font-weight: bold;">{temp}</span> | '
                f'Trials: {trial_count} | Min: {min_items} | Max: {max_items} | Average: {avg_per_trial:.1f} | '
                f'Total Items: {total_items} | Unique: {unique_items} ({unique_pct:.1f}%)'
            )

            html_content += f'        <div class="plot-section" data-format="{fmt}" data-model="{model}" data-temperature="{temp}" data-prompt="{prompt}" data-experiment="{exp}">\n'
            html_content += f'            <div class="plot-title">{title_html}</div>\n'
            html_content += f'            <div class="plot-wrapper" style="background-color: {background_color};">\n'
            html_content += figures_html[combo_key]
            html_content += '            </div>\n'
            html_content += '        </div>\n\n'

        html_content += '    </div>\n\n'

    html_content += """    </div>
    </div>

    <script>
        // Handle dual-column layout if requested
        function initializeDualColumns() {
            const params = new URLSearchParams(window.location.search);
            if (params.get('cols') !== '2') return;

            const mainContent = document.getElementById('main-content');

            // Save Plotly chart data/layout before DOM manipulation.
            // cloneNode(true) copies HTML structure but NOT Plotly's internal
            // JavaScript state, so cloned charts are static snapshots that
            // cannot be resized. We must re-create them with Plotly.newPlot().
            const chartData = [];
            mainContent.querySelectorAll('.plotly-graph-div').forEach(function(div) {
                if (div.data && div.layout) {
                    chartData.push({
                        id: div.id,
                        data: JSON.parse(JSON.stringify(div.data)),
                        layout: JSON.parse(JSON.stringify(div.layout))
                    });
                }
            });

            const clone = mainContent.cloneNode(true);

            // Create column wrappers
            const column1 = document.createElement('div');
            column1.className = 'column';
            column1.appendChild(mainContent);

            const column2 = document.createElement('div');
            column2.className = 'column';
            column2.appendChild(clone);

            // Replace body content with columns
            document.body.innerHTML = '';
            document.body.classList.add('columns-2');
            document.body.appendChild(column1);
            document.body.appendChild(column2);

            // Synchronized vertical scrolling
            column1.addEventListener('scroll', function() {
                column2.scrollTop = column1.scrollTop;
            });
            column2.addEventListener('scroll', function() {
                column1.scrollTop = column2.scrollTop;
            });

            // Each column has independent filters (no syncing)

            // Re-initialize Plotly charts after DOM layout has settled
            setTimeout(function() {
                // Column 1: originals still have Plotly state, just resize
                column1.querySelectorAll('.plotly-graph-div').forEach(function(div) {
                    Plotly.Plots.resize(div);
                });

                // Column 2: cloned divs have no Plotly state, re-create from saved data
                var col2Plots = column2.querySelectorAll('.plotly-graph-div');
                chartData.forEach(function(saved, idx) {
                    if (idx < col2Plots.length) {
                        Plotly.newPlot(col2Plots[idx], saved.data, saved.layout, {responsive: true});
                    }
                });
            }, 300);
        }

        // Initialize dual columns before setting up event listeners
        initializeDualColumns();

        // Column-scoped visibility: each column's checkboxes control only
        // that column's plots. In single-column mode, scope is the document.
        var columnContainers = document.querySelectorAll('.column');
        if (columnContainers.length === 0) {
            columnContainers = [document];
        }

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
        }

        function resizeColumnPlots(container) {
            container.querySelectorAll('.plotly-graph-div').forEach(function(plotDiv) {
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

        // Set up each column container independently
        columnContainers.forEach(function(container) {
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
                        resizeColumnPlots(container);
                    });
                });

            // Initialize visibility for this column
            updateColumnVisibility(container);
        });

        // Zoom is intentionally global (affects both columns via CSS variable)
        var zoomLevel = 1.0;
        var minPlotWidth = 300;
        var maxPlotWidth = 1200;
        var baseWidth = 600;

        function updateZoom() {
            var newWidth = Math.round(baseWidth * zoomLevel);
            document.documentElement.style.setProperty('--plot-min-width', newWidth + 'px');
            document.querySelectorAll('[id="zoom-level"]').forEach(function(el) {
                el.textContent = Math.round(zoomLevel * 100) + '%';
            });
        }

        document.addEventListener('keydown', function(event) {
            if (document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
                if (event.key === '+' || event.key === '=') {
                    event.preventDefault();
                    zoomLevel = Math.min(zoomLevel + 0.1, maxPlotWidth / baseWidth);
                    updateZoom();
                } else if (event.key === '-' || event.key === '_') {
                    event.preventDefault();
                    zoomLevel = Math.max(zoomLevel - 0.1, minPlotWidth / baseWidth);
                    updateZoom();
                } else if (event.key === '0') {
                    event.preventDefault();
                    zoomLevel = 1.0;
                    updateZoom();
                }
            }
        });
    </script>

</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)


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
        click.echo(f"Error: Input file not found: {input_path}", err=True)
        sys.exit(1)

    try:
        # Load data
        click.echo("Loading consolidated data...")
        data = load_consolidated_data(input_path)

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
        generate_html_report_with_filters(items_by_format_model, all_items_sorted, formats, models, temperatures, experiments, prompts, data, str(output_path))

        click.echo(f"Success. Report generated at {output_path}")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
