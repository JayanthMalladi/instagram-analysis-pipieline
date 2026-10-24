import random
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from instagram_pipeline.analysis import AnalysisConfig, analyze
from instagram_pipeline.fetch import Reel
from instagram_pipeline.report import render_text, shift_online_followers, to_json

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
IST = ZoneInfo("Asia/Kolkata")


def make_reel(i, local_dt, reach, interactions):
    ts = local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    return Reel(id=str(i), timestamp=ts, insights={"reach": reach, "total_interactions": interactions})


def synthetic(n=90, seed=1):
    """Reels posted at random times; Wednesday/Saturday ~7-9 PM IST do ~3x better."""
    rng = random.Random(seed)
    reels = []
    for i in range(n):
        day = rng.randrange(3, 90)
        hour = rng.choice([8, 9, 12, 13, 15, 18, 19, 20, 21, 22])
        dt = (NOW.astimezone(IST) - timedelta(days=day)).replace(hour=hour, minute=rng.randrange(60))
        good = dt.weekday() in (2, 5) and 19 <= hour <= 20
        reach = rng.lognormvariate(7, 0.3) * (3 if good else 1)
        rate = rng.uniform(0.04, 0.06) * (1.5 if good else 1)
        reels.append(make_reel(i, dt, reach, reach * rate))
    return reels


class AnalysisTests(unittest.TestCase):
    def test_finds_planted_best_slots(self):
        res = analyze(synthetic(), AnalysisConfig(timezone="Asia/Kolkata"), now=NOW)
        self.assertTrue(res.recommended)
        top = res.recommended[0]
        self.assertIn(top.day, (2, 5))
        self.assertIn(top.hour, range(18, 22))
        self.assertGreater(top.lift_pct, 30)

    def test_timezone_shifts_hours(self):
        reels = synthetic()
        dubai = analyze(reels, AnalysisConfig(timezone="Asia/Dubai"), now=NOW).recommended[0]
        utc = analyze(reels, AnalysisConfig(timezone="UTC"), now=NOW).recommended[0]
        self.assertEqual(dubai.day, utc.day)
        self.assertEqual((dubai.hour - utc.hour) % 24, 4)  # Dubai is UTC+4

    def test_young_reels_skipped(self):
        fresh = make_reel(0, NOW - timedelta(hours=5), 10, 1)
        res = analyze(synthetic(20) + [fresh], AnalysisConfig(), now=NOW)
        self.assertEqual(res.skipped_too_new, 1)

    def test_single_viral_reel_is_shrunk(self):
        rng = random.Random(3)
        reels = [make_reel(i, NOW - timedelta(days=5 + i, hours=rng.randrange(24)), 1000, 50)
                 for i in range(20)]
        reels.append(make_reel(99, datetime(2026, 10, 14, 3, tzinfo=timezone.utc), 500000, 50000))
        res = analyze(reels, AnalysisConfig(), now=NOW)
        cell = next(c for c in res.cells if c.day == 2 and c.hour == 3)
        self.assertLess(cell.lift_pct, 200)  # raw lift would be ~50000%
        self.assertNotIn((2, 3), [(s.day, s.hour) for s in res.recommended])

    def test_small_sample_warning_and_reports_render(self):
        res = analyze(synthetic(6), AnalysisConfig(timezone="Asia/Kolkata"), now=NOW)
        self.assertTrue(any("directional" in w for w in res.warnings))
        text = render_text(res, {"username": "me", "followers_count": 1200}, {20: 50.0, 21: 40.0})
        self.assertIn("@me", text)
        to_json(res)

    def test_empty(self):
        res = analyze([], AnalysisConfig(), now=NOW)
        self.assertEqual(res.recommended, [])
        render_text(res)

    def test_online_followers_shift(self):
        shifted = shift_online_followers({0: 1.0}, "UTC", now=NOW)  # PDT = UTC-7 in October
        self.assertEqual(shifted, {7: 1.0})


if __name__ == "__main__":
    unittest.main()
