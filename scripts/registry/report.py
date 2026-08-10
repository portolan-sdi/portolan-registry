"""Progress output.

Diagnostics go to stderr so stdout carries only the export, which is what
makes `--output -` pipeable:

    diff <(uv run scripts/publish_export.py --output -) exports/catalogs.json
"""

from __future__ import annotations

import sys


def log(message: str = "") -> None:
    """Write a progress line to stderr."""
    print(message, file=sys.stderr)
