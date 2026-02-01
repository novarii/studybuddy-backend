**Status:** Accepted

# MCP Knowledge Server

## Purpose
Expose StudyBuddy's custom knowledge retriever over the Model Context Protocol (MCP) so other clients (Claude Desktop, Cursor MCP console, etc.) can query slide + lecture embeddings without going through the FastAPI chat endpoint.

## Implementation
- **Module**: `app/mcp/server.py`
  - Uses `FastMCP` from the official MCP Python SDK.
  - Registers the `retrieve_course_material` tool, which simply calls `app.agents.chat_agent.retrieve_documents` to reuse the exact logic as the chat agent (still defaults to the temporary owner/course IDs until a real client passes authenticated context).
  - Returns JSON with the original query, hit count, and a `results` array containing `{content, name?, metadata?}` objects ready for LLM references.
- **Shared logic**: `retrieve_documents` was extracted inside `app/agents/chat_agent.py` so both the AgentOS chat layer and MCP server stay in sync.

## Running the Server
1. Activate your env (`uv run` or `.venv`).
2. Launch the MCP server over stdio (Claude Desktop-compatible):
   ```bash
   uv run mcp run app/mcp/server.py
   ```
   - Use `uv run mcp install app/mcp/server.py --name StudyBuddy` to register it with Claude Desktop.
3. For HTTP transport (helpful for local testing or MCP-aware IDEs):
   ```bash
   uv run python app/mcp/server.py --transport streamable-http --host 0.0.0.0 --port 9000
   ```
   The CLI flags configure the FastMCP server’s host/port before calling `mcp.run`.

## Tool Parameters
- `query` *(required)* — free-form search string.
- `owner_id`, `course_id`, `document_id`, `lecture_id` *(optional)* — metadata filters. `owner_id` / `course_id` default to the temporary dev IDs so you get usable results even when clients don't send context.
- `filters` — extra metadata dict merged into the vector filters.
- `max_results` — default 5 hits from slides + 5 from lectures.

## Next Steps
- Replace the fallback owner/course defaults once the frontend (or MCP client) can pass authenticated context.
- Consider exposing additional tools (e.g., transcript summaries, lecture listing) via the same server if needed.
