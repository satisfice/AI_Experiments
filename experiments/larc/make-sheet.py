import argparse
import json
import os
import sys
import csv
from collections import OrderedDict


def configs_to_spreadsheet(config_dir, output_file):
    """
    Reads all JSON config files from a directory and creates a spreadsheet.

    Args:
        config_dir: Directory containing JSON config files
        output_file: Output CSV file path
    """
    try:
        # Check if directory exists
        if not os.path.exists(config_dir):
            print(f"Error: Directory '{config_dir}' does not exist")
            sys.exit(1)

        # Get all JSON files
        config_files = [f for f in os.listdir(config_dir) if f.endswith('.json')]

        if not config_files:
            print(f"No JSON config files found in '{config_dir}'")
            sys.exit(1)

        print(f"Found {len(config_files)} config files")

        # Read all configs and collect all unique keys
        configs = []
        all_keys = set()

        for config_file in sorted(config_files):
            config_path = os.path.join(config_dir, config_file)
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    configs.append({
                        'filename': config_file,
                        'data': config
                    })
                    all_keys.update(config.keys())
            except json.JSONDecodeError as e:
                print(f"Warning: Invalid JSON in {config_file}: {e}")
                continue
            except Exception as e:
                print(f"Warning: Could not read {config_file}: {e}")
                continue

        if not configs:
            print("No valid config files found")
            sys.exit(1)

        # Define desired column order
        desired_order = [
            'testid',
            'testset',
            'description',
            'source_file',
            'survey_prompt_file',
            'presence_prompt_file',
            'model',
            'temperature',
            'trials',
            'mongo_uri'
        ]

        # Start with desired order, then add any remaining keys alphabetically
        sorted_keys = []
        for key in desired_order:
            if key in all_keys:
                sorted_keys.append(key)

        # Add any remaining keys not in desired order
        remaining_keys = sorted(all_keys - set(sorted_keys))
        sorted_keys.extend(remaining_keys)

        # Write to CSV
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            # Create header with filename as first column
            fieldnames = ['filename'] + sorted_keys
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()

            for config in configs:
                row = {'filename': config['filename']}
                for key in sorted_keys:
                    value = config['data'].get(key, '')
                    # Convert non-string values to JSON strings
                    if isinstance(value, (dict, list)):
                        row[key] = json.dumps(value)
                    else:
                        row[key] = value
                writer.writerow(row)

        print(f"Spreadsheet created: {output_file}")
        print(f"Rows: {len(configs)}")
        print(f"Columns: {len(fieldnames)}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def spreadsheet_to_configs(spreadsheet_file, output_dir):
    """
    Reads a spreadsheet and creates JSON config files.

    Args:
        spreadsheet_file: Input CSV file path
        output_dir: Directory to write JSON config files
    """
    try:
        # Check if spreadsheet exists
        if not os.path.exists(spreadsheet_file):
            print(f"Error: Spreadsheet '{spreadsheet_file}' does not exist")
            sys.exit(1)

        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        # Read CSV
        with open(spreadsheet_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            print("No data found in spreadsheet")
            sys.exit(1)

        print(f"Found {len(rows)} rows in spreadsheet")

        # Process each row
        created_count = 0
        for row in rows:
            filename = row.get('filename', '')
            if not filename:
                print("Warning: Row missing filename, skipping")
                continue

            # Remove filename from row data
            config_data = OrderedDict()
            for key, value in row.items():
                if key == 'filename':
                    continue

                # Skip empty values
                if value == '':
                    continue

                # Try to parse JSON values (for lists/dicts)
                if value.startswith('{') or value.startswith('['):
                    try:
                        config_data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        config_data[key] = value
                else:
                    # Try to convert to appropriate type
                    # Check for numbers
                    try:
                        if '.' in value:
                            config_data[key] = float(value)
                        else:
                            config_data[key] = int(value)
                    except ValueError:
                        # Keep as string
                        config_data[key] = value

            # Write config file
            output_path = os.path.join(output_dir, filename)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)

            print(f"Created: {output_path}")
            created_count += 1

        print()
        print(f"Successfully created {created_count} config files in '{output_dir}'")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def configs_to_html(config_dir, output_file):
    """
    Reads all JSON config files from a directory and creates an HTML table.

    Args:
        config_dir: Directory containing JSON config files
        output_file: Output HTML file path
    """
    try:
        # Check if directory exists
        if not os.path.exists(config_dir):
            print(f"Error: Directory '{config_dir}' does not exist")
            sys.exit(1)

        # Get all JSON files
        config_files = [f for f in os.listdir(config_dir) if f.endswith('.json')]

        if not config_files:
            print(f"No JSON config files found in '{config_dir}'")
            sys.exit(1)

        print(f"Found {len(config_files)} config files")

        # Read all configs and collect all unique keys
        configs = []
        all_keys = set()

        for config_file in sorted(config_files):
            config_path = os.path.join(config_dir, config_file)
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    configs.append({
                        'filename': config_file,
                        'data': config
                    })
                    all_keys.update(config.keys())
            except json.JSONDecodeError as e:
                print(f"Warning: Invalid JSON in {config_file}: {e}")
                continue
            except Exception as e:
                print(f"Warning: Could not read {config_file}: {e}")
                continue

        if not configs:
            print("No valid config files found")
            sys.exit(1)

        # Define desired column order (files at the end, mongo_uri excluded)
        desired_order = [
            'testid',
            'testset',
            'description',
            'model',
            'temperature',
            'trials'
        ]

        # Files to appear at the end
        file_columns = [
            'source_file',
            'survey_prompt_file',
            'presence_prompt_file'
        ]

        # Start with desired order
        sorted_keys = []
        for key in desired_order:
            if key in all_keys:
                sorted_keys.append(key)

        # Add any remaining keys not in desired order or file columns or mongo_uri
        remaining_keys = sorted(all_keys - set(sorted_keys) - set(file_columns) - {'mongo-uri'})
        sorted_keys.extend(remaining_keys)

        # Add file columns at the end
        for key in file_columns:
            if key in all_keys:
                sorted_keys.append(key)

        # Build HTML
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LARC Reports</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
        }
        .controls {
            margin-bottom: 20px;
            padding: 15px;
            background-color: white;
            border-radius: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .controls input {
            padding: 8px;
            width: 300px;
            font-size: 14px;
            border: 1px solid #ddd;
            border-radius: 3px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background-color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        th {
            background-color: #4CAF50;
            color: white;
            padding: 12px;
            text-align: left;
            position: sticky;
            top: 0;
            cursor: pointer;
            user-select: none;
        }
        th:hover {
            background-color: #45a049;
        }
        td {
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .report-btn {
            background-color: #2196F3;
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 12px;
        }
        .report-btn:hover {
            background-color: #0b7dda;
        }
        .report-btn:disabled {
            background-color: #ccc;
            cursor: not-allowed;
        }
        .info {
            margin-top: 10px;
            color: #666;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <h1>LARC Reports</h1>

    <div class="controls">
        <input type="text" id="searchInput" placeholder="Search in all columns..." onkeyup="filterTable()">
        <div class="info">
            Showing <span id="rowCount">0</span> of <span id="totalRows">0</span> experiments
        </div>
    </div>

    <table id="configTable">
        <thead>
            <tr>
                <th>Report</th>
                <th onclick="sortTable(1)">Filename ▼</th>"""

        # Add column headers
        for i, key in enumerate(sorted_keys):
            html += f'\n                <th onclick="sortTable({i+2})">{key} ▼</th>'

        html += """
            </tr>
        </thead>
        <tbody>"""

        # Add data rows
        for config in configs:
            filename = config['filename']
            # Generate report URL using testid from config
            testid = config['data'].get('testid', '')
            if testid:
                report_name = testid + '.htm'
                report_url = f'https://www.satisfice.com/reports/{report_name}'
                button_html = f'<button class="report-btn" onclick="window.open(\'{report_url}\', \'_blank\')">View Report</button>'
            else:
                button_html = '<button class="report-btn" disabled>No Test ID</button>'

            html += f"""
            <tr>
                <td>{button_html}</td>
                <td>{filename}</td>"""

            for key in sorted_keys:
                value = config['data'].get(key, '')
                # Convert non-string values to display strings
                if isinstance(value, (dict, list)):
                    display_value = json.dumps(value)
                else:
                    display_value = str(value)
                html += f'\n                <td>{display_value}</td>'

            html += """
            </tr>"""

        html += f"""
        </tbody>
    </table>

    <script>
    // Initialize row counts
    document.addEventListener('DOMContentLoaded', function() {{
        updateRowCount();
    }});

    function filterTable() {{
        const input = document.getElementById('searchInput');
        const filter = input.value.toLowerCase();
        const table = document.getElementById('configTable');
        const rows = table.getElementsByTagName('tr');

        let visibleCount = 0;

        // Start from 1 to skip header row
        for (let i = 1; i < rows.length; i++) {{
            const cells = rows[i].getElementsByTagName('td');
            let found = false;

            // Search in all cells except the button cell
            for (let j = 1; j < cells.length; j++) {{
                const cellText = cells[j].textContent || cells[j].innerText;
                if (cellText.toLowerCase().indexOf(filter) > -1) {{
                    found = true;
                    break;
                }}
            }}

            if (found) {{
                rows[i].style.display = '';
                visibleCount++;
            }} else {{
                rows[i].style.display = 'none';
            }}
        }}

        document.getElementById('rowCount').textContent = visibleCount;
    }}

    function sortTable(columnIndex) {{
        const table = document.getElementById('configTable');
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));

        rows.sort((a, b) => {{
            const aValue = a.cells[columnIndex].textContent;
            const bValue = b.cells[columnIndex].textContent;

            // Try numeric comparison
            const aNum = parseFloat(aValue);
            const bNum = parseFloat(bValue);

            if (!isNaN(aNum) && !isNaN(bNum)) {{
                return bNum - aNum;
            }} else {{
                return aValue.localeCompare(bValue);
            }}
        }});

        rows.forEach(row => tbody.appendChild(row));
        updateRowCount();
    }}

    function updateRowCount() {{
        const table = document.getElementById('configTable');
        const rows = table.getElementsByTagName('tr');
        const totalRows = rows.length - 1; // Exclude header
        document.getElementById('totalRows').textContent = totalRows;
        document.getElementById('rowCount').textContent = totalRows;
    }}
    </script>
</body>
</html>"""

        # Write HTML file
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"HTML file created: {output_file}")
        print(f"Rows: {len(configs)}")
        print(f"Columns: {len(sorted_keys) + 2}")  # +2 for Report button and Filename

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Convert between JSON config files and spreadsheet')
    parser.add_argument('--config-dir', type=str, default='experiments/run_configs',
                       help='Directory containing config files (default: experiments/run_configs)')
    parser.add_argument('--output', type=str, default='configs.csv',
                       help='Output CSV file (default: configs.csv)')
    parser.add_argument('--html', action='store_true',
                       help='Generate HTML output instead of CSV')
    parser.add_argument('--reverse', nargs=2, metavar=('SPREADSHEET', 'OUTPUT_DIR'),
                       help='Convert spreadsheet to config files: --reverse <spreadsheet.csv> <output_dir>')

    args = parser.parse_args()

    if args.reverse:
        # Reverse mode: spreadsheet to configs
        spreadsheet_file = args.reverse[0]
        output_dir = args.reverse[1]
        spreadsheet_to_configs(spreadsheet_file, output_dir)
    elif args.html:
        # HTML mode: configs to HTML
        # Change default output extension to .htm if using default
        if args.output == 'configs.csv':
            output_file = 'reports.htm'
        else:
            output_file = args.output
        configs_to_html(args.config_dir, output_file)
    else:
        # Normal mode: configs to spreadsheet
        configs_to_spreadsheet(args.config_dir, args.output)


if __name__ == '__main__':
    main()
