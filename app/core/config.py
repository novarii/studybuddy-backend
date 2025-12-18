from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple
from uuid import UUID

from dotenv import load_dotenv


def _optional_int_env(var_name: str) -> Optional[int]:
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return None
    raw_value = raw_value.strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def _int_env(var_name: str, default: int) -> int:
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return default
    raw_value = raw_value.strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


# Resolve repo root so .env files remain discoverable after moving this module to app/core.
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
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
    direct_stream_required: bool = os.getenv("DIRECT_STREAM_REQUIRED", "true").lower() in {"1", "true", "yes"}
    admin_user_ids: Tuple[UUID, ...] = field(
        default_factory=lambda: tuple(
            UUID(raw.strip())
            for raw in os.getenv("ADMIN_USER_IDS", "").split(",")
            if raw.strip()
        )
    )
    openrouter_api_key: Optional[str] = os.getenv("OPENROUTER_API_KEY")
    whisper_server_ip: Optional[str] = os.getenv("WHISPER_SERVER_IP")
    whisper_server_port: Optional[int] = _optional_int_env("WHISPER_SERVER_PORT")
    whisper_request_timeout_seconds: int = _int_env("WHISPER_REQUEST_TIMEOUT_SECONDS", 180)
    whisper_poll_interval_seconds: int = _int_env("WHISPER_POLL_INTERVAL_SECONDS", 5)
    whisper_poll_timeout_seconds: int = _int_env("WHISPER_POLL_TIMEOUT_SECONDS", 600)
    voyage_api_key: Optional[str] = os.getenv("VOYAGE_API_KEY")
    knowledge_schema: str = os.getenv("KNOWLEDGE_VECTOR_SCHEMA", "ai")
    slide_knowledge_table: str = os.getenv("SLIDE_KNOWLEDGE_TABLE", "slide_chunks_knowledge")
    lecture_knowledge_table: str = os.getenv("LECTURE_KNOWLEDGE_TABLE", "lecture_chunks_knowledge")
    voyage_model_id: str = os.getenv("VOYAGE_MODEL_ID", "voyage-3-lite")
    voyage_dimensions: int = _int_env("VOYAGE_EMBED_DIMENSIONS", 512)
    # Test IDs for AgentOS playground testing
    test_course_id: Optional[str] = os.getenv("TEST_COURSE_ID")
    test_owner_id: Optional[str] = os.getenv("TEST_OWNER_ID")


settings = Settings()
