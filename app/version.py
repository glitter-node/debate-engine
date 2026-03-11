from pathlib import Path

def get_app_version():
    version_file = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        return version_file.read_text().strip()
    except FileNotFoundError:
        return "0.0.0"

