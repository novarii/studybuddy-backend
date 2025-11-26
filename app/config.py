from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


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


settings = Settings()
