#!/usr/bin/env python3
"""
Query Ollama for available local models and list configured shortcuts.
"""

import json
import sys
import urllib.request
import urllib.error

from config import get_available_models

OLLAMA_API_URL = "http://localhost:11434/api/tags"


def query_ollama_models():
    """Query Ollama for available local models."""
    try:
        with urllib.request.urlopen(OLLAMA_API_URL, timeout=5) as response:
            data = json.loads(response.read())
            models = data.get('models', [])
            return [model['name'] for model in models]
    except (urllib.error.URLError, json.JSONDecodeError):
        return None


def list_configured_models():
    """List all configured model shortcuts from models.json."""
    models_by_provider = get_available_models()

    print("\n=== Configured Model Shortcuts ===\n")
    for provider in sorted(models_by_provider.keys()):
        print(f"[{provider.upper()}]")
        for shortcut, actual_model in sorted(models_by_provider[provider].items()):
            print(f"  {shortcut:15} -> {actual_model}")
        print()


def query_ollama():
    """Query and display Ollama models."""
    print("Querying Ollama local models...")
    models = query_ollama_models()

    if models is None:
        print("Error: Could not connect to Ollama at http://localhost:11434")
        print("Make sure Ollama is running.")
        return

    if not models:
        print("No models found in Ollama.")
        return

    print(f"\nFound {len(models)} model(s) in Ollama:\n")
    for model in models:
        print(f"  {model}")

    print("\nAdd these to models.cfg [ollama] section with shortcuts if desired.")


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == 'ollama':
            query_ollama()
        elif sys.argv[1] == 'list':
            list_configured_models()
        else:
            print(f"Unknown command: {sys.argv[1]}")
            print("Usage: python query_models.py [ollama|list]")
    else:
        print("=== Model Query Tool ===\n")
        print("Usage: python query_models.py [command]\n")
        print("Commands:")
        print("  ollama    - Query available Ollama models")
        print("  list      - List all configured model shortcuts")
        print()
        list_configured_models()


if __name__ == '__main__':
    main()
