# LARC - LLM Aggregated Retrieval Consistency Tests

This directory contains tools and test results for testing LLM consistency in retrieving items from a text.

There are two key ideas behind LARC:

1. Compare open retrieval ("list all items of type X") with closed retrieval ("is item Y an X that is present in the text?"). They should be perfectly consistent.

2. Perform many trials, since LLMs are probabilistic.

## Prerequisites

- Python 3.x
- MongoDB running at localhost:27017 (default)
- Required Python packages: pymongo, pydantic

## Programs

### 1. larc.py - Core LLM Consistency Test (Ollama)

Tests how consistently an LLM can identify and retrieve noun phrases from source text.

**Usage:**
```bash
python larc.py --config <config_file>
```

**Or with individual arguments:**
```bash
python larc.py \
  --source-file <text_file> \
  --survey-prompt-file <prompt_file> \
  --presence-prompt-file <presence_file> \
  --model llama3.1:8b \
  --temperature 0.7 \
  --trials 10 \
  --testid "test_name" \
  --testset "test_set_name" \
  --description "test_description"
```

**Arguments:**
- `--config`: JSON config file with all settings (overrides other arguments)
- `--source-file`: File containing source text to analyze
- `--survey-prompt-file`: File containing survey prompt template
- `--presence-prompt-file`: File containing presence check prompt template
- `--model`: LLM model name (default: llama3.1:8b)
- `--temperature`: Temperature for generation (default: 0.7)
- `--testid`: Identifier for this test (default: N/A)
- `--description`: Test description (default: N/A)
- `--testset`: Test set name (default: N/A)
- `--trials`: Number of trials to run (default: 10)
- `--mongo-uri`: MongoDB connection URI (default: mongodb://localhost:27017/)
- `--continue`: Continue from saved state file (pkl format)
- `--output-file`: File to save results JSON
- `--report`: Generate HTML report automatically after completion

**Example:**
```bash
python larc.py --report --config experiments/run_configs/llama-synthetic-soft-10-T0.json
```

### 2. larc-openai.py - LLM Consistency Test (OpenAI)

Same as larc.py but uses OpenAI API instead of Ollama.

**Usage and Arguments:**
Same as larc.py - the command-line interface is identical.

**Example:**
```bash
python larc-openai.py --config experiments/run_configs/openai-config.json
```

### 3. larc-report.py - HTML Report Generator

Generates an interactive HTML report from test results stored in MongoDB.

**Usage:**
```bash
python larc-report.py --test-run-id <test_run_id> --output-file <output.htm>
```

Or with file-based input:
```bash
python larc-report.py \
  --text-file <source.txt> \
  --phrases-file <phrases.json> \
  --max-count <number> \
  --output-file <output.htm>
```

**Arguments:**
- `--test-run-id`: Test run ID from MongoDB (fetches data automatically)
- `--text-file`: Source text file (required if not using --test-run-id)
- `--phrases-file`: JSON file with phrases to highlight
- `--max-count`: Maximum count value for determining perfect matches
- `--output-file`: Output HTML file path
- `--title`: Title for the HTML document (default: "Highlighted Text")
- `--mongo-uri`: MongoDB connection URI (default: mongodb://localhost:27017/)

**Example:**
```bash
python larc-report.py --test-run-id abc123def456 --output-file report.htm
```

### 4. list-experiments.py - Experiment Management

Lists all experiments from MongoDB and provides utilities for managing them.

**Usage:**

List all experiments:
```bash
python list-experiments.py
```

Remove an experiment by row number:
```bash
python list-experiments.py --rm <row_number>
```

Generate HTML report for a single experiment:
```bash
python list-experiments.py --report <row_number>
```

Generate HTML reports for all completed experiments:
```bash
python list-experiments.py --reportall
```

**Arguments:**
- `--mongo-uri`: MongoDB connection URI (default: mongodb://localhost:27017/)
- `--rm`: Remove experiment at specified row number
- `--report`: Generate HTML report for experiment at row number
- `--reportall`: Generate HTML reports for all completed experiments

### 5. make-sheet.py - Configuration File Management

Converts between JSON configuration files and spreadsheet formats (CSV and HTML).

**Usage:**

Convert config directory to CSV spreadsheet:
```bash
python make-sheet.py --config-dir experiments/run_configs --output configs.csv
```

Convert config directory to HTML spreadsheet:
```bash
python make-sheet.py --config-dir experiments/run_configs --html --output configs.html
```

Convert CSV back to JSON config files:
```bash
python make-sheet.py --reverse configs.csv experiments/run_configs
```

**Arguments:**
- `--config-dir`: Directory containing JSON config files (default: experiments/run_configs)
- `--output`: Output file path (default: configs.csv)
- `--html`: Generate HTML output instead of CSV
- `--reverse`: Convert spreadsheet back to config files: `--reverse <spreadsheet.csv> <output_dir>`

### 6. np-create.py - Test Data Generator

Generates random noun phrases and sentences for testing purposes.

**Usage:**
```bash
python np-create.py
```

**Output:**
- Generates 10 random sentences
- Lists all noun phrases found in the sentences

This script is used for creating an authoritative oracle for noun phrase extraction experiments.

### 7. extract-perf-stats.py - Performance Statistics Extractor

Extracts performance statistics from MongoDB for a given test run ID and outputs them to a TSV file.

**Usage:**
```bash
python extract-perf-stats.py <testrunid> --output <output_file>
```

**Arguments:**
- `testrunid`: Test run ID to search for (required)
- `--output`: Output TSV file path (default: stats.tsv)
- `--mongo-uri`: MongoDB connection URI (default: mongodb://localhost:27017/)

**Example:**
```bash
python extract-perf-stats.py abc123def456 --output performance.tsv
```

**Output:**
- TSV file with columns: record_id, testStep, duration, load_duration, prompt_eval_duration, eval_duration, prompt_eval_count, eval_count

### 8. export-experiments.py - Experiment Data Exporter

Exports all completed experiment runs from the ARC database to a CSV file with metadata and performance metrics.

**Usage:**
```bash
python export-experiments.py --output <output_file>
```

**Arguments:**
- `--output`, `-o`: Output CSV file path (default: experiments.csv)
- `--mongo-uri`: MongoDB connection URI (default: mongodb://localhost:27017/)

**Example:**
```bash
python export-experiments.py --output results.csv
```

**Output:**
- CSV file with columns including: testRunId, timestamp, test_set, test_id, model, temperature, trials, performance metrics, and timing statistics
- Sorted by timestamp (most recent first)
- Only includes completed experiments

### 9. runtests.bat - Batch Test Runner (Windows)

Batch file that runs multiple test configurations sequentially.

**Usage:**
```bash
runtests.bat
```

**Current tests:**
- llama-synthetic-soft-10-T0.json
- llama-synthetic-hard-10-T4.json
- llama-synthetic-hard-10-T8.json

To modify which tests run, edit the file and add/remove lines with the test configurations.

## Workflow Example

1. Create test configuration files in `experiments/run_configs/`:
```bash
python make-sheet.py --config-dir experiments/run_configs --output current_configs.csv
# Edit current_configs.csv as needed
python make-sheet.py --reverse current_configs.csv experiments/run_configs
```

2. Run a test:
```bash
python larc.py --config experiments/run_configs/my-test.json --report
```

3. View results in MongoDB or as HTML report:
```bash
python list-experiments.py --reportall
```

4. Analyze results:
```bash
python make-sheet.py --config-dir experiments/run_configs --html --output results.html
```

## Output Locations

- **MongoDB**: Results stored in ARC database with collections per test set
- **HTML Reports**: `experiments/run_reports/` directory
- **Result Files**: Specified with `--output-file` argument (JSON format)
- **Configuration Spreadsheets**: Specified with `--output` argument

## Database

All test results are stored in MongoDB. Make sure MongoDB is running before starting tests.

**Database name:** ARC
**Collections:** Named after test sets (configurable per test)
**Logging database:** HTTP_logging (stores API call logs)
