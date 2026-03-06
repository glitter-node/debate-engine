import json
import logging
import os
import threading
from ipaddress import ip_address, ip_network

from api.middleware.ip_resolver import get_client_ip
from django.http import HttpRequest, HttpResponse

import data.config._conf_ as const

logger = logging.getLogger(__name__)
BLOCK_IP_JSON_PATH = os.environ.get("BLOCK_IP_JSON_PATH", const.BLOCK_IP)


def is_internal_ip(ip_str: str) -> bool:
    try:
        ip_obj = ip_address(ip_str)
        return any(ip_obj in net for net in const.INTERNAL_IP_RANGES)
    except ValueError:
        return False


def is_trusted_bot(user_agent: str) -> bool:
    return bool(user_agent) and any(
        bot.lower() in user_agent.lower() for bot in const.TRUSTED_BOTS
    )


def verify_request(request: HttpRequest) -> bool:
    client_ip = get_client_ip(request)
    logger.debug("verify_request client_ip=%s", client_ip)
    if not client_ip:
        return False
    if is_internal_ip(client_ip):
        return True
    ua = request.META.get("HTTP_USER_AGENT", "")
    return is_trusted_bot(ua)


class IPBlocker:
    def __init__(self, path: str, interval: int = 5):
        self.path = path
        self.interval = interval
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread_started = False
        self._mtime = None
        self._nets = []

    def _parse_nets(self, items):
        nets = []
        for raw in items:
            s = str(raw).replace("\ufeff", "").replace("\u200b", "").strip()
            if not s:
                continue
            try:
                nets.append(ip_network(s, strict=False))
                continue
            except Exception:
                pass
            for suf in ("/32", "/128"):
                try:
                    nets.append(ip_network(f"{s}{suf}", strict=False))
                    break
                except Exception:
                    continue
        return nets

    def _validate_block_file(self) -> bool:
        return (
            os.path.exists(self.path)
            and os.path.isfile(self.path)
            and os.access(self.path, os.R_OK)
        )

    def _load_blocks(self):
        if not self._validate_block_file():
            return [], None
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in blocklist file: %s", self.path)
                return [], os.path.getmtime(self.path)
        if not isinstance(data, list):
            return [], os.path.getmtime(self.path)
        nets = self._parse_nets(data)
        return nets, os.path.getmtime(self.path)

    def refresh(self) -> None:
        nets, mtime = self._load_blocks()
        with self._lock:
            self._nets = nets
            self._mtime = mtime

    def _watch(self) -> None:
        while not self._stop_event.is_set():
            try:
                mtime = os.path.getmtime(self.path)
                with self._lock:
                    known = self._mtime
                if known is None or mtime != known:
                    self.refresh()
            except Exception:
                pass
            self._stop_event.wait(self.interval)

    def start_watcher_once(self) -> None:
        if self._thread_started:
            return
        self._thread_started = True
        self._stop_event.clear()
        threading.Thread(target=self._watch, daemon=True).start()

    def stop_watcher(self) -> None:
        self._stop_event.set()

    def is_blocked_request(self, request: HttpRequest) -> bool:
        ip_str = get_client_ip(request)
        if not ip_str:
            return False
        try:
            ip_obj = ip_address(ip_str)
        except Exception:
            return False
        with self._lock:
            nets = tuple(self._nets)
        for net in nets:
            if ip_obj in net:
                return True
        return False


_blocker = IPBlocker(BLOCK_IP_JSON_PATH, interval=5)
_blocker.refresh()


class IPBlockMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        _blocker.start_watcher_once()

    def __call__(self, request: HttpRequest):
        if _blocker.is_blocked_request(request):
            return HttpResponse(
                "Forbidden", status=403, content_type="text/plain; charset=utf-8"
            )
        return self.get_response(request)
