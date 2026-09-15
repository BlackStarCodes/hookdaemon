"""Shared test helpers. Not collected by pytest (filename has no ``_test``)."""

import json
from io import StringIO
from typing import Any


def parse_json_lines(buf: StringIO) -> list[dict[str, Any]]:
    """Parse every non-empty line of the buffer as a JSON object.

    Raises ``json.JSONDecodeError`` if any line is not valid JSON. That is
    intentional: our logging pipeline emits JSON-only, so a non-JSON line in
    a test is a bug, not something to skip silently.
    """
    out: list[dict[str, Any]] = []
    for line in buf.getvalue().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        out.append(json.loads(line))
    return out
