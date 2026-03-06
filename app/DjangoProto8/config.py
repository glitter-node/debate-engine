from __future__ import annotations

import os
from pathlib import Path

TRUE_VALUES = {"1", "true", "yes", "on"}

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

REQUIRED_ENV_KEYS = ("SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD")
ENV_KEY_ALIASES = {
    "SECRET_KEY": ("SECRET_KEY", "DJANGO_SECRET_KEY"),
    "DB_NAME": ("DB_NAME", "APP_DB_NAME"),
    "DB_USER": ("DB_USER", "APP_DB_USER"),
    "DB_PASSWORD": ("DB_PASSWORD", "APP_DB_PASSWORD"),
}


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_dotenv() -> None:
    env_file = (os.environ.get("APP_ENV_FILE") or "").strip()
    if not env_file:
        raise RuntimeError("APP_ENV_FILE must be set.")

    candidate = Path(env_file)
    if not candidate.exists():
        raise RuntimeError(f"Env file not found: {candidate}")

    with candidate.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, value = s.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

    missing = []
    for canonical_key in REQUIRED_ENV_KEYS:
        aliases = ENV_KEY_ALIASES[canonical_key]
        resolved_value = ""
        for alias in aliases:
            value = (os.environ.get(alias) or "").strip()
            if value:
                resolved_value = value
                break
        if not resolved_value:
            missing.append(canonical_key)
            continue
        for alias in aliases:
            os.environ.setdefault(alias, resolved_value)

    if missing:
        raise RuntimeError(
            "Missing required environment keys after loading APP_ENV_FILE: "
            + ", ".join(missing)
        )


def env_path(name: str, default: Path) -> str:
    return os.environ.get(name, str(default))
