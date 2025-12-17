#!/usr/bin/env python
"""Extract transcript text from a JSON file and copy it to the clipboard."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


def _extract_text(payload: Dict[str, Any]) -> str:
    """Return the best-effort transcript text from the Whisper payload."""

    for key in ("transcript", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    result = payload.get("result")
    if isinstance(result, dict):
        for key in ("transcript", "text"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    raise ValueError("No transcript text field found in JSON payload")


def _copy_to_clipboard(text: str) -> None:
    """Copy text to the clipboard (prefers pbcopy on macOS)."""

    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    try:
        import pyperclip

        pyperclip.copy(text)
        return
    except Exception:  # pragma: no cover - fallback best-effort
        pass

    sys.stdout.write(text)
    raise RuntimeError("Unable to copy text to clipboard; printed to stdout instead")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path, help="Path to the transcript JSON file")
    args = parser.parse_args()

    data = json.loads(args.json_path.read_text())
    text = _extract_text(data)
    _copy_to_clipboard(text)
    print(f"Copied {len(text.split())} words from {args.json_path} to the clipboard.")


if __name__ == "__main__":
    main()
