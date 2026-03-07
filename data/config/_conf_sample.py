"""
data.config._conf_sample
"""

FAV_VRO = "/volume1/DjangoFrame/DjangoProto8/app/staticfiles/img/favicon.ico"
SGK_TXT = "/volume1/DjangoFrame/DjangoProto8/data/runtime/robots.txt"
SGK_XML = "/volume1/DjangoFrame/DjangoProto8/data/runtime/sitemap.xml"
BLOCK_IP = "/volume1/hwi/config/blocked.json"
TRUSTED_PROXIES = ""

TRUSTED_PROXY_NETS = [
    "127.0.0.1/32",
    "::1/128",
]

INTERNAL_IP_RANGES = [
    "127.0.0.0/8",
    "10.11.12.0/24",
    "::1/128",
]

TRUSTED_BOTS = [
    "Googlebot",
    "Bingbot",
    "Daumoa",
    "DuckDuckBot",
    "Mediapartners-Google",
    "Naverbot",
    "OAI-SearchBot",
    "YandexBot",
    "Yeti",
]

TEMPLATES_CONTEXT = {
    "fav_url": "/favicon.ico",
}
