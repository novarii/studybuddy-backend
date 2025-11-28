# Documentation Index

This folder hosts all knowledge required to work on StudyBuddy Backend. Start here to find the right reference.

## System Docs
- [System/project_architecture.md](System/project_architecture.md) — Overall project goal, architecture, tech stack, API surface, and core flows.
- [System/database_schema.md](System/database_schema.md) — Detailed PostgreSQL schema, enums, constraints, and lifecycle notes.

## Tasks
- `.agent/Tasks/` — Product requirements & implementation plans per feature (see file names for scope).

## SOPs
- `.agent/SOP/` — Step-by-step guides for common workflows (add new entries as needed).

## Utilities
- `scripts/run_migrations.sh` — Apply all SQL migrations sequentially (run after `docker compose up -d db`).

Keep this index updated whenever new documentation is added or reorganized.
