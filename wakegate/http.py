"""Small urllib wrapper. URLs are never included in raised messages."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from wakegate import __version__
from wakegate.errors import CheckError, WakeError

USER_AGENT = f"wakegate/{__version__}"
MAX_BYTES = 2_000_000


def fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CheckError(f"HTTP {exc.code} while fetching") from exc
    except urllib.error.URLError as exc:
        raise CheckError(f"fetch failed: {exc.reason}") from exc
    if len(data) > MAX_BYTES:
        raise CheckError(f"response exceeded {MAX_BYTES} bytes")
    return data


def post_json(
    url: str,
    payload: dict,
    headers: dict[str, str] | None,
    timeout: float,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    request = urllib.request.Request(url, data=body, headers=merged, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1024)
    except urllib.error.HTTPError as exc:
        raise WakeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise WakeError(f"request failed: {exc.reason}") from exc
