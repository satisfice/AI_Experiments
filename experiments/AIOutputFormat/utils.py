"""Utility functions for the AIOutputFormat project."""

import sys
from datetime import datetime
from pathlib import Path


def format_error(program_name: str, message: str) -> str:
    """
    Format an error message with program name and timestamp.

    Args:
        program_name: Name of the program producing the error (e.g., 'experiment', 'summarize')
        message: The error message

    Returns:
        Formatted error string: "[ProgramName] [TIMESTAMP] ERROR: message"
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"[{program_name}] [{timestamp}] ERROR: {message}"


def print_error(program_name: str, message: str, exit_code: int = None):
    """
    Print a formatted error message to stderr.

    Args:
        program_name: Name of the program producing the error
        message: The error message
        exit_code: Optional exit code (if provided, calls sys.exit)
    """
    formatted_msg = format_error(program_name, message)
    sys.stderr.write(formatted_msg + "\n")
    sys.stderr.flush()
    if exit_code is not None:
        sys.exit(exit_code)
