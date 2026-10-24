import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from instagram_pipeline import mcp_server

from .test_analysis import synthetic


def rpc(lines):
    out = io.StringIO()
    mcp_server.serve(io.StringIO("".join(json.dumps(l) + "\n" for l in lines)), out)
    return [json.loads(l) for l in out.getvalue().splitlines()]


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"IG_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_handshake_and_list(self):
        resps = rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "nope"},
        ])
        self.assertEqual(len(resps), 3)  # no reply to the notification
        self.assertEqual(resps[0]["result"]["protocolVersion"], "2025-06-18")
        names = {t["name"] for t in resps[1]["result"]["tools"]}
        self.assertEqual(names, {"best_posting_times", "list_reels", "fetch_reels"})
        self.assertEqual(resps[2]["error"]["code"], -32601)

    def test_unknown_protocol_version_gets_latest(self):
        r = rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "1999-01-01"}}])[0]
        self.assertEqual(r["result"]["protocolVersion"], mcp_server.SUPPORTED_PROTOCOL_VERSIONS[0])

    def test_best_posting_times_uses_cache(self):
        reels = synthetic()
        mcp_server.data_path().write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "account": {"username": "me"}, "reels": [r.to_dict() for r in reels]}))
        r = rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "best_posting_times",
                             "arguments": {"timezone": "Asia/Kolkata", "min_age_hours": 0}}}])[0]
        self.assertFalse(r["result"]["isError"])
        self.assertIn("RECOMMENDED SLOTS", r["result"]["content"][0]["text"])

        r = rpc([{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "list_reels", "arguments": {"sort_by": "score", "limit": 3}}}])[0]
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["count"], 3)

    def test_missing_credentials_is_tool_error(self):
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "", "IG_USER_ID": ""}):
            r = rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "fetch_reels", "arguments": {}}}])[0]
        self.assertTrue(r["result"]["isError"])
        self.assertIn("IG_ACCESS_TOKEN", r["result"]["content"][0]["text"])
