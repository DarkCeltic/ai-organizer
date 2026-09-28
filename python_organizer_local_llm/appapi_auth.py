"""Helpers for Nextcloud AppAPI shared-secret authentication."""
from __future__ import annotations

import base64
import hmac
from typing import Optional

from python_organizer_local_llm.settings import EnvironmentSettings


def encode_appapi_token(user_id: str, secret: str) -> str:
    # App-level AppAPI endpoints (for example UI registration during /enabled)
    # legitimately authenticate with an empty user ID. User-scoped DAV calls
    # enforce a non-empty user separately in NextcloudClient._require_active_user().
    user_id = str(user_id or "").strip()
    secret = str(secret or "")
    if not secret:
        raise RuntimeError("APP_SECRET is not set.")
    return base64.b64encode(f"{user_id}:{secret}".encode("utf-8")).decode("ascii")


def outgoing_headers(runtime: EnvironmentSettings, user_id: str) -> dict[str, str]:
    return {
        "OCS-APIRequest": "true",
        "AA-VERSION": runtime.app_api_version,
        "EX-APP-ID": runtime.app_id,
        "EX-APP-VERSION": runtime.app_version,
        "AUTHORIZATION-APP-API": encode_appapi_token(user_id, runtime.app_secret),
    }


def user_from_incoming_header(value: Optional[str], expected_secret: str) -> Optional[str]:
    """Return the proxied user if an AppAPI auth header matches our secret."""
    if not value or not expected_secret:
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        user_id, secret = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    user_id = user_id.strip()
    if not user_id or not hmac.compare_digest(secret, expected_secret):
        return None
    return user_id
