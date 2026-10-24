import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from instagram_pipeline.cli import main
from instagram_pipeline.config import Config, ConfigError, load_dotenv

from .test_analysis import synthetic


class CliTests(unittest.TestCase):
    def test_analyze_offline_writes_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "reels.json"
            data.write_text(json.dumps({
                "account": {"username": "me", "followers_count": 900},
                "online_followers_pacific": {str(h): float(h) for h in range(24)},
                "reels": [r.to_dict() for r in synthetic()],
            }))
            out = Path(d) / "out"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["--env-file", "/nonexistent", "analyze", "--data", str(data),
                             "--tz", "Asia/Kolkata", "--out", str(out), "--min-age-hours", "0"])
            self.assertEqual(code, 0)
            self.assertIn("Best times to post Reels for @me", buf.getvalue())
            rec = json.loads((out / "recommendations.json").read_text())
            self.assertEqual(rec["config"]["timezone"], "Asia/Kolkata")
            self.assertTrue((out / "reels_scored.csv").read_text().startswith("id,"))

    def test_bad_timezone(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "reels.json"
            data.write_text(json.dumps({"reels": []}))
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["analyze", "--data", str(data), "--tz", "Mars/Olympus"])
            self.assertEqual(code, 2)


class ConfigTests(unittest.TestCase):
    def test_missing_env(self):
        old = {k: os.environ.pop(k, None) for k in ("IG_ACCESS_TOKEN", "IG_USER_ID")}
        try:
            with self.assertRaises(ConfigError):
                Config.from_env()
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_dotenv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text('# c\nexport IG_TEST_A="x y"\nIG_TEST_B=z\n')
            os.environ["IG_TEST_B"] = "keep"
            load_dotenv(p)
            self.assertEqual(os.environ.pop("IG_TEST_A"), "x y")
            self.assertEqual(os.environ.pop("IG_TEST_B"), "keep")


class InstagramLoginTests(unittest.TestCase):
    """Tokens from 'Instagram API with Instagram Login' need no Facebook Page or user id."""

    def run_cli(self, argv, routes, env):
        seen = []

        def transport(url, timeout):
            seen.append(url)
            for prefix, body in routes.items():
                if url.startswith(prefix):
                    return 200, json.dumps(body)
            return 404, json.dumps({"error": {"message": "no route", "code": 803}})

        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch("instagram_pipeline.graph_client._urllib_transport", transport), \
                contextlib.redirect_stdout(buf):
            code = main(["--env-file", "/nonexistent", *argv])
        return code, buf.getvalue(), seen

    def test_host_and_user_id_from_token(self):
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "IGAAxyz", "IG_USER_ID": "",
                                          "IG_GRAPH_HOST": ""}):
            cfg = Config.from_env()
        self.assertEqual((cfg.host, cfg.ig_user_id), ("graph.instagram.com", "me"))
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "EAAxyz", "IG_USER_ID": "",
                                          "IG_GRAPH_HOST": ""}):
            with self.assertRaises(ConfigError):
                Config.from_env()  # Facebook Login still needs the IG account id

    def test_find_account(self):
        code, out, seen = self.run_cli(
            ["find-account"],
            {"https://graph.instagram.com/v23.0/me?": {"user_id": "1789", "username": "me",
                                                       "account_type": "MEDIA_CREATOR"}},
            {"IG_ACCESS_TOKEN": "IGAAxyz", "IG_USER_ID": "", "IG_GRAPH_HOST": ""})
        self.assertEqual(code, 0)
        self.assertIn("IG_USER_ID=1789", out)

    def test_refresh_token(self):
        code, out, seen = self.run_cli(
            ["refresh-token"],
            {"https://graph.instagram.com/refresh_access_token?": {
                "access_token": "IGAAnew", "expires_in": 5184000}},
            {"IG_ACCESS_TOKEN": "IGAAold", "IG_GRAPH_HOST": ""})
        self.assertEqual(code, 0)
        self.assertIn("IGAAnew", out)
        self.assertIn("grant_type=ig_refresh_token", seen[0])
        self.assertIn("60 days", out)

    def test_refresh_rejects_facebook_token(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _, seen = self.run_cli(["refresh-token"], {},
                                         {"IG_ACCESS_TOKEN": "EAAold", "IG_GRAPH_HOST": ""})
        self.assertEqual((code, seen), (2, []))
