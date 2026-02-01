#!/usr/bin/env python3
"""Run Agno database migrations to create required tables."""

import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agno.db.migrations.manager import MigrationManager
from agno.db.postgres import PostgresDb

from app.core.config import settings


def run_migrations():
    """Run all Agno migrations."""
    # Convert psycopg2 URL format to psycopg3 format required by Agno
    db_url = settings.database_url
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    print(f"Connecting to database...")
    db = PostgresDb(
        db_url=db_url,
        session_table="agno_sessions",
    )

    print("Running Agno migrations...")
    MigrationManager(db).up()
    print("Migrations complete!")


if __name__ == "__main__":
    run_migrations()
