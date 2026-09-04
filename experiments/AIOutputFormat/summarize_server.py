#!/usr/bin/env python3
"""Flask server for Summarize Results frontend."""

import json
import subprocess
import sys
from pathlib import Path
from flask import Flask, jsonify, request, send_file

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from cli_helpers import collect_available_values

app = Flask(__name__, static_folder=None)
RESULTS_DIR = Path("results")
CONFIG_FILE = RESULTS_DIR / "summarize_config.json"


def load_config():
    """Load summarize_config.json or return empty config."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"experiments": [], "exclude_model": [], "temperatures": []}


def save_config(config):
    """Save config to summarize_config.json."""
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


@app.route('/')
def index():
    """Serve the frontend HTML."""
    with open(Path(__file__).parent / "summarize_frontend.html", 'r') as f:
        return f.read()


@app.route('/api/available-values')
def available_values():
    """Get available experiments, models, and temperatures."""
    experiments, models, temperatures = collect_available_values()
    return jsonify({
        "experiments": experiments,
        "models": models,
        "temperatures": [str(t) for t in temperatures]
    })


@app.route('/api/config', methods=['GET'])
def get_config():
    """Get saved configuration."""
    return jsonify(load_config())


@app.route('/api/config', methods=['POST'])
def post_config():
    """Save configuration."""
    config = request.json
    save_config(config)
    return jsonify({"success": True})


@app.route('/api/summarize', methods=['POST'])
def summarize():
    """Run summarize with the given parameters."""
    params = request.json

    # Build command
    cmd = [sys.executable, "summarize.py"]

    # Add experiments filter
    if params.get("experiments"):
        cmd.extend(["--experiment", params["experiments"][0]])

    # Add model exclusions
    for model in params.get("exclude_model", []):
        cmd.extend(["--exclude-model", model])

    # Add temperature filter (use first selected)
    if params.get("temperatures"):
        try:
            temp = float(params["temperatures"][0]) if params["temperatures"][0] != "xx" else None
            if temp is not None:
                cmd.extend(["--temperature", str(temp)])
        except (ValueError, IndexError):
            pass

    cmd.append("--no-prompt")
    cmd.append("-v")

    try:
        result = subprocess.run(
            cmd,
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=120
        )
        output = result.stdout + result.stderr
        return jsonify({
            "success": result.returncode == 0,
            "output": output
        })
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "output": "Error: Summarize command timed out after 120 seconds"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "output": f"Error: {str(e)}"
        })


if __name__ == "__main__":
    print("Starting Summarize Results server at http://localhost:5000")
    print("Press Ctrl+C to stop")
    app.run(debug=True, port=5000, use_reloader=False)
