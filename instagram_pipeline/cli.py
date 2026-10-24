"""Find your best Instagram Reels posting times from your own Graph API insights.

    python -m instagram_pipeline run      # fetch from the Graph API, then analyse
    python -m instagram_pipeline fetch    # only fetch, save raw data to JSON
    python -m instagram_pipeline analyze  # re-analyse saved data, no API calls

Setup helpers:

    python -m instagram_pipeline find-account     # show the IG account id for your token
    python -m instagram_pipeline exchange-token   # short-lived -> 60-day token
    python -m instagram_pipeline refresh-token    # extend an Instagram Login token

Claude Desktop:

    python -m instagram_pipeline mcp              # local MCP server over stdio
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .analysis import AnalysisConfig
from .config import Config, ConfigError, load_dotenv
from .graph_client import GraphAPIError, GraphClient
from .pipeline import analyze_payload, fetch_payload, validate_timezone
from .report import render_text, write_outputs

log = logging.getLogger("instagram_pipeline")


def _default_tz() -> str:
    return os.environ.get("IG_TIMEZONE") or "UTC"


def _add_fetch_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lookback-days", type=int, default=180,
                   help="Only fetch reels posted in the last N days (default 180)")
    p.add_argument("--max-media", type=int, default=1000,
                   help="Stop after scanning this many media items (default 1000)")
    p.add_argument("--data", type=Path, default=Path("data/reels.json"),
                   help="Where raw fetched data is saved (default data/reels.json)")


def _add_analysis_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--tz", default=None,
                   help="IANA timezone for recommendations, e.g. Asia/Kolkata "
                        "(default: $IG_TIMEZONE, else UTC)")
    p.add_argument("--objective", choices=["balanced", "reach", "engagement"], default="balanced",
                   help="What 'best' means (default balanced: reach and engagement rate equally)")
    p.add_argument("--half-life", type=float, default=30.0,
                   help="Recency half-life in days: a reel this old counts half (default 30)")
    p.add_argument("--min-age-hours", type=float, default=48.0,
                   help="Ignore reels younger than this; their reach is still growing (default 48)")
    p.add_argument("--prior-strength", type=float, default=2.0,
                   help="How strongly thin slots are pulled toward average (default 2)")
    p.add_argument("--top", type=int, default=5, help="Number of recommended slots (default 5)")
    p.add_argument("--out", type=Path, default=Path("output"),
                   help="Directory for report.txt, recommendations.json, reels_scored.csv")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="instagram_pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--env-file", default=".env", help="Optional .env file (default .env)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Fetch reels + insights, then analyse")
    _add_fetch_args(p_run)
    _add_analysis_args(p_run)

    p_fetch = sub.add_parser("fetch", help="Fetch reels + insights and save them")
    _add_fetch_args(p_fetch)

    sub.add_parser("find-account",
                   help="Show the Instagram account id for your token (needs only "
                        "IG_ACCESS_TOKEN)")
    sub.add_parser("exchange-token",
                   help="Exchange a short-lived IG_ACCESS_TOKEN for a ~60 day token "
                        "(needs IG_APP_SECRET, plus IG_APP_ID for Facebook Login)")
    sub.add_parser("refresh-token",
                   help="Extend a long-lived Instagram Login token by another ~60 days")

    sub.add_parser("mcp", help="Run as a local MCP server for Claude Desktop (stdio)")

    p_an = sub.add_parser("analyze", help="Analyse previously fetched data (no API calls)")
    p_an.add_argument("--data", type=Path, default=Path("data/reels.json"))
    _add_analysis_args(p_an)
    return parser


def do_find_account() -> None:
    cfg = Config.from_env(require_user_id=False)
    client = GraphClient(cfg)
    if cfg.instagram_login:
        me = client.get("me", {"fields": "user_id,username,account_type,followers_count"})
        print(f"@{me.get('username')} ({me.get('account_type', '?')}, "
              f"{me.get('followers_count', '?')} followers): IG_USER_ID={me.get('user_id')}")
        print("With an Instagram Login token IG_USER_ID is optional; 'me' is used by default.")
        return
    found = False
    for page in client.paginate("me/accounts", {
            "fields": "name,instagram_business_account{id,username,followers_count}"}):
        ig = page.get("instagram_business_account")
        if ig:
            found = True
            print(f"Page {page.get('name')!r}: IG_USER_ID={ig['id']}  (@{ig.get('username')}, "
                  f"{ig.get('followers_count', '?')} followers)")
        else:
            print(f"Page {page.get('name')!r}: no linked Instagram professional account")
    if not found:
        print("No linked Instagram account found. Check that your Instagram account is a "
              "Business/Creator account linked to one of these Pages, and that the token has "
              "instagram_basic, pages_show_list and pages_read_engagement.")


def _print_token(data: dict) -> None:
    days = int(data.get("expires_in", 0)) // 86400
    print("Long-lived token (put this in IG_ACCESS_TOKEN in your .env; do not share it):")
    print(data["access_token"])
    if days:
        print(f"Expires in about {days} days.")


def do_exchange_token() -> None:
    cfg = Config.from_env(require_user_id=False)
    secret = (cfg.app_secret or "").strip()
    # appsecret_proof is not needed (or wanted) on the token endpoints.
    client = GraphClient(Config(access_token=cfg.access_token, ig_user_id="",
                                host=cfg.host, api_version=cfg.api_version))
    if cfg.instagram_login:
        if not secret:
            raise ConfigError("Set IG_APP_SECRET (the Instagram app secret) to exchange the token")
        data = client.get(f"https://{cfg.host}/access_token", {
            "grant_type": "ig_exchange_token", "client_secret": secret,
            "access_token": cfg.access_token})
    else:
        app_id = os.environ.get("IG_APP_ID", "").strip()
        if not app_id or not secret:
            raise ConfigError("Set IG_APP_ID and IG_APP_SECRET to exchange the token")
        data = client.get("oauth/access_token", {"grant_type": "fb_exchange_token",
                                                 "client_id": app_id, "client_secret": secret,
                                                 "fb_exchange_token": cfg.access_token})
    _print_token(data)


def do_refresh_token() -> None:
    cfg = Config.from_env(require_user_id=False)
    if not cfg.instagram_login:
        raise ConfigError("refresh-token is for Instagram Login tokens. For a Facebook Login "
                          "token, generate a new one and run exchange-token.")
    client = GraphClient(Config(access_token=cfg.access_token, ig_user_id="",
                                host=cfg.host, api_version=cfg.api_version))
    _print_token(client.get(f"https://{cfg.host}/refresh_access_token", {
        "grant_type": "ig_refresh_token", "access_token": cfg.access_token}))


def do_fetch(args: argparse.Namespace) -> dict:
    payload = fetch_payload(args.lookback_days, args.max_media)
    account = payload["account"]
    log.info("Account @%s, %s followers", account.get("username"), account.get("followers_count"))
    args.data.parent.mkdir(parents=True, exist_ok=True)
    args.data.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(payload['reels'])} reels to {args.data}")
    return payload


def do_analyze(args: argparse.Namespace, payload: dict) -> None:
    tz = validate_timezone(args.tz or _default_tz())
    if tz == "UTC" and not args.tz and not os.environ.get("IG_TIMEZONE"):
        print("Note: no timezone given, using UTC. Pass --tz or set IG_TIMEZONE "
              "(e.g. Asia/Kolkata) to get times in your audience's local time.\n")
    cfg = AnalysisConfig(timezone=tz, objective=args.objective, half_life_days=args.half_life,
                         min_age_hours=args.min_age_hours, prior_strength=args.prior_strength,
                         top_n=args.top)
    result, account, online = analyze_payload(payload, cfg)
    print(render_text(result, account, online))
    paths = write_outputs(result, args.out, account, online)
    print("Wrote " + ", ".join(str(p) for p in paths.values()))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    load_dotenv(args.env_file)
    # Claude Desktop may start us from any directory, so also try the project's own .env.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if args.command == "mcp":
        from .mcp_server import serve
        serve()
        return 0
    try:
        if args.command == "find-account":
            do_find_account()
        elif args.command == "exchange-token":
            do_exchange_token()
        elif args.command == "refresh-token":
            do_refresh_token()
        elif args.command == "fetch":
            do_fetch(args)
        elif args.command == "run":
            do_analyze(args, do_fetch(args))
        else:
            if not args.data.is_file():
                raise ConfigError(f"{args.data} not found; run `fetch` first")
            do_analyze(args, json.loads(args.data.read_text(encoding="utf-8")))
    except (ConfigError, GraphAPIError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    return 0
