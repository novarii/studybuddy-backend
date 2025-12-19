"""Service for syncing courses from CDCS catalog."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import Course


@dataclass
class SyncResult:
    """Result of a course sync operation."""

    created: int
    updated: int
    unchanged: int
    deleted: int
    total: int
    terms: list[str]
    deletion_skipped: bool = False  # True if deletion was skipped due to safeguard


class CourseSyncService:
    """Fetches courses from CDCS XML endpoint and syncs to database."""

    CDCS_BASE_URL = "https://cdcs.ur.rochester.edu/XMLQuery.aspx"
    DEFAULT_TERMS = ["Fall 2025", "Spring 2025"]
    REQUEST_TIMEOUT = 60  # seconds
    # Safety threshold: only delete stale courses if scraped count >= 80% of existing
    DELETION_SAFETY_THRESHOLD = 0.8

    def __init__(self) -> None:
        pass

    def fetch_courses_from_cdcs(
        self,
        term: str,
        course_type: str = "Lecture",
    ) -> list[dict]:
        """
        Fetch courses from CDCS XML endpoint for a given term.

        Args:
            term: Term string like "Fall 2025" or "Spring 2025"
            course_type: Course type filter (default: "Lecture")

        Returns:
            List of dicts with 'code' and 'title' keys
        """
        url = f"{self.CDCS_BASE_URL}?id=XML&term={quote(term)}&type={quote(course_type)}"

        response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
        response.raise_for_status()

        root = ET.fromstring(response.content)

        courses = []
        for course_elem in root.findall("course"):
            cn_elem = course_elem.find("cn")
            title_elem = course_elem.find("title")

            if cn_elem is None or cn_elem.text is None:
                continue
            if title_elem is None or title_elem.text is None:
                continue

            # Strip section/program suffix:
            # - Simple sections: "ACC 201-1" -> "ACC 201", "CSC 160-01" -> "CSC 160"
            # - Program sections: "ACC 401-FA.MB" -> "ACC 401", "ACC 501-SP.PH" -> "ACC 501"
            raw_code = cn_elem.text.strip()
            code = re.sub(r"-[A-Za-z0-9.]+$", "", raw_code)

            title = title_elem.text.strip()

            courses.append({"code": code, "title": title})

        return courses

    def sync_courses(
        self,
        db: Session,
        terms: Optional[list[str]] = None,
        course_type: str = "Lecture",
        dry_run: bool = False,
    ) -> SyncResult:
        """
        Fetch courses from CDCS for given terms and sync to database.

        Args:
            db: SQLAlchemy database session
            terms: List of terms to fetch (default: Fall 2025, Spring 2025)
            course_type: Course type filter (default: "Lecture")
            dry_run: If True, don't commit changes to database

        Returns:
            SyncResult with counts of created, updated, unchanged courses
        """
        if terms is None:
            terms = self.DEFAULT_TERMS

        # Fetch and deduplicate courses across all terms
        all_courses: dict[str, str] = {}
        for term in terms:
            fetched = self.fetch_courses_from_cdcs(term, course_type)
            for course in fetched:
                code = course["code"]
                # Keep first occurrence (or could prefer longer title)
                if code not in all_courses:
                    all_courses[code] = course["title"]

        # Fetch existing courses from database
        existing_courses = {
            c.code: c for c in db.execute(select(Course)).scalars().all()
        }
        existing_official_codes = {
            code for code, course in existing_courses.items() if course.is_official
        }

        created = 0
        updated = 0
        unchanged = 0
        deleted = 0
        deletion_skipped = False

        # Upsert courses from CDCS
        for code, title in all_courses.items():
            if code in existing_courses:
                course = existing_courses[code]
                # Check if update needed
                if course.title != title or not course.is_official:
                    course.title = title
                    course.is_official = True
                    updated += 1
                else:
                    unchanged += 1
            else:
                # Create new course
                new_course = Course(
                    code=code,
                    title=title,
                    is_official=True,
                )
                db.add(new_course)
                created += 1

        # Delete stale official courses (with safety check)
        scraped_codes = set(all_courses.keys())
        stale_codes = existing_official_codes - scraped_codes

        if stale_codes:
            # Safety check: only delete if scraped count is reasonably close to existing
            if len(existing_official_codes) == 0 or (
                len(scraped_codes) >= len(existing_official_codes) * self.DELETION_SAFETY_THRESHOLD
            ):
                for code in stale_codes:
                    course = existing_courses[code]
                    db.delete(course)
                    deleted += 1
            else:
                # Scraped count too low - likely CDCS error, skip deletion
                deletion_skipped = True

        if not dry_run:
            db.commit()

        return SyncResult(
            created=created,
            updated=updated,
            unchanged=unchanged,
            deleted=deleted,
            total=len(all_courses),
            terms=terms,
            deletion_skipped=deletion_skipped,
        )
