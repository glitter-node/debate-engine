from functools import lru_cache
from ipaddress import IPv6Address, ip_address, ip_network

from django.http import HttpRequest

import data.config._conf_ as const


def _normalize_ip(text: str) -> str:
    try:
        ip_obj = ip_address((text or "").strip())
    except ValueError:
        return ""
    if isinstance(ip_obj, IPv6Address) and ip_obj.ipv4_mapped is not None:
        return str(ip_obj.ipv4_mapped)
    return str(ip_obj)


@lru_cache(maxsize=1)
def _trusted_proxy_nets():
    raw = getattr(const, "TRUSTED_PROXIES", "") or ""
    raw = raw.strip()
    nets = []
    if raw:
        for part in (p.strip() for p in raw.split(",") if p.strip()):
            try:
                nets.append(ip_network(part, strict=False))
            except ValueError:
                pass
    if not nets:
        nets = list(const.TRUSTED_PROXY_NETS)
    return tuple(nets)


def _is_trusted_proxy(peer: str) -> bool:
    if not peer:
        return False
    try:
        peer_ip = ip_address(peer)
    except ValueError:
        return False
    return any(peer_ip in net for net in _trusted_proxy_nets())


def get_client_ip(request: HttpRequest) -> str:
    peer = _normalize_ip(request.META.get("REMOTE_ADDR", ""))
    if not peer:
        return ""
    if not _is_trusted_proxy(peer):
        return peer
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not xff:
        return peer
    cand = _normalize_ip(xff.split(",", 1)[0])
    return cand or peer
