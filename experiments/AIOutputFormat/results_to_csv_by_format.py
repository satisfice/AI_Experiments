#!/usr/bin/env python3
"""Parse results.json and create CSV with animals as rows, formats as columns."""

import json
import csv
from pathlib import Path
from collections import defaultdict

def parse_results_json(results_file):
    """
    Parse results.json and create CSV with:
    - Rows: unique animals (items)
    - Columns: formats
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

            # Count each item for this format
            items = file_data.get("items", [])
            for item in items:
                if isinstance(item, str) and item.strip():
                    animal_format_counts[item.lower()][format_type] += 1

    # Sort formats and animals
    sorted_formats = sorted(all_formats)
    sorted_animals = sorted(animal_format_counts.keys())

    # Write CSV
    output_file = Path(results_file).parent / "animals_by_format.csv"
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header row: animal, format1, format2, ...
        writer.writerow(["animal"] + sorted_formats)

        # Data rows
        for animal in sorted_animals:
            row = [animal]
            for format_type in sorted_formats:
                count = animal_format_counts[animal][format_type]
                row.append(count if count > 0 else "")
            writer.writerow(row)

    print(f"Created {output_file}")
    print(f"Animals: {len(sorted_animals)}, Formats: {len(sorted_formats)}")
    return output_file

if __name__ == "__main__":
    import sys
    results_file = sys.argv[1] if len(sys.argv) > 1 else "results.json"
    parse_results_json(results_file)
