#!/usr/bin/env python3
"""
format_runner.py — Exercise format validators and cleanup functions against
example files in format_examples/ (or a directory specified by --dir).

Operates standalone: requires no results/ directory or experiment infrastructure.

Usage:
    python format_runner.py [--dir DIR] [-v]
"""

import sys
import argparse
from pathlib import Path

# Ensure summarize.py is importable when run from any working directory
sys.path.insert(0, str(Path(__file__).parent))

from summarize import (
    detect_intended_format,
    detect_format_style,
    PARSERS,
    process_and_track,
)


def run_file(path, verbose=False):
    """Process one example file and print a summary of each pipeline stage."""
    ext = path.suffix
    content = path.read_text(encoding='utf-8')

    # Stage 1: format detection
    intended = detect_intended_format(ext)
    style    = detect_format_style(content, ext)

    # Stage 2: parsing
    parser = PARSERS.get(ext)
    if parser is None:
        print(f"{path.name}: no parser registered for {ext!r}\n")
        return

    raw_items, parse_cleanups, parse_quality = parser(content)

    # Stage 3: cleanup pipeline
    processed, processing, metadata = process_and_track(raw_items, ext)

    # Output
    print(f"{'-' * 56}")
    print(f"  file       {path.name}")
    print(f"  intended   {intended}")
    print(f"  style      {style}")
    print(f"  raw        {raw_items}")
    if parse_cleanups:
        print(f"  p.cleanup  {parse_cleanups}")
    if parse_quality:
        print(f"  p.quality  {parse_quality}")
    print(f"  final      {processed}")
    if verbose:
        if metadata.get('processingCleanups'):
            print(f"  cleanups   {metadata['processingCleanups']}")
        if metadata.get('processingQualityIssues'):
            print(f"  quality    {metadata['processingQualityIssues']}")
        if metadata.get('itemIssues'):
            print(f"  items      {metadata['itemIssues']}")
        print(f"  count      {metadata.get('itemCount')}  "
              f"alpha={metadata.get('alphabeticalOrder')}  "
              f"case={processing.get('case')}")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Run format validators and cleanup functions against example files."
    )
    ap.add_argument(
        '--dir', default='format_examples',
        help='Directory containing example files (default: format_examples)'
    )
    ap.add_argument(
        '-v', '--verbose', action='store_true',
        help='Show cleanup keys, quality issues, and item metadata'
    )
    args = ap.parse_args()

    examples_dir = Path(args.dir)
    if not examples_dir.exists():
        print(f"error: {examples_dir} not found", file=sys.stderr)
        sys.exit(1)

    files = sorted(f for f in examples_dir.iterdir() if f.is_file())
    if not files:
        print(f"no files found in {examples_dir}")
        sys.exit(0)

    for f in files:
        run_file(f, verbose=args.verbose)


if __name__ == '__main__':
    main()
