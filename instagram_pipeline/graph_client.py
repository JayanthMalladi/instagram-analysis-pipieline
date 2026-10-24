"""A small, dependency-free client for Meta's Graph API (plain HTTPS via urllib).

Handles pagination, retries with backoff on rate limits / transient errors,
optional appsecret_proof signing, and makes sure the access token never ends
up in logs or exception messages.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator

from .config import Config

log = logging.getLogger(__name__)

# Graph API error codes that mean "slow down" or "try again".
_RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002}
_TRANSIENT_CODES = {1, 2}

_TOKEN_RE = re.compile(r"(access_token=|appsecret_proof=|client_secret=|fb_exchange_token=)[^&\s\"']+")


def redact(text: str) -> str:
    return _TOKEN_RE.sub(r"\1REDACTED", text)


class GraphAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: int | None = None,
                 subcode: int | None = None, fbtrace_id: str | None = None):
        super().__init__(redact(message))
        self.status = status
        self.code = code
        self.subcode = subcode
        self.fbtrace_id = fbtrace_id


# A transport takes (url, timeout) and returns (status, body_text).
Transport = Callable[[str, float], "tuple[int, str]"]


def _urllib_transport(url: str, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "instagram-pipeline/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


class GraphClient:
    def __init__(self, config: Config, *, transport: Transport | None = None,
                 timeout: float = 30.0, max_retries: int = 5,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self.base = f"https://{config.host}/{config.api_version}"
        self._transport = transport or _urllib_transport
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep
        self._proof = None
        if config.app_secret:
            self._proof = hmac.new(config.app_secret.encode(), config.access_token.encode(),
                                   hashlib.sha256).hexdigest()

    def _auth_params(self) -> dict[str, str]:
        params = {"access_token": self.config.access_token}
        if self._proof:
            params["appsecret_proof"] = self._proof
        return params

    def _build_url(self, path_or_url: str, params: dict[str, Any] | None) -> str:
        if path_or_url.startswith("https://"):
            # Paging URLs from the API already carry the query (and the token).
            url = path_or_url
            if params:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
            return url
        query = {k: v for k, v in (params or {}).items() if v is not None}
        query.update(self._auth_params())
        return f"{self.base}/{path_or_url.lstrip('/')}?{urllib.parse.urlencode(query)}"

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._build_url(path_or_url, params)
        attempt = 0
        while True:
            attempt += 1
            try:
                status, body = self._transport(url, self.timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt > self.max_retries:
                    raise GraphAPIError(f"Network error: {e}") from None
                self._backoff(attempt, f"network error: {e}")
                continue

            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}

            if 200 <= status < 300 and "error" not in data:
                return data

            err = data.get("error", {}) if isinstance(data, dict) else {}
            code = err.get("code")
            retryable = status >= 500 or status == 429 or code in _RATE_LIMIT_CODES \
                or code in _TRANSIENT_CODES or err.get("is_transient")
            if retryable and attempt <= self.max_retries:
                self._backoff(attempt, f"HTTP {status} code={code}")
                continue
            raise GraphAPIError(
                f"Graph API error (HTTP {status}, code {code}): "
                f"{err.get('message') or body[:300]}",
                status=status, code=code, subcode=err.get("error_subcode"),
                fbtrace_id=err.get("fbtrace_id"),
            )

    def _backoff(self, attempt: int, why: str) -> None:
        delay = min(60.0, 2.0 ** attempt)
        log.warning("Retrying in %.0fs (%s)", delay, redact(why))
        self._sleep(delay)

    def paginate(self, path: str, params: dict[str, Any] | None = None,
                 max_items: int | None = None) -> Iterator[dict[str, Any]]:
        page = self.get(path, params)
        count = 0
        while True:
            for item in page.get("data", []):
                yield item
                count += 1
                if max_items is not None and count >= max_items:
                    return
            nxt = page.get("paging", {}).get("next")
            if not nxt:
                return
            page = self.get(nxt)
