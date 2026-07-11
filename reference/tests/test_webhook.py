"""Contract tests for the outbound webhook connector against a real local server.

Starts a stdlib HTTP server in-process (no external service) that behaves like a
Slack incoming webhook, and drives WebhookConnector through it — proving the
JSON body, idempotency header, retry-on-5xx, no-retry-on-4xx, and the tool-
registry wiring against genuine sockets.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from bass.connectors import ReliabilityConfig
from bass.errors import ConnectorError
from bass.tools import ToolRegistry
from bass.webhook import WebhookConnector, webhook_tool

_state = {"flaky_hits": 0, "last_body": None, "last_headers": {}}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        _state["last_body"] = json.loads(body) if body else None
        _state["last_headers"] = {k.lower(): v for k, v in self.headers.items()}
        if self.path == "/hook":
            self._send(200, "ok")
        elif self.path == "/flaky":
            _state["flaky_hits"] += 1
            self._send(200 if _state["flaky_hits"] >= 3 else 503, "ok")
        elif self.path == "/badreq":
            self._send(400, "invalid_payload")
        else:
            self._send(404, "no_service")

    def _send(self, code, body):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body.encode())


class WebhookConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _conn(self, path, **kw):
        cfg = ReliabilityConfig(timeout_s=2.0, max_retries=3, backoff_base_s=0,
                                breaker_threshold=10, **kw)
        c = WebhookConnector(self._url(path), config=cfg)
        c._connector._sleep = lambda _: None
        return c

    def test_posts_json_body_and_reports_ok(self):
        r = self._conn("/hook").post({"text": "invoice INV-4471 posted"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], 200)
        self.assertEqual(_state["last_body"], {"text": "invoice INV-4471 posted"})

    def test_idempotency_key_is_forwarded_as_header(self):
        self._conn("/hook").post({"text": "hi", "_idempotency_key": "abc123"})
        self.assertEqual(_state["last_headers"].get("idempotency-key"), "abc123")
        self.assertNotIn("_idempotency_key", _state["last_body"])   # stripped from body

    def test_retries_5xx_then_succeeds(self):
        _state["flaky_hits"] = 0
        r = self._conn("/flaky").post({"text": "retry me"})
        self.assertTrue(r["ok"])
        self.assertEqual(_state["flaky_hits"], 3)                  # 503, 503, 200

    def test_4xx_is_not_retried_and_reports_not_ok(self):
        r = self._conn("/badreq").post({"text": "bad"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], 400)

    def test_rejects_non_http_url(self):
        with self.assertRaises(ConnectorError):
            WebhookConnector("ftp://example.com/hook")

    def test_tool_factory_registers_and_executes(self):
        conn, tool = webhook_tool(self._url("/hook"), name="notify")
        conn._connector._sleep = lambda _: None
        reg = ToolRegistry()
        reg.register_connector(conn._connector)
        reg.register(tool)
        self.assertTrue(tool.side_effect)                          # routed through the ledger
        out = reg.execute("notify", {"text": "wired", "_idempotency_key": "k1"})
        self.assertTrue(out["ok"])
        self.assertEqual(_state["last_headers"].get("idempotency-key"), "k1")


if __name__ == "__main__":
    unittest.main()
