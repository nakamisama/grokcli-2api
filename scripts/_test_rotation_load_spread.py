#!/usr/bin/env python3
"""Rotation load-spread: concurrent picks should not stampede the same account."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="g2a-spread-")
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


def _cred(aid: str) -> SimpleNamespace:
    return SimpleNamespace(
        auth_key=aid,
        user_id=aid,
        expired=False,
        refresh_token=None,
        access_token=f"tok-{aid}",
        token=f"tok-{aid}",
        email=f"{aid}@t.local",
    )


def _reset_local() -> None:
    with ap._local_spread_lock:
        ap._local_inflight.clear()
        ap._local_soft_used.clear()


def test_note_release_local_inflight() -> None:
    print("[local inflight note/release]")
    _reset_local()
    ap.note_account_pick("a1")
    ap.note_account_pick("a1")
    infl, soft = ap._load_spread_hints(["a1", "a2"])
    ok(infl.get("a1") == 2, f"inflight a1={infl.get('a1')}")
    ok(soft.get("a1", 0) > 0, "soft used stamped")
    ap.release_account_pick("a1")
    infl2, _ = ap._load_spread_hints(["a1"])
    ok(infl2.get("a1") == 1, f"after release inflight={infl2.get('a1')}")
    ap.release_account_pick("a1")
    infl3, _ = ap._load_spread_hints(["a1"])
    ok(not infl3.get("a1"), f"fully released: {infl3}")


def test_least_used_prefers_idle() -> None:
    print("[least_used prefers idle over busy]")
    _reset_local()
    idle = _cred("idle")
    busy = _cred("busy")
    state = {
        "idle": {"enabled": True, "request_count": 0, "last_used_at": 0, "weight": 1, "success_count": 0, "fail_count": 0, "consecutive_fails": 0},
        "busy": {"enabled": True, "request_count": 0, "last_used_at": 0, "weight": 1, "success_count": 0, "fail_count": 0, "consecutive_fails": 0},
    }
    # Mark busy as in-flight
    ap.note_account_pick("busy")
    ap.note_account_pick("busy")
    picked = ap._pick_least_used([idle, busy], state)
    ok(picked.auth_key == "idle", f"picked {picked.auth_key}")


def test_health_penalty_inflight() -> None:
    print("[health penalty includes inflight]")
    meta = {"consecutive_fails": 0, "fail_count": 0, "success_count": 10, "weight": 1}
    p0 = ap._health_penalty(meta, inflight=0)
    p2 = ap._health_penalty(meta, inflight=2)
    ok(p2 > p0, f"p2={p2} > p0={p0}")


def test_try_acquire_spreads_after_note() -> None:
    print("[try_acquire spreads after first pick]")
    _reset_local()
    a = _cred("acc-a")
    b = _cred("acc-b")
    state = {
        "acc-a": {"enabled": True, "pool_status": "active", "request_count": 0, "last_used_at": 0, "weight": 1, "success_count": 5, "fail_count": 0, "consecutive_fails": 0},
        "acc-b": {"enabled": True, "pool_status": "active", "request_count": 0, "last_used_at": 0, "weight": 1, "success_count": 5, "fail_count": 0, "consecutive_fails": 0},
    }
    # Pre-mark acc-a busy
    ap.note_account_pick("acc-a")
    ap.note_account_pick("acc-a")
    with mock.patch.object(ap, "list_live_credentials", return_value=[a, b]), \
         mock.patch.object(ap, "get_account_pool_meta_many", return_value=state), \
         mock.patch.object(ap, "get_account_pool_state", return_value=state), \
         mock.patch.object(ap, "get_cached_account_pool_state", return_value=state), \
         mock.patch.object(ap, "max_failover_attempts", return_value=4), \
         mock.patch.object(ap, "is_model_blocked", return_value=False), \
         mock.patch.object(ap, "_ensure_fresh_creds", side_effect=lambda c, **k: c), \
         mock.patch.object(ap, "get_account_mode", return_value="least_used"), \
         mock.patch.object(ap, "_ensure_multi_account_layout"), \
         mock.patch.object(ap, "peek_credentials_by_id", return_value=None), \
         mock.patch.object(ap, "get_cached_live_credentials", return_value=[a, b]):
        chain = ap.try_acquire_sequence(model="grok-4", max_attempts=4)
    ids = [c.auth_key for c in chain]
    ok(ids[0] == "acc-b", f"head should be idle acc-b, got {ids}")


def main() -> int:
    tests = [
        test_note_release_local_inflight,
        test_health_penalty_inflight,
        test_least_used_prefers_idle,
        test_try_acquire_spreads_after_note,
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
