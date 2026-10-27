"""Niche research: what's working for other creators in your niche.

Uses two Graph API features that only exist on the Facebook Login flow
(graph.facebook.com, Instagram account linked to a Facebook Page):

- Business Discovery: public profile + recent posts (likes, comments, caption,
  timestamp) of any other Business/Creator account, by @username.
- Hashtag search: the top posts for a hashtag. Meta allows 30 unique hashtags
  per 7 days per Instagram account.

Neither gives other people's views or reach, so posts are ranked by likes +
comments, and against the creator's own median so a big account's average
post doesn't drown out a small account's breakout.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from statistics import median
from typing import Any

from .config import ConfigError
from .graph_client import GraphAPIError, GraphClient

log = logging.getLogger(__name__)

NEEDS_FACEBOOK_LOGIN = (
    "Niche research needs a Facebook Login token: Instagram only offers Business "
    "Discovery and hashtag search on that flow. Link your Instagram to a Facebook Page "
    "and follow 'Option B: Facebook Login' in the README, then set IG_ACCESS_TOKEN "
    "(starts with EAA) and IG_USER_ID."
)

POST_FIELDS = "id,caption,media_type,media_product_type,like_count,comments_count,timestamp,permalink"
HASHTAG_POST_FIELDS = "id,caption,media_type,like_count,comments_count,timestamp,permalink"
_HASHTAG_RE = re.compile(r"#(\w+)")


def _require_facebook_login(client: GraphClient) -> None:
    if client.config.instagram_login:
        raise ConfigError(NEEDS_FACEBOOK_LOGIN)


def clean_username(name: str) -> str:
    name = name.strip()
    if "instagram.com/" in name:
        name = name.split("instagram.com/", 1)[1]
    return name.strip("/@ ").split("/")[0].split("?")[0]


def _interactions(post: dict[str, Any]) -> int:
    return int(post.get("like_count") or 0) + int(post.get("comments_count") or 0)


def _is_reel(post: dict[str, Any]) -> bool:
    if "media_product_type" in post:
        return post["media_product_type"] == "REELS"
    return post.get("media_type") == "VIDEO"


def hook(caption: str | None, limit: int = 120) -> str:
    """First line of the caption, which is usually the hook or the on-screen text."""
    first = (caption or "").strip().splitlines()[0] if (caption or "").strip() else ""
    return first[:limit]


def fetch_creator(client: GraphClient, username: str, posts: int = 25) -> dict[str, Any]:
    _require_facebook_login(client)
    username = clean_username(username)
    fields = (f"business_discovery.username({username})"
              f"{{username,name,biography,followers_count,media_count,"
              f"media.limit({posts}){{{POST_FIELDS}}}}}")
    data = client.get(client.config.ig_user_id, {"fields": fields})
    bd = data.get("business_discovery") or {}
    bd["media"] = (bd.get("media") or {}).get("data", [])
    return bd


def summarize_creator(bd: dict[str, Any], top: int = 5) -> dict[str, Any]:
    posts = bd.get("media") or []
    reels = [p for p in posts if _is_reel(p)]
    followers = bd.get("followers_count") or 0
    pool = reels or posts
    med = median([_interactions(p) for p in pool]) if pool else 0
    ranked = sorted(pool, key=_interactions, reverse=True)[:top]
    return {
        "username": bd.get("username"),
        "followers": followers,
        "bio": (bd.get("biography") or "")[:150],
        "posts_checked": len(posts),
        "reels_share_pct": round(100 * len(reels) / len(posts)) if posts else 0,
        "median_interactions_per_reel": med,
        "median_interactions_pct_of_followers":
            round(100 * med / followers, 2) if followers else None,
        "top_reels": [{
            "posted": p.get("timestamp"),
            "likes": p.get("like_count"),
            "comments": p.get("comments_count"),
            "x_vs_their_median": round(_interactions(p) / med, 1) if med else None,
            "hook": hook(p.get("caption")),
            "permalink": p.get("permalink"),
        } for p in ranked],
        "hashtags": [t for t, _ in Counter(
            t.lower() for p in posts for t in _HASHTAG_RE.findall(p.get("caption") or "")
        ).most_common(8)],
    }


def fetch_hashtag_top(client: GraphClient, tag: str, limit: int = 50) -> list[dict[str, Any]]:
    _require_facebook_login(client)
    tag = tag.strip().lstrip("#")
    found = client.get("ig_hashtag_search", {"user_id": client.config.ig_user_id, "q": tag})
    ids = [d["id"] for d in found.get("data", []) if d.get("id")]
    if not ids:
        return []
    return list(client.paginate(f"{ids[0]}/top_media", {
        "user_id": client.config.ig_user_id, "fields": HASHTAG_POST_FIELDS, "limit": limit,
    }, max_items=limit))


def summarize_hashtag(tag: str, posts: list[dict[str, Any]], top: int = 8) -> dict[str, Any]:
    videos = [p for p in posts if _is_reel(p)]
    ranked = sorted(videos, key=_interactions, reverse=True)[:top]
    return {
        "hashtag": tag.strip().lstrip("#"),
        "top_posts_checked": len(posts),
        "videos": len(videos),
        "top_reels": [{
            "posted": p.get("timestamp"), "likes": p.get("like_count"),
            "comments": p.get("comments_count"), "hook": hook(p.get("caption")),
            "permalink": p.get("permalink"),
        } for p in ranked],
        "related_hashtags": [t for t, _ in Counter(
            t.lower() for p in posts for t in _HASHTAG_RE.findall(p.get("caption") or "")
            if t.lower() != tag.strip().lstrip("#").lower()
        ).most_common(10)],
    }


def niche_report(client: GraphClient, creators: list[str], hashtags: list[str],
                 posts_per_creator: int = 25) -> dict[str, Any]:
    """Summaries for each creator and hashtag; one failing doesn't stop the rest."""
    out: dict[str, Any] = {"creators": [], "hashtags": [], "errors": []}
    for name in creators:
        try:
            out["creators"].append(summarize_creator(fetch_creator(client, name, posts_per_creator)))
        except GraphAPIError as e:
            # Personal accounts and typos both come back as "not found".
            out["errors"].append(f"@{clean_username(name)}: {e} (only public Business/Creator "
                                 "accounts can be looked up)")
    for tag in hashtags:
        try:
            out["hashtags"].append(summarize_hashtag(tag, fetch_hashtag_top(client, tag)))
        except GraphAPIError as e:
            out["errors"].append(f"#{tag.lstrip('#')}: {e}")
    return out


def split_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").split(",")
    return [s.strip() for s in items if str(s).strip()]
