"""
app.manage
Django's command-line utility for administrative tasks.
"""
import os
import sys
from pathlib import Path


def main():
    root_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "DjangoProto8.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
    