"""A local MCP (Model Context Protocol) server so Claude Desktop can use this pipeline.

Claude Desktop starts this process on your computer and talks to it over
stdin/stdout with newline-delimited JSON-RPC 2.0. It's written against the
MCP spec directly with the standard library (no SDK). Your Instagram token
stays on your machine: Claude only sees the tool results (reel metrics and
recommendations), never the token.

Run it by hand to check it starts:  python -m instagram_pipeline mcp
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from .analysis import DAYS, AnalysisConfig
from .config import ConfigError
from .graph_client import GraphAPIError
from .pipeline import analyze_payload, fetch_payload
from .report import render_text, to_json, write_outputs

log = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = (
    "Tools for analysing the user's own Instagram Reels via Meta's Graph API. "
    "Call best_posting_times to answer 'when should I post'; it reuses cached data and "
    "fetches automatically if none exists. Use refresh=true or fetch_reels only when the "
    "user wants fresh numbers. Lift percentages are relative to the user's own average "
    "reel. Mention the confidence/evidence when recommending a slot."
)


def data_dir() -> Path:
    return Path(os.environ.get("IG_DATA_DIR") or Path.home() / ".instagram_pipeline")


def data_path() -> Path:
    return data_dir() / "reels.json"


def _load_cached() -> dict[str, Any] | None:
    p = data_path()
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _fetch_and_save(lookback_days: int) -> dict[str, Any]:
    payload = fetch_payload(lookback_days=lookback_days)
    p = data_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _payload(refresh: bool, lookback_days: int) -> dict[str, Any]:
    cached = None if refresh else _load_cached()
    return cached if cached is not None else _fetch_and_save(lookback_days)


def _analysis_config(args: dict[str, Any]) -> AnalysisConfig:
    return AnalysisConfig(
        timezone=args.get("timezone") or os.environ.get("IG_TIMEZONE") or "UTC",
        objective=args.get("objective") or "balanced",
        half_life_days=float(args.get("half_life_days") or 30),
        min_age_hours=float(args.get("min_age_hours") if args.get("min_age_hours") is not None else 48),
        top_n=int(args.get("top") or 5),
    )


# --- tools ------------------------------------------------------------------

_ANALYSIS_PROPS = {
    "timezone": {"type": "string",
                 "description": "IANA timezone for the answer, e.g. Asia/Kolkata. "
                                "Defaults to IG_TIMEZONE, else UTC."},
    "objective": {"type": "string", "enum": ["balanced", "reach", "engagement"],
                  "description": "reach = get seen, engagement = interactions per view, "
                                 "balanced = both (default)."},
    "half_life_days": {"type": "number",
                       "description": "Recency weighting: a reel this many days old counts half. "
                                      "Default 30."},
    "min_age_hours": {"type": "number",
                      "description": "Ignore reels younger than this (still growing). Default 48."},
    "refresh": {"type": "boolean",
                "description": "Re-fetch from Instagram instead of using cached data."},
    "lookback_days": {"type": "integer", "description": "Fetch window if fetching. Default 180."},
}


def tool_best_posting_times(args: dict[str, Any]) -> str:
    payload = _payload(bool(args.get("refresh")), int(args.get("lookback_days") or 180))
    cfg = _analysis_config(args)
    result, account, online = analyze_payload(payload, cfg)
    write_outputs(result, data_dir() / "output", account, online)
    summary = to_json(result, account, online)
    for key in ("by_hour", "online_followers_by_hour"):  # already in the text report
        summary.pop(key, None)
    return (render_text(result, account, online)
            + f"\nData fetched at {payload.get('fetched_at')}.\n\nJSON:\n"
            + json.dumps(summary, indent=1))


def tool_fetch_reels(args: dict[str, Any]) -> str:
    payload = _fetch_and_save(int(args.get("lookback_days") or 180))
    acct = payload.get("account") or {}
    return (f"Fetched {len(payload['reels'])} reels for @{acct.get('username')} "
            f"({acct.get('followers_count')} followers), saved to {data_path()}.")


def tool_list_reels(args: dict[str, Any]) -> str:
    payload = _payload(bool(args.get("refresh")), int(args.get("lookback_days") or 180))
    cfg = _analysis_config({**args, "min_age_hours": args.get("min_age_hours", 0)})
    result, _, _ = analyze_payload(payload, cfg)
    sort_by = args.get("sort_by") or "recent"
    keys: dict[str, Callable] = {
        "recent": lambda s: s.local_time, "score": lambda s: s.score,
        "reach": lambda s: s.reach, "engagement_rate": lambda s: s.engagement_rate,
    }
    rows = sorted(result.scored, key=keys.get(sort_by, keys["recent"]), reverse=True)
    rows = rows[: int(args.get("limit") or 25)]
    out = [{
        "posted": s.local_time.strftime("%Y-%m-%d %H:%M"), "weekday": DAYS[s.local_time.weekday()],
        "reach": int(s.reach), "views": s.reel.metric("views"),
        "interactions": int(s.interactions), "shares": s.reel.metric("shares"),
        "saves": s.reel.metric("saved"), "engagement_rate_pct": round(s.engagement_rate * 100, 2),
        "score_vs_average": round(s.score, 3),
        "caption": (s.reel.caption or "")[:80], "permalink": s.reel.permalink,
    } for s in rows]
    return json.dumps({"timezone": cfg.timezone, "count": len(out), "reels": out}, indent=1)


TOOLS: dict[str, tuple[dict[str, Any], Callable[[dict[str, Any]], str]]] = {
    "best_posting_times": ({
        "name": "best_posting_times",
        "title": "Best times to post Reels",
        "description": "Recommend the best day-of-week and hour slots to post Instagram Reels, "
                       "based on the user's own recent Reels insights (reach and engagement "
                       "rate vs their own average, recency-weighted, shrunk for small samples).",
        "inputSchema": {"type": "object", "properties": _ANALYSIS_PROPS},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }, tool_best_posting_times),
    "list_reels": ({
        "name": "list_reels",
        "title": "List Reels with metrics",
        "description": "List the user's recent Reels with post time (local), reach, views, "
                       "interactions, shares, saves, engagement rate, caption and link.",
        "inputSchema": {"type": "object", "properties": {
            "sort_by": {"type": "string", "enum": ["recent", "score", "reach", "engagement_rate"]},
            "limit": {"type": "integer", "description": "Max rows, default 25."},
            "timezone": _ANALYSIS_PROPS["timezone"],
            "refresh": _ANALYSIS_PROPS["refresh"],
        }},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }, tool_list_reels),
    "fetch_reels": ({
        "name": "fetch_reels",
        "title": "Refresh Reels data",
        "description": "Re-download the user's Reels and insights from Instagram and cache them "
                       "locally. Other tools use the cache, so call this for fresh numbers.",
        "inputSchema": {"type": "object", "properties": {
            "lookback_days": _ANALYSIS_PROPS["lookback_days"]}},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }, tool_fetch_reels),
}


# --- JSON-RPC plumbing --------------------------------------------------------

class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; returns the response, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_request = "id" in msg and method is not None
    if not is_request:
        return None  # notifications (initialized, cancelled, ...) and stray responses

    try:
        result = _dispatch(method, msg.get("params") or {})
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    except RpcError as e:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": e.code, "message": str(e)}}


def _dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS \
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "instagram-pipeline", "title": "Instagram Reels insights",
                           "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [spec for spec, _ in TOOLS.values()]}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            raise RpcError(-32602, f"Unknown tool: {name}")
        try:
            text = TOOLS[name][1](params.get("arguments") or {})
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except (ConfigError, GraphAPIError, ValueError) as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
        except Exception as e:  # keep the server alive; details go to stderr
            log.error("Tool %s failed:\n%s", name, traceback.format_exc())
            return {"content": [{"type": "text", "text": f"Unexpected error: {e}"}],
                    "isError": True}
    raise RpcError(-32601, f"Method not found: {method}")


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp: Any = {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "Parse error"}}
        else:
            if isinstance(msg, list):  # JSON-RPC batch (older protocol versions)
                resp = [r for r in (handle(m) for m in msg if isinstance(m, dict)) if r] or None
            elif isinstance(msg, dict):
                resp = handle(msg)
            else:
                resp = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32600, "message": "Invalid request"}}
        if resp:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


def main() -> int:
    # stdout is reserved for protocol messages; all logging goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("instagram-pipeline MCP server %s ready (data: %s)", __version__, data_dir())
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
