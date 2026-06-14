"""Tiny stdlib HTTP helper. No third-party deps so the funding signal
works with nothing installed but Python.

SEC EDGAR requires a descriptive User-Agent with contact info and asks
for <= 10 requests/sec. Set SEC_EDGAR_UA to override the default.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

# macOS system Python often can't verify TLS via urllib (no system CA access).
# Use certifi's bundle when available so SEC EDGAR / data.sec.gov work out of the box.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    _SSL_CTX = ssl.create_default_context()

DEFAULT_UA = os.environ.get(
    "SEC_EDGAR_UA", "dtscore/0.1 (deeptech-scorer; contact: set SEC_EDGAR_UA)"
)
_LAST_CALL = [0.0]
_MIN_INTERVAL = 0.15  # ~7 req/s, under EDGAR's limit


class HttpError(RuntimeError):
    pass


def _throttle():
    import time as _t
    elapsed = _t.monotonic() - _LAST_CALL[0]
    if elapsed < _MIN_INTERVAL:
        _t.sleep(_MIN_INTERVAL - elapsed)
    _LAST_CALL[0] = _t.monotonic()


def get(url: str, *, as_json: bool = False, ua: str | None = None, timeout: float = 20.0):
    _throttle()
    req = urllib.request.Request(url, headers={
        "User-Agent": ua or DEFAULT_UA,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json, text/xml, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            elif r.headers.get("Content-Encoding") == "deflate":
                import zlib
                raw = zlib.decompress(raw)
            text = raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise HttpError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise HttpError(f"network error for {url}: {e.reason}") from e
    return json.loads(text) if as_json else text
