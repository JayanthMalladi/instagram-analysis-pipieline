"""Shared fetch -> analyse steps used by both the CLI and the MCP server."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .analysis import AnalysisConfig, AnalysisResult, analyze
from .config import Config, ConfigError
from .fetch import Reel, fetch_account, fetch_online_followers, fetch_reels
from .graph_client import GraphClient
from .report import shift_online_followers


def fetch_payload(lookback_days: int = 180, max_media: int = 1000) -> dict[str, Any]:
    """Fetch account, reels with insights, and online followers. Contains no secrets."""
    client = GraphClient(Config.from_env())
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "account": fetch_account(client),
        "reels": [r.to_dict() for r in fetch_reels(client, lookback_days=lookback_days,
                                                   max_media=max_media)],
        "online_followers_pacific": fetch_online_followers(client),
    }


def validate_timezone(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"Unknown timezone {tz!r}; use an IANA name like Asia/Kolkata") from None
    return tz


def analyze_payload(payload: dict[str, Any], cfg: AnalysisConfig
                    ) -> tuple[AnalysisResult, dict[str, Any] | None, dict[int, float] | None]:
    validate_timezone(cfg.timezone)
    reels = [Reel.from_dict(d) for d in payload.get("reels", [])]
    online_raw = payload.get("online_followers_pacific")
    online = shift_online_followers({int(k): v for k, v in online_raw.items()}, cfg.timezone) \
        if online_raw else None
    return analyze(reels, cfg), payload.get("account"), online
