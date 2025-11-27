from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent.parent
for env_name in (".env.local", ".env"):
    env_path = _BASE_DIR / env_name
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


@dataclass
class Settings:
    """Runtime configuration sourced from environment variables."""

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/studybuddy",
    )
    storage_root: Path = Path(os.getenv("STORAGE_ROOT", "storage"))
    documents_prefix: str = os.getenv("DOCUMENTS_STORAGE_PREFIX", "documents")
    audio_tmp_prefix: str = os.getenv("AUDIO_TEMP_STORAGE_PREFIX", "audio_tmp")
    clerk_secret_key: Optional[str] = os.getenv("CLERK_SECRET_KEY")
    clerk_authorized_parties: Tuple[str, ...] = field(
        default_factory=lambda: tuple(
            party.strip()
            for party in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
            if party.strip()
        )
    )
    cors_allow_origins: Tuple[str, ...] = field(
        default_factory=lambda: tuple(
            origin.strip()
            for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
            if origin.strip()
        )
    )
    dev_routes_enabled: bool = os.getenv("DEV_ROUTES_ENABLED", "false").lower() in {"1", "true", "yes"}


settings = Settings()
