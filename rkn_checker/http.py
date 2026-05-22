from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

from . import __version__
from .targets import STUB_MARKERS

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
BODY_SNIPPET_LEN = 2000

GENERIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)
HONEST_USER_AGENT = (
    f"rkn-block-checker/{__version__} "
    "(+https://github.com/MayersScott/rkn-block-checker)"
)

GENERIC_HEADERS: dict[str, str] = {
    "User-Agent": GENERIC_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class HttpProbe:
    status_code: Optional[int] = None
    elapsed_ms: Optional[float] = None
    body_snippet: str = ""
    error: Optional[str] = None
    timed_out: bool = False


def build_headers(identify: bool = False) -> dict[str, str]:
    """Return the header set to use for a probe.

    `identify=False` (default): generic Chrome-like headers — minimizes the
    fingerprint left in logs. This is what end users running the tool to
    check their own connection should send.

    `identify=True`: honest, self-identifying UA so that operators of probed
    infrastructure can distinguish this tool from anonymous traffic. Use
    this when you control or have permission to probe the -
    """
    if identify:
        return {**GENERIC_HEADERS, "User-Agent": HONEST_USER_AGENT}
    return dict(GENERIC_HEADERS)


def fetch(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    identify: bool = False,
    proxy_url: Optional[str] = None,
) -> HttpProbe:
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers=build_headers(identify=identify),
            proxies=proxies,
        )
        return HttpProbe(
            status_code=r.status_code,
            elapsed_ms=r.elapsed.total_seconds() * 1000,
            body_snippet=r.text[:BODY_SNIPPET_LEN].lower(),
        )
    except requests.exceptions.Timeout:
        return HttpProbe(error="timeout", timed_out=True)
    except requests.exceptions.RequestException as e:
        return HttpProbe(error=f"{type(e).__name__}: {e}")

def looks_like_stub(body_snippet: str) -> bool:
    return any(marker in body_snippet for marker in STUB_MARKERS)
