from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ListLoadError(ValueError):
    """Raised when a user-supplied list file is malformed"""


def load_targets(path: str) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        raise ListLoadError(f"file not found: {path}")

    suffix = p.suffix.lower()
    if suffix == ".json":
        return _load_json(p)
    return _load_text(p)


def _load_json(p: Path) -> dict[str, str]:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ListLoadError(f"{p}: invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ListLoadError(
            f"{p}: top-level JSON value must be an object {{name: url, ...}}"
        )

    out: dict[str, str] = {}
    for name, url in data.items():
        if not isinstance(name, str) or not isinstance(url, str):
            raise ListLoadError(
                f"{p}: each entry must be string→string, got {type(name).__name__}"
                f"→{type(url).__name__}"
            )
        out[name] = _normalize_url(url)
    return out


def _load_text(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        name, url = _parse_text_line(line)
        if url is None:
            url = _normalize_url(line)
            name = _autoname(url)

        if name in out:
            logger.warning("%s:%d: duplicate name %r, overwriting", p, lineno, name)
        out[name] = url

    if not out:
        raise ListLoadError(f"{p}: no usable entries (all comments/blank?)")
    return out


def _parse_text_line(line: str) -> tuple[str, Optional[str]]:
    if "=" in line and " " not in line.split("=", 1)[0]:
        name, _, url = line.partition("=")
        name = name.strip()
        url = url.strip()
        if name and url:
            return name, _normalize_url(url)

    parts = line.split(None, 1)
    if len(parts) == 2:
        name, second = parts[0].strip(), parts[1].strip()
        if "://" in second or _looks_like_hostname(second):
            return name, _normalize_url(second)

    return line, None


def _looks_like_hostname(s: str) -> bool:
    if " " in s or "\t" in s:
        return False
    return "." in s


def _normalize_url(url: str) -> str:
    if "://" not in url:
        url = "https://" + url
    return url


def _autoname(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.replace(".", "-")