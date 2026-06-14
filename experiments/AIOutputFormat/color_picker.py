#!/usr/bin/env python3
"""
Interactive color picker for model colors in models.json.
Allows users to assign and customize colors for each model.
"""

import json
import click
from pathlib import Path
from config import get_all_models_with_colors, load_models_config


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to hex color."""
    return f'#{r:02X}{g:02X}{b:02X}'


def increase_saturation(hex_color: str, factor: float = 1.3) -> str:
    """
    Increase saturation of a color for temperature-based variants.

    Args:
        hex_color: Hex color string (e.g., '#FF6B6B')
        factor: Saturation increase factor (1.0 = no change, > 1.0 = more saturated)

    Returns:
        Modified hex color string
    """
    import colorsys

    r, g, b = hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

    # Increase saturation, capped at 1.0
    s = min(s * factor, 1.0)

    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return rgb_to_hex(int(r * 255), int(g * 255), int(b * 255))


def display_color(hex_color: str) -> str:
    """Display color in a terminal-friendly way."""
    # ANSI color codes for background
    r, g, b = hex_to_rgb(hex_color)
    # Use 256-color approximation
    ansi_code = 16 + (36 * (r // 51) + 6 * (g // 51) + (b // 51))
    return f"\033[48;5;{ansi_code}m  {hex_color}  \033[0m"


def prompt_for_color(current_color: str = None) -> str:
    """
    Prompt user to enter a color.
    Accepts: hex color (#RRGGBB) or lets user pick from common colors.
    """
    if current_color:
        click.echo(f"Current color: {display_color(current_color)}")

    click.echo("\nOptions:")
    click.echo("  1. Enter hex color (e.g., #FF6B6B)")
    click.echo("  2. Pick from common colors")
    click.echo("  3. Keep current color")

    choice = click.prompt("Choose option", type=click.Choice(['1', '2', '3'], case_sensitive=False))

    if choice == '3':
        return current_color

    if choice == '2':
        colors = {
            'Red': '#FF6B6B',
            'Green': '#6BCB77',
            'Blue': '#4D96FF',
            'Purple': '#A78BFA',
            'Cyan': '#4ECDC4',
            'Yellow': '#FFD93D',
            'Orange': '#FF8C42',
            'Pink': '#FB7185',
        }
        click.echo("\nCommon colors:")
        for i, (name, color) in enumerate(colors.items(), 1):
            click.echo(f"  {i}. {name} {display_color(color)}")

        color_choice = click.prompt("Select color", type=click.IntRange(1, len(colors)))
        color_list = list(colors.values())
        return color_list[color_choice - 1]

    # Choice 1: enter hex
    while True:
        hex_input = click.prompt("Enter hex color (e.g., #FF6B6B)").strip()
        if len(hex_input) == 7 and hex_input.startswith('#'):
            try:
                int(hex_input[1:], 16)
                return hex_input.upper()
            except ValueError:
                click.echo("Invalid hex color. Please use format #RRGGBB")
        else:
            click.echo("Invalid format. Please use #RRGGBB")


def main():
    """Main color picker interface."""
    click.echo("=" * 60)
    click.echo("MODEL COLOR PICKER")
    click.echo("=" * 60)

    config_path = Path(__file__).parent / 'models.json'
    config = load_models_config()
    all_models = get_all_models_with_colors()

    # Display current colors
    click.echo("\nCurrent Model Colors:\n")
    for provider, models in all_models.items():
        click.echo(f"[{provider.upper()}]")
        for shortcut, info in models.items():
            temp_str = "✓ Temp" if info['supports_temperature'] else "✗ No Temp"
            click.echo(f"  {shortcut:15} {info['name']:40} {display_color(info['color'])} {temp_str}")
        click.echo()

    # Offer to edit
    while True:
        click.echo("\nOptions:")
        click.echo("  1. Edit model color")
        click.echo("  2. Generate random colors")
        click.echo("  3. Exit")

        option = click.prompt("Choose option", type=click.Choice(['1', '2', '3'], case_sensitive=False))

        if option == '3':
            break

        if option == '2':
            import random
            click.echo("\nGenerating random colors...")
            for provider, models in config.items():
                if 'models' in models:
                    for shortcut in models['models']:
                        # Generate random color
                        r = random.randint(0, 255)
                        g = random.randint(0, 255)
                        b = random.randint(0, 255)
                        new_color = rgb_to_hex(r, g, b)

                        if isinstance(models['models'][shortcut], dict):
                            models['models'][shortcut]['color'] = new_color
                        else:
                            # Convert to new format
                            old_name = models['models'][shortcut]
                            models['models'][shortcut] = {
                                'name': old_name,
                                'color': new_color
                            }

            # Save config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            click.echo("Colors updated!")
            click.echo("\nNew colors:")
            for provider, models in load_models_config().items():
                click.echo(f"\n[{provider.upper()}]")
                if 'models' in models:
                    for shortcut, entry in models['models'].items():
                        color = entry.get('color') if isinstance(entry, dict) else '#999999'
                        click.echo(f"  {shortcut:15} {display_color(color)}")

        elif option == '1':
            # Select model to edit
            all_shortcuts = []
            provider_model_map = {}

            for provider, models in all_models.items():
                for shortcut, info in models.items():
                    all_shortcuts.append(shortcut)
                    provider_model_map[shortcut] = (provider, info)

            click.echo("\nAvailable models:")
            for i, shortcut in enumerate(all_shortcuts, 1):
                provider, info = provider_model_map[shortcut]
                click.echo(f"  {i:2d}. {shortcut:15} ({provider})")

            choice = click.prompt("Select model to edit", type=click.IntRange(1, len(all_shortcuts)))
            selected = all_shortcuts[choice - 1]
            provider, info = provider_model_map[selected]

            click.echo(f"\nEditing: {selected}")
            new_color = prompt_for_color(info['color'])

            # Update config
            if isinstance(config[provider]['models'][selected], dict):
                config[provider]['models'][selected]['color'] = new_color
            else:
                old_name = config[provider]['models'][selected]
                config[provider]['models'][selected] = {
                    'name': old_name,
                    'color': new_color
                }

            # Save config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)

            click.echo(f"✓ Color updated: {display_color(new_color)}")

    click.echo("\nGoodbye!")


if __name__ == '__main__':
    main()
