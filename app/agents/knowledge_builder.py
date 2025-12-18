from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional

from agno.knowledge import Knowledge
from agno.knowledge.embedder.voyageai import VoyageAIEmbedder
from agno.vectordb.pgvector import PgVector
from sqlalchemy import create_engine, text

from ..core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_slide_knowledge() -> Optional[Knowledge]:
    """Build (and cache) the Knowledge instance for slide chunks."""

    return _build_knowledge(
        table_name=settings.slide_knowledge_table,
        description="slide chunks knowledge",
    )


@lru_cache(maxsize=1)
def get_lecture_knowledge() -> Optional[Knowledge]:
    """Build (and cache) the Knowledge instance for lecture transcripts/audio."""

    return _build_knowledge(
        table_name=settings.lecture_knowledge_table,
        description="lecture knowledge",
    )


def _build_knowledge(*, table_name: str, description: str) -> Optional[Knowledge]:
    if not settings.voyage_api_key:
        logger.info("VOYAGE_API_KEY not configured; skipping %s initialization", description)
        return None

    _ensure_schema_exists(settings.knowledge_schema)
    embedder = VoyageAIEmbedder(
        id=settings.voyage_model_id,
        dimensions=settings.voyage_dimensions,
        api_key=settings.voyage_api_key,
    )
    return Knowledge(
        vector_db=PgVector(
            table_name=table_name,
            schema=settings.knowledge_schema,
            db_url=settings.database_url,
            embedder=embedder,
            auto_upgrade_schema=True,
        )
    )


@lru_cache(maxsize=1)
def _ensure_schema_exists(schema_name: str) -> None:
    # Validate schema name format (alphanumeric and underscore only)
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema_name):
        raise ValueError(f"Invalid schema name: {schema_name}")

    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        # Safe after validation - PostgreSQL identifiers can't be parameterized
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
