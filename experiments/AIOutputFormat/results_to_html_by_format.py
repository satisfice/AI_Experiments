#!/usr/bin/env python3
"""Parse results.json and create an interactive HTML table with formats as columns."""

import json
import sys
from pathlib import Path
from collections import defaultdict

def parse_results_json(results_file):
    """
    Parse results.json and create interactive HTML table with:
    - Rows: unique animals (items)
    - Columns: formats (sortable)
    - Cells: count of occurrences per animal per format
    """
    with open(results_file, 'r') as f:
        results = json.load(f)

    # Collect all animals and formats
    animal_format_counts = defaultdict(lambda: defaultdict(int))
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

    # Sort formats and animals
    sorted_formats = sorted(all_formats)
    sorted_animals = sorted(animal_format_counts.keys())

    # Generate HTML
    html = generate_html(sorted_animals, sorted_formats, animal_format_counts)

    # Write file
    output_file = Path(results_file).parent / "animals_by_format.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"Created {output_file}")
    print(f"Animals: {len(sorted_animals)}, Formats: {len(sorted_formats)}")
    return output_file

def generate_html(animals, formats, counts):
    """Generate self-contained HTML with embedded CSS and JavaScript."""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Animals by Format</title>
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
            background-color: #f9f9f9;
        }}

        tbody tr:hover td:first-child {{
            background-color: #f0f0f0;
        }}

        td.count {{
            text-align: center;
            font-weight: 500;
            color: #667eea;
        }}

        td.count:empty::after {{
            content: '−';
            color: #ccc;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Animals by Format</h1>
        <div class="info">Click column headers to sort • {len(animals)} animals × {len(formats)} formats</div>

        <div class="table-wrapper">
            <table id="dataTable">
                <thead>
                    <tr>
                        <th class="sortable" data-column="animal">Animal</th>
"""

    for format_type in formats:
        html += f'                        <th class="sortable" data-column="{format_type}">{format_type}</th>\n'

    html += """                    </tr>
                </thead>
                <tbody>
"""

    for animal in animals:
        html += f'                    <tr>\n                        <td>{animal}</td>\n'
        for format_type in formats:
            count = counts[animal][format_type]
            count_str = str(count) if count > 0 else ""
            html += f'                        <td class="count">{count_str}</td>\n'
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
                        aValue = parseInt(aValue) || 0;
                        bValue = parseInt(bValue) || 0;
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
