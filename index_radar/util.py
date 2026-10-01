"""Kleine Helfer: HTTP mit Retry, Logging, Zeit."""
from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timezone

import requests

from .config import HTTP_TIMEOUT, USER_AGENT

log = logging.getLogger("index_radar")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        _session = s
    return _session


def get(url: str, *, tries: int = 3, timeout: int = HTTP_TIMEOUT, **kw) -> requests.Response:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            r = session().get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < tries - 1:
                time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"GET {url} fehlgeschlagen: {last}") from last


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_ticker(raw: object) -> str:
    """Wikipedia/S&P schreiben Ticker uneinheitlich (BRK.B vs BRK-B)."""
    s = str(raw or "").strip().upper()
    s = re.sub(r"\[.*?\]", "", s)
    s = s.split()[0] if s else ""
    return s.replace(".", "-")


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"
