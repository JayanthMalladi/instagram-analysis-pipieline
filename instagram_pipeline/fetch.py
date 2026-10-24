"""Pull the account's recent Reels and their per-reel insights."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .graph_client import GraphAPIError, GraphClient

log = logging.getLogger(__name__)

MEDIA_FIELDS = ",".join([
    "id", "caption", "media_type", "media_product_type", "permalink",
    "timestamp", "like_count", "comments_count",
])

# Current Reels insight metrics. "views" replaced "plays" (and
# ig_reels_aggregated_all_plays_count) in Graph API v22 (2025); "plays" is kept
# as a fallback for accounts pinned to older API versions.
REEL_METRICS = [
    "reach", "views", "likes", "comments", "shares", "saved",
    "total_interactions", "ig_reels_avg_watch_time",
]
FALLBACK_METRICS = {"views": "plays"}


@dataclass
class Reel:
    id: str
    timestamp: str                    # ISO 8601, UTC, as returned by the API
    permalink: str | None = None
    caption: str | None = None
    like_count: int | None = None
    comments_count: int | None = None
    insights: dict[str, float] = field(default_factory=dict)

    @property
    def posted_at(self) -> datetime:
        return parse_ig_timestamp(self.timestamp)

    def metric(self, name: str) -> float | None:
        if name in self.insights:
            return self.insights[name]
        if name == "views" and "plays" in self.insights:
            return self.insights["plays"]
        if name == "likes" and self.like_count is not None:
            return float(self.like_count)
        if name == "comments" and self.comments_count is not None:
            return float(self.comments_count)
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Reel":
        return cls(**{k: d.get(k) for k in ("id", "timestamp", "permalink", "caption",
                                            "like_count", "comments_count")},
                   insights=dict(d.get("insights") or {}))


def parse_ig_timestamp(ts: str) -> datetime:
    # The API returns e.g. "2026-10-01T14:03:22+0000".
    if len(ts) >= 5 and (ts[-5] in "+-") and ts[-3] != ":":
        ts = ts[:-2] + ":" + ts[-2:]
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_insights(payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in payload.get("data", []):
        name = item.get("name")
        value = None
        if item.get("values"):
            value = item["values"][0].get("value")
        elif "total_value" in item:
            value = item["total_value"].get("value")
        if name and isinstance(value, (int, float)):
            out[name] = float(value)
    return out


def fetch_account(client: GraphClient) -> dict[str, Any]:
    return client.get(client.config.ig_user_id,
                      {"fields": "id,username,followers_count,media_count"})


def fetch_reel_insights(client: GraphClient, media_id: str) -> dict[str, float]:
    """Fetch all metrics in one call; if the API rejects the set (e.g. a metric
    isn't supported on this API version), fall back to one call per metric."""
    try:
        return _parse_insights(client.get(f"{media_id}/insights",
                                          {"metric": ",".join(REEL_METRICS)}))
    except GraphAPIError as e:
        log.debug("Batch insights failed for %s (%s); trying metrics individually", media_id, e)

    out: dict[str, float] = {}
    for metric in REEL_METRICS:
        for name in (metric, FALLBACK_METRICS.get(metric)):
            if not name:
                continue
            try:
                out.update(_parse_insights(client.get(f"{media_id}/insights", {"metric": name})))
                break
            except GraphAPIError as e:
                log.debug("Metric %s unavailable for %s: %s", name, media_id, e)
    return out


def _list_recent_media(client: GraphClient, fields: str, cutoff: datetime,
                       max_media: int) -> list[dict[str, Any]]:
    items = []
    for item in client.paginate(f"{client.config.ig_user_id}/media",
                                {"fields": fields, "limit": 50}, max_items=max_media):
        ts = item.get("timestamp")
        if ts and parse_ig_timestamp(ts) < cutoff:
            break  # media is returned newest first
        items.append(item)
    return items


def fetch_reels(client: GraphClient, *, lookback_days: int = 180,
                max_media: int = 1000, now: datetime | None = None) -> list[Reel]:
    """Return Reels posted in the last `lookback_days`, newest first, with insights.

    First tries to get insights inline with the media listing (field expansion,
    one request per 50 posts). If the API rejects that, e.g. because a metric
    doesn't apply to a photo in the same page, it lists media plainly and makes
    one insights request per reel.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    expanded = f"{MEDIA_FIELDS},insights.metric({','.join(REEL_METRICS)})"
    try:
        items = _list_recent_media(client, expanded, cutoff, max_media)
    except GraphAPIError as e:
        log.info("Inline insights not available (%s); fetching per reel", e)
        items = _list_recent_media(client, MEDIA_FIELDS, cutoff, max_media)

    reels: list[Reel] = []
    for item in items:
        if item.get("media_product_type") != "REELS" or not item.get("timestamp"):
            continue
        reel = Reel(id=item["id"], timestamp=item["timestamp"], permalink=item.get("permalink"),
                    caption=item.get("caption"), like_count=item.get("like_count"),
                    comments_count=item.get("comments_count"))
        inline = item.get("insights")
        reel.insights = _parse_insights(inline) if inline else {}
        if not reel.insights:
            reel.insights = fetch_reel_insights(client, reel.id)
        reels.append(reel)
        log.debug("Reel %s (%s): %s", reel.id, reel.timestamp,
                  {k: int(v) for k, v in reel.insights.items()})
    log.info("Scanned %d media items, kept %d reels", len(items), len(reels))
    return reels


def fetch_online_followers(client: GraphClient) -> dict[int, float] | None:
    """Hour-of-day (0-23, in the account's reported timezone) -> avg followers online.

    Meta only returns this for accounts with 100+ followers, and returns it in
    Pacific time. Treated as optional supporting context; None if unavailable.
    """
    try:
        payload = client.get(f"{client.config.ig_user_id}/insights",
                             {"metric": "online_followers", "period": "lifetime"})
    except GraphAPIError as e:
        log.info("online_followers unavailable: %s", e)
        return None
    totals: dict[int, list[float]] = {}
    for item in payload.get("data", []):
        for v in item.get("values", []):
            for hour, n in (v.get("value") or {}).items():
                if isinstance(n, (int, float)):
                    totals.setdefault(int(hour), []).append(float(n))
    if not totals:
        return None
    return {h: sum(v) / len(v) for h, v in sorted(totals.items())}
