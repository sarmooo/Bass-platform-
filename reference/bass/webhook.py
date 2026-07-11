"""Outbound webhook connector (Slack-style incoming webhooks, generic HTTP POST).

A concrete connector for the most common integration a company automates: post a
notification to an external endpoint. It composes `connectors.Connector` (timeout
+ retry + circuit breaker) with `connectors.http_call`, so a flaky webhook
degrades one tool rather than the platform.

`WebhookConnector.post` is a **side-effect** operation — the workflow engine
threads its idempotency key through so a crash-replay does not double-notify. The
endpoint contract mirrors Slack: HTTP 200 with body `ok` on success; a 4xx is a
permanent client error (bad payload/URL) and is not retried; a 5xx or connection
error is transient and retried.
"""

from __future__ import annotations

from typing import Any

from .connectors import Connector, ReliabilityConfig, http_call
from .errors import ConnectorError
from .models import Tool


class WebhookConnector:
    """Posts JSON to a fixed endpoint through a reliability-wrapped connector."""

    def __init__(self, url: str, *, name: str = "webhook",
                 headers: dict[str, str] | None = None,
                 config: ReliabilityConfig | None = None,
                 connector: Connector | None = None):
        if not url.lower().startswith(("http://", "https://")):
            raise ConnectorError(f"webhook url must be http(s): {url!r}")
        self.url = url
        self.name = name
        self.headers = dict(headers or {})
        self._connector = connector or Connector(name, config)

    def post(self, args: dict) -> Any:
        """Send a message. args: {text?, blocks?, ...} — the whole dict minus the
        idempotency key is sent as the JSON body. Returns {status, ok, body}."""
        payload = {k: v for k, v in args.items() if k != "_idempotency_key"}
        headers = dict(self.headers)
        key = args.get("_idempotency_key")
        if key:
            # Lets an idempotent receiver dedupe a retried delivery.
            headers.setdefault("Idempotency-Key", str(key))
        resp = self._connector.run(http_call, {
            "method": "POST", "url": self.url, "headers": headers, "json": payload,
        })
        status = int(resp.get("status", 0))
        return {"status": status, "ok": 200 <= status < 300, "body": resp.get("body", "")}


def webhook_tool(url: str, *, name: str = "notify",
                 description: str = "Post a notification to an external webhook.",
                 connector_name: str = "webhook",
                 config: ReliabilityConfig | None = None) -> tuple[WebhookConnector, Tool]:
    """Build a (connector, Tool) pair ready to register in a ToolRegistry.

    The tool is marked `side_effect=True`, so the engine routes it through the
    idempotency ledger and the policy gate like any other mutating action.
    """
    conn = WebhookConnector(url, name=connector_name, config=config)
    tool = Tool(
        name=name, description=description, connector=connector_name,
        fn=conn.post, side_effect=True,
        input_schema={"type": "object",
                      "properties": {"text": {"type": "string"}},
                      "required": ["text"]},
    )
    return conn, tool
