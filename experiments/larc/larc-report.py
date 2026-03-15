import json
import sys
import argparse
import re
from html import escape
from pymongo import MongoClient

def highlight_text(text, phrases, max_count):
    """
    Highlights phrases in text with green (perfect), yellow (incomplete), or orange (repudiated).
    Handles overlapping phrases with precedence: orange > yellow > green.

    Args:
        text: The source text to highlight
        phrases: List of dicts with keys 'phrase', 'count', 'repudiation_count'
        max_count: Maximum count value for determining perfect matches

    Returns:
        Tuple of (HTML string with highlighted phrases, list of missing phrases)
    """
    # Color priority map (higher number = higher priority)
    color_priority = {'green': 1, 'yellow': 2, 'orange': 3}

    # Create a list of all matches with their metadata
    all_matches = []
    missing_phrases = []

    for phrase_dict in phrases:
        phrase = phrase_dict['phrase']
        count = phrase_dict.get('count', 0)
        repudiation_count = phrase_dict.get('repudiation_count', 0)

        # Determine color based on count and repudiation
        if count == max_count and repudiation_count == 0:
            color = 'green'
        elif repudiation_count > 0:
            color = 'orange'
        else:
            color = 'yellow'

        # Create tooltip text
        tooltip = f"Phrase: {phrase} | Count: {count}, Repudiation: {repudiation_count}"

        # Find all occurrences of this phrase (case-insensitive)
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        matches_found = list(pattern.finditer(text))

        if not matches_found:
            # Phrase not found in text
            missing_phrases.append({
                'phrase': phrase,
                'count': count,
                'repudiation_count': repudiation_count
            })
        else:
            for match in matches_found:
                start, end = match.span()
                all_matches.append({
                    'start': start,
                    'end': end,
                    'color': color,
                    'tooltip': tooltip,
                    'priority': color_priority[color],
                    'length': end - start
                })

    # Sort by priority (highest first), then by length (longest first) for tie-breaking
    all_matches.sort(key=lambda x: (-x['priority'], -x['length']))

    # Build a character-level map of colors and tooltips
    char_map = {}  # {position: (color, tooltip)}

    for match in all_matches:
        for pos in range(match['start'], match['end']):
            if pos not in char_map:
                char_map[pos] = (match['color'], match['tooltip'])

    # Build the highlighted HTML by grouping consecutive characters with same color/tooltip
    result = []
    last_pos = 0
    i = 0

    while i < len(text):
        if i in char_map:
            # Start of a highlight
            if i > last_pos:
                # Add any unhighlighted text before this highlight
                result.append(escape(text[last_pos:i]))

            color, tooltip = char_map[i]
            highlight_start = i

            # Find the end of this contiguous highlight with same color/tooltip
            while i < len(text) and i in char_map and char_map[i] == (color, tooltip):
                i += 1

            # Add the highlighted span with popup
            result.append(f'<span class="highlight" style="background-color: {color};">{escape(text[highlight_start:i])}<span class="popup">{escape(tooltip)}</span></span>')
            last_pos = i
        else:
            i += 1

    # Add any remaining unhighlighted text
    if last_pos < len(text):
        result.append(escape(text[last_pos:]))

    return ''.join(result), missing_phrases

