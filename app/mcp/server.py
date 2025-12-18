"""FastMCP server exposing StudyBuddy's knowledge search as a tool."""

from __future__ import annotations

import argparse
from typing import Any, Dict, Optional
from pathlib import Path

from mcp.server.fastmcp import FastMCP

if __package__ is None or __package__ == "":
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.append(str(repo_root))
    from app.agents.chat_agent import (  # type: ignore[import-not-found]
        ReferenceType,
        retrieve_documents,
    )
else:
    from ..agents.chat_agent import (
        ReferenceType,
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
    owner_id: Optional[str] = None,
    course_id: Optional[str] = None,
    document_id: Optional[str] = None,
    lecture_id: Optional[str] = None,
    max_results: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return slide + lecture chunks relevant to a course-related query."""

    if not owner_id:
        return {
            "query": query,
            "results": [],
            "count": 0,
            "error": "owner_id is required",
        }

    results: list[ReferenceType] = retrieve_documents(
        query=query,
        num_documents=max_results,
        filters=filters,
        owner_id=owner_id,
        course_id=course_id,
        document_id=document_id,
        lecture_id=lecture_id,
    )
    return {
        "query": query,
        "results": results,
        "count": len(results),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the CourseMaterialRAG MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport type; use streamable-http when exposing over HTTP",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host when using streamable-http")
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP port when using streamable-http",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)
