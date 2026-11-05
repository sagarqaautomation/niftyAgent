"""Kite Connect browser-login callback and local token storage.

Kite requires the account holder to complete login/2FA. This module removes
manual request-token exchange and .env editing; it does not bypass login.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings

def token_file() -> Path:
    return Path(os.getenv("KITE_TOKEN_FILE", "data/kite_token.json"))

def save_access_token(access_token: str, public_token: str | None = None) -> None:
    """Atomically store the token with owner-only permissions where supported."""
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "public_token": public_token,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    fd, temp_name = tempfile.mkstemp(prefix=".kite-token-", dir=str(path.parent))
    try:
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.write("\n")
        os.replace(temp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass

def load_access_token() -> str:
    """Prefer the callback-managed token, falling back to the legacy env value."""
    path = token_file()
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if isinstance(token, str) and token.strip():
            return token.strip()
    except (OSError, json.JSONDecodeError):
        pass
    return settings.kite_access_token.strip()

def token_status() -> dict[str, Any]:
    path = token_file()
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("access_token"), str):
            return {"configured": True, "source": "callback_store", "saved_at": payload.get("saved_at")}
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "configured": bool(settings.kite_access_token.strip()),
        "source": "environment" if settings.kite_access_token.strip() else "missing",
        "saved_at": None,
    }