def create_html_document(text, phrases, max_count, title="Highlighted Text", completion_record=None):
    """
    Creates a complete HTML document with highlighted phrases.

    Args:
        text: The source text
        phrases: List of phrase dictionaries
        max_count: Maximum count value
        title: HTML document title
        completion_record: Optional full completion record from MongoDB to display

    Returns:
        Complete HTML document as string
    """
    highlighted_content, missing_phrases = highlight_text(text, phrases, max_count)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escape(title)}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 900px;
            margin: 40px auto;
            padding: 20px;
            line-height: 1.6;
        }}
        .content {{
            white-space: pre-wrap;
            background-color: #f5f5f5;
            padding: 20px;
            border-radius: 5px;
        }}
        .legend {{
            margin-bottom: 20px;
            padding: 15px;
            background-color: #e9e9e9;
            border-radius: 5px;
        }}
        .legend-item {{
            display: inline-block;
            margin-right: 20px;
            margin-bottom: 10px;
        }}
        .legend-color {{
            display: inline-block;
            width: 20px;
            height: 20px;
            vertical-align: middle;
            margin-right: 5px;
        }}
        .highlight {{
            cursor: help;
            position: relative;
        }}
        .highlight .popup {{
            visibility: hidden;
            position: absolute;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            background-color: #333;
            color: #fff;
            padding: 10px 15px;
            border-radius: 6px;
            white-space: nowrap;
            z-index: 1000;
            font-size: 14px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }}
        .highlight .popup::after {{
            content: "";
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            border-width: 5px;
            border-style: solid;
            border-color: #333 transparent transparent transparent;
        }}
        .highlight:hover .popup {{
            visibility: visible;
        }}
        h1 {{
            color: #333;
        }}
        h2 {{
            color: #555;
            margin-top: 40px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background-color: white;
        }}
        th {{
            background-color: #4CAF50;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: bold;
        }}
        td {{
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }}
        tr:hover {{
            background-color: #f5f5f5;
        }}
        .metadata-table {{
            max-width: 600px;
            margin: 20px 0;
        }}
        .metadata-table td:first-child {{
            font-weight: bold;
            text-align: right;
            padding-right: 20px;
            width: 250px;
        }}
        .data-table th {{
            cursor: pointer;
            user-select: none;
        }}
        .data-table th:hover {{
            background-color: #45a049;
        }}
        .collapsible {{
            background-color: #e9e9e9;
            color: #333;
            cursor: pointer;
            padding: 15px;
            width: 100%;
            border: none;
            text-align: left;
            outline: none;
            font-size: 16px;
            font-weight: bold;
            margin-top: 20px;
            border-radius: 5px;
        }}
        .collapsible:hover {{
            background-color: #ddd;
        }}
        .collapsible:after {{
            content: '\\25BC'; /* Down arrow */
            float: right;
            margin-left: 5px;
        }}
        .collapsible.active:after {{
            content: '\\25B2'; /* Up arrow */
        }}
        .collapsible-content {{
            padding: 0 15px;
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.2s ease-out;
            background-color: #f9f9f9;
            border-left: 3px solid #4CAF50;
            margin-bottom: 20px;
        }}
        .prompt-section {{
            padding: 15px;
            white-space: pre-wrap;
            font-family: monospace;
            background-color: white;
            border-radius: 3px;
            margin: 10px 0;
        }}
        .prompt-label {{
            font-weight: bold;
            color: #4CAF50;
            margin-bottom: 5px;
        }}
    </style>
