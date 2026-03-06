from __future__ import annotations

import threading
import time

from django.core.cache import cache

_fallback_lock = threading.Lock()
_fallback_counts: dict[str, tuple[int, int]] = {}


def _incr_with_fallback(key: str, window_seconds: int) -> int:
    bucket = int(time.time() // window_seconds)
    cache_key = f"authflow:rl:{key}:{bucket}"
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


def allow_access_request(email: str, client_ip: str) -> bool:
    email_norm = (email or "").strip().lower()
    ip_norm = (client_ip or "").strip() or "unknown"

    minute_email = _incr_with_fallback(f"email:{email_norm}", 60)
    hour_email = _incr_with_fallback(f"email-hour:{email_norm}", 3600)
    minute_ip = _incr_with_fallback(f"ip:{ip_norm}", 60)
    hour_ip = _incr_with_fallback(f"ip-hour:{ip_norm}", 3600)

    if minute_email > 3 or hour_email > 10:
        return False
    if minute_ip > 10 or hour_ip > 60:
        return False
    return True


def allow_google_onetap_request(google_sub: str, email: str, client_ip: str) -> bool:
    sub_norm = (google_sub or "").strip()
    email_norm = (email or "").strip().lower()
    ip_norm = (client_ip or "").strip() or "unknown"

    minute_ip = _incr_with_fallback(f"google:ip:{ip_norm}", 60)
    hour_ip = _incr_with_fallback(f"google:ip-hour:{ip_norm}", 3600)
    minute_sub = _incr_with_fallback(f"google:sub:{sub_norm}", 60)
    hour_sub = _incr_with_fallback(f"google:sub-hour:{sub_norm}", 3600)
    minute_email = _incr_with_fallback(f"google:email:{email_norm}", 60)
    hour_email = _incr_with_fallback(f"google:email-hour:{email_norm}", 3600)

    if minute_ip > 30 or hour_ip > 180:
        return False
    if minute_sub > 10 or hour_sub > 60:
        return False
    if minute_email > 10 or hour_email > 60:
        return False
    return True
