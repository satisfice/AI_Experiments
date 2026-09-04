#!/usr/bin/env python3
"""Parse results.json and create HTML table with percentage distribution by format."""

import json
import sys
from pathlib import Path
from collections import defaultdict

def parse_results_json(results_file):
    """
    Parse results.json and create interactive HTML table with:
    - Rows: unique animals (items)
    - Columns: formats (sortable)
    - Cells: percentage of that animal relative to total for that format
    """
    with open(results_file, 'r') as f:
        results = json.load(f)

    # Collect all animals and formats
    animal_format_counts = defaultdict(lambda: defaultdict(int))
    format_totals = defaultdict(int)
    all_formats = set()

    # Iterate through all formats
    for format_type, files in results.items():
        if not isinstance(files, list):
            continue

        all_formats.add(format_type)

        for file_data in files:
            if not isinstance(file_data, dict):
                continue

            items = file_data.get("items", [])
            for item in items:
                if isinstance(item, str) and item.strip():
                    animal_format_counts[item.lower()][format_type] += 1
                    format_totals[format_type] += 1

    # Calculate percentages
    animal_format_pct = defaultdict(dict)
    max_pct = 0
    for animal in animal_format_counts:
        for format_type in all_formats:
            count = animal_format_counts[animal][format_type]
            total = format_totals[format_type]
            pct = (count / total * 100) if total > 0 else 0
            animal_format_pct[animal][format_type] = pct
            max_pct = max(max_pct, pct)

    # Sort formats and animals
    sorted_formats = sorted(all_formats)
    sorted_animals = sorted(animal_format_pct.keys())

    # Generate HTML
    html = generate_html(sorted_animals, sorted_formats, animal_format_pct, format_totals, max_pct)

    # Write file
    output_file = Path(results_file).parent / "animals_by_format_pct.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"Created {output_file}")
    print(f"Animals: {len(sorted_animals)}, Formats: {len(sorted_formats)}")
    return output_file

