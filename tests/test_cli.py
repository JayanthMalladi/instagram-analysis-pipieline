import contextlib
import io
import json
import os
import tempfile
import unittest
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
