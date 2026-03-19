# Configuration and constants

import os
import json
import re


def load_formats_config():
    """Load format configuration from formats.json."""
    config_path = os.path.join(os.path.dirname(__file__), 'formats.json')
    with open(config_path, 'r') as f:
        return json.load(f)


# Load format extensions and valid formats from formats.json
_formats_config = load_formats_config()
FORMAT_EXTENSIONS = {fmt: info['extension'] for fmt, info in _formats_config.items()}
VALID_FORMATS = list(FORMAT_EXTENSIONS.keys())

# Model name sanitization: remove punctuation for filename
PUNCTUATION_REMOVE = {
    '.': '',
    ':': '',
    '/': '',
    '\\': '',
    '-': '',
}


def sanitize_model_name(model_name: str) -> str:
    """Remove punctuation from model name for filename."""
    result = model_name
    for char, replacement in PUNCTUATION_REMOVE.items():
        result = result.replace(char, replacement)
    return result


def load_models_config():
    """Load model shortcuts from models.json."""
    config_path = os.path.join(os.path.dirname(__file__), 'models.json')
    with open(config_path, 'r') as f:
        return json.load(f)


def resolve_model_name(model_shortcut: str) -> str:
    """
    Resolve a model shortcut to its actual name.
    If shortcut not found, return as-is (assume it's already the actual name).
    """
    config = load_models_config()

    # Search all providers for the shortcut
    for provider, provider_config in config.items():
        if 'models' in provider_config and model_shortcut in provider_config['models']:
            model_entry = provider_config['models'][model_shortcut]
            # Handle both old string format and new dict format
            if isinstance(model_entry, dict):
                return model_entry.get('name', model_shortcut)
            else:
                return model_entry

    # If not found, return as-is
    return model_shortcut


def get_model_color(model_identifier: str) -> str:
    """
    Get the color for a model by shortcut, full name, or sanitized filename name.

    The sanitized name is the full model name with punctuation stripped, which is
    how models appear in result filenames (e.g. 'gpt41nano20250414' for
    'gpt-4.1-nano-2025-04-14').  Accepting all three forms means adding a new
    model to models.json with a color is sufficient -- no other code needs to
    change.

    Returns a hex color string.  Falls back to '#999999' (gray) if not found.
    """
    config = load_models_config()

    for provider, provider_config in config.items():
        if 'models' in provider_config:
            for shortcut, model_entry in provider_config['models'].items():
                if isinstance(model_entry, dict):
                    full_name = model_entry.get('name', '')
                    if (model_identifier == shortcut
                            or model_identifier == full_name
                            or model_identifier == sanitize_model_name(full_name)):
                        return model_entry.get('color', '#999999')
                else:
                    # Old string format has no color
                    if (model_identifier == shortcut
                            or model_identifier == model_entry
                            or model_identifier == sanitize_model_name(model_entry)):
                        return '#999999'

    return '#999999'


def get_model_timeout(model_identifier: str, default: int = 600) -> int:
    """
    Get the timeout in seconds for a model by shortcut, full name, or sanitized filename name.

    Accepts the same three forms as get_model_color(): shortcut key, full model name,
    or sanitized filename name (punctuation stripped).  Adding timeout_seconds to a
    model entry in models.json is sufficient -- no other code needs to change.

    Returns the configured timeout_seconds value, or default (600) if not configured.
    """
    config = load_models_config()

    for provider, provider_config in config.items():
        if 'models' in provider_config:
            for shortcut, model_entry in provider_config['models'].items():
                if isinstance(model_entry, dict):
                    full_name = model_entry.get('name', '')
                    if (model_identifier == shortcut
                            or model_identifier == full_name
                            or model_identifier == sanitize_model_name(full_name)):
                        return model_entry.get('timeout_seconds', default)
                else:
                    if (model_identifier == shortcut
                            or model_identifier == model_entry
                            or model_identifier == sanitize_model_name(model_entry)):
                        return default

    return default


