"""
data.config._conf_ Docstring
"""
from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Network, IPv6Network
from typing import Any, Callable, Iterable, Mapping, MutableMapping

Network = IPv4Network | IPv6Network

CGK_URL: str
CHK_URL: str
E44_URL: str
DGK_URL: str
GAT_URL: str
GBZ_URL: str
GIM_URL: str
GKR_URL: str
GMY_URL: str
GTW_URL: str
MGK_URL: str
NGK_URL: str
MSG_URL: str
PGK_URL: str
VGK_URL: str
VGG_URL: str
WGK_URL: str

SGK_ENV: str

SGK_SEC_TXT: str
SGK_TXT: str
SGK_XML: str

BLOCK_IP: str
TRUSTED_PROXIES: str

TRUSTED_PROXY_NETS: list[Network]
INTERNAL_IP_RANGES: list[Network]
PRIVATE_OR_LOCAL_NETS: list[Network]

TRUSTED_BOTS: list[str]

tz_seoul: Any
now: datetime

def get_plugin_state_change_time() -> str: ...

class Settings:
    APP_IS_DEBUG: bool

settings: Settings
cache_plugin_state: MutableMapping[str, Any]

ck_url: str
dk_url: str
gk_url: str
nk_url: str
er_url: str
gb_url: str
gi_url: str
gm_url: str
gt_url: str
mg_url: str
dg_url: str
pg_url: str
vg_url: str
vgg_url: str

gg_tel: str
gg_mail: str
gg_mail_to: str
kakao_js_key: str

ABM_ROT: str
APP_ROT: str
BIK_ROT: str
BLG_ROT: str
CAM_ROT: str
CF_TITLE: str
CFG_ROT: str
CSS_ROT: str
DAT_ROT: str
FAV_ROT: str
GKR_ROT: str
GLI_ROT: str
GPX_ROT: str
HWI_ROT: str
GIG_ROT: str
IMG_ROT: str
MDA_ROT: str
PLO_ROT: str
STT_ROT: str
TPL_ROT: str
USR_ROT: str
VOL_ROT: str
WEB_ROT: str

CFG_PY_: str
FAV_ICO: str
CMK_PNG: str
WMK_PNG: str

APP_URI: str
WEB_VDI: str
DAT_URI: str
HWI_VDI: str

CFG_URI: str
FAV_VDI: str
IMG_VDI: str
MDA_VDI: str
STT_VDI: str
TPL_VDI: str
USR_VDI: str

FAV_VRO: str
CMK_VRO: str
WMK_VRO: str

ALBUM_FS_ROOT: str

TEMPLATES_CONTEXT: dict[str, Any]
