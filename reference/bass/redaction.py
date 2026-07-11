"""Redaction of secrets and PII, applied *at write time*.

Every payload is scrubbed before it is persisted to the event log or emitted to
a model context, so a raw secret never lands in storage or logs — you cannot
redact at read time what was already written in the clear.

The ruleset is intentionally conservative and extensible. A production
deployment would add tenant-configurable PII classes and a proper DLP model;
this module provides the enforcement point and a sensible default.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

# Key names whose values are always secret, regardless of shape.
_SECRET_KEYS = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|credential|private[_-]?key)",
    re.IGNORECASE,
)

# Value patterns that look like secrets/PII even under an innocent key.
_VALUE_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),                       # Anthropic keys
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # emails
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),                        # card-like PANs
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),      # bearer tokens
]


def _scrub_str(value: str) -> str:
    out = value
    for pat in _VALUE_PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def redact(value: Any) -> Any:
    """Return a deep copy of ``value`` with secrets and PII masked.

    Handles nested dicts/lists; leaves non-string scalars untouched unless a key
    marks them secret.
    """
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEYS.search(k):
                result[k] = REDACTED
            else:
                result[k] = redact(v)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _scrub_str(value)
    return value
