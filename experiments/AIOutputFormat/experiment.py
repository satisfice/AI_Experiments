#!/usr/bin/env python3

import click
import os
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from providers import generate, get_provider, reinitialize_ollama_model
from config import VALID_FORMATS, FORMAT_EXTENSIONS, sanitize_model_name, resolve_model_name, get_available_models, get_format_instruction, model_supports_temperature, parse_temperature, get_model_timeout
from utils import format_error, print_error
import json

logger = logging.getLogger(__name__)

LAST_RUN_FILE = Path(".experiment_last_run.json")
MAX_GENERATION_RETRIES = 3  # Number of retries after initial attempt on timeout


def find_completed_iterations(model_name: str, experiment: str, prompt_name: str, format_type: str) -> dict:
    """
    Scan results folder to find which iterations exist for this (model, experiment, prompt, format).
    Returns dict: {iteration_num: (filepath, timestamp), ...}
    Used to detect incomplete experiments and support resuming.
    """
    completed = {}
    results_dir = Path("results")

    if not results_dir.exists():
        return completed

    ext = FORMAT_EXTENSIONS.get(format_type, format_type)

    # Search for files matching pattern: *-EXPERIMENT-PROMPT-MODEL-*-*.FORMAT
    # Iteration number is the second-to-last part when split by '-'
    for filepath in results_dir.glob(f"*-{experiment}-{prompt_name}-{model_name}-*-*.{ext}"):
        try:
            stem = filepath.stem
            parts = stem.split('-')
            # iteration is second-to-last part; extract it
            iteration_str = parts[-1]
            iteration = int(iteration_str)
            # timestamp is first part (14 digits)
            timestamp = parts[0]
            completed[iteration] = (filepath, timestamp)
        except (ValueError, IndexError):
            # Skip files that don't match expected pattern
            pass

    return completed


