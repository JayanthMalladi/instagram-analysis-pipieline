"""Render analysis results as a console report, JSON, and CSV."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .analysis import DAYS, AnalysisResult, Slot, format_hour

META_ONLINE_FOLLOWERS_TZ = "America/Los_Angeles"


def shift_online_followers(online: dict[int, float], target_tz: str,
                           now: datetime | None = None) -> dict[int, float]:
    """Re-key Meta's online_followers (Pacific time hours) to the target timezone."""
    now = now or datetime.now(timezone.utc)
    src = now.astimezone(ZoneInfo(META_ONLINE_FOLLOWERS_TZ)).utcoffset()
    dst = now.astimezone(ZoneInfo(target_tz)).utcoffset()
    shift = (dst - src).total_seconds() / 3600
    out: dict[int, float] = {}
    for h, v in online.items():
        out[int((h + shift) % 24)] = v  # half-hour zones round down to the hour
    return dict(sorted(out.items()))


def _bar(score: float, width: int = 12) -> str:
    n = min(width, int(round(abs(score) / 0.5 * width / 2)))
    return ("+" if score >= 0 else "-") * n


def _slot_line(s: Slot) -> str:
    return (f"  {s.label():<20} {s.lift_pct:+6.0f}% vs your average   "
            f"(evidence: {s.support:.1f} reels, {s.confidence} confidence)")


def render_text(result: AnalysisResult, account: dict[str, Any] | None = None,
                online: dict[int, float] | None = None) -> str:
    cfg = result.config
    lines: list[str] = []
    name = f"@{account['username']}" if account and account.get("username") else "your account"
    lines.append(f"Best times to post Reels for {name}")
    lines.append("=" * len(lines[0]))
    lines.append(f"Timezone: {cfg.timezone}   Objective: {cfg.objective}   "
                 f"Recency half-life: {cfg.half_life_days:g} days")
    followers = account.get("followers_count") if account else None
    skipped = []
    if result.skipped_too_new:
        skipped.append(f"{result.skipped_too_new} too new")
    if result.skipped_no_data:
        skipped.append(f"{result.skipped_no_data} without insights")
    lines.append(f"Reels analysed: {len(result.scored)}"
                 + (f" (skipped {', '.join(skipped)})" if skipped else "")
                 + (f"   Followers: {followers:,}" if followers else ""))
    if result.scored:
        line = (f"Baseline: median reach {result.baseline_reach:,.0f}, "
                f"engagement rate {result.baseline_rate * 100:.1f}% of reach")
        if followers:
            line += f", reach = {result.baseline_reach / followers * 100:.0f}% of followers"
        lines.append(line)
    lines.append("")

    if result.recommended:
        lines.append("RECOMMENDED SLOTS")
        lines.extend(_slot_line(s) for s in result.recommended)
        lines.append("")
    if result.worth_testing:
        lines.append("WORTH TESTING (promising, but little data yet)")
        lines.extend(_slot_line(s) for s in result.worth_testing)
        lines.append("")

    if result.day_effects:
        lines.append("BY DAY OF WEEK")
        for s in sorted(result.day_effects, key=lambda s: s.score, reverse=True):
            lines.append(f"  {DAYS[s.day]:<10} {s.lift_pct:+6.0f}%  {_bar(s.score):<12}  "
                         f"({s.support:.1f} reels)")
        lines.append("")
        lines.append("BY HOUR OF DAY")
        top_online = None
        if online:
            top_online = max(online.values()) or None
        for s in result.hour_effects:
            extra = ""
            if online and top_online and s.hour in online:
                extra = f"   followers online: {online[s.hour] / top_online * 100:3.0f}%"
            lines.append(f"  {format_hour(s.hour):>8} {s.lift_pct:+6.0f}%  {_bar(s.score):<12}  "
                         f"({s.support:.1f} reels){extra}")
        lines.append("")

    if online:
        best = sorted(online.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines.append("Your followers are most active around: "
                     + ", ".join(format_hour(h) for h, _ in sorted(best)) + f" ({cfg.timezone})")
        lines.append("")

    for w in result.warnings:
        lines.append(f"Note: {w}")
    if result.scored:
        lines.append("Lift is relative to your own typical reel; 'evidence' is the recency-weighted "
                     "number of reels near that slot.")
    return "\n".join(lines).rstrip() + "\n"


def _slot_dict(s: Slot) -> dict[str, Any]:
    return {"day": DAYS[s.day] if s.day >= 0 else None,
            "hour": s.hour if s.hour >= 0 else None,
            "label": s.label() if s.day >= 0 and s.hour >= 0 else None,
            "lift_pct": round(s.lift_pct, 1), "score": round(s.score, 4),
            "support": round(s.support, 2), "stderr": round(s.stderr, 4),
            "confidence": s.confidence}


def to_json(result: AnalysisResult, account: dict[str, Any] | None = None,
            online: dict[int, float] | None = None) -> dict[str, Any]:
    cfg = result.config
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account": {k: account.get(k) for k in ("id", "username", "followers_count")} if account else None,
        "config": vars(cfg),
        "reels_analysed": len(result.scored),
        "skipped_too_new": result.skipped_too_new,
        "skipped_no_data": result.skipped_no_data,
        "baseline": {"median_reach": result.baseline_reach, "engagement_rate": result.baseline_rate},
        "recommended": [_slot_dict(s) for s in result.recommended],
        "worth_testing": [_slot_dict(s) for s in result.worth_testing],
        "by_day": [_slot_dict(s) for s in result.day_effects],
        "by_hour": [_slot_dict(s) for s in result.hour_effects],
        "online_followers_by_hour": online,
        "warnings": result.warnings,
    }


def write_reels_csv(result: AnalysisResult, path: Path) -> None:
    cols = ["id", "posted_local", "weekday", "hour", "age_days", "weight", "reach", "views",
            "likes", "comments", "shares", "saved", "interactions", "engagement_rate",
            "reach_index", "engagement_index", "score", "permalink"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for s in sorted(result.scored, key=lambda s: s.local_time, reverse=True):
            r = s.reel
            w.writerow([r.id, s.local_time.isoformat(), DAYS[s.local_time.weekday()],
                        s.local_time.hour, f"{s.age_days:.1f}", f"{s.weight:.3f}",
                        int(s.reach), _int(r.metric("views")), _int(r.metric("likes")),
                        _int(r.metric("comments")), _int(r.metric("shares")),
                        _int(r.metric("saved")), int(s.interactions),
                        f"{s.engagement_rate:.4f}", f"{s.reach_index:.3f}",
                        f"{s.engagement_index:.3f}", f"{s.score:.3f}", r.permalink or ""])


def _int(v: float | None) -> str:
    return "" if v is None else str(int(v))


def write_outputs(result: AnalysisResult, out_dir: Path, account: dict[str, Any] | None,
                  online: dict[int, float] | None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"report": out_dir / "report.txt", "json": out_dir / "recommendations.json",
             "csv": out_dir / "reels_scored.csv"}
    paths["report"].write_text(render_text(result, account, online), encoding="utf-8")
    paths["json"].write_text(json.dumps(to_json(result, account, online), indent=2), encoding="utf-8")
    write_reels_csv(result, paths["csv"])
    return paths