</head>
<body>
    <h1>{escape(title)}</h1>

    <div class="legend">
        <strong>Legend:</strong>
        <div class="legend-item">
            <span class="legend-color" style="background-color: green;"></span>
            <span>Perfect match (count = {max_count}, repudiation = 0)</span>
        </div>
        <div class="legend-item">
            <span class="legend-color" style="background-color: yellow;"></span>
            <span>Incomplete match (count &lt; {max_count}, repudiation = 0)</span>
        </div>
        <div class="legend-item">
            <span class="legend-color" style="background-color: orange;"></span>
            <span>Repudiated match (repudiation &gt; 0)</span>
        </div>
    </div>"""

    # Add prompts section if completion_record is available
    if completion_record:
        texts = completion_record.get('texts', {})
        survey_prompt = texts.get('survey_prompt', '')
        presence_prompt = texts.get('presence_prompt', '')

        if survey_prompt or presence_prompt:
            html += """
    <button class="collapsible">Show Prompts Used in This Test</button>
    <div class="collapsible-content">"""

            if survey_prompt:
                html += f"""
        <div class="prompt-section">
            <div class="prompt-label">Survey Prompt Template:</div>
            {escape(survey_prompt)}
        </div>"""

            if presence_prompt:
                html += f"""
        <div class="prompt-section">
            <div class="prompt-label">Presence Check Prompt Template:</div>
            {escape(presence_prompt)}
        </div>"""

            html += """
    </div>"""

    html += f"""

    <div class="content">
{highlighted_content}
    </div>"""

    # Add missing phrases table if there are any (before test summary)
    if missing_phrases:
        html += f"""
    <h2>Missing Phrases (Hallucination?)</h2>
    <i>NOTE: Matching logic is case insensitive but does not account for punctuation.</i>
    <table>
        <thead>
            <tr>
                <th>Phrase</th>
                <th>Count</th>
                <th>Repudiation Count</th>
            </tr>
        </thead>
        <tbody>"""

        for missing in missing_phrases:
            html += f"""
            <tr>
                <td>{escape(missing['phrase'])}</td>
                <td>{missing['count']}</td>
                <td>{missing['repudiation_count']}</td>
            </tr>"""

        html += """
        </tbody>
    </table>"""

    # Add completion record summary if available
    if completion_record:
        metadata = completion_record.get('metadata', {})
        metrics = completion_record.get('metrics', {})

        html += f"""
    <h2>Test Summary</h2>
    <table class="metadata-table">
        <tr>
            <td>Test Run ID</td>
            <td>{escape(str(completion_record.get('testRunId', 'N/A')))}</td>
        </tr>
        <tr>
            <td>Time Completed</td>
            <td>{escape(str(metadata.get('timestamp', 'N/A')))}</td>
        </tr>
        <tr>
            <td>Test</td>
            <td>{escape(str(metadata.get('test_id', 'N/A')))}</td>
        </tr>
        <tr>
            <td>Model</td>
            <td>{escape(str(metadata.get('model', 'N/A')))}</td>
        </tr>
        <tr>
            <td>Temperature</td>
            <td>{metadata.get('temperature', 'N/A')}</td>
        </tr>
        <tr>
            <td colspan="2" style="height: 10px;"></td>
        </tr>
        <tr>
            <td colspan="2" style="font-weight: bold; background-color: #e9e9e9; text-align: left;">General Performance</td>
        </tr>
        <tr>
            <td>Size of Text (estimated tokens)</td>
            <td>{metrics.get('text_size_estimated_tokens', 'N/A')}</td>
        </tr>
        <tr>
            <td>Total Prompts</td>
            <td>{metrics.get('total_prompts', 'N/A')}</td>
        </tr>
        <tr>
            <td>Run Time (mm:ss start to end)</td>
            <td>{metrics.get('clocktime_minutes', 'N/A')}</td>
        </tr>
        <tr>
            <td>API Time (mm:ss)</td>
            <td>{escape(str(metrics.get('api_time_mmss', 'N/A')))}</td>
        </tr>
        <tr>
            <td colspan="2" style="height: 10px;"></td>
        </tr>
        <tr>
            <td colspan="2" style="font-weight: bold; background-color: #e9e9e9; text-align: left;">Survey Prompt Performance</td>
        </tr>"""

    survey_perf = metrics.get('survey_prompt_performance', {})
    if survey_perf and any(survey_perf.get(k, 0) != 0 for k in ['mean_load_duration', 'mean_prompt_eval_duration', 'mean_eval_duration', 'mean_prompt_eval_count', 'mean_eval_count']):
        html += f"""
        <tr>
            <td style="padding-left: 30px;">Mean Load Duration (seconds)</td>
            <td>{survey_perf.get('mean_load_duration', 0) / 1e9:.3f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Prompt Eval Duration (seconds)</td>
            <td>{survey_perf.get('mean_prompt_eval_duration', 0) / 1e9:.3f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Eval Duration (seconds)</td>
            <td>{survey_perf.get('mean_eval_duration', 0) / 1e9:.3f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Prompt Eval Count</td>
            <td>{survey_perf.get('mean_prompt_eval_count', 0):.2f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Eval Count</td>
            <td>{survey_perf.get('mean_eval_count', 0):.2f}</td>
        </tr>"""
    else:
        html += """
        <tr>
            <td style="padding-left: 30px;" colspan="2">N/A</td>
        </tr>"""

    html += """
        <tr>
            <td colspan="2" style="height: 10px;"></td>
        </tr>
        <tr>
            <td colspan="2" style="font-weight: bold; background-color: #e9e9e9; text-align: left;">Presence Prompt Performance</td>
        </tr>"""

    presence_perf = metrics.get('presence_prompt_performance', {})
    if presence_perf and any(presence_perf.get(k, 0) != 0 for k in ['mean_load_duration', 'mean_prompt_eval_duration', 'mean_eval_duration', 'mean_prompt_eval_count', 'mean_eval_count']):
        html += f"""
        <tr>
            <td style="padding-left: 30px;">Mean Load Duration (seconds)</td>
            <td>{presence_perf.get('mean_load_duration', 0) / 1e9:.3f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Prompt Eval Duration (seconds)</td>
            <td>{presence_perf.get('mean_prompt_eval_duration', 0) / 1e9:.3f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Eval Duration (seconds)</td>
            <td>{presence_perf.get('mean_eval_duration', 0) / 1e9:.3f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Prompt Eval Count</td>
            <td>{presence_perf.get('mean_prompt_eval_count', 0):.2f}</td>
        </tr>
        <tr>
            <td style="padding-left: 30px;">Mean Eval Count</td>
            <td>{presence_perf.get('mean_eval_count', 0):.2f}</td>
        </tr>"""
    else:
        html += """
        <tr>
            <td style="padding-left: 30px;" colspan="2">N/A</td>
        </tr>"""

    html += f"""
        <tr>
            <td colspan="2" style="height: 10px;"></td>
        </tr>
        <tr>
            <td colspan="2" style="font-weight: bold; background-color: #e9e9e9; text-align: left;">Results</td>
        </tr>
        <tr>
            <td>Total unique items</td>
            <td>{metrics.get('total_unique_items', 'N/A')}</td>
        </tr>
        <tr>
            <td>Total items counted</td>
            <td>{metrics.get('total_items_counted', 'N/A')}</td>
        </tr>
        <tr>
            <td>Trials</td>
            <td>{metadata.get('trials', 'N/A')}</td>
        </tr>
        <tr>
            <td colspan="2" style="height: 10px;"></td>
        </tr>
        <tr>
            <td>Repudiated Presence %</td>
            <td>{metrics.get('repudiated_presence_pct', 0):.2f}</td>
        </tr>
        <tr>
            <td>Miss Rate %</td>
            <td>{metrics.get('miss_rate', 0):.2f}</td>
        </tr>
        <tr>
            <td>Ambivalence %</td>
            <td>{metrics.get('ambivalence_pct', 0):.2f} ({metrics.get('ambivalent_items_count', 0)}/{metrics.get('ambivalent_items_total', 0)})</td>
        </tr>
    </table>

    <h2>Results Visualization</h2>
    <div style="height: 400px; margin-bottom: 30px;">
        <canvas id="resultsChart"></canvas>
    </div>

    <div style="height: 500px; margin-bottom: 30px;">
        <canvas id="bubbleChart"></canvas>
    </div>

    <h2>Detailed Results</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th onclick="sortTable(0)">Phrase ▼</th>
                <th onclick="sortTable(1)">Identified ▼</th>
                <th onclick="sortTable(2)">Repudiated Presence ▼</th>
            </tr>
        </thead>
        <tbody>"""

    # Sort items by count (descending) for chart
    sorted_items = sorted(completion_record.get('item_details', []),
                        key=lambda x: x.get('count', 0),
                        reverse=True)

    # Prepare chart data as JSON
    chart_labels_json = json.dumps([item.get('phrase', '')[:30] + ('...' if len(item.get('phrase', '')) > 30 else '')
                                    for item in sorted_items])
    chart_counts_json = json.dumps([item.get('count', 0) for item in sorted_items])
    chart_repudiations_json = json.dumps([item.get('repudiation_count', 0) for item in sorted_items])

    # Prepare bubble chart data - group items by (count, repudiation_count) coordinates
    from collections import defaultdict
    bubble_data = defaultdict(list)
    for item in completion_record.get('item_details', []):
        count = item.get('count', 0)
        repudiation = item.get('repudiation_count', 0)
        phrase = item.get('phrase', '')
        bubble_data[(count, repudiation)].append(phrase)

    # Create bubble chart dataset with area-based scaling
    import math

    # Find the maximum count to scale appropriately
    max_phrase_count = max(len(phrases) for phrases in bubble_data.values()) if bubble_data else 1

    # Set max radius to reasonable size (e.g., 20 pixels)
    max_radius = 20

    bubble_points = []
    for (count, repudiation), phrases in bubble_data.items():
        # Scale by area: area = π * r^2, so r = sqrt(area/π)
        # Normalize count to max, scale to max_radius
        normalized_count = len(phrases) / max_phrase_count
        radius = math.sqrt(normalized_count) * max_radius
        # Ensure minimum radius of 3 for visibility
        radius = max(3, radius)

        bubble_points.append({
            'x': count,
            'y': repudiation,
            'r': radius,
            'phrases': phrases,
            'count': len(phrases)
        })

    bubble_chart_data_json = json.dumps(bubble_points)

    # Add all phrases from item_details
    for item in completion_record.get('item_details', []):
        html += f"""
        <tr>
            <td>{escape(item.get('phrase', ''))}</td>
            <td>{item.get('count', 0)}</td>
            <td>{item.get('repudiation_count', 0)}</td>
        </tr>"""

    html += f"""
        </tbody>
    </table>

    <script>
    function sortTable(columnIndex) {{
        const table = document.querySelector('.data-table');
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));

        rows.sort((a, b) => {{
            const aValue = a.cells[columnIndex].textContent;
            const bValue = b.cells[columnIndex].textContent;

            // Check if numeric
            const aNum = parseFloat(aValue);
            const bNum = parseFloat(bValue);

            if (!isNaN(aNum) && !isNaN(bNum)) {{
                return bNum - aNum; // Descending for numbers
            }} else {{
                return aValue.localeCompare(bValue); // Ascending for text
            }}
        }});

        rows.forEach(row => tbody.appendChild(row));
    }}

    // Initialize chart
    const ctx = document.getElementById('resultsChart');
    if (ctx) {{
        const chartLabels = {chart_labels_json};
        const chartCounts = {chart_counts_json};
        const chartRepudiations = {chart_repudiations_json};

        console.log('Chart data loaded:', {{
            labels: chartLabels.length,
            counts: chartCounts.length,
            repudiations: chartRepudiations.length
        }});

        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: chartLabels,
                datasets: [{{
                    label: 'Count (Identified)',
                    data: chartCounts,
                    borderColor: 'rgb(75, 192, 192)',
                    backgroundColor: 'rgba(75, 192, 192, 0.7)',
                    borderWidth: 1
                }},
                {{
                    label: 'Repudiation Count',
                    data: chartRepudiations,
                    borderColor: 'rgb(255, 99, 132)',
                    backgroundColor: 'rgba(255, 99, 132, 0.7)',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: 'Count'
                        }},
                        grid: {{
                            display: false
                        }}
                    }},
                    x: {{
                        title: {{
                            display: true,
                            text: 'Phrases (sorted by count, highest first)'
                        }},
                        ticks: {{
                            display: false
                        }},
                        grid: {{
                            display: false
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        display: true,
                        position: 'top'
                    }},
                    title: {{
                        display: true,
                        text: 'Phrase Identification and Repudiation'
                    }}
                }}
            }}
        }});
    }}

    // Initialize bubble chart
    const bubbleCtx = document.getElementById('bubbleChart');
    if (bubbleCtx) {{
        const bubbleData = {bubble_chart_data_json};

        console.log('Bubble data loaded:', bubbleData.length, 'unique coordinate pairs');

        new Chart(bubbleCtx, {{
            type: 'bubble',
            data: {{
                datasets: [{{
                    label: 'Phrases',
                    data: bubbleData,
                    backgroundColor: 'rgba(75, 192, 192, 0.6)',
                    borderColor: 'rgb(75, 192, 192)',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: 'Repudiated Presence'
                        }},
                        ticks: {{
                            stepSize: 1
                        }}
                    }},
                    x: {{
                        beginAtZero: true,
                        reverse: true,
                        title: {{
                            display: true,
                            text: 'Identified Count'
                        }},
                        ticks: {{
                            stepSize: 1
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        display: false
                    }},
                    title: {{
                        display: true,
                        text: 'Phrase Distribution by Identification and Repudiation'
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                const point = context.raw;
                                const phrases = point.phrases || [];
                                const lines = [
                                    `Identified: ${{point.x}}`,
                                    `Repudiated: ${{point.y}}`,
                                    `Number of phrases: ${{point.count}}`,
                                    '',
                                    'Phrases:'
                                ];

                                // Add first 10 phrases, truncate if more
                                const maxPhrases = 10;
                                phrases.slice(0, maxPhrases).forEach(p => {{
                                    lines.push(`  - ${{p.substring(0, 50)}}${{p.length > 50 ? '...' : ''}}`);
                                }});

                                if (phrases.length > maxPhrases) {{
                                    lines.push(`  ... and ${{phrases.length - maxPhrases}} more`);
                                }}

                                return lines;
                            }}
                        }}
                    }}
                }}
            }}
        }});
    }}

    // Add click handlers for collapsible sections
    document.addEventListener('DOMContentLoaded', function() {{
        const collapsibles = document.getElementsByClassName('collapsible');
        for (let i = 0; i < collapsibles.length; i++) {{
            collapsibles[i].addEventListener('click', function() {{
                this.classList.toggle('active');
                const content = this.nextElementSibling;
                if (content.style.maxHeight) {{
                    content.style.maxHeight = null;
                }} else {{
                    content.style.maxHeight = content.scrollHeight + 'px';
                }}
            }});
        }}
    }});
    </script>"""

    html += """
