from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Generator, List, Optional, Sequence, Union
from uuid import UUID

from agno.agent import Agent
from agno.db.postgres import PostgresDb
from agno.filters import FilterExpr
from agno.knowledge import Knowledge
from agno.knowledge.document import Document
from agno.models.google import Gemini
from agno.models.openrouter import OpenRouter
from agno.run.agent import CustomEvent
from agno.tools import tool

from ..core.config import settings
from .context_formatter import format_retrieval_context
from .knowledge_builder import get_lecture_knowledge, get_slide_knowledge


def create_rag_sources_event(sources: List[Dict[str, Any]]) -> CustomEvent:
    """Create a custom event for streaming RAG sources metadata to frontend."""
    import time
    event = CustomEvent(
        sources=sources,
        created_at=int(time.time_ns()),
        event="rag_sources",
        agent_id="",
        agent_name="",
    )
    return event

logger = logging.getLogger(__name__)

# Singleton instance for Agno database connection
_agno_db: Optional[PostgresDb] = None


def get_agno_db() -> PostgresDb:
    """Get or create the shared Agno database instance for session persistence."""
    global _agno_db
    if _agno_db is None:
        # Convert psycopg2 URL format to psycopg3 format required by Agno
        db_url = settings.database_url
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

        _agno_db = PostgresDb(
            db_url=db_url,
            db_schema="ai",  # Agno tables are in the 'ai' schema
            session_table="agno_sessions",
        )
    return _agno_db


KnowledgeFactory = Callable[[], Optional[Knowledge]]
FilterType = Union[Dict[str, Any], Sequence[FilterExpr], None]
ReferenceType = Union[Dict[str, Any], str]

