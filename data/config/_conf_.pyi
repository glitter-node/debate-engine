"""
data.config._conf_ Docstring
"""

from __future__ import annotations

from ipaddress import IPv4Network, IPv6Network

Network = IPv4Network | IPv6Network

FAV_VRO: str
SGK_TXT: str
SGK_XML: str
BLOCK_IP: str
TRUSTED_PROXIES: str

TRUSTED_PROXY_NETS: list[Network]
INTERNAL_IP_RANGES: list[Network]
TRUSTED_BOTS: list[str]

TEMPLATES_CONTEXT: dict[str, str]
