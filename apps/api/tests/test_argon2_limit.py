"""Argon2 reserva 64 MiB por operación: el número simultáneo por proceso está acotado."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import security
from app.core.config import get_settings


def test_concurrent_password_checks_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    running = peak = 0
    lock = threading.Lock()

    class SlowHasher:
        def verify(self, password_hash: str, password: str) -> bool:
            return slow_verify()

    def slow_verify() -> bool:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return True

    monkeypatch.setattr(security, "_hasher", SlowHasher())
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: security.verify_password("h", "p"), range(20)))

    assert all(results)
    assert peak == get_settings().argon2_max_concurrency
