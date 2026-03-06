"""
data.config._conf_ Docstring
"""
import os
from datetime import datetime
from ipaddress import ip_network
from pathlib import Path

import pytz

CGK_URL = "https://captcha.glitter.kr"
CHK_URL = "https://cheer.glitter.kr"
E44_URL = "https://404.glitter.kr"
DGK_URL = "https://deny.glitter.kr"
GAT_URL = "https://gate.glitter.kr"
GBZ_URL = "https://glitter.bz"
GIM_URL = "https://glitter.im"
GKR_URL = "https://glitter.kr"
GMY_URL = "https://glitter.my"
GTW_URL = "https://glitter.tw"
MGK_URL = "https://m.glitter.kr"
NGK_URL = "https://new.glitter.kr"
MSG_URL = "https://msg.glitter.kr"
PGK_URL = "https://policy.glitter.kr"
SGK_URL = "https://shield.glitter.kr"
VGK_URL = "https://vlog.glitter.kr"
VGG_URL = "https://vlog.glitter.kr/gate"
WGK_URL = "https://whitepaper.glitter.kr"


ABM_ROT = "album"
APP_ROT = "app"
BIK_ROT = "bike"
BLG_ROT = "blog"
CAM_ROT = "cam"
CF_TITLE = "Glitter Django Proto8"
CFG_ROT = "config"
CSS_ROT = "css"
DAT_ROT = "data"
DJG_ROT = "DjangoProto8"
ENV_ROT = "env"
FAV_ROT = "favicon"
GKR_ROT = "glitterkr"
GLI_ROT = "glitter"
GPX_ROT = "gpx"
HWI_ROT = "hwi"
GIG_ROT = "gimg"
IMG_ROT = "img"
MDA_ROT = "Media"
PLO_ROT = "profiles"
STT_ROT = "static"
TPL_ROT = "templates"
USR_ROT = "user"
VOL_ROT = "volume1"
WEB_ROT = "web"

DOT_ENV = ".env"
CFG_PY_ = "_conf_.py"
FAV_ICO = "favicon.ico"
CMK_PNG = "glitter_circle.png"
WMK_PNG = "glitter_mark.png"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.getenv("APP_RUNTIME_ROOT", str(PROJECT_ROOT / "data" / "runtime")))

APP_URI = f"{PROJECT_ROOT}/{APP_ROT}"
DAT_URI = f"{PROJECT_ROOT}/{DAT_ROT}"
HWI_VDI = f"/{VOL_ROT}/{HWI_ROT}"

CFG_URI = f"{DAT_URI}/{CFG_ROT}"
CFG_VDI = f"{HWI_VDI}/{CFG_ROT}"
ENV_VDI = f"{HWI_VDI}/{CFG_ROT}/{ENV_ROT}/{DJG_ROT}"
FAV_VDI = f"{HWI_VDI}/{GIG_ROT}/{FAV_ROT}"
IMG_VDI = f"{HWI_VDI}/{GIG_ROT}"
MDA_VDI = f"/{VOL_ROT}/{MDA_ROT}"
USR_VDI = f"{DAT_URI}/{USR_ROT}"

APP_ENV_FILE = os.getenv("APP_ENV_FILE", str(Path(ENV_VDI) / DOT_ENV))
FAV_VRO = os.getenv("FAVICON_FILE_PATH", str(Path(FAV_VDI) / FAV_ICO))
CMK_VRO = f"{HWI_VDI}/{GIG_ROT}/{CMK_PNG}"
WMK_VRO = f"{HWI_VDI}/{GIG_ROT}/{WMK_PNG}"

ALBUM_FS_ROOT = f"/{DAT_ROT}/{ABM_ROT}"

SGK_ENV = os.getenv(
    "APP_ENV_FILE",
    str(PROJECT_ROOT / "data" / "config" / "env" / "DjangoProto8" / ".env"),
)

