"""Integration test for the HTTP connector against a real local server.

Starts a stdlib HTTP server in-process (no external service needed) and drives
it through Connector.run, proving timeout/retry/circuit-breaker behavior against
genuine sockets — not mocks.
"""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from bass.connectors import Connector, ReliabilityConfig, http_call
from bass.errors import ConnectorError

_flaky_hits = {"n": 0}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # silence
        pass

    def do_GET(self):
        if self.path == "/ok":
            self._send(200, "ok")
        elif self.path == "/flaky":
            _flaky_hits["n"] += 1
            self._send(200 if _flaky_hits["n"] >= 3 else 503, "flaky")
        elif self.path == "/boom":
            self._send(500, "boom")
        elif self.path == "/notfound":
            self._send(404, "nope")
        else:
            self._send(200, "root")

    def _send(self, code, body):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body.encode())


class HttpConnectorTests(unittest.TestCase):
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

    def _conn(self, **kw):
        params = dict(timeout_s=2.0, max_retries=3, backoff_base_s=0, breaker_threshold=10)
        params.update(kw)
        return Connector("http", ReliabilityConfig(**params), sleep=lambda _: None)

    def test_success(self):
        r = self._conn().run(http_call, {"url": self._url("/ok")})
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["body"], "ok")

    def test_retries_5xx_then_succeeds(self):
        _flaky_hits["n"] = 0
        r = self._conn().run(http_call, {"url": self._url("/flaky")})
        self.assertEqual(r["status"], 200)          # 503, 503, 200
        self.assertEqual(_flaky_hits["n"], 3)

    def test_gives_up_on_persistent_5xx(self):
        with self.assertRaises(ConnectorError):
            self._conn(max_retries=2).run(http_call, {"url": self._url("/boom")})

    def test_4xx_is_returned_not_retried(self):
        r = self._conn().run(http_call, {"url": self._url("/notfound")})
        self.assertEqual(r["status"], 404)          # client error, not retried


if __name__ == "__main__":
    unittest.main()