</body>
</html>"""

    return html

def find_completion_record(testRunId, mongo_uri='mongodb://localhost:27017/'):
    """
    Searches through all collections in the ARC database to find a completion record
    with the given testRunId.

    Args:
        testRunId: The test run ID to search for
        mongo_uri: MongoDB connection URI

    Returns:
        Tuple of (completion_record, collection_name) or (None, None) if not found
    """
    try:
        client = MongoClient(mongo_uri)
        db = client["ARC"]

        # Get all collections in the ARC database
        collections = db.list_collection_names()

        for collection_name in collections:
            collection = db[collection_name]
            # Search for completion record with this testRunId
            record = collection.find_one({"testRunId": testRunId, "type": "completion"})
            if record:
                print(f"Found completion record in collection: {collection_name}")
                return record, collection_name

        print(f"No completion record found for testRunId: {testRunId}")
        return None, None

    except Exception as e:
        print(f"Error searching MongoDB: {e}")
        return None, None

def main():
    parser = argparse.ArgumentParser(description='Highlight phrases in text and generate HTML document')

    # Create mutually exclusive group for input methods
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--test-run-id', type=str, help='Test run ID to fetch from MongoDB ARC database')
    input_group.add_argument('--text-file', type=str, help='File containing source text')

    parser.add_argument('--phrases-file', type=str, help='JSON file containing phrase list (required if using --text-file)')
    parser.add_argument('--max-count', type=int, help='Maximum count value for perfect matches (required if using --text-file)')
    parser.add_argument('--mongo-uri', type=str, default='mongodb://localhost:27017/', help='MongoDB connection URI (default: mongodb://localhost:27017/)')
    parser.add_argument('--output-file', type=str, help='Output HTML file path (optional when using --test-run-id, will use testid.htm)')
    parser.add_argument('--title', type=str, default='Highlighted Text', help='HTML document title (default: "Highlighted Text")')

    args = parser.parse_args()

    # Handle MongoDB mode
    if args.test_run_id:
        # Fetch from MongoDB
        completion_record, collection_name = find_completion_record(args.test_run_id, args.mongo_uri)

        if not completion_record:
            print(f"Error: Could not find completion record for testRunId: {args.test_run_id}")
            sys.exit(1)

        # Extract data from completion record
        text = completion_record.get('texts', {}).get('source', '')
        if not text:
            print("Error: No source text found in completion record")
            sys.exit(1)

        phrases = completion_record.get('item_details', [])
        if not phrases:
            print("Error: No item_details found in completion record")
            sys.exit(1)

        max_count = completion_record.get('metadata', {}).get('trials', 0)
        if not max_count:
            print("Error: No trials count found in completion record metadata")
            sys.exit(1)

        # Get test_id for title and output file
        test_id = completion_record.get('metadata', {}).get('test_id', args.test_run_id)

        # Update title if not specified
        if args.title == 'Highlighted Text':
            args.title = f"Test Results: {test_id}"

        # Set output file if not specified
        if not args.output_file:
            args.output_file = f"{test_id}.htm"

        print(f"Loaded data from MongoDB: {len(phrases)} phrases, max_count={max_count}")
        print(f"Output file: {args.output_file}")

        # Store completion_record for HTML generation
        completion_record_for_html = completion_record

    # Handle file mode
    else:
        completion_record_for_html = None
        # Validate required arguments for file mode
        if not args.text_file or not args.phrases_file or args.max_count is None:
            print("Error: --text-file, --phrases-file, and --max-count are required when not using --test-run-id")
            sys.exit(1)

        if not args.output_file:
            print("Error: --output-file is required when using --text-file mode")
            sys.exit(1)

        # Read text file
        try:
            with open(args.text_file, 'r', encoding='utf-8') as f:
                text = f.read()
        except FileNotFoundError:
            print(f"Error: Text file '{args.text_file}' not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading text file: {e}")
            sys.exit(1)

        # Read phrases file
        try:
            with open(args.phrases_file, 'r', encoding='utf-8') as f:
                phrases = json.load(f)
        except FileNotFoundError:
            print(f"Error: Phrases file '{args.phrases_file}' not found")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in phrases file: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading phrases file: {e}")
            sys.exit(1)

        # Validate phrases format
        if not isinstance(phrases, list):
            print("Error: Phrases file must contain a JSON array")
            sys.exit(1)

        max_count = args.max_count

    # Generate HTML
    html_document = create_html_document(text, phrases, max_count, args.title, completion_record_for_html)

    # Write output file
    try:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            f.write(html_document)
        print(f"HTML document created successfully: {args.output_file}")
    except Exception as e:
        print(f"Error writing output file: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