DEFAULT_INSTRUCTIONS = """
You are StudyBuddy, a friendly course companion that helps students understand their lecture materials.

You have access to the `search_course_materials` tool to search the student's lecture transcripts and slide decks. Use it when the student asks questions about course content, concepts, or needs help studying.
References are indexed 1-10, with 1 - 5 from slide decks and 6 - 10 from lecture transcripts, with no priority given to either source. 
DO NOT concatenate multiple references into one citation (e.g. [1,2]). ALWAYS cite each source separately (e.g [1][2]).

WHEN TO USE THE SEARCH TOOL:
- Questions about course content, concepts, or topics covered in lectures/slides
- Requests to explain or clarify material from class
- Study help or review questions
- When the student references specific lectures or slides

WHEN NOT TO USE THE SEARCH TOOL:
- Casual conversation, greetings, or thank-yous
- General knowledge questions unrelated to the course
- Follow-up questions where you already have the relevant context
- Clarifying questions about your previous response

WHEN CITING COURSE MATERIALS:
- Cite reference numbers in brackets after claims: "The mitochondria is the powerhouse of the cell [2]."
- You may cite multiple sources: "This concept appears in both the slides [1] and lecture [3]."
- Point students to specific slides or lecture segments worth reviewing.
- If the student asks "What did my lecturer/teacher say about X?", prioritize lecture transcripts.
- If the search returns no relevant information, say so and help however you can.
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


def retrieve_documents(
    *,
    query: str,
    num_documents: int = 5,
    filters: FilterType = None,
    owner_id: Union[str, UUID, None] = None,
    course_id: Union[str, UUID, None] = None,
    document_id: Union[str, UUID, None] = None,
    lecture_id: Union[str, UUID, None] = None,
) -> List[ReferenceType]:
    slide_extra = {
        key: value
        for key, value in {
            "owner_id": _stringify(owner_id) if owner_id else None,
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

    import os
    if os.getenv("DEBUG_SEARCH_TOOL", "").lower() in ("1", "true", "yes"):
        print(f"[RETRIEVE] Slides found: {len(slide_docs)}, Lectures found: {len(lecture_docs)}")

    combined = slide_docs + lecture_docs
    references: list[ReferenceType] = []
    for doc in combined:
        if isinstance(doc, Document):
            reference: dict[str, Any] = {"content": doc.content}
            if doc.name:
                reference["name"] = doc.name
            if doc.meta_data:
                reference["metadata"] = doc.meta_data
            references.append(reference)
        else:
            references.append(doc)
    return references


def create_search_tool(
    owner_id: Union[str, UUID],
    course_id: Union[str, UUID],
    document_id: Union[str, UUID, None] = None,
    lecture_id: Union[str, UUID, None] = None,
):
    """
    Create a search_course_materials tool with the given context baked in.

    The tool is created at request time with the user's owner_id and course_id
    so the agent can search without needing to know these identifiers.

    The tool yields a RAGSourcesEvent with client_sources for the frontend,
    then returns model_context for the LLM.
    """

    @tool(
        name="search_course_materials",
        description="Search the student's course materials (lecture transcripts and slide decks) for information relevant to their question.",
    )
    def search_course_materials(query: str) -> Generator[CustomEvent, None, str]:
        """
        Search course materials for relevant information.

        Args:
            query: The search query describing what information to find.

        Yields:
            CustomEvent with sources for frontend display.

        Returns:
            Formatted references from lectures and slides, numbered for citation.
        """
        import os
        debug = os.getenv("DEBUG_SEARCH_TOOL", "").lower() in ("1", "true", "yes")

        if debug:
            print("=" * 60)
            print(f"[SEARCH TOOL] Query: {query}")
            print(f"[SEARCH TOOL] Context: owner_id={owner_id}, course_id={course_id}")

        raw_references = retrieve_documents(
            query=query,
            num_documents=5,
            owner_id=owner_id,
            course_id=course_id,
            document_id=document_id,
            lecture_id=lecture_id,
        )

        if debug:
            print(f"[SEARCH TOOL] Raw references count: {len(raw_references)}")
            for i, ref in enumerate(raw_references):
                if isinstance(ref, dict):
                    meta = ref.get("metadata", {})
                    content_preview = ref.get("content", "")[:100]
                    print(f"[SEARCH TOOL] Ref {i+1}: metadata={meta}")
                    print(f"[SEARCH TOOL] Ref {i+1}: content_preview={content_preview}...")
                else:
                    print(f"[SEARCH TOOL] Ref {i+1}: {str(ref)[:100]}...")

        if not raw_references:
            if debug:
                print("[SEARCH TOOL] No references found, returning empty message")
            return "No relevant course materials found for this query."

        formatted = format_retrieval_context(raw_references, order_by="chronological")

        if debug:
            print(f"[SEARCH TOOL] Client sources count: {len(formatted.client_sources)}")
            for i, src in enumerate(formatted.client_sources):
                print(f"[SEARCH TOOL] Source {i+1}: {src}")
            print(f"[SEARCH TOOL] Model context:\n{formatted.model_context}")
            print("=" * 60)

        # Yield custom event with sources for frontend before returning model context
        if formatted.client_sources:
            yield create_rag_sources_event(formatted.client_sources)

        return formatted.model_context or "No relevant course materials found for this query."

    return search_course_materials


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
) -> Optional[List[ReferenceType]]:
    """
    Custom retriever that queries slide knowledge first and lecture knowledge second.

    The caller can optionally provide course/owner identifiers which we merge into
    the metadata filters so the vector search only touches documents the user owns.

    Note: This is kept for backwards compatibility but the preferred approach is
    to use create_search_tool() which gives the agent control over when to search.
    """

    owner_lookup = owner_id or agent.user_id
    course_lookup = course_id
    if not owner_lookup:
        logger.warning("No owner context provided for retrieval")
        return []

    references = retrieve_documents(
        query=query,
        num_documents=num_documents,
        filters=filters,
        owner_id=owner_lookup,
        course_id=course_lookup,
        document_id=document_id,
        lecture_id=lecture_id,
    )
    return references or None


def create_chat_agent(
    *,
    instructions: Optional[str] = None,
    db: Optional[PostgresDb] = None,
    tools: Optional[List[Any]] = None,
) -> Agent:
    """Instantiate the StudyBuddy chat agent.

    Args:
        instructions: Optional custom instructions for the agent.
        db: Optional PostgresDb instance for session persistence.
            When provided, enables multi-turn conversation history.
        tools: Optional list of tools to give the agent. Use create_search_tool()
            to create a context-aware search tool for the current request.
    """

    if settings.openrouter_api_key:
        model = OpenRouter(id="google/gemini-3-flash-preview", api_key=settings.openrouter_api_key)
    else:
        model = Gemini(id="gemini-2.5-pro")

    return Agent(
        name="StudyBuddyChatAgent",
        model=model,
        instructions=instructions or DEFAULT_INSTRUCTIONS,
        tools=tools or [],
        # Session persistence (enabled when db is provided)
        db=db,
        add_history_to_context=db is not None,  # Include previous messages in context
        num_history_runs=10,  # Number of previous turns to include
        markdown=True,
    )


def _test_retriever(
    agent: Agent,
    query: str,
    num_documents: int = 5,
    **kwargs: Any,
) -> Optional[List[ReferenceType]]:
    """Test retriever using TEST_COURSE_ID and TEST_OWNER_ID from env vars."""
    from .context_formatter import format_retrieval_context

    if not settings.test_course_id or not settings.test_owner_id:
        logger.warning("TEST_COURSE_ID and TEST_OWNER_ID must be set in env for test retriever")
        return []

    raw_refs = retrieve_documents(
        query=query,
        num_documents=num_documents,
        owner_id=settings.test_owner_id,
        course_id=settings.test_course_id,
    )
    # Return formatted context as a single "reference" for the model
    formatted = format_retrieval_context(raw_refs, order_by="chronological")
    if formatted.model_context:
        return [{"content": formatted.model_context}]
    return []


def create_test_chat_agent() -> Agent:
    """Create a test agent for AgentOS with hardcoded course_id."""
    if settings.openrouter_api_key:
        model = OpenRouter(id="x-ai/grok-4.1-fast", api_key=settings.openrouter_api_key)
    else:
        model = Gemini(id="gemini-2.5-pro")

    return Agent(
        name="StudyBuddyTestAgent",
        model=model,
        instructions=DEFAULT_INSTRUCTIONS,
        knowledge_retriever=_test_retriever,
        search_knowledge=True,
        add_knowledge_to_context=True,
        markdown=True,
    )


__all__ = ["create_chat_agent", "create_search_tool", "custom_retriever", "create_test_chat_agent", "get_agno_db", "retrieve_documents"]
