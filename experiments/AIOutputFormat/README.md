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

Edit `models.cfg` to add or modify model shortcuts. Format:

```ini
[provider]
shortcut=actual_model_name

[anthropic]
haiku=claude-3-5-haiku-20241022
sonnet35=claude-3-5-sonnet-20241022
opus=claude-3-opus-20250219

[openai]
gpt4=gpt-4.1-nano-2025-04-14
gpt35turbo=gpt-3.5-turbo

[ollama]
llama=llama3.1:8b
gemma=gemma3:12b
```

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
- `--debug`: Enable debug logging

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
- `providers.py`: Direct API providers for Ollama (HTTP), OpenAI (SDK), and Anthropic (SDK)
- `config.py`: Configuration loading from models.cfg and formats.json, shared utilities
- `check_for_models.py`: Ollama connection checker and model tester
- `query_models.py`: Quick model query tool for Ollama and configured shortcuts
- `models.cfg`: INI configuration file with model shortcuts
- `formats.json`: JSON configuration for output formats
