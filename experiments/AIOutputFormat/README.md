# AIOutputFormat

A system for running prompts through LLMs in batch mode and generating outputs in specified formats. Supports local models via Ollama, with integration for OpenAI and Anthropic APIs.

## Installation

1. Ensure Python 3.9+ is installed
2. Install dependencies:
   ```bash
   pip install click plotly pyyaml python-dotenv openai anthropic
   ```
3. For local models, install and run Ollama: https://ollama.ai

## Configuration

### Models

Edit `models.json` to add or modify model shortcuts. Each provider entry has a `supports_temperature` flag and a `models` map of shortcut to model details:

```json
{
  "anthropic": {
    "supports_temperature": false,
    "models": {
      "haiku": { "name": "claude-haiku-4-5-20251001", "color": "#FFB6D9" },
      "sonnet35": { "name": "claude-3-5-sonnet-20241022", "color": "#FF8A7F" }
    }
  },
  "openai": {
    "supports_temperature": true,
    "models": {
      "gpt4": { "name": "gpt-4.1-nano-2025-04-14", "color": "#74B9FF" }
    }
  },
  "ollama": {
    "supports_temperature": true,
    "models": {
      "llama318b": { "name": "llama3.1:8b", "color": "#C8A2C8", "timeout_seconds": 300 },
      "gemma": { "name": "gemma3:12b", "color": "#DA70D6" }
    }
  }
}
```

Use `color_picker.py` to interactively assign or randomize colors instead of editing `models.json` by hand.

### Output Formats

Edit `formats.json` to configure output formats. Each format specifies an extension and a format instruction:

```json
{
  "txt": {
    "extension": "txt",
    "prompt": "Return the results in plain text format."
  },
  "json": {
    "extension": "json",
    "prompt": "Return the results in JSON format."
  }
}
```

### API Keys

For OpenAI and Anthropic models, set the following environment variables:

**OpenAI:**
```bash
export OPENAI_API_KEY="sk-..."
```

