"""Authentication & authorization for the HTTP API.

Bearer JWTs (HS256) carry the caller's tenant, subject, and roles. The
`Principal` is the security context every request runs under — notably the
`tenant_id` that scopes all data access, and the roles the RBAC checks gate on.

HS256 is implemented on the standard library (`hmac`/`hashlib`) so the reference
has no crypto dependency and runs anywhere. A production deployment federates to
an IdP (OIDC/SAML) and verifies RS256/ES256 tokens against the issuer's JWKS —
swap `decode_token` for that verifier; the `Principal` contract is unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

from .errors import BassError


class AuthError(BassError):
    """Invalid, expired, or missing credentials."""


ROLES = ("owner", "admin", "builder", "approver", "viewer")


@dataclass
class Principal:
    tenant_id: str
    subject: str
    roles: list[str] = field(default_factory=list)

    def has_any(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _sign(secret: str, signing_input: str) -> str:
    mac = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return _b64url(mac)


def mint_token(secret: str, tenant_id: str, subject: str, roles: list[str],
               ttl_s: int = 3600) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"},
                                separators=(",", ":")).encode())
    payload = _b64url(json.dumps(
        {"sub": subject, "tenant_id": tenant_id, "roles": roles,
         "iat": now, "exp": now + ttl_s}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    return f"{signing_input}.{_sign(secret, signing_input)}"


def decode_token(secret: str, token: str) -> Principal:
    try:
        header_b64, payload_b64, sig = token.split(".")
    except ValueError as exc:
        raise AuthError("malformed token") from exc
    expected = _sign(secret, f"{header_b64}.{payload_b64}")
    if not hmac.compare_digest(expected, sig):        # constant-time
        raise AuthError("bad signature")
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("unreadable token payload") from exc
    if float(claims.get("exp", 0)) < time.time():
        raise AuthError("token expired")
    tenant_id, subject = claims.get("tenant_id"), claims.get("sub")
    if not tenant_id or not subject:
        raise AuthError("token missing tenant_id/sub")
    return Principal(tenant_id=tenant_id, subject=subject,
                     roles=list(claims.get("roles", [])))
