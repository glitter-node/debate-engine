from __future__ import annotations

import threading
import time

from django.core.cache import cache

_fallback_lock = threading.Lock()
_fallback_counts: dict[str, tuple[int, int]] = {}


def _incr_with_fallback(key: str, window_seconds: int) -> int:
    bucket = int(time.time() // window_seconds)
    cache_key = f"thinking:report:rl:{key}:{bucket}"
    timeout = window_seconds + 5
    try:
        created = cache.add(cache_key, 1, timeout=timeout)
        if created:
            return 1
        return int(cache.incr(cache_key))
    except Exception:
        pass

    fallback_key = f"{cache_key}:fb"
    with _fallback_lock:
        count, known_bucket = _fallback_counts.get(fallback_key, (0, bucket))
        if known_bucket != bucket:
            count = 0
            known_bucket = bucket
        count += 1
        _fallback_counts[fallback_key] = (count, known_bucket)
        return count


def allow_report_submit(user_id: int | None) -> bool:
    key = str(user_id or "anon")
    minute_count = _incr_with_fallback(f"user:{key}", 60)
    return minute_count <= 3
