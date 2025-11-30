from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Union
from uuid import UUID

from agno.agent import Agent
from agno.filters import FilterExpr
from agno.knowledge import Knowledge
from agno.knowledge.document import Document
from agno.models.xai import xAI

from .knowledge_builder import get_lecture_knowledge, get_slide_knowledge

logger = logging.getLogger(__name__)

KnowledgeFactory = Callable[[], Optional[Knowledge]]
FilterType = Union[Dict[str, Any], Sequence[FilterExpr], None]

DEFAULT_INSTRUCTIONS = """
You are StudyBuddy's course companion. Answer questions using the student's
lecture transcripts and slide decks. When the question references class materials, search
the knowledge base before responding and cite the relevant lecture chunk or slide.
""".strip()


def _merge_filters(filters: FilterType, extra_filters: Optional[Dict[str, str]]) -> FilterType:
    """Merge caller provided filters with context derived identifiers."""

    if not extra_filters:
        return filters

    if filters is None:
        return extra_filters

    if isinstance(filters, dict):
        merged = dict(filters)
        merged.update(extra_filters)
        return merged

    # Filter expressions are already validated elsewhere; leave untouched.
    return filters


def _strip_dict_key(filters: FilterType, key: str) -> FilterType:
    if isinstance(filters, dict) and key in filters:
        cleaned = dict(filters)
        cleaned.pop(key, None)
        return cleaned
    return filters


def _stringify(value: Union[str, UUID]) -> str:
    return str(value)


def _search_knowledge(
    factory: KnowledgeFactory,
    query: str,
    *,
    max_results: int,
    filters: FilterType,
) -> List[Document]:
    knowledge = factory()
    if knowledge is None:
        logger.debug("Knowledge factory %s returned None; skipping search", factory.__name__)
        return []

    try:
        results = knowledge.search(query=query, max_results=max_results, filters=filters) or []
        logger.debug("Retrieved %s documents from %s", len(results), factory.__name__)
        return list(results)
    except Exception:
        logger.exception("Knowledge search failed for %s", factory.__name__)
        return []


def custom_retriever(
    agent: Agent,
    query: str,
    num_documents: int = 5,
    *,
    filters: FilterType = None,
    owner_id: Union[str, UUID, None] = None,
    course_id: Union[str, UUID, None] = None,
    document_id: Union[str, UUID, None] = None,
    lecture_id: Union[str, UUID, None] = None,
    **kwargs: Any,
) -> Optional[List[Document]]:
    """
    Custom retriever that queries slide knowledge first and lecture knowledge second.

    The caller can optionally provide course/owner identifiers which we merge into
    the metadata filters so the vector search only touches documents the user owns.
    """

    slide_extra = {
        key: value
        for key, value in {
            "owner_id": _stringify(owner_id or agent.user_id) if (owner_id or agent.user_id) else None,
            "course_id": _stringify(course_id) if course_id else None,
            "document_id": _stringify(document_id) if document_id else None,
        }.items()
        if value is not None
    }
    lecture_extra = {
        key: value
        for key, value in {
            "course_id": _stringify(course_id) if course_id else None,
            "lecture_id": _stringify(lecture_id) if lecture_id else None,
        }.items()
        if value is not None
    }

    slide_filters = _merge_filters(filters, slide_extra)
    lecture_filters = _merge_filters(_strip_dict_key(filters, "owner_id"), lecture_extra)

    slide_docs = _search_knowledge(
        get_slide_knowledge,
        query,
        max_results=num_documents,
        filters=slide_filters,
    )
    lecture_docs = _search_knowledge(
        get_lecture_knowledge,
        query,
        max_results=num_documents,
        filters=lecture_filters,
    )

    combined = slide_docs + lecture_docs
    return combined or None


def create_chat_agent(*, instructions: Optional[str] = None) -> Agent:
    """Instantiate the Grok-powered StudyBuddy chat agent with custom retrieval."""

    return Agent(
        name="StudyBuddyChatAgent",
        model=xAI(id="grok-4.1-fast"),
        instructions=instructions or DEFAULT_INSTRUCTIONS,
        knowledge_retriever=custom_retriever,
        search_knowledge=True,
        markdown=True,
    )


__all__ = ["create_chat_agent", "custom_retriever"]
