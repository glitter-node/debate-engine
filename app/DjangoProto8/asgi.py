"""
app.DjangoProto8.asgi
ASGI config for DjangoProto8 project. It exposes the ASGI callable as a module-level variable named ``application``.
For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os
import sys
from pathlib import Path

from django.core.asgi import get_asgi_application

root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "DjangoProto8.settings")

application = get_asgi_application()
