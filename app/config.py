from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


settings = Settings()