def save_run_config(model, format_type, prompts, experiments, iterations, temperatures, batch_file, debug):
    """Save experiment configuration to file for quick re-run."""
    config = {
        "model": list(model) if model else [],
        "format_type": list(format_type) if format_type else [],
        "prompts": list(prompts) if prompts else [],
        "experiments": list(experiments) if experiments else [],
        "iterations": iterations,
        "temperatures": list(temperatures) if temperatures else [],
        "batch_file": batch_file,
        "debug": debug,
        "timestamp": datetime.now().isoformat()
    }
    try:
        with open(LAST_RUN_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logger.debug(f"Saved run configuration to {LAST_RUN_FILE}")
    except Exception as e:
        logger.warning(f"Could not save run configuration: {e}")


def load_run_config():
    """Load previous experiment configuration from file."""
    if not LAST_RUN_FILE.exists():
        return None
    try:
        with open(LAST_RUN_FILE, 'r') as f:
            config = json.load(f)
        logger.debug(f"Loaded run configuration from {LAST_RUN_FILE}")
        return config
    except Exception as e:
        logger.warning(f"Could not load run configuration: {e}")
        return None


def display_last_run_offer(config):
    """Display previous run configuration and ask if user wants to use it."""
    click.echo("\n" + "=" * 70)
    click.echo("PREVIOUS EXPERIMENT CONFIGURATION")
    click.echo("=" * 70 + "\n")

    timestamp = config.get("timestamp", "unknown")
    click.echo(f"Last run: {timestamp}\n")

    click.echo("Configuration:")
    click.echo(f"  Models:       {', '.join(config.get('model', []))}")
    click.echo(f"  Format:       {config.get('format_type', 'not set')}")
    click.echo(f"  Prompts:      {', '.join(config.get('prompts', []))}")
    click.echo(f"  Experiments:  {', '.join(config.get('experiments', []))}")
    click.echo(f"  Iterations:   {config.get('iterations', 'not set')}")
    click.echo(f"  Temperatures: {', '.join(config.get('temperatures', [])) if config.get('temperatures') else 'default'}")
    if config.get('batch_file'):
        click.echo(f"  Batch file:   {config.get('batch_file')}")
    click.echo()

    try:
        use_previous = click.confirm("Use previous configuration?", default=True)
        return use_previous
    except click.Abort:
        return False


def check_model_access_times(models_to_check: List[str]) -> Dict[str, Optional[float]]:
    """
    Test access time for each model.

    Args:
        models_to_check: List of model shortcuts or names to test

    Returns:
        Dict mapping model names to access time in seconds (or None if failed)
    """
    access_times = {}

    for model in models_to_check:
        # Resolve shortcut to full name
        actual_model = resolve_model_name(model)

        try:
            click.echo(f"  Testing {model:<20}...", nl=False)
            sys.stderr.flush()

            # Time the provider creation
            start = time.time()
            provider = get_provider(actual_model)

            # Quick test invoke to ensure it's responsive (with 10 second timeout)
            response = generate(actual_model, "test", provider=provider, timeout=10)
            elapsed = time.time() - start

            access_times[model] = elapsed
            click.echo(f" OK ({elapsed:.2f}s)")

        except TimeoutError:
            access_times[model] = None
            click.echo(" TIMEOUT (>10s)")
        except Exception as e:
            access_times[model] = None
            click.echo(f" ERROR: {type(e).__name__}")
            logger.debug(f"Error testing {model}: {e}")

    return access_times


def present_model_access_report(access_times: Dict[str, Optional[float]]) -> bool:
    """
    Present model access times to user and ask for confirmation.

    Args:
        access_times: Dict of model access times from check_model_access_times()

    Returns:
        True if user wants to continue, False if user wants to cancel
    """
    click.echo("\n" + "=" * 70)
    click.echo("MODEL ACCESS TIME REPORT")
    click.echo("=" * 70 + "\n")

    working_models = []
    problem_models = []

    for model, elapsed in sorted(access_times.items()):
        if elapsed is None:
            problem_models.append(model)
            click.echo(f"  ✗ {model:<20} UNAVAILABLE")
        else:
            working_models.append((model, elapsed))
            status = "OK" if elapsed < 5 else "SLOW" if elapsed < 10 else "VERY SLOW"
            click.echo(f"  ✓ {model:<20} {elapsed:6.2f}s [{status}]")

    click.echo()

    if working_models:
        click.echo(f"Working models: {', '.join([m[0] for m in working_models])}")

    if problem_models:
        click.echo(format_error("experiment", f"Unavailable models: {', '.join(problem_models)}"), err=True)

    click.echo()

    # Ask for confirmation
    try:
        response = click.confirm("Continue with experiment?", default=True)
        return response
    except click.Abort:
        return False


def generate_filename(timestamp: str, prompt_name: str, experiment: str, model: str, index: int, format_type: str, temp_component: str = None, supports_temp: bool = True) -> str:
    """
    Generate output filename: YYYYMMDDHHMMSS-experimentname-promptname-modelname-tNN-nn.ext
    where NN is the temperature component (two digits for supported models, or "xx" for unsupported).
    """
    ext = FORMAT_EXTENSIONS[format_type]
    sanitized_model = sanitize_model_name(model)
    index_str = f"{index:02d}"

    if supports_temp and temp_component is not None:
        temp_str = f"-t{temp_component}"
    else:
        temp_str = "-txx"

    return f"{timestamp}-{experiment}-{prompt_name}-{sanitized_model}{temp_str}-{index_str}.{ext}"


def get_first_iteration_timestamp() -> str:
    """Return current timestamp for output files (YYYYMMDDHHmmss)."""
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_model_help():
    """Generate help text with available model shortcuts."""
    available = get_available_models()
    shortcuts = []
    for provider, models in available.items():
        for shortcut in models.keys():
            shortcuts.append(shortcut)
    return f"Model shortcut or full name (configured: {', '.join(sorted(shortcuts))})"


def process_format(actual_model, format_type, master_prompt, experiment, prompt_name, iterations, temperature, batch_file, temp_float, temp_component, supports_temp, provider=None, prompt_file_idx=None, total_prompt_files=None, format_instructions_cache=None, resume_mode=False):
    """
    Process a single format: append format instruction and generate output files.
    Returns tuple: (processed_files, timestamp) for successful processing
                   or (processed_files, timestamp, timeout_info) if timeout occurs
    where timeout_info = {"timed_out": True, "model": model_name}

    If provider is provided, uses it for all generations. Otherwise creates new provider
    for each call (less efficient for local models).
    prompt_file_idx and total_prompt_files indicate which command-line prompt file this is.
    format_instructions_cache: Optional pre-computed format instructions (performance optimization)
    """
    logger.info(f"Starting format processing - Format: {format_type}, Model: {actual_model}, Iterations: {iterations}")

    # Check for completed iterations if resuming
    completed_iterations = {}
    skip_iterations_msg = None
    if resume_mode:
        completed_iterations = find_completed_iterations(actual_model, experiment, prompt_name, format_type)
        if completed_iterations:
            max_completed = max(completed_iterations.keys())
            # Preserve the original timestamp from the first completed iteration
            first_completed_timestamp = completed_iterations[min(completed_iterations.keys())][1]
            timestamp = first_completed_timestamp
            if max_completed >= iterations:
                logger.info(f"All {iterations} iterations already completed")
                return [], timestamp
            skip_iterations_msg = f"Skipping iterations 1-{max_completed} (already completed)"
            logger.info(skip_iterations_msg)

    # Get timestamp for this format's first iteration (if not already set by resume)
    if not completed_iterations:
        timestamp = get_first_iteration_timestamp()

    # Get format instruction (use cache if provided, otherwise compute)
    if format_instructions_cache and format_type in format_instructions_cache:
        format_instruction = format_instructions_cache[format_type]
    else:
        format_instruction = get_format_instruction(format_type)
    logger.debug(f"Format instruction: {format_instruction}")

    # Append format instruction to prompt
    formatted_prompt = master_prompt + "\n\n" + format_instruction

    # Determine if batch processing
    prompts = [formatted_prompt]
    if batch_file:
        logger.info(f"Loading batch file: {batch_file}")
        with open(batch_file, 'r') as f:
            batch_prompts = [line.rstrip('\n') for line in f if line.strip()]
        # Append format instruction to each batch prompt
        batch_prompts = [p + "\n\n" + format_instruction for p in batch_prompts]
        prompts = batch_prompts
        logger.info(f"Loaded {len(prompts)} prompts from batch file")

    processed_files = []

    # Process each prompt
    for prompt_idx, prompt in enumerate(prompts, 1):
        # Run iterations for this prompt
        for iteration in range(1, iterations + 1):
            # Skip if already completed (in resume mode)
            if resume_mode and iteration in completed_iterations:
                continue

            # Build message with command-line prompt file info if available
            if prompt_file_idx is not None and total_prompt_files is not None:
                prompt_part = f"prompt file {prompt_file_idx}/{total_prompt_files}"
                if len(prompts) > 1:
                    prompt_part += f" (batch prompt {prompt_idx}/{len(prompts)})"
            else:
                if len(prompts) > 1:
                    prompt_part = f"prompt {prompt_idx}/{len(prompts)}"
                else:
                    prompt_part = "prompt"

            msg = f"Processing {prompt_part}, iteration {iteration}/{iterations}, format {format_type}..."
            click.echo(msg)
            logger.info(msg)

            timeout_secs = get_model_timeout(actual_model)
            gen_error = None
            timed_out = False
            output = None

            for attempt in range(1, MAX_GENERATION_RETRIES + 2):
                try:
                    logger.debug(f"Calling generate() for {actual_model} (attempt {attempt}/{MAX_GENERATION_RETRIES + 1})")
                    output = generate(actual_model, prompt, provider=provider)
                    gen_error = None
                    timed_out = False
                    break  # Success
                except KeyboardInterrupt:
                    logger.warning("Processing interrupted by user")
                    click.echo(format_error("experiment", "Processing cancelled by user"), err=True)
                    sys.exit(1)
                except TimeoutError:
                    timed_out = True
                    timeout_msg = (
                        f"TIMEOUT: {actual_model} exceeded {timeout_secs}s on prompt {prompt_idx},"
                        f" iteration {iteration}, attempt {attempt}/{MAX_GENERATION_RETRIES + 1}"
                    )
                    click.echo(format_error("experiment", timeout_msg), err=True)
                    logger.error(timeout_msg)
                    if attempt <= MAX_GENERATION_RETRIES:
                        reinit_msg = (
                            f"Reinitializing {actual_model} before retry"
                            f" {attempt + 1}/{MAX_GENERATION_RETRIES + 1}..."
                        )
                        click.echo(reinit_msg)
                        logger.warning(reinit_msg)
                        try:
                            reinitialize_ollama_model(actual_model)
                        except KeyboardInterrupt:
                            # User pressed Ctrl-C during reinitialize; propagate immediately
                            raise
                except Exception as e:
                    gen_error = e
                    err_msg = f"Error processing prompt {prompt_idx}, iteration {iteration}: {type(e).__name__}: {e}"
                    click.echo(format_error("experiment", err_msg), err=True)
                    logger.error(err_msg, exc_info=True)
                    break  # Non-timeout errors are not retried

            if timed_out and output is None:
                # All retry attempts exhausted
                return processed_files, timestamp, {"timed_out": True, "model": actual_model}

            if gen_error is not None:
                continue  # Skip file save; move on to next iteration

            logger.debug(f"Generated output, length: {len(output)} characters")

            # Generate filename
            filename = generate_filename(timestamp, prompt_name, experiment, actual_model, iteration, format_type, temp_component, supports_temp)

            # Write to file in results folder
            output_path = Path("results") / filename

            # Check if file already exists
            if output_path.exists():
                err_msg = f"File already exists: {output_path}"
                click.echo(format_error("experiment", err_msg), err=True)
                logger.error(err_msg)
                continue

            # Write with UTF-8 encoding to handle any Unicode characters
            logger.debug(f"Writing output to {output_path}")
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(output)

            # Check if output contains non-ASCII characters
            has_non_ascii = any(ord(char) > 127 for char in output)
            processed_files.append((filename, has_non_ascii))

            if has_non_ascii:
                logger.info(f"Saved (with non-ASCII): {output_path}")
            else:
                logger.info(f"Saved: {output_path}")
            click.echo(f"Saved: {output_path}")

    logger.info(f"Format {format_type} processing complete. Processed {len(processed_files)} files.")
    return processed_files, timestamp


def prompt_for_multiple(prompt_text, is_path=False, validate_fn=None, previous_value=None):
    """
    Prompt user for multiple values. Press Enter to finish.
    If is_path=True, verify files exist.
    If validate_fn provided, calls it with the value and expects (is_valid, error_msg) tuple.
    If previous_value provided (tuple), offer Option 0 to use previous configuration.
    """
    values = []
    index = 1

    # Show Option 0 if previous value exists
    if previous_value:
        click.echo(f"  0) Use previous: {', '.join(previous_value)}")

    while True:
        if is_path:
            prompt_msg = f"{prompt_text} #{index} (or press Enter to finish, or '0' for previous): "
        else:
            prompt_msg = f"{prompt_text} #{index} (or press Enter to finish, or '0' for previous): "

        value = click.prompt(prompt_msg, default='').strip()

        # Check for Option 0 (use previous)
        if value == '0':
            if previous_value:
                return previous_value
            else:
                click.echo(format_error("experiment", "No previous configuration available"), err=True)
                continue

        # Empty input finishes
        if not value:
            break

        # Validate file exists if needed
        if is_path and not Path(value).exists():
            click.echo(format_error("experiment", f"File does not exist: {value}"), err=True)
            continue

        # Validate with custom function if provided
        if validate_fn:
            is_valid, error_msg = validate_fn(value)
            if not is_valid:
                click.echo(format_error("experiment", error_msg), err=True)
                continue

        values.append(value)
        index += 1

    return tuple(values)


def validate_model(model_name):
    """
    Validate that a model name is supported (configured in models.json).
    Returns (is_valid, error_msg) tuple.
    """
    available = get_available_models()
    all_shortcuts = []
    for provider, models_dict in available.items():
        all_shortcuts.extend(models_dict.keys())

    # Check if it's a known shortcut
    if model_name in all_shortcuts:
        return True, ""

    # Check if it resolves to a known model via resolve_model_name
    resolved = resolve_model_name(model_name)
    if resolved != model_name:  # It was resolved to something
        return True, ""

    # Not found
    available_str = ', '.join(sorted(all_shortcuts))
    return False, f"Model '{model_name}' not found. Available: {available_str}"


def prompt_for_format(previous_value=None):
    """
    Prompt user to select output format(s).
    Returns tuple of formats. If user types 'all', returns all valid formats.
    If previous_value provided (tuple), offer Option 0 to use previous configuration.
    Press Enter with no input to finish selection.
    """
    click.echo("Available formats: " + ", ".join(VALID_FORMATS))

    # Show Option 0 if previous value exists
    if previous_value:
        click.echo(f"  0) Use previous: {', '.join(previous_value)}")

    selected_formats = []
    index = 1

    while True:
        prompt_msg = f"Enter format #{index} (or press Enter to finish, or '0' for previous, or type 'all' for all formats): "
        fmt = click.prompt(prompt_msg, default='').strip()

        # Check for Option 0 (use previous)
        if fmt == '0':
            if previous_value:
                return previous_value
            else:
                click.echo(format_error("experiment", "No previous configuration available"), err=True)
                continue

        # Empty input finishes
        if not fmt:
            if not selected_formats:
                click.echo(format_error("experiment", "At least one format is required"), err=True)
                continue
            return tuple(selected_formats)

        # User selected all - return all valid formats
        if fmt == 'all':
            return tuple(VALID_FORMATS)

        # Validate format
        if fmt not in VALID_FORMATS:
            click.echo(format_error("experiment", f"Unknown format: {fmt}. Available: {', '.join(VALID_FORMATS)}"), err=True)
            continue

        # Add format if not duplicate
        if fmt not in selected_formats:
            selected_formats.append(fmt)
            click.echo(f"  Added: {fmt}")
        else:
            click.echo(f"  {fmt} already selected")

        index += 1


@click.command()
@click.option('-m', '--model', multiple=True, help=get_model_help())
@click.option('-f', '--format', 'format_type', type=click.Choice(VALID_FORMATS + ['all']),
              help='Output format (or "all" to iterate through each format)')
@click.option('-p', '--prompt', 'prompts', multiple=True, type=click.Path(exists=True),
              help='Path to file containing prompt (can specify multiple times)')
@click.option('-e', '--experiment', 'experiments', multiple=True, help='Experiment name (can specify multiple times)')
@click.option('-i', '--iterations', type=click.IntRange(1, 99),
              help='Number of iterations (1-99, default 1)')
@click.option('-t', '--temperature', 'temperatures', multiple=True, type=str, default=None,
              help='Temperature parameter (can specify multiple times)')
@click.option('-b', '--batch-file', type=click.Path(exists=True),
              help='Optional file with multiple prompts (one per line)')
@click.option('--debug', is_flag=True, default=False,
              help='Enable debug logging to stdout and file')
@click.option('--restart', is_flag=True, default=False,
              help='Force restart: regenerate all iterations even if some exist')
def main(model, format_type, prompts, experiments, iterations, temperatures, batch_file, debug, restart):
    """
    Generate LLM outputs with specified format(s), model(s), prompt(s), experiment(s), and temperature(s).
    Logs experiment details and any encoding translations required.
    """
    available = get_available_models()
    all_shortcuts = []
    for provider, models_dict in available.items():
        all_shortcuts.extend(models_dict.keys())

    # Check for previous run configuration
    previous_config = load_run_config()

    if previous_config and not model and not format_type and not prompts and not experiments:
        # Display previous run configuration for reference
        click.echo("\n" + "=" * 70)
        click.echo("PREVIOUS EXPERIMENT CONFIGURATION (available as Option 0)")
        click.echo("=" * 70 + "\n")

        timestamp = previous_config.get("timestamp", "unknown")
        click.echo(f"Last run: {timestamp}\n")

        click.echo("Configuration:")
        click.echo(f"  Models:       {', '.join(previous_config.get('model', []))}")
        # Handle both old-style (string) and new-style (list) format_type
        fmt_config = previous_config.get('format_type', [])
        fmt_display = fmt_config if isinstance(fmt_config, str) else ', '.join(fmt_config)
        click.echo(f"  Formats:      {fmt_display}")
        click.echo(f"  Prompts:      {', '.join(previous_config.get('prompts', []))}")
        click.echo(f"  Experiments:  {', '.join(previous_config.get('experiments', []))}")
        click.echo(f"  Iterations:   {previous_config.get('iterations', 'not set')}")
        click.echo(f"  Temperatures: {', '.join(previous_config.get('temperatures', [])) if previous_config.get('temperatures') else 'default'}")
        if previous_config.get('batch_file'):
            click.echo(f"  Batch file:   {previous_config.get('batch_file')}")
        click.echo()

    # Prompt for missing required parameters
    if not model:
        click.echo(f"Available models: {', '.join(sorted(all_shortcuts))}")
        previous_models = tuple(previous_config.get('model', [])) if previous_config else None
        model = prompt_for_multiple("Enter model", validate_fn=validate_model, previous_value=previous_models)
        if not model:
            click.echo(format_error("experiment", "At least one model is required"), err=True)
            sys.exit(1)

    # Test model access times AFTER models have been selected
    click.echo("\n" + "=" * 70)
    click.echo("SANITY CHECK: Testing selected model access times")
    click.echo("=" * 70 + "\n")

    access_times = check_model_access_times(list(model))

    # Present results and ask for confirmation only if there are problems
    problem_models = [m for m, t in access_times.items() if t is None]
    if problem_models:
        if not present_model_access_report(access_times):
            click.echo(format_error("experiment", "Experiment cancelled by user"), err=True)
            sys.exit(0)
    else:
        # All selected models are available, show brief status
        click.echo("All selected models are available.\n")

    click.echo()

    if not format_type:
        # Handle both old-style (string) and new-style (list) format_type in config
        previous_formats = None
        if previous_config:
            fmt_config = previous_config.get('format_type', [])
            if isinstance(fmt_config, str):
                # Old style: single string
                previous_formats = (fmt_config,) if fmt_config else None
            else:
                # New style: list
                previous_formats = tuple(fmt_config) if fmt_config else None
        format_type = prompt_for_format(previous_value=previous_formats)

    if not prompts:
        previous_prompts = tuple(previous_config.get('prompts', [])) if previous_config else None
        prompts = prompt_for_multiple("Enter prompt file path", is_path=True, previous_value=previous_prompts)
        if not prompts:
            click.echo(format_error("experiment", "At least one prompt file is required"), err=True)
            sys.exit(1)

    if not experiments:
        previous_experiments = tuple(previous_config.get('experiments', [])) if previous_config else None
        experiments = prompt_for_multiple("Enter experiment name", previous_value=previous_experiments)
        if not experiments:
            click.echo(format_error("experiment", "At least one experiment name is required"), err=True)
            sys.exit(1)

    if iterations is None:
        previous_iterations = previous_config.get('iterations', 1) if previous_config else 1
        iterations = click.prompt("Enter number of iterations (1-99)", type=click.IntRange(1, 99), default=previous_iterations)

    if not temperatures:
        click.echo("Enter temperature values (2-digit format like 08 for 0.8, or leave blank for default)")
        previous_temps = tuple(previous_config.get('temperatures', [])) if previous_config else None
        temperatures = prompt_for_multiple("Enter temperature", previous_value=previous_temps)

    # Configure logging based on debug flag
    log_level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler('experiment_run.log', mode='a')
        ],
        force=True
    )

    logger.info("=" * 80)
    logger.info(f"EXPERIMENT BATCH START")
    logger.info(f"Models: {', '.join(model)}")
    logger.info(f"Experiments: {', '.join(experiments)}")
    logger.info(f"Prompts: {', '.join(prompts)}")
    logger.info(f"Temperatures: {', '.join(temperatures) if temperatures else 'default'}")
    logger.info(f"Formats: {', '.join(format_type) if format_type else 'all'}, Iterations: {iterations}")
    logger.info(f"Debug mode: {debug}")
    logger.info("=" * 80)

    # Default to [None] if no temperatures specified
    temps_list = list(temperatures) if temperatures else [None]

    # Validate all models
    for m in model:
        is_valid, error_msg = validate_model(m)
        if not is_valid:
            click.echo(format_error("experiment", error_msg), err=True)
            sys.exit(1)

    # Detect incomplete experiments and ask about resuming
    resume_mode = True  # Default to resuming
    if not restart:
        # Check if any experiments are incomplete
        logger.info("Checking for incomplete experiments...")
        incomplete_combos = []
        for m in model:
            actual_model = resolve_model_name(m)
            for exp in experiments:
                for prompt_file in prompts:
                    prompt_name = Path(prompt_file).stem
                    for fmt in format_type if isinstance(format_type, (list, tuple)) else [format_type]:
                        if fmt == 'all':
                            formats_check = VALID_FORMATS
                        else:
                            formats_check = [fmt]
                        for fmt_check in formats_check:
                            completed = find_completed_iterations(actual_model, exp, prompt_name, fmt_check)
                            if completed and max(completed.keys()) < iterations:
                                incomplete_combos.append(
                                    (m, exp, prompt_file, fmt_check, max(completed.keys()))
                                )
                                break

        if incomplete_combos and not restart:
            click.echo(f"\nFound {len(set((c[0], c[1], c[2]) for c in incomplete_combos))} incomplete experiment combination(s)")
            for combo in incomplete_combos[:3]:
                m, exp, prompt_file, fmt, max_iter = combo
                click.echo(f"  {m} / {exp} / {Path(prompt_file).stem} / {fmt}: up to iteration {max_iter}")
            if len(incomplete_combos) > 3:
                click.echo(f"  ... and {len(incomplete_combos) - 3} more")
            click.echo()

            if not click.confirm("Resume from where you left off?", default=True):
                resume_mode = False
                click.echo("Restarting from iteration 1 (may overwrite existing files)\n")
            else:
                click.echo("Resuming incomplete experiments\n")
                logger.info("Resume mode enabled: will skip completed iterations")
    else:
        resume_mode = False
        logger.info("--restart flag set: regenerating all iterations")

    # Track models that timed out and should be skipped
    timed_out_models = set()

    try:
        # Optimization 1: Cache prompt files (read once, reuse across all iterations)
        logger.info("Pre-loading prompts into cache...")
        prompts_cache = {}
        for prompt_file in prompts:
            with open(prompt_file, 'r') as f:
                prompts_cache[prompt_file] = f.read()
        logger.info(f"Cached {len(prompts_cache)} prompt file(s)")

        # Optimization 2: Cache format instructions (compute once)
        # format_type can be a string (from command-line) or tuple (from prompt_for_format)
        if not format_type:
            formats_to_process = VALID_FORMATS
        elif isinstance(format_type, str):
            # Handle string from command-line option
            formats_to_process = [format_type] if format_type != 'all' else VALID_FORMATS
        else:
            # Handle tuple from prompt_for_format
            formats_to_process = list(format_type)
        logger.info(f"Formats to process: {', '.join(formats_to_process)}")

        logger.info("Pre-computing format instructions...")
        format_instructions_cache = {}
        for fmt in formats_to_process:
            format_instructions_cache[fmt] = get_format_instruction(fmt)
        logger.info(f"Cached {len(format_instructions_cache)} format instruction(s)")

        # Optimization 3: Cache model temperature support (check once per model)
        logger.info("Pre-computing model temperature support...")
        model_temp_support_cache = {}

        # Iterate through all combinations, creating provider once per model
        for model_idx, m in enumerate(model, 1):
            # Resolve model shortcut to actual model name (once per model)
            actual_model = resolve_model_name(m)
            logger.info(f"Resolved model shortcut '{m}' to '{actual_model}' ({model_idx}/{len(model)})")

            # Check if this model has already timed out
            if actual_model in timed_out_models:
                skip_msg = f"SKIPPING {actual_model} - previously timed out (all retries exhausted)"
                click.echo(format_error("experiment", skip_msg), err=True)
                logger.warning(skip_msg)
                continue

            # Create provider once per model for efficiency (especially for local models)
            logger.info(f"Creating provider for {actual_model}...")
            provider = get_provider(actual_model)
            logger.info(f"Provider created for {actual_model}")

            # Cache temperature support for this model (Optimization 3)
            model_temp_support_cache[actual_model] = model_supports_temperature(actual_model)

            # Determine which temperatures to use for this model
            supports_temp = model_temp_support_cache[actual_model]
            if supports_temp:
                temps_to_use = temps_list
            else:
                # Models that don't support temperature only run once (use default temperature)
                temps_to_use = ['default']
                click.echo(f"Note: {actual_model} does not support temperature. Running once with default settings.")
                logger.info(f"{actual_model} does not support temperature - will process {len(experiments)} experiment(s) x {len(prompts)} prompt(s) = {len(experiments) * len(prompts)} combinations instead of {len(experiments) * len(prompts) * len(temps_list)}")

            for exp_idx, exp in enumerate(experiments, 1):
                for temp_idx, temperature in enumerate(temps_to_use, 1):
                    # Parse temperature parameter (once per temperature)
                    try:
                        # Handle 'default' temperature for models that don't support temperature
                        if temperature == 'default':
                            temp_float, temp_component = None, None
                            logger.info(f"Temperature: default (model does not support temperature parameter)")
                        else:
                            temp_float, temp_component = parse_temperature(temperature)
                            logger.info(f"Temperature: {temp_float if temp_float is not None else 'default'}")
                    except ValueError as e:
                        err_msg = str(e)
                        click.echo(format_error("experiment", err_msg), err=True)
                        logger.error(err_msg)
                        sys.exit(1)

                    # Check if model supports temperature (use cached value - Optimization 3)
                    supports_temp = model_temp_support_cache[actual_model]
                    logger.info(f"Model supports temperature: {supports_temp}")

                    for prompt_file_idx, prompt_file in enumerate(prompts, 1):
                        logger.info(f"\n--- Processing: Model {model_idx}/{len(model)}: {m}, Experiment {exp_idx}/{len(experiments)}: {exp}, Temp {temp_idx}/{len(temps_to_use)}: {temperature}, Prompt {prompt_file_idx}/{len(prompts)}: {prompt_file} ---")

                        # Get master prompt from cache (Optimization 1 - already loaded upfront)
                        master_prompt = prompts_cache[prompt_file]
                        logger.debug(f"Using cached prompt, length: {len(master_prompt)} characters")

                        # Extract prompt filename without extension for output filenames
                        prompt_name = Path(prompt_file).stem

                        # Collect all processed files and their encoding info for this combination
                        all_processed_files = []
                        first_timestamp = None

                        # Process each format (pass format_instructions_cache for Optimization 2)
                        for fmt in formats_to_process:
                            result = process_format(actual_model, fmt, master_prompt, exp, prompt_name, iterations, temperature, batch_file, temp_float, temp_component, supports_temp, provider=provider, prompt_file_idx=prompt_file_idx, total_prompt_files=len(prompts), format_instructions_cache=format_instructions_cache, resume_mode=resume_mode)

                            # Check if result indicates a timeout
                            if len(result) == 3 and isinstance(result[2], dict) and result[2].get("timed_out"):
                                timed_out_models.add(actual_model)
                                timeout_secs = get_model_timeout(actual_model)
                                timeout_msg = f"TIMEOUT: {actual_model} exceeded {timeout_secs}s (all retries exhausted) - skipping remaining formats and experiments for this model"
                                click.echo(format_error("experiment", timeout_msg), err=True)
                                logger.error(timeout_msg)
                                break  # Break out of format loop

                            processed_files, timestamp = result[0], result[1]
                            all_processed_files.extend(processed_files)
                            if first_timestamp is None:
                                first_timestamp = timestamp

                        # If model timed out, break out of all remaining loops for this model
                        if actual_model in timed_out_models:
                            break

                    if actual_model in timed_out_models:
                        break

                if actual_model in timed_out_models:
                    break

                # Write experiment log using first format's timestamp (only if we have results)
                if first_timestamp:
                    log_filename = f"{first_timestamp}-{exp}.log"
                    log_path = Path("results") / log_filename

                    logger.debug(f"Writing experiment log to {log_path}")
                    with open(log_path, 'w', encoding='utf-8') as f:
                        f.write(f"Experiment Log\n")
                        f.write(f"==============\n\n")
                        f.write(f"Timestamp: {first_timestamp}\n")
                        f.write(f"Experiment: {exp}\n")
                        f.write(f"Model: {actual_model}\n")
                        f.write(f"Prompt: {prompt_file}\n")
                        f.write(f"Format(s): {', '.join(formats_to_process)}\n")
                        f.write(f"Temperature: {temperature if temperature else 'default'}\n")
                        f.write(f"Iterations: {iterations}\n\n")

                        files_with_translations = [filename for filename, has_non_ascii in all_processed_files if has_non_ascii]

                        if files_with_translations:
                            f.write(f"Files Requiring UTF-8 Encoding (Non-ASCII Characters):\n")
                            f.write(f"-----------------------------------------------------\n")
                            for filename in files_with_translations:
                                f.write(f"  - {filename}\n")
                        else:
                            f.write(f"No files required UTF-8 encoding (all outputs contained only ASCII characters).\n")

                        f.write(f"\nTotal Files Generated: {len(all_processed_files)}\n")
                        f.write(f"Files with Non-ASCII Characters: {len(files_with_translations)}\n")

                    click.echo(f"Experiment log saved: {log_path}")
                    logger.info(f"Completed: {exp} ({actual_model}). Generated {len(all_processed_files)} files.")

        # Save configuration for next run
        save_run_config(model, format_type, prompts, experiments, iterations, temperatures, batch_file, debug)

        # Display final summary including timeout information
        click.echo("\n" + "=" * 80)
        logger.info("=" * 80)

        if timed_out_models:
            timeout_summary = f"\n!!! EXPERIMENT COMPLETED WITH TIMEOUTS !!!\n\nModels that timed out (all retries exhausted) and were skipped:\n"
            for timed_out_model in sorted(timed_out_models):
                timeout_summary += f"  - {timed_out_model}\n"
            timeout_summary += "\nThese models were not processed for remaining experiments."
            click.echo(format_error("experiment", timeout_summary), err=True)
            logger.error(timeout_summary)
        else:
            logger.info("EXPERIMENT BATCH COMPLETED SUCCESSFULLY")
            click.echo("EXPERIMENT BATCH COMPLETED SUCCESSFULLY")

        click.echo("=" * 80)
        logger.info("=" * 80)

    except KeyboardInterrupt:
        logger.warning("Experiment interrupted by user")
        click.echo(format_error("experiment", "Experiment cancelled by user"), err=True)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error during experiment: {type(e).__name__}: {e}", exc_info=True)
        click.echo(format_error("experiment", f"{type(e).__name__}: {e}"), err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
