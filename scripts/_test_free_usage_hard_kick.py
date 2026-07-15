#!/usr/bin/env python3
"""Free-usage / no-quota always hard-kicks account out of rotation."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="g2a-kick-")
os.environ["REDIS_URL"] = ""
os.environ["GROK2API_REDIS_URL"] = ""
os.environ["GROK2API_STORE_BACKEND"] = "file"
os.environ["GROK2API_AFFINITY_FILE"] = str(Path(_tmp) / "affinity.json")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import account_pool as ap  # noqa: E402


def ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def test_parse_free_usage_cn() -> None:
    print("[parse free usage expanded]")
    p = ap.parse_free_usage_error("额度耗尽，免费额度已用完", 429)
    ok(p is not None, f"parsed cn: {p}")
    p2 = ap.parse_free_usage_error(
        "subscription:free-usage-exhausted You've used all the included free usage for model grok-4",
        429,
    )
    ok(p2 is not None and p2.get("code"), f"parsed official: {p2}")


def test_apply_hard_kick() -> None:
    print("[apply_free_usage_cooldown hard kick]")
    saved = {}

    def _get_meta(aid):
        return dict(saved.get(aid) or {"enabled": True, "pool_status": "active"})

    def _patch(aid, patch):
        cur = _get_meta(aid)
        cur.update(patch)
        saved[aid] = cur
        return cur

    with mock.patch.object(ap, "get_account_pool_meta", side_effect=_get_meta), \
         mock.patch.object(ap, "patch_account_pool_meta", side_effect=_patch), \
         mock.patch.object(ap, "block_model"), \
         mock.patch.object(ap, "invalidate_pool_summary_cache"), \
         mock.patch.object(ap, "release_account_pick"), \
         mock.patch("conversation_affinity.clear_affinity_for_account", return_value=1):
        out = ap.apply_free_usage_cooldown(
            "acc-1",
            error="subscription:free-usage-exhausted used all free usage for model grok-4",
            status_code=429,
            model="grok-4",
            source="live",
        )
    ok(out is not None, "returned result")
    ok(out.get("pool_status") == "cooldown", f"status={out.get('pool_status')}")
    ok(out.get("kicked_from_rotation") is True, "kicked_from_rotation")
    ok(out.get("enabled") is True, "enabled remains True for probe recovery")
    meta = saved["acc-1"]
    ok(meta.get("pool_status") == "cooldown", "meta pool_status cooldown")
    ok(float(meta.get("cooldown_until") or 0) > time.time(), "cooldown_until future")
    ok(ap.is_in_cooldown(meta) is True, "is_in_cooldown true")
    # Must not be in live rotation
    ok(meta.get("kicked_from_rotation") is True, "meta kicked flag")


def test_temporary_usage_also_kicks() -> None:
    print("[temporary usage / rate limit kicks]")
    saved = {}

    def _get_meta(aid):
        return dict(saved.get(aid) or {"enabled": True})

    def _patch(aid, patch):
        cur = _get_meta(aid)
        cur.update(patch)
        saved[aid] = cur
        return cur

    with mock.patch.object(ap, "get_account_pool_meta", side_effect=_get_meta), \
         mock.patch.object(ap, "patch_account_pool_meta", side_effect=_patch), \
         mock.patch.object(ap, "block_model"), \
         mock.patch.object(ap, "invalidate_pool_summary_cache"), \
         mock.patch.object(ap, "release_account_pick"), \
         mock.patch("conversation_affinity.clear_affinity_for_account", return_value=0):
        out = ap.apply_free_usage_cooldown(
            "acc-2",
            error="Too Many Requests / rate limit",
            status_code=429,
            model="grok-4",
            source="live",
        )
    ok(out is not None, f"out={out}")
    ok(out.get("kicked_from_rotation") is True, "kicked")
    ok(ap.is_in_cooldown(saved["acc-2"]) is True, "cooling")


def test_cooling_excluded_from_try_acquire() -> None:
    print("[cooling kicked account not in try_acquire]")
    from types import SimpleNamespace

    ready = SimpleNamespace(
        auth_key="ready", user_id="ready", expired=False, refresh_token=None,
        access_token="t", token="t", email="r@t.local",
    )
    kicked = SimpleNamespace(
        auth_key="kicked", user_id="kicked", expired=False, refresh_token=None,
        access_token="t", token="t", email="k@t.local",
    )
    now = time.time()
    state = {
        "ready": {"enabled": True, "pool_status": "active", "request_count": 0, "last_used_at": 0},
        "kicked": {
            "enabled": True,
            "pool_status": "cooldown",
            "cooldown_until": now + 1e7,
            "cooldown_reason": "临时额度耗尽，已冷却踢出轮询",
            "kicked_from_rotation": True,
            "request_count": 0,
            "last_used_at": 0,
        },
    }
    with mock.patch.object(ap, "list_live_credentials", return_value=[ready, kicked]), \
         mock.patch.object(ap, "get_account_pool_meta_many", return_value=state), \
         mock.patch.object(ap, "get_account_pool_state", return_value=state), \
         mock.patch.object(ap, "get_cached_account_pool_state", return_value=state), \
         mock.patch.object(ap, "max_failover_attempts", return_value=4), \
         mock.patch.object(ap, "is_model_blocked", return_value=False), \
         mock.patch.object(ap, "_ensure_fresh_creds", side_effect=lambda c, **k: c), \
         mock.patch.object(ap, "get_account_mode", return_value="round_robin"), \
         mock.patch.object(ap, "_ensure_multi_account_layout"), \
         mock.patch.object(ap, "peek_credentials_by_id", return_value=None), \
         mock.patch.object(ap, "get_cached_live_credentials", return_value=[ready, kicked]), \
         mock.patch.object(ap, "note_account_pick"):
        chain = ap.try_acquire_sequence(model="grok-4", max_attempts=4)
    ids = [c.auth_key for c in chain]
    ok("ready" in ids, f"ready present {ids}")
    ok("kicked" not in ids, f"kicked excluded {ids}")


def main() -> int:
    tests = [
        test_parse_free_usage_cn,
        test_apply_hard_kick,
        test_temporary_usage_also_kicks,
        test_cooling_excluded_from_try_acquire,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
