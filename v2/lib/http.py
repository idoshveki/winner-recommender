"""HTTP with retries. Every network call in v2 goes through here.

v1 scattered bare requests.get calls with inconsistent error handling; several
swallowed failures with `except Exception: pass`, which is how a pipeline runs
green for five months while writing nothing.
"""
from __future__ import annotations

import time
from typing import Optional

import requests


class FetchError(RuntimeError):
    """Raised when a request cannot be completed. Never swallowed."""


def get(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: int = 20,
    retries: int = 3,
    backoff: float = 1.5,
) -> requests.Response:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.RequestException as exc:      # network-level
            last = exc
        else:
            if r.status_code == 429 or r.status_code >= 500:
                last = FetchError(f"HTTP {r.status_code} for {url}")
            else:
                if r.status_code >= 400:
                    # 4xx is a real error (bad key, dead endpoint) - failing
                    # fast here is what surfaced v1's removed SofaScore route.
                    raise FetchError(f"HTTP {r.status_code} for {url}: {r.text[:200]}")
                return r
        if attempt < retries - 1:
            time.sleep(backoff ** attempt)
    raise FetchError(f"giving up on {url} after {retries} attempts: {last}")
