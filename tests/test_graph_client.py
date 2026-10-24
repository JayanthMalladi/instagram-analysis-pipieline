import json
import unittest
import urllib.parse

from instagram_pipeline.config import Config
from instagram_pipeline.fetch import fetch_reel_insights, fetch_reels, parse_ig_timestamp
from instagram_pipeline.graph_client import GraphAPIError, GraphClient, redact

TOKEN = "SECRET_TOKEN_123"
CFG = Config(access_token=TOKEN, ig_user_id="1784")


class FakeTransport:
    """Routes requests to handler functions by path; records every URL."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.split("/", 2)[2]  # strip /vXX.X/
        query = dict(urllib.parse.parse_qsl(parsed.query))
        status, body = self.routes[path](query)
        return status, json.dumps(body)


def error(code, msg="bad"):
    return 400, {"error": {"message": msg, "code": code}}


class GraphClientTests(unittest.TestCase):
    def test_token_sent_and_redacted_in_errors(self):
        t = FakeTransport({"x": lambda q: error(190, f"bad token access_token={TOKEN}")})
        client = GraphClient(CFG, transport=t, sleep=lambda s: None)
        with self.assertRaises(GraphAPIError) as cm:
            client.get("x")
        self.assertIn(f"access_token={TOKEN}", t.urls[0])
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertNotIn(TOKEN, redact(t.urls[0]))

    def test_retries_rate_limit_then_succeeds(self):
        calls = {"n": 0}

        def handler(q):
            calls["n"] += 1
            return error(4, "rate limited") if calls["n"] < 3 else (200, {"ok": True})

        sleeps = []
        client = GraphClient(CFG, transport=FakeTransport({"x": handler}), sleep=sleeps.append)
        self.assertEqual(client.get("x"), {"ok": True})
        self.assertEqual(len(sleeps), 2)

    def test_appsecret_proof(self):
        t = FakeTransport({"x": lambda q: (200, {"q": q})})
        cfg = Config(access_token=TOKEN, ig_user_id="1", app_secret="s3cret")
        q = GraphClient(cfg, transport=t).get("x")["q"]
        self.assertEqual(len(q["appsecret_proof"]), 64)

    def test_pagination_and_reel_filtering(self):
        page2_url = "https://graph.facebook.com/v23.0/1784/media?after=abc&access_token=" + TOKEN

        def media(q):
            if q.get("after") == "abc":
                return 200, {"data": [
                    {"id": "3", "media_product_type": "REELS", "timestamp": "2026-10-10T10:00:00+0000"},
                    {"id": "4", "media_product_type": "REELS", "timestamp": "2025-01-01T10:00:00+0000"},
                ]}
            return 200, {"data": [
                {"id": "1", "media_product_type": "REELS", "timestamp": "2026-10-20T10:00:00+0000",
                 "like_count": 5},
                {"id": "2", "media_product_type": "FEED", "timestamp": "2026-10-15T10:00:00+0000"},
            ], "paging": {"next": page2_url}}

        def insights(q):
            return 200, {"data": [{"name": m, "values": [{"value": 10}]}
                                  for m in q["metric"].split(",")]}

        def media_no_expansion(q):
            if "insights" in q.get("fields", ""):
                return error(100, "metric not supported for FEED")
            return media(q)

        t = FakeTransport({"1784/media": media_no_expansion,
                           "1/insights": insights, "3/insights": insights})
        client = GraphClient(CFG, transport=t)
        reels = fetch_reels(client, lookback_days=180,
                            now=parse_ig_timestamp("2026-10-24T00:00:00+0000"))
        self.assertEqual([r.id for r in reels], ["1", "3"])  # FEED skipped, old reel cut off
        self.assertEqual(reels[0].insights["reach"], 10)
        self.assertEqual(reels[0].metric("likes"), 10)

    def test_inline_insights_avoid_per_reel_calls(self):
        def media(q):
            self.assertIn("insights.metric(", q["fields"])
            return 200, {"data": [{
                "id": "1", "media_product_type": "REELS", "timestamp": "2026-10-20T10:00:00+0000",
                "insights": {"data": [{"name": "reach", "values": [{"value": 321}]}]}}]}

        t = FakeTransport({"1784/media": media})
        reels = fetch_reels(GraphClient(CFG, transport=t),
                            now=parse_ig_timestamp("2026-10-24T00:00:00+0000"))
        self.assertEqual(reels[0].insights, {"reach": 321.0})
        self.assertEqual(len(t.urls), 1)

    def test_insights_fall_back_per_metric(self):
        def insights(q):
            if "," in q["metric"] or q["metric"] in ("views", "ig_reels_avg_watch_time"):
                return error(100, "metric not supported")
            return 200, {"data": [{"name": q["metric"], "values": [{"value": 7}]}]}

        client = GraphClient(CFG, transport=FakeTransport({"9/insights": insights}))
        got = fetch_reel_insights(client, "9")
        self.assertEqual(got["plays"], 7)  # fallback for views
        self.assertNotIn("ig_reels_avg_watch_time", got)
        self.assertEqual(got["reach"], 7)


if __name__ == "__main__":
    unittest.main()
