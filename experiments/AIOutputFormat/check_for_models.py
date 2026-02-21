#!/usr/bin/env python3
"""
Check Ollama connection and list available models.
Supports testing models from models.cfg configuration.
"""

import sys
import json
import logging
import urllib.request
import urllib.error
from typing import Optional, List, Dict

from config import load_models_config, resolve_model_name

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def get_configured_models() -> Dict[str, List[tuple]]:
    """
    Get all configured models grouped by provider.

    Returns:
        Dict mapping provider names to list of (shortcut, full_name) tuples
    """
    config = load_models_config()
    result = {}

    for section in config.sections():
        result[section] = sorted(config.items(section))

    return result


def prompt_for_model(base_url: str = "http://localhost:11434") -> Optional[str]:
    """
    Interactively prompt user to select a model from models.cfg.

    Args:
        base_url: Base URL for Ollama service

    Returns:
        Model name to test, or None if cancelled
    """
    models_by_provider = get_configured_models()

    if not models_by_provider:
        logger.error("No configured models found in models.cfg")
        return None

    print("\n" + "=" * 70)
    print("AVAILABLE MODELS FROM models.cfg")
    print("=" * 70)

    all_models = []
    model_num = 1

    for provider in sorted(models_by_provider.keys()):
        print(f"\n[{provider.upper()}]")
        for shortcut, full_name in models_by_provider[provider]:
            print(f"  {model_num:2d}. {shortcut:<20} -> {full_name}")
            all_models.append((shortcut, full_name, provider))
            model_num += 1

    print(f"\n   0. Cancel")
    print()

    while True:
        try:
            choice = input("Select model to test (enter number): ").strip()

            if choice == "0":
                return None

            choice_int = int(choice)
            if 1 <= choice_int <= len(all_models):
                shortcut, full_name, provider = all_models[choice_int - 1]
                logger.info(f"Selected: {shortcut} ({full_name})")
                return full_name

            print(f"Invalid choice. Enter 0-{len(all_models)}")
        except ValueError:
            print("Please enter a valid number")
        except KeyboardInterrupt:
            print("\nCancelled")
            return None


def check_ollama_connection(base_url: str = "http://localhost:11434") -> bool:
    """
    Check if Ollama service is running and accessible.

    Args:
        base_url: Base URL for Ollama service (default: http://localhost:11434)

    Returns:
        True if connection successful, False otherwise
    """
    try:
        logger.info(f"Checking Ollama connection at {base_url}...")
        url = f"{base_url}/api/tags"

        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status == 200:
                logger.info("Successfully connected to Ollama")
                return True
            else:
                logger.error(f"Ollama returned status code {response.status}")
                return False

    except urllib.error.URLError as e:
        if hasattr(e, 'reason'):
            logger.error(f"Cannot connect to Ollama at {base_url}")
            logger.error(f"  Reason: {e.reason}")
            logger.error("  Is Ollama running? Try: ollama serve")
        else:
            logger.error(f"Error connecting to Ollama: {e}")
        return False
    except urllib.error.HTTPError as e:
        logger.error(f"Ollama returned HTTP error: {e.code}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__}: {e}")
        return False


def list_models(base_url: str = "http://localhost:11434") -> Optional[dict]:
    """
    List all available models from Ollama.

    Args:
        base_url: Base URL for Ollama service

    Returns:
        Dict with model list, or None if error
    """
    try:
        url = f"{base_url}/api/tags"

        with urllib.request.urlopen(url, timeout=5) as response:
            data = response.read()
            return json.loads(data)

    except Exception as e:
        logger.error(f"Error fetching models: {type(e).__name__}: {e}")
        return None


def test_model(model_name: str, base_url: str = "http://localhost:11434") -> bool:
    """
    Test a specific model by sending a simple prompt via Ollama HTTP API.

    Args:
        model_name: Model shortcut or full name to test
        base_url: Base URL for Ollama service

    Returns:
        True if model responds, False otherwise
    """
    actual_model_name = resolve_model_name(model_name)
    logger.info(f"Testing model: {model_name} ({actual_model_name})...")

    try:
        url = f"{base_url}/api/generate"
        payload = {
            "model": actual_model_name,
            "prompt": "Say 'test'",
            "stream": False
        }

        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())

            if "response" in result:
                logger.info(f"Model '{model_name}' responded successfully")
                logger.info(f"  Response: {result['response'].strip()[:80]}")
                return True
            else:
                logger.warning(f"Model '{model_name}' returned unexpected response")
                return False

    except urllib.error.URLError as e:
        logger.error(f"Cannot reach model: {e}")
        return False
    except json.JSONDecodeError:
        logger.error(f"Invalid response from model")
        return False
    except Exception as e:
        logger.error(f"Error testing model: {type(e).__name__}: {e}")
        return False


def main():
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Check Ollama connection and test models from models.cfg"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:11434",
        help="Ollama base URL (default: http://localhost:11434)"
    )
    parser.add_argument(
        "--test-model",
        help="Test a specific model by name (skip interactive selection)"
    )
    parser.add_argument(
        "--list-configured",
        action="store_true",
        help="List all models from models.cfg and exit"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle list-configured flag
    if args.list_configured:
        models_by_provider = get_configured_models()
        if models_by_provider:
            print("\nConfigured models in models.cfg:\n")
            for provider in sorted(models_by_provider.keys()):
                print(f"[{provider.upper()}]")
                for shortcut, full_name in models_by_provider[provider]:
                    print(f"  {shortcut:<20} -> {full_name}")
                print()
        else:
            print("No models found in models.cfg")
        sys.exit(0)

    print("=" * 70)
    print("OLLAMA CONNECTION CHECK")
    print("=" * 70)
    print()

    # Check connection
    if not check_ollama_connection(args.url):
        logger.error("\nCannot reach Ollama. Exiting.")
        sys.exit(1)

    print()

    # List available models on Ollama
    logger.info("Fetching available models from Ollama...")
    models_data = list_models(args.url)

    if models_data and "models" in models_data:
        models = models_data["models"]

        if models:
            logger.info(f"Found {len(models)} model(s) on Ollama:")
            print()
            for model in models:
                name = model.get("name", "unknown")
                size = model.get("size", 0)
                size_gb = size / (1024**3) if size > 0 else 0
                modified = model.get("modified_at", "unknown")

                logger.info(f"  {name:<30} {size_gb:>6.1f} GB  (modified: {modified})")
        else:
            logger.warning("No models found on Ollama")
    else:
        logger.error("Failed to retrieve model list")

    print()

    # Determine which model to test
    model_to_test = args.test_model

    if not model_to_test:
        # Interactive mode: prompt user to select from configured models
        model_to_test = prompt_for_model(args.url)

    if model_to_test:
        print()
        if test_model(model_to_test, args.url):
            logger.info(f"\nModel test passed")
            sys.exit(0)
        else:
            logger.error(f"\nModel test failed")
            sys.exit(1)

    print("=" * 70)
    logger.info("Connection check complete (no model tested)")
    print("=" * 70)


if __name__ == "__main__":
    main()
