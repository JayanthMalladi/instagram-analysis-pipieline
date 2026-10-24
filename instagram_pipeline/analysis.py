"""Turn per-reel insights into day-of-week / hour-of-day posting recommendations.

Method (all pure Python, no numpy needed):

1. Score each reel against the account's own baseline, so the analysis is
   about *timing* rather than about which reels happened to be good:
     - reach index       = log(reach / weighted median reach)
     - engagement index  = log(engagement rate / weighted median rate), where the
       engagement rate is interactions / reach, smoothed towards the account's
       typical rate so a reel with tiny reach can't post a fake 40% rate.
   The default objective is the average of both. Scores are clipped to +/-1.5
   (about 4.5x) so a single viral reel can't decide the answer by itself.
2. Weight each reel by recency: weight = 0.5 ** (age_days / half_life_days),
   rescaled so the weights sum to the effective sample size. Reels younger than `min_age_hours` are skipped because their reach is still
   growing.
3. Estimate effects in the chosen local timezone, with empirical-Bayes style
   shrinkage (`prior_strength` pseudo-reels) so thinly sampled slots are pulled
   back toward the average instead of looking amazing off one post:
     - hour effect: Gaussian-kernel smoothing over neighbouring hours (circular)
     - day effect: per weekday
     - day x hour cell: kernel-smoothed within the weekday, shrunk toward
       day effect + hour effect.
4. Slots with enough evidence are "recommended"; strong slots with little data
   are listed as "worth testing".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from .fetch import Reel

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SCORE_CLIP = 1.5


@dataclass
class AnalysisConfig:
    timezone: str = "UTC"
    objective: str = "balanced"         # balanced | reach | engagement
    half_life_days: float = 30.0
    min_age_hours: float = 48.0
    prior_strength: float = 2.0         # pseudo-reels pulling estimates to average
    hour_bandwidth: float = 1.5         # hours, Gaussian kernel sigma
    min_support: float = 1.5            # effective reels needed to "recommend" a slot
    top_n: int = 5
    rate_prior_reach: float = 200.0     # pseudo-reach used to smooth engagement rates


@dataclass
class ScoredReel:
    reel: Reel
    local_time: datetime
    age_days: float
    weight: float
    reach: float
    interactions: float
    engagement_rate: float
    reach_index: float
    engagement_index: float
    score: float


@dataclass
class Slot:
    day: int
    hour: int
    score: float          # log-lift vs the account baseline
    support: float        # effective number of reels backing this estimate
    stderr: float

    @property
    def lift_pct(self) -> float:
        return (math.exp(self.score) - 1.0) * 100.0

    @property
    def confidence(self) -> str:
        if self.support >= 3:
            return "high"
        if self.support >= 1.5:
            return "medium"
        return "low"

    def label(self) -> str:
        return f"{DAYS[self.day]} {format_hour(self.hour)}"


@dataclass
class AnalysisResult:
    config: AnalysisConfig
    scored: list[ScoredReel]
    skipped_too_new: int
    skipped_no_data: int
    baseline_reach: float
    baseline_rate: float
    hour_effects: list[Slot]
    day_effects: list[Slot]
    cells: list[Slot]
    recommended: list[Slot]
    worth_testing: list[Slot]
    warnings: list[str] = field(default_factory=list)


def format_hour(h: int) -> str:
    suffix = "AM" if h < 12 else "PM"
    hh = h % 12 or 12
    return f"{hh}:00 {suffix}"


def weighted_median(values: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def _interactions(reel: Reel) -> float | None:
    total = reel.metric("total_interactions")
    if total is not None:
        return total
    parts = [reel.metric(m) for m in ("likes", "comments", "shares", "saved")]
    if all(p is None for p in parts):
        return None
    return sum(p or 0.0 for p in parts)


def _circ_dist(a: float, b: float) -> float:
    d = abs(a - b) % 24
    return min(d, 24 - d)


def _kernel(a: float, b: float, bw: float) -> float:
    d = _circ_dist(a, b)
    return math.exp(-0.5 * (d / bw) ** 2)


def score_reels(reels: Iterable[Reel], cfg: AnalysisConfig, now: datetime | None = None
                ) -> tuple[list[ScoredReel], int, int, float, float]:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(cfg.timezone)
    rows = []
    too_new = no_data = 0
    for reel in reels:
        posted = reel.posted_at
        age_h = (now - posted).total_seconds() / 3600
        if age_h < cfg.min_age_hours:
            too_new += 1
            continue
        reach = reel.metric("reach")
        inter = _interactions(reel)
        if not reach or reach <= 0 or inter is None:
            no_data += 1
            continue
        w = 0.5 ** ((age_h / 24) / cfg.half_life_days)
        rows.append((reel, posted.astimezone(tz), age_h / 24, w, reach, inter))

    if not rows:
        return [], too_new, no_data, 0.0, 0.0

    # Rescale weights so they sum to the Kish effective sample size. Relative
    # recency emphasis is unchanged, but "support" then reads as a reel count.
    raw = [r[3] for r in rows]
    scale = sum(raw) / sum(w * w for w in raw)
    rows = [(*r[:3], r[3] * scale, *r[4:]) for r in rows]
    weights = [r[3] for r in rows]
    base_reach = weighted_median([r[4] for r in rows], weights)
    # Pooled rate is a stable prior for smoothing individual rates.
    pooled_rate = sum(r[5] * r[3] for r in rows) / sum(r[4] * r[3] for r in rows)
    m = cfg.rate_prior_reach
    rates = [(inter + pooled_rate * m) / (reach + m) for *_, reach, inter in rows]
    base_rate = weighted_median(rates, weights)

    def clip(x: float) -> float:
        return max(-SCORE_CLIP, min(SCORE_CLIP, x))

    scored = []
    for (reel, local, age_d, w, reach, inter), rate in zip(rows, rates):
        ri = clip(math.log(reach / base_reach))
        ei = clip(math.log(rate / base_rate)) if base_rate > 0 else 0.0
        if cfg.objective == "reach":
            s = ri
        elif cfg.objective == "engagement":
            s = ei
        else:
            s = 0.5 * (ri + ei)
        scored.append(ScoredReel(reel, local, age_d, w, reach, inter, inter / reach, ri, ei, s))
    return scored, too_new, no_data, base_reach, base_rate


def analyze(reels: Iterable[Reel], cfg: AnalysisConfig | None = None,
            now: datetime | None = None) -> AnalysisResult:
    cfg = cfg or AnalysisConfig()
    if cfg.objective not in ("balanced", "reach", "engagement"):
        raise ValueError(f"Unknown objective: {cfg.objective}")
    scored, too_new, no_data, base_reach, base_rate = score_reels(reels, cfg, now)
    warnings: list[str] = []
    k, bw = cfg.prior_strength, cfg.hour_bandwidth

    if not scored:
        warnings.append("No reels with usable insights in the analysis window.")
        return AnalysisResult(cfg, [], too_new, no_data, base_reach, base_rate,
                              [], [], [], [], [], warnings)

    xs = [(s.local_time.weekday(), s.local_time.hour + s.local_time.minute / 60, s.weight, s.score)
          for s in scored]
    wsum = sum(w for *_, w, _ in xs)
    mean = sum(w * v for *_, w, v in xs) / wsum
    var = sum(w * (v - mean) ** 2 for *_, w, v in xs) / wsum
    sigma = math.sqrt(var) if var > 0 else 0.5

    def stderr(support: float) -> float:
        return sigma / math.sqrt(support + k)

    # Hour effects (all days pooled), smoothed across neighbouring hours.
    hour_effects = []
    for h in range(24):
        mass = num = 0.0
        for _, hr, w, v in xs:
            kw = w * _kernel(h, hr, bw)
            mass += kw
            num += kw * v
        hour_effects.append(Slot(-1, h, num / (mass + k), mass, stderr(mass)))

    # Day-of-week effects.
    day_effects = []
    for d in range(7):
        mass = sum(w for dd, _, w, _ in xs if dd == d)
        num = sum(w * v for dd, _, w, v in xs if dd == d)
        day_effects.append(Slot(d, -1, num / (mass + k), mass, stderr(mass)))

    # Day x hour cells, shrunk toward day + hour effects.
    cells = []
    for d in range(7):
        same_day = [(hr, w, v) for dd, hr, w, v in xs if dd == d]
        for h in range(24):
            prior = day_effects[d].score + hour_effects[h].score
            mass = num = 0.0
            for hr, w, v in same_day:
                kw = w * _kernel(h, hr, bw)
                mass += kw
                num += kw * v
            est = (num + k * prior) / (mass + k)
            cells.append(Slot(d, h, est, mass, stderr(mass)))

    recommended = _pick_distinct(
        [c for c in cells if c.support >= cfg.min_support and c.score - 0.5 * c.stderr > 0],
        cfg.top_n)
    worth_testing = _pick_distinct(
        [c for c in cells if cfg.min_support * 0.5 <= c.support < cfg.min_support and c.score > 0.1
         and not any(c.day == r.day and _circ_dist(c.hour, r.hour) <= 2 for r in recommended)],
        3)

    n_eff = wsum  # weights were rescaled to sum to the effective sample size
    if len(scored) < 10:
        warnings.append(f"Only {len(scored)} reels analysed; treat results as directional "
                        "and keep experimenting with new slots.")
    elif n_eff < 8:
        warnings.append(f"Recency weighting leaves ~{n_eff:.1f} effective reels; consider a "
                        "longer --half-life or --lookback-days for steadier estimates.")
    if not recommended:
        warnings.append("No slot has enough evidence to beat your average yet; see "
                        "'worth testing' and the hour/day trends instead.")

    return AnalysisResult(cfg, scored, too_new, no_data, base_reach, base_rate,
                          hour_effects, day_effects, cells, recommended, worth_testing, warnings)


def _pick_distinct(candidates: list[Slot], n: int) -> list[Slot]:
    """Best-first, skipping slots within 2 hours of an already chosen slot on the same day."""
    chosen: list[Slot] = []
    for c in sorted(candidates, key=lambda s: s.score - 0.5 * s.stderr, reverse=True):
        if any(c.day == p.day and _circ_dist(c.hour, p.hour) <= 2 for p in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= n:
            break
    return chosen
