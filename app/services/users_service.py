from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ..database.models import User


def ensure_user_exists(db: Session, user_id: UUID) -> None:
    """Insert a user row if it doesn't already exist."""

    if db.get(User, user_id) is None:
        db.add(User(id=user_id))
        db.flush()