def generate_html(animals, formats, percentages, totals, max_pct):
    """Generate self-contained HTML with color-coded percentages scaled to actual max."""

    # Determine color bands based on actual max
    band_25 = max_pct * 0.25
    band_50 = max_pct * 0.5
    band_75 = max_pct * 0.75

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Animals by Format (Percentage)</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 40px 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 30px;
        }}

        h1 {{
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }}

        .info {{
            color: #666;
            margin-bottom: 20px;
            font-size: 14px;
        }}

        .legend {{
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            font-size: 12px;
            color: #666;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .legend-box {{
            width: 30px;
            height: 20px;
            border-radius: 4px;
        }}

        .legend-0 {{ background: rgba(102, 126, 234, 0.1); }}
        .legend-25 {{ background: rgba(102, 126, 234, 0.4); }}
        .legend-50 {{ background: rgba(102, 126, 234, 0.7); }}
        .legend-100 {{ background: rgba(102, 126, 234, 1); }}

        .format-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}

        .stat-box {{
            background: #f5f5f5;
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid #667eea;
        }}

        .stat-label {{
            font-size: 12px;
            color: #999;
            text-transform: uppercase;
            font-weight: 600;
        }}

        .stat-value {{
            font-size: 20px;
            font-weight: 700;
            color: #333;
            margin-top: 5px;
        }}

        .table-wrapper {{
            overflow-x: auto;
            border-radius: 8px;
            border: 1px solid #e0e0e0;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        thead {{
            background: #f5f5f5;
            position: sticky;
            top: 0;
            z-index: 10;
        }}

        th {{
            padding: 12px 15px;
            text-align: left;
            font-weight: 600;
            color: #333;
            border-bottom: 2px solid #667eea;
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
            transition: background-color 0.2s;
        }}

        th:hover {{
            background-color: #efefef;
        }}

        th.sortable::after {{
            content: ' ⇅';
            color: #999;
            margin-left: 5px;
            font-size: 11px;
        }}

        th.sort-asc::after {{
            content: ' ↑';
            color: #667eea;
        }}

        th.sort-desc::after {{
            content: ' ↓';
            color: #667eea;
        }}

        td {{
            padding: 10px 15px;
            border-bottom: 1px solid #f0f0f0;
            color: #555;
        }}

        td:first-child {{
            font-weight: 500;
            color: #333;
            background: #fafafa;
            position: sticky;
            left: 0;
            z-index: 5;
        }}

        tbody tr:hover {{
            filter: brightness(0.98);
        }}

        tbody tr:hover td:first-child {{
            background-color: #f0f0f0;
        }}

        td.pct {{
            text-align: center;
            font-weight: 600;
            border-radius: 4px;
            background-color: transparent;
            transition: background-color 0.2s;
            position: relative;
        }}

        td.pct::after {{
            content: '%';
            font-size: 11px;
            margin-left: 2px;
        }}

        td.pct:empty::before {{
            content: '−';
            color: #ccc;
        }}

        td.pct.pct-0 {{ background-color: rgba(102, 126, 234, 0.1); color: #999; }}
        td.pct.pct-1 {{ background-color: rgba(102, 126, 234, 0.25); color: #555; }}
        td.pct.pct-25 {{ background-color: rgba(102, 126, 234, 0.5); color: #333; }}
        td.pct.pct-50 {{ background-color: rgba(102, 126, 234, 0.75); color: #fff; }}
        td.pct.pct-75plus {{ background-color: #667eea; color: #fff; font-weight: 700; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Animals by Format (Percentage Distribution)</h1>
        <div class="info">Each cell shows the percentage of that animal relative to total animals in that format • Click headers to sort</div>

        <div class="legend">
            <div class="legend-item"><div class="legend-box legend-0"></div> <span>0–{band_25:.1f}%</span></div>
            <div class="legend-item"><div class="legend-box legend-25"></div> <span>{band_25:.1f}–{band_50:.1f}%</span></div>
            <div class="legend-item"><div class="legend-box legend-50"></div> <span>{band_50:.1f}–{band_75:.1f}%</span></div>
            <div class="legend-item"><div class="legend-box legend-100"></div> <span>{band_75:.1f}%+</span></div>
        </div>

        <div class="format-stats">
"""

    # Add format statistics
    for fmt in sorted(list(set(totals.keys()))):
        html += f"""            <div class="stat-box">
                <div class="stat-label">{fmt} Format</div>
                <div class="stat-value">{totals[fmt]:,}</div>
            </div>
"""

    html += """        </div>

        <div class="table-wrapper">
            <table id="dataTable">
                <thead>
                    <tr>
                        <th class="sortable" data-column="animal">Animal</th>
"""

    for fmt in formats:
        html += f'                        <th class="sortable" data-column="{fmt}">{fmt}</th>\n'

    html += """                    </tr>
                </thead>
                <tbody>
"""

    for animal in animals:
        html += f'                    <tr>\n                        <td>{animal}</td>\n'
        for fmt in formats:
            pct = percentages[animal][fmt]
            if pct > 0:
                pct_str = f"{pct:.1f}"
                if pct >= band_75:
                    pct_class = "pct-75plus"
                elif pct >= band_50:
                    pct_class = "pct-50"
                elif pct >= band_25:
                    pct_class = "pct-25"
                else:
                    pct_class = "pct-1"
                html += f'                        <td class="pct {pct_class}">{pct_str}</td>\n'
            else:
                html += '                        <td class="pct pct-0"></td>\n'
        html += '                    </tr>\n'

    html += """                </tbody>
            </table>
        </div>
    </div>

    <script>
        const table = document.getElementById('dataTable');
        const headers = table.querySelectorAll('th.sortable');
        let currentSort = { column: null, direction: 'asc' };

        headers.forEach(header => {
            header.addEventListener('click', function() {
                const column = this.dataset.column;
                const isNumeric = column !== 'animal';

                // Update sort direction
                if (currentSort.column === column) {
                    currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
                } else {
                    currentSort.column = column;
                    currentSort.direction = 'asc';
                }

                // Update header appearance
                headers.forEach(h => {
                    h.classList.remove('sort-asc', 'sort-desc');
                    if (h.dataset.column === column) {
                        h.classList.add(currentSort.direction === 'asc' ? 'sort-asc' : 'sort-desc');
                    }
                });

                // Sort table
                sortTable(column, currentSort.direction, isNumeric);
            });
        });

        function sortTable(column, direction, isNumeric) {
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));

            rows.sort((a, b) => {
                let aValue, bValue;

                if (column === 'animal') {
                    aValue = a.cells[0].textContent.trim();
                    bValue = b.cells[0].textContent.trim();
                } else {
                    const colIndex = Array.from(headers).findIndex(h => h.dataset.column === column);
                    aValue = a.cells[colIndex].textContent.trim();
                    bValue = b.cells[colIndex].textContent.trim();

                    if (isNumeric) {
                        aValue = parseFloat(aValue) || 0;
                        bValue = parseFloat(bValue) || 0;
                    }
                }

                if (isNumeric) {
                    return direction === 'asc' ? aValue - bValue : bValue - aValue;
                } else {
                    return direction === 'asc'
                        ? aValue.localeCompare(bValue)
                        : bValue.localeCompare(aValue);
                }
            });

            rows.forEach(row => tbody.appendChild(row));
        }
    </script>
</body>
</html>
"""

    return html

if __name__ == "__main__":
    results_file = sys.argv[1] if len(sys.argv) > 1 else "results.json"
    parse_results_json(results_file)
