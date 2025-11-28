from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def extract_panopto_session_id(url: str) -> str:
    """Derive a deterministic session identifier from a Panopto URL."""

    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    for key in ("id", "sessionId", "session_id", "sid"):
        values = query_params.get(key)
        if values:
            return values[0].strip()

    path_segments = [segment for segment in parsed.path.split("/") if segment]
    if path_segments:
        return path_segments[-1]

    raise ValueError("Unable to determine Panopto session id from URL")