SGK_SEC_TXT = os.getenv("SECURITY_TXT_PATH", str(RUNTIME_ROOT / "security.txt"))
SGK_TXT = os.getenv("ROBOTS_TXT_PATH", str(RUNTIME_ROOT / "robots.txt"))
SGK_XML = os.getenv("SITEMAP_XML_PATH", str(RUNTIME_ROOT / "sitemap.xml"))

BLOCK_IP = os.getenv("BLOCK_IP_JSON_PATH", str(Path(CFG_VDI) / "blocked.json"))

TRUSTED_PROXIES = os.getenv("TRUSTED_PROXIES", "")

TRUSTED_PROXY_NETS = [
    ip_network("127.0.0.1/32"),
    ip_network("::1/128"),
]

INTERNAL_IP_RANGES = [
    ip_network("127.0.0.0/8"),
    ip_network("10.11.12.0/24"),
    ip_network("::1/128"),
]

PRIVATE_OR_LOCAL_NETS = [
    ip_network("0.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("127.0.0.0/8"),
    ip_network("169.254.0.0/16"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("224.0.0.0/4"),
    ip_network("240.0.0.0/4"),
    ip_network("::/128"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
    ip_network("ff00::/8"),
]

TRUSTED_BOTS = [
    "Googlebot", "Bingbot", "Daumoa", "DuckDuckBot",
    "Mediapartners-Google", "Naverbot", "OAI-SearchBot", "YandexBot", "Yeti"
]

tz_seoul = pytz.timezone("Asia/Seoul")
now = datetime.now(tz_seoul)

def get_plugin_state_change_time():
    return datetime.now(tz_seoul).isoformat()

class Settings:
    APP_IS_DEBUG = True

settings = Settings()
cache_plugin_state = {}

ck_url = os.getenv("CGK_URL", CGK_URL)
dk_url = os.getenv("DGK_URL", DGK_URL)
gk_url = os.getenv("GKR_URL", GKR_URL)
nk_url = os.getenv("NGK_URL", NGK_URL)
er_url = os.getenv("E44_URL", E44_URL)
gb_url = os.getenv("GBZ_URL", GBZ_URL)
gi_url = os.getenv("GIM_URL", GIM_URL)
gm_url = os.getenv("GMY_URL", GMY_URL)
gt_url = os.getenv("GTW_URL", GTW_URL)
mg_url = os.getenv("MSG_URL", MSG_URL)
dg_url = os.getenv("DGK_URL", DGK_URL)
pg_url = os.getenv("PGK_URL", PGK_URL)
vg_url = os.getenv("VGK_URL", VGK_URL)
sg_url = os.getenv("SGK_URL", SGK_URL)
vgg_url = os.getenv("VGG_URL", VGG_URL)

gg_tel = os.getenv("GG_TEL", "tel:+8201036125558")
gg_mail = os.getenv("GG_MAIL", "admin@glitter.kr")
gg_mail_to = os.getenv("GG_MAIL_TO", "mailto:admin@glitter.kr")
kakao_js_key = os.getenv("KAKAO_JS_KEY", "KAKAO_JS_KEY")

TEMPLATES_CONTEXT = {
    "site_name": "Glitter Django Proto8",
    "author": "Glitter Gim",
    "hwi_dir": HWI_ROT,
    "cmk_vro": CMK_VRO,
    "fav_url": "/favicon.ico",
    "wmk_vro": WMK_VRO,
    "er_url": er_url,
    "fav_vdi": FAV_VDI,
    "gb_url": gb_url,    
    "gg_tel"    : gg_tel,    
    "gg_mail_to": gg_mail_to,
    "gg_mail": gg_mail,
    "gk_url": gk_url,
    "pg_url"    : pg_url,
    "sg_url"    : sg_url,
    "kakao_js_key": kakao_js_key,
    "vg_url"    : vg_url,        
    "vgg_url"   : vgg_url, 
}
