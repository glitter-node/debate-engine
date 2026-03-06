"""
app.DjangoProto8.wsgi
WSGI config for DjangoProto8 project. It exposes the WSGI callable as a module-level variable named ``application``.
For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/"""

import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "DjangoProto8.settings")

application = get_wsgi_application()
