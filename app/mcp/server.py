"""FastMCP server exposing StudyBuddy's knowledge search as a tool."""

from __future__ import annotations

from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from ..agents.chat_agent import (
    ReferenceType,
    _TEST_COURSE_ID,
    _TEST_OWNER_ID,
    retrieve_documents,
)


mcp = FastMCP(
    name="CourseMaterialRAG",
    instructions=(
        "Retrieve relevant course material (slides + lecture chunks) given a query."
    ),
)


@mcp.tool()
def retrieve_course_material(
    query: str,
    *,
    owner_id: Optional[str] = _TEST_OWNER_ID,
    course_id: Optional[str] = _TEST_COURSE_ID,
    document_id: Optional[str] = None,
    lecture_id: Optional[str] = None,
    max_results: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return slide + lecture chunks relevant to a course-related query."""

    results: list[ReferenceType] = retrieve_documents(
        query=query,
        num_documents=max_results,
        filters=filters,
        owner_id=owner_id,
        course_id=course_id,
        document_id=document_id,
        lecture_id=lecture_id,
        use_test_defaults=True,
    )
    return {
        "query": query,
        "results": results,
        "count": len(results),
    }


if __name__ == "__main__":
    mcp.run()
