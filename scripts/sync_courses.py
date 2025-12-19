#!/usr/bin/env python3
"""Sync courses from CDCS catalog to database.

Usage:
    python scripts/sync_courses.py                     # Sync Fall 2025 + Spring 2025
    python scripts/sync_courses.py --terms "Fall 2025" # Sync specific term
    python scripts/sync_courses.py --dry-run           # Preview changes without committing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.db import SessionLocal
from app.services.course_sync_service import CourseSyncService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync courses from University of Rochester CDCS catalog to database."
    )
    parser.add_argument(
        "--terms",
        nargs="+",
        default=["Fall 2025", "Spring 2025"],
        help="Terms to sync (default: Fall 2025, Spring 2025)",
    )
    parser.add_argument(
        "--type",
        default="Lecture",
        help="Course type to sync (default: Lecture)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without committing to database",
    )
    args = parser.parse_args()

    print(f"Syncing courses from CDCS...")
    print(f"  Terms: {', '.join(args.terms)}")
    print(f"  Type: {args.type}")
    print(f"  Dry run: {args.dry_run}")
    print()

    service = CourseSyncService()

    with SessionLocal() as db:
        try:
            result = service.sync_courses(
                db,
                terms=args.terms,
                course_type=args.type,
                dry_run=args.dry_run,
            )
        except Exception as e:
            print(f"Error: {e}")
            return 1

    print("Sync complete!")
    print(f"  Created: {result.created}")
    print(f"  Updated: {result.updated}")
    print(f"  Unchanged: {result.unchanged}")
    print(f"  Deleted: {result.deleted}")
    print(f"  Total unique courses: {result.total}")

    if result.deletion_skipped:
        print("\n  WARNING: Stale course deletion was skipped!")
        print("  Scraped count was too low compared to existing courses.")
        print("  This may indicate a CDCS issue. Check manually if needed.")

    if args.dry_run:
        print("\n(Dry run - no changes were committed)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
