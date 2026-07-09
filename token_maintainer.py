"""Background maintenance for multi-account auth on long-running servers.

- Normalize auth.json keys (CLI client_id → per-user multi-account)
- Proactively refresh access tokens via refresh_token before expiry
- Adaptive interval: refresh sooner when any token is near expiry
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

_stop = threading.Event()
_thread: threading.Thread | None = None
_last_run: dict[str, Any] = {}
_wakeup = threading.Event()  # force an early cycle from admin UI


def _interval() -> float:
    try:
        return max(60.0, float(os.getenv("GROK2API_TOKEN_MAINTAIN_INTERVAL", "300")))
    except ValueError:
        return 300.0


def _skew() -> float:
    try:
        return float(os.getenv("GROK2API_TOKEN_REFRESH_SKEW", "120"))
    except ValueError:
        return 120.0


def _min_remaining_seconds() -> float | None:
    """Smallest access-token remaining lifetime across live accounts."""
    try:
        from auth import list_live_credentials

        now = time.time()
        remains: list[float] = []
        for c in list_live_credentials(include_expired=True, auto_refresh=False):
            if c.expires_at is None:
                continue
            remains.append(float(c.expires_at) - now)
        if not remains:
            return None
        return min(remains)
    except Exception:
        return None


def _next_wait_seconds() -> float:
    """
    Adaptive sleep: if any token expires soon, poll more frequently so
    expires_at gets refreshed automatically without manual clicks.
    """
    base = _interval()
    rem = _min_remaining_seconds()
    if rem is None:
        return base
    # Within 15 minutes of expiry → check every 60s
    if rem <= 15 * 60:
        return min(base, 60.0)
    # Within 1 hour → check every 2 minutes
    if rem <= 3600:
        return min(base, 120.0)
    return base


def run_once(*, force: bool = False) -> dict[str, Any]:
    """
    Normalize keys + refresh tokens.
    force=True refreshes every account that has refresh_token (updates expires_at).
    """
    result: dict[str, Any] = {
        "ok": True,
        "normalized": None,
        "refresh": None,
        "force": force,
        "accounts": [],
    }
    try:
        from accounts import list_accounts
        from oidc_auth import normalize_auth_file_keys, refresh_all_accounts

        result["normalized"] = normalize_auth_file_keys()
        skew = max(300.0, _skew() * 2)
        result["refresh"] = refresh_all_accounts(
            only_near_expiry=not force,
            skew_seconds=skew,
        )
        # Snapshot current expiry times for admin UI
        result["accounts"] = [
            {
                "id": a.get("id"),
                "email": a.get("email"),
                "expires_at": a.get("expires_at"),
                "expired": a.get("expired"),
                "has_refresh_token": a.get("has_refresh_token"),
                "remaining_sec": (
                    max(0, int(float(a["expires_at"]) - time.time()))
                    if a.get("expires_at")
                    else None
                ),
            }
            for a in list_accounts()
        ]
        result["min_remaining_sec"] = _min_remaining_seconds()
    except Exception as e:  # noqa: BLE001
        result["ok"] = False
        result["error"] = str(e)[:400]
    _last_run.clear()
    _last_run.update(result)
    _last_run["at"] = time.time()
    return result


def request_run_soon() -> None:
    """Wake the background worker for an early cycle."""
    _wakeup.set()


def _worker() -> None:
    # small delay so startup doesn't block first requests
    if _stop.wait(5.0):
        return
    while not _stop.is_set():
        run_once(force=False)
        wait = _next_wait_seconds()
        # Wait either for interval or an admin-triggered wakeup
        _wakeup.clear()
        triggered = _wakeup.wait(timeout=wait)
        if _stop.is_set():
            break
        if triggered:
            # admin asked for refresh — do a force pass
            run_once(force=True)


def start_background() -> None:
    global _thread
    if os.getenv("GROK2API_TOKEN_MAINTAIN", "1").lower() in ("0", "false", "no"):
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_worker, name="g2a-token-maintainer", daemon=True)
    _thread.start()


def stop_background() -> None:
    _stop.set()
    _wakeup.set()


def status() -> dict[str, Any]:
    rem = _min_remaining_seconds()
    return {
        "running": bool(_thread and _thread.is_alive()),
        "enabled": os.getenv("GROK2API_TOKEN_MAINTAIN", "1").lower()
        not in ("0", "false", "no"),
        "interval_sec": _interval(),
        "next_wait_sec": _next_wait_seconds(),
        "refresh_skew_sec": _skew(),
        "min_remaining_sec": rem,
        "last": dict(_last_run) if _last_run else None,
    }
