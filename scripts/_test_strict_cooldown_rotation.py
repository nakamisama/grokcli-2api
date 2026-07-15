#!/usr/bin/env python3
"""Strict cooldown pool: used-up accounts must not re-enter live rotation.

Regression for v1.9.84 — soft recovery used to re-include cooling accounts when
the ready pool was empty. Free-usage 用完 / rate-limit / empty_upstream accounts
must stay in the cooldown pool until probe success or admin clear.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="g2a-cooldown-test-")
os.environ["REDIS_URL"] = ""
os.environ["GROK2API_REDIS_URL"] = ""
os.environ["GROK2API_STORE_BACKEND"] = "file"
os.environ["GROK2API_CONVERSATION_AFFINITY"] = "0"
os.environ["GROK2API_AFFINITY_FILE"] = str(Path(_tmp) / "affinity.json")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import account_pool as ap  # noqa: E402


def ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def _cred(aid: str, *, expired: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        auth_key=aid,
        user_id=aid,
        expired=expired,
        refresh_token=None,
        access_token=f"tok-{aid}",
        token=f"tok-{aid}",
        email=f"{aid}@t.local",
    )


def test_try_acquire_excludes_cooling() -> None:
    print("[try_acquire_sequence excludes cooling]")
    ready = _cred("ready-1")
    cooling = _cred("cool-1")
    now = time.time()
    state = {
        "ready-1": {
            "enabled": True,
            "pool_status": "active",
            "request_count": 0,
            "last_used_at": 0,
        },
        "cool-1": {
            "enabled": True,
            "pool_status": "cooldown",
            "cooldown_until": now + 3600,
            "cooldown_reason": "free_usage",
            "request_count": 0,
            "last_used_at": 0,
        },
    }

    with mock.patch.object(ap, "list_live_credentials", return_value=[ready, cooling]), \
         mock.patch.object(ap, "get_account_pool_meta_many", return_value=state), \
         mock.patch.object(ap, "get_account_pool_state", return_value=state), \
         mock.patch.object(ap, "get_cached_account_pool_state", return_value=state), \
         mock.patch.object(ap, "max_failover_attempts", return_value=8), \
         mock.patch.object(ap, "is_model_blocked", return_value=False), \
         mock.patch.object(ap, "_ensure_fresh_creds", side_effect=lambda c, **k: c), \
         mock.patch.object(ap, "get_account_mode", return_value="round_robin"), \
         mock.patch.object(ap, "_ensure_multi_account_layout"), \
         mock.patch.object(ap, "peek_credentials_by_id", return_value=None), \
         mock.patch.object(ap, "get_cached_live_credentials", return_value=[ready, cooling]):
        chain = ap.try_acquire_sequence(model="grok-4", max_attempts=8)

    ids = [c.auth_key for c in chain]
    ok("ready-1" in ids, f"ready account present: {ids}")
    ok("cool-1" not in ids, f"cooling account excluded: {ids}")


def test_try_acquire_all_cooling_returns_empty() -> None:
    print("[try_acquire_sequence all cooling → empty chain]")
    cooling_a = _cred("cool-a")
    cooling_b = _cred("cool-b")
    now = time.time()
    state = {
        "cool-a": {
            "enabled": True,
            "pool_status": "cooldown",
            "cooldown_until": now + 1800,
            "cooldown_reason": "free_usage",
        },
        "cool-b": {
            "enabled": True,
            "pool_status": "cooldown",
            "cooldown_until": now + 900,
            "cooldown_reason": "temporary_rate_limit",
        },
    }

    with mock.patch.object(ap, "list_live_credentials", return_value=[cooling_a, cooling_b]), \
         mock.patch.object(ap, "get_account_pool_meta_many", return_value=state), \
         mock.patch.object(ap, "get_account_pool_state", return_value=state), \
         mock.patch.object(ap, "get_cached_account_pool_state", return_value=state), \
         mock.patch.object(ap, "max_failover_attempts", return_value=8), \
         mock.patch.object(ap, "is_model_blocked", return_value=False), \
         mock.patch.object(ap, "_ensure_fresh_creds", side_effect=lambda c, **k: c), \
         mock.patch.object(ap, "get_account_mode", return_value="round_robin"), \
         mock.patch.object(ap, "_ensure_multi_account_layout"), \
         mock.patch.object(ap, "peek_credentials_by_id", return_value=None), \
         mock.patch.object(ap, "get_cached_live_credentials", return_value=[cooling_a, cooling_b]):
        chain = ap.try_acquire_sequence(model="grok-4", max_attempts=8)

    ok(chain == [], f"empty chain when all cooling, got {[c.auth_key for c in chain]}")


def test_acquire_raises_when_all_cooling() -> None:
    print("[acquire raises AuthError when all cooling]")
    cooling = _cred("cool-only")
    now = time.time()
    state = {
        "cool-only": {
            "enabled": True,
            "pool_status": "cooldown",
            "cooldown_until": now + 7200,
            "cooldown_reason": "free_usage",
        }
    }

    with mock.patch.object(ap, "list_live_credentials", return_value=[cooling]), \
         mock.patch.object(ap, "get_account_pool_meta_many", return_value=state), \
         mock.patch.object(ap, "get_account_pool_state", return_value=state), \
         mock.patch.object(ap, "get_cached_account_pool_state", return_value=state), \
         mock.patch.object(ap, "is_model_blocked", return_value=False), \
         mock.patch.object(ap, "get_account_mode", return_value="round_robin"), \
         mock.patch.object(ap, "_ensure_multi_account_layout"):
        try:
            ap.acquire(model="grok-4")
            raise AssertionError("expected AuthError")
        except ap.AuthError as e:
            msg = str(e).lower()
            ok("cooldown" in msg, f"error mentions cooldown: {e}")


def test_sticky_cooling_not_forced() -> None:
    print("[sticky cooling account not re-injected]")
    ready = _cred("ready-2")
    sticky_cool = _cred("sticky-cool")
    now = time.time()
    state = {
        "ready-2": {"enabled": True, "pool_status": "active"},
        "sticky-cool": {
            "enabled": True,
            "pool_status": "cooldown",
            "cooldown_until": now + 3600,
            "cooldown_reason": "free_usage",
        },
    }

    with mock.patch.object(ap, "list_live_credentials", return_value=[ready, sticky_cool]), \
         mock.patch.object(ap, "get_account_pool_meta_many", return_value=state), \
         mock.patch.object(ap, "get_account_pool_state", return_value=state), \
         mock.patch.object(ap, "get_cached_account_pool_state", return_value=state), \
         mock.patch.object(ap, "max_failover_attempts", return_value=8), \
         mock.patch.object(ap, "is_model_blocked", return_value=False), \
         mock.patch.object(ap, "_ensure_fresh_creds", side_effect=lambda c, **k: c), \
         mock.patch.object(ap, "get_account_mode", return_value="round_robin"), \
         mock.patch.object(ap, "_ensure_multi_account_layout"), \
         mock.patch.object(ap, "peek_credentials_by_id", return_value=sticky_cool), \
         mock.patch.object(ap, "get_account_pool_meta", return_value=state["sticky-cool"]), \
         mock.patch.object(ap, "get_cached_live_credentials", return_value=[ready, sticky_cool]):
        chain = ap.try_acquire_sequence(
            model="grok-4",
            prefer_account_id="sticky-cool",
            max_attempts=8,
        )

    ids = [c.auth_key for c in chain]
    ok(ids == ["ready-2"], f"only ready account, got {ids}")


def test_is_in_cooldown_reason_free_usage() -> None:
    print("[is_in_cooldown free_usage meta]")
    meta = {
        "pool_status": "cooldown",
        "cooldown_until": time.time() + 100,
        "cooldown_reason": "free_usage",
    }
    ok(ap.is_in_cooldown(meta) is True, "free_usage cooling")
    ok(ap.is_in_cooldown({"pool_status": "active", "cooldown_until": 0}) is False, "active not cooling")
    ok(
        ap.is_in_cooldown({"pool_status": "cooldown", "cooldown_until": 0}) is True,
        "legacy status-only cooldown",
    )


def main() -> int:
    tests = [
        test_is_in_cooldown_reason_free_usage,
        test_try_acquire_excludes_cooling,
        test_try_acquire_all_cooling_returns_empty,
        test_acquire_raises_when_all_cooling,
        test_sticky_cooling_not_forced,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