def get_all_models_with_colors() -> dict:
    """
    Get all models with their colors grouped by provider.
    Returns: {provider: {shortcut: {name, color, supports_temp}}}
    """
    config = load_models_config()
    result = {}

    for provider, provider_config in config.items():
        result[provider] = {}
        supports_temp = provider_config.get('supports_temperature', True)

        if 'models' in provider_config:
            for shortcut, model_entry in provider_config['models'].items():
                if isinstance(model_entry, dict):
                    result[provider][shortcut] = {
                        'name': model_entry.get('name', shortcut),
                        'color': model_entry.get('color', '#999999'),
                        'supports_temperature': supports_temp
                    }
                else:
                    # Old format
                    result[provider][shortcut] = {
                        'name': model_entry,
                        'color': '#999999',
                        'supports_temperature': supports_temp
                    }

    return result


def get_available_models() -> dict:
    """Get all configured model shortcuts grouped by provider."""
    config = load_models_config()
    models = {}

    for provider, provider_config in config.items():
        if 'models' in provider_config:
            models[provider] = provider_config['models']

    return models


def get_format_instruction(format_type: str) -> str:
    """
    Get the prompt instruction for a specific format.
    Returns the instruction string to append to prompts.
    """
    if format_type in _formats_config:
        return _formats_config[format_type]['prompt']
    # Fallback if format not found
    return f"Return the results in {format_type} format"


def model_supports_temperature(model_name: str) -> bool:
    """
    Determine if a model supports temperature parameter.
    Reads from models.json provider configuration.
    """
    config = load_models_config()

    # Search all providers for the model and check temperature support.
    # Accept shortcut, full name, or sanitized filename name (punctuation stripped).
    for provider, provider_config in config.items():
        if 'models' in provider_config:
            for shortcut, model_entry in provider_config['models'].items():
                if isinstance(model_entry, dict):
                    full_name = model_entry.get('name', shortcut)
                    if (model_name == shortcut
                            or model_name == full_name
                            or model_name == sanitize_model_name(full_name)):
                        return provider_config.get('supports_temperature', True)
                else:
                    if (model_name == shortcut
                            or model_name == model_entry
                            or model_name == sanitize_model_name(model_entry)):
                        return provider_config.get('supports_temperature', True)

    # Default to True if provider not found (assume supports temperature)
    return True


def parse_temperature(temp_str: str) -> tuple:
    """
    Parse temperature from 2-digit string format (00-20).
    Returns (temperature_value, filename_component).
    Example: "14" -> (1.4, "14")
    """
    if temp_str is None:
        return None, None

    try:
        # Check if user tried to pass multiple values (common mistake)
        if ' ' in temp_str:
            raise ValueError(
                f"Temperature '{temp_str}' contains spaces. "
                f"To specify multiple temperatures, use separate -t flags. "
                f"Example: -t 01 -t 04 -t 08  (not -t '01 04 08')"
            )

        # Try to convert to integer
        try:
            temp_int = int(temp_str)
        except ValueError:
            raise ValueError(
                f"Temperature '{temp_str}' must be numeric (00-20). "
                f"Use 2-digit format: 00 for 0.0, 05 for 0.5, 10 for 1.0, etc."
            )

        if temp_int < 0 or temp_int > 20:
            raise ValueError(f"Temperature must be 00-20, got {temp_str}")
        temp_float = temp_int / 10.0
        return temp_float, f"{temp_int:02d}"
    except ValueError as e:
        # Re-raise our custom ValueError as-is, wrap other ValueErrors
        if "contain spaces" in str(e) or "must be" in str(e):
            raise
        raise ValueError(f"Invalid temperature: {e}")


def abbreviate_model_name(model_name):
    """
    Remove date stamps and other suffixes from model names for display.
    E.g., 'claude-3-5-haiku-20241022' -> 'claude-3-5-haiku'
    E.g., 'gpt41nano20250414' -> 'gpt41nano'
    """
    # Remove trailing date-like patterns (8+ digits, with optional hyphen prefix)
    return re.sub(r'-?\d{8,}$', '', model_name)
