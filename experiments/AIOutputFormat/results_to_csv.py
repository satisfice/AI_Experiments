#!/usr/bin/env python3
"""Parse results.json and create CSV with animals as rows, prompts as columns."""

import json
import csv
from pathlib import Path
from collections import defaultdict

def parse_results_json(results_file):
    """
    Parse results.json and create CSV with:
    - Rows: unique animals (items)
    - Columns: prompts
    - Cells: count of occurrences per animal per prompt
    """
    with open(results_file, 'r') as f:
        results = json.load(f)
    
    # Collect all animals and prompts
    animal_prompt_counts = defaultdict(lambda: defaultdict(int))
    all_prompts = set()
    
    # Iterate through all formats
    for format_type, files in results.items():
        if not isinstance(files, list):
            continue
            
        for file_data in files:
            if not isinstance(file_data, dict):
                continue
            
            # Get prompt from metadata
            metadata = file_data.get("metadata", {})
            prompt = metadata.get("prompt", "unknown")
            all_prompts.add(prompt)
            
            # Count each item for this prompt
            items = file_data.get("items", [])
            for item in items:
                if isinstance(item, str) and item.strip():
                    animal_prompt_counts[item.lower()][prompt] += 1
    
    # Sort prompts and animals
    sorted_prompts = sorted(all_prompts)
    sorted_animals = sorted(animal_prompt_counts.keys())
    
    # Write CSV
    output_file = Path(results_file).parent / "animals_by_prompt.csv"
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        
        # Header row: animal, prompt1, prompt2, ...
        writer.writerow(["animal"] + sorted_prompts)
        
        # Data rows
        for animal in sorted_animals:
            row = [animal]
            for prompt in sorted_prompts:
                count = animal_prompt_counts[animal][prompt]
                row.append(count if count > 0 else "")
            writer.writerow(row)
    
    print(f"Created {output_file}")
    print(f"Animals: {len(sorted_animals)}, Prompts: {len(sorted_prompts)}")
    return output_file

if __name__ == "__main__":
    import sys
    results_file = sys.argv[1] if len(sys.argv) > 1 else "results.json"
    parse_results_json(results_file)
