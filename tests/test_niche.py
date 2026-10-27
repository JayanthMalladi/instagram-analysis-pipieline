import json
import os
import unittest
from unittest import mock

from instagram_pipeline import mcp_server
from instagram_pipeline.config import Config, ConfigError, niche_config
from instagram_pipeline.graph_client import GraphClient
from instagram_pipeline.niche import (clean_username, fetch_creator, fetch_hashtag_top,
                                      niche_report, summarize_creator)

from .test_graph_client import FakeTransport, error
from .test_mcp_server import rpc

CFG = Config(access_token="EAAtoken", ig_user_id="1784")


def post(i, likes, comments=0, kind="REELS", caption=""):
    return {"id": str(i), "like_count": likes, "comments_count": comments,
            "media_type": "VIDEO", "media_product_type": kind,
            "timestamp": "2026-10-20T14:00:00+0000", "caption": caption,
            "permalink": f"https://instagram.com/p/{i}"}


def discovery(q):
    if "nobody" in q["fields"]:
        return error(100, "Invalid user id")
    return 200, {"business_discovery": {
        "username": "lifter", "followers_count": 1000, "biography": "gym",
        "media": {"data": [post(1, 100, caption="glow up 😳\n#gym #fitness"),
                           post(2, 10, caption="leg day #gym"), post(3, 12),
                           post(4, 999, kind="FEED")]}}}


class NicheTests(unittest.TestCase):
    def client(self, routes, cfg=CFG):
        return GraphClient(cfg, transport=FakeTransport(routes), sleep=lambda s: None)

    def test_creator_summary_ranks_reels_against_their_median(self):
        c = self.client({"1784": discovery})
        s = summarize_creator(fetch_creator(c, "@lifter"))
        self.assertEqual(s["posts_checked"], 4)
        self.assertEqual(s["median_interactions_per_reel"], 12)
        top = s["top_reels"][0]
        self.assertEqual((top["likes"], top["hook"]), (100, "glow up 😳"))
        self.assertEqual(top["x_vs_their_median"], 8.3)
        self.assertEqual(s["hashtags"][0], "gym")

    def test_hashtag_top_keeps_videos(self):
        routes = {
            "ig_hashtag_search": lambda q: (200, {"data": [{"id": "h1"}]}),
            "h1/top_media": lambda q: (200, {"data": [
                {"id": "a", "media_type": "VIDEO", "like_count": 5, "caption": "x #gymtok"},
                {"id": "b", "media_type": "IMAGE", "like_count": 50}]}),
        }
        c = self.client(routes)
        posts = fetch_hashtag_top(c, "#gym")
        self.assertEqual(len(posts), 2)
        report = niche_report(c, [], ["gym"])
        self.assertEqual(report["hashtags"][0]["videos"], 1)
        self.assertEqual(report["hashtags"][0]["related_hashtags"], ["gymtok"])

    def test_one_bad_creator_does_not_stop_the_rest(self):
        report = niche_report(self.client({"1784": discovery}), ["nobody", "lifter"], [])
        self.assertEqual(len(report["creators"]), 1)
        self.assertIn("@nobody", report["errors"][0])

    def test_instagram_login_token_explains_what_is_needed(self):
        cfg = Config(access_token="IGAA", ig_user_id="me", host="graph.instagram.com")
        with self.assertRaises(ConfigError) as cm:
            niche_report(self.client({}, cfg), ["lifter"], [])
        self.assertIn("Facebook", str(cm.exception))

    def test_clean_username(self):
        self.assertEqual(clean_username("https://www.instagram.com/some.one/?hl=en"), "some.one")
        self.assertEqual(clean_username(" @lifter "), "lifter")

    def test_separate_niche_token(self):
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "IGAAx",
                                          "IG_NICHE_ACCESS_TOKEN": "EAAy",
                                          "IG_NICHE_USER_ID": "42"}):
            cfg = niche_config()
        self.assertEqual((cfg.access_token, cfg.ig_user_id, cfg.instagram_login),
                         ("EAAy", "42", False))


class NicheMcpTests(unittest.TestCase):
    def test_tool_without_input_is_error(self):
        with mock.patch.dict(os.environ, {"IG_NICHE_CREATORS": "", "IG_NICHE_HASHTAGS": ""}):
            r = rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "niche_research", "arguments": {}}}])[0]
        self.assertTrue(r["result"]["isError"])

    def test_reel_ideas_prompt(self):
        resps = rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
             "params": {"name": "reel_ideas", "arguments": {"topic": "face gains", "count": "2"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "x"}},
        ])
        self.assertEqual(resps[0]["result"]["prompts"][0]["name"], "reel_ideas")
        text = resps[1]["result"]["messages"][0]["content"]["text"]
        self.assertIn("next 2 Instagram Reels about face gains", text)
        self.assertIn("niche_research", text)
        self.assertEqual(resps[2]["error"]["code"], -32602)
