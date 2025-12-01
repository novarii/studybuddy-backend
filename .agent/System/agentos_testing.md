# AgentOS & Chat Agent Testing

## Purpose
With the StudyBuddy chat agent now wired into Agno AgentOS, you can test the entire retrieval workflow (slides + lectures) without building the frontend. AgentOS ships with a self-hosted web UI plus REST endpoints for running agents.

## Requirements
- `VOYAGE_API_KEY`, lecture/slide knowledge tables, and storage paths configured as described in the other system docs.
- `GOOGLE_API_KEY` (or whichever Gemini credential you use) exported so the chat agent can reach `gemini-2.5-flash-lite`.
- `uv` / `uvicorn` or `fastapi` CLI for running the FastAPI app locally.

## Running Locally
1. Start Postgres + pgvector if you haven’t already (`docker compose up db`).
2. Launch the app with hot reload:
   ```bash
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   AgentOS hooks into the existing FastAPI instance automatically (see `app/main.py`).
3. Open `http://localhost:8000/` — the AgentOS Control Plane loads with the StudyBuddy agent listed. Use the built-in Chat page to run prompts and inspect knowledge hits.

## Useful Endpoints
- `/agents`, `/teams`, `/config` — AgentOS APIs for listing what’s registered.
- `/chat` — Web UI backed by AgentOS; supports selecting the StudyBuddy agent and issuing prompts.
- `/knowledge` — Browse slide/lecture chunks and verify metadata filters.

## Notes
- The chat agent relies on `custom_retriever` (see `app/agents/chat_agent.py`) so metadata filters continue to enforce user/course scoping when you query knowledge through AgentOS.
- When running behind dev auth or tunnels, ensure your CORS settings allow the AgentOS origins (handled automatically by `AgentOS(base_app=app)` but double-check if you add custom middleware).
- To disable AgentOS temporarily, comment out the construction at the bottom of `app/main.py` and restart the server.