**Anthropic:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Alternatively, create a `.env` file in the project directory:
```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

Local models via Ollama do not require API keys.

## Programs

### experiment.py

Generate LLM outputs with specified format(s), model(s), prompt(s), experiment(s), and temperature(s). Supports batch processing with multiple parameters.

```bash
python experiment.py -m <model> -f <format> -p <prompt_file> -e <experiment>
```

**Parameters:**

- `-m, --model`: Model shortcut (can specify multiple times)
- `-f, --format`: Output format or "all" for all formats (can specify multiple times)
- `-p, --prompt`: Path to prompt file (can specify multiple times)
- `-e, --experiment`: Experiment name (can specify multiple times)
- `-i, --iterations`: Number of iterations (1-99, default 1)
- `-t, --temperature`: Temperature value in 2-digit format like 08 for 0.8 (can specify multiple times)
- `-b, --batch-file`: Optional file with multiple prompts (one per line)
- `--restart`: Force all iterations to regenerate, even if output files already exist
- `--debug`: Enable debug logging

**Resuming interrupted experiments:**

If a run is interrupted, re-running with the same parameters will detect incomplete iterations and prompt to resume. Resuming reuses the original timestamp, preserving set integrity. Use `--restart` to bypass this and regenerate from scratch.

**Examples:**

Single run:
```bash
python experiment.py -m gpt4 -f json -p animals.prompt -e "animals5"
```

Batch run with multiple models and formats:
```bash
python experiment.py -m gpt4 -m llama -f txt -f json -p animals.prompt -e "animals5" -i 3
```

Multiple prompts:
```bash
python experiment.py -m gpt4 -f all -p prompt1.txt -p prompt2.txt -e "test" -i 2
```

### summarize.py

Parse all generated output files and create results.json containing items extracted from each file.

```bash
python summarize.py [OPTIONS]
```

**Parameters:**

- `--filter TEXT`: Filter files by string in filename (legacy)
- `--model TEXT`: Filter by model name
- `--format TEXT`: Filter by file format
- `--experiment TEXT`: Filter by experiment name
- `--timestamp TEXT`: Filter by timestamp
- `--temperature FLOAT`: Filter by temperature
- `--max-item-length INT`: Maximum allowed item length in characters (default 25)
- `-a, --analysis`: Generate data analysis report by model and temperature

**Examples:**

Consolidate all files with analysis:
```bash
python summarize.py -a
```

Filter to specific experiment:
```bash
python summarize.py --experiment animals5
```

Filter to specific model and format:
```bash
python summarize.py --model gpt4 --format json
```

### color_picker.py

Interactive terminal tool to assign or randomize colors for each model in `models.json`. Colors are used in the HTML report to distinguish model outputs.

```bash
python color_picker.py
```

No command-line parameters. The interactive menu lets you:

- View all current model colors with terminal color swatches
- Edit the color for a specific model (enter hex or pick from a palette)
- Generate random colors for all models

### generate_report.py

Generate an interactive HTML report with visualizations from results.json.

```bash
python generate_report.py -i <results.json> -o <report.html>
```

**Parameters:**

- `-i, --input`: Path to results.json file (default: results/results.json)
- `-o, --output`: Output HTML report path (default: results/report.html)

**Viewing the report:**

Open the generated HTML file in a browser. For side-by-side comparison with independent filters, append `?cols=2` to the URL:
```
results/report.html?cols=2
```

**Examples:**

Default output:
```bash
python generate_report.py
```

Custom output:
```bash
python generate_report.py -i results/results.json -o reports/analysis.html
```

### format_runner.py

Exercise format validators and cleanup functions against example files in
`format_examples/` without requiring any experiment infrastructure (no `results/`
directory, no models, no API keys).

```bash
python format_runner.py [--dir DIR] [-v]
```

**Parameters:**

- `--dir`: Directory containing example files (default: `format_examples`)
- `-v, --verbose`: Show cleanup keys, quality issues, and item metadata

**Examples:**

Run against the default `format_examples/` directory:
```bash
python format_runner.py
```

Run with full metadata output:
```bash
python format_runner.py -v
```

Run against a different directory:
```bash
python format_runner.py --dir my_examples -v
```

### format_examples/

Example input files used by `format_runner.py`. Files are named
`<label>.<ext>` where `<ext>` determines the parser and format validator
invoked. Currently contains one valid example per supported format:

| File | Format | Expected style / notes |
|---|---|---|
| `valid.txt` | plain text | plain text |
| `valid.txt1` | numbered text | numbered text |
| `valid.json` | JSON | multiple lines |
| `valid.yml` | YAML | leading hyphen |
| `valid_li_ul.html` | HTML | multiple lines |
| `valid_li_ul_body.html` | HTML | multiple lines |
| `valid_li_ul_body_html.html` | HTML | multiple lines |
| `valid.csv` | CSV | single row |
| `valid.md` | markdown | (unknown — no style validator) |
| `txt_numbered.txt` | plain text | mismatch: numbered items in a .txt file |
| `txt1_plain.txt1` | numbered text | mismatch: plain items in a .txt1 file |
| `json_codefenced.json` | JSON | mismatch: JSON wrapped in markdown backticks |
| `html_codefenced.html` | HTML | mismatch: HTML wrapped in markdown backticks |
| `yaml_codefenced.yml` | YAML | mismatch: YAML wrapped in markdown backticks |

## Output Files

Output files are named with the pattern:
```
YYYYMMDDHHmmss-experimentname-promptname-modelname-tNN-ii.ext
```

Where:
- `YYYYMMDDHHmmss`: Timestamp with seconds
- `experimentname`: Experiment name
- `promptname`: Prompt file name without extension
- `modelname`: Sanitized model name (e.g., gpt4, llama)
- `tNN`: Temperature component (2 digits for supported models, "xx" for unsupported)
- `ii`: Iteration number (01-99)
- `ext`: File extension based on format

Example:
```
20260216175230-animals5-animals-gpt4-t10-01.json
20260216175231-animals5-animals-llama-txx-02.txt
```

## Workflow

1. Create prompt files (e.g., `animals.prompt`)
2. Run experiments to generate output files:
   ```bash
   python experiment.py -m gpt4 -f all -p animals.prompt -e "animals5" -i 5
   ```
3. Consolidate results from output files:
   ```bash
   python summarize.py -a
   ```
4. Generate HTML report from results.json:
   ```bash
   python generate_report.py
   ```

## Architecture

- `experiment.py`: CLI entry point for batch LLM generation using Click
- `summarize.py`: Parser for output files, consolidates results into JSON with quality analysis
- `generate_report.py`: Generates interactive HTML reports using Plotly with dual-column comparison mode
- `color_picker.py`: Interactive terminal tool for assigning and randomizing model colors in `models.json`
- `providers.py`: Direct API providers for Ollama (HTTP), OpenAI (SDK), and Anthropic (SDK)
- `config.py`: Configuration loading from `models.json` and `formats.json`, shared utilities
- `utils.py`: Shared utility functions (error formatting, stderr output)
- `check_for_models.py`: Ollama connection checker and model tester
- `query_models.py`: Quick model query tool for Ollama and configured shortcuts
- `analyze_isolation.py`: Developer diagnostic script; analyzes `experiment.py` for state carryover risks
- `models.json`: JSON configuration file with model shortcuts, colors, and provider settings
- `formats.json`: JSON configuration for output formats
