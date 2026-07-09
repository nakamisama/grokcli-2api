"""Thread/process-safe auth.json store for multi-account on Linux servers.

Centralizes read/write with:
  - process-local RLock (thread safety)
  - optional file lock via portalocker-like fcntl / msvcrt (best-effort)
  - atomic tmp + replace writes
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import AUTH_FILE

_thread_lock = threading.RLock()


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


@contextmanager
def _file_lock(path: Path, *, timeout: float = 10.0) -> Iterator[None]:
    """Best-effort exclusive file lock (Linux fcntl / Windows msvcrt)."""
    lock_file = _lock_path(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_file, "a+b")
    try:
        if fh.tell() == 0:
            fh.write(b"0")
            fh.flush()
    except OSError:
        pass
    deadline = time.time() + timeout
    locked = False
    try:
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (OSError, BlockingIOError):
                if time.time() >= deadline:
                    # proceed without lock rather than deadlock the API
                    break
                time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            fh.close()
        except OSError:
            pass


@contextmanager
def auth_lock(timeout: float = 10.0) -> Iterator[None]:
    with _thread_lock:
        with _file_lock(AUTH_FILE, timeout=timeout):
            yield


def read_auth_map(path: Path | None = None) -> dict[str, Any]:
    path = path or AUTH_FILE
    with auth_lock():
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


def write_auth_map(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or AUTH_FILE
    with auth_lock():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        # Windows: replace may fail if dest open; retry briefly
        last_err: Exception | None = None
        for _ in range(8):
            try:
                os.replace(str(tmp), str(path))
                last_err = None
                break
            except OSError as e:
                last_err = e
                time.sleep(0.03)
        if last_err is not None:
            try:
                tmp.unlink(missing_ok=True)  # type: ignore[call-arg]
            except TypeError:
                if tmp.exists():
                    tmp.unlink()
            raise last_err


def mutate_auth_map(mutator) -> dict[str, Any]:
    """
    Read → mutate(dict) → write under one lock.
    mutator receives the map and may modify in place; return value is ignored.
    """
    with auth_lock():
        path = AUTH_FILE
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (OSError, json.JSONDecodeError):
                data = {}
        mutator(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(tmp), str(path))
        return data
