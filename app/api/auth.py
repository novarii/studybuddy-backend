from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException, Request, status
from clerk_backend_api.security import AuthenticateRequestOptions, authenticate_request

from ..core.config import settings


@dataclass
class AuthenticatedUser:
    """Represents the signed-in Clerk user extracted from a request."""

    user_id: UUID
    session_id: Optional[str]
    claims: Dict[str, Any]


def _build_auth_options() -> AuthenticateRequestOptions:
    secret_key = settings.clerk_secret_key
    if not secret_key:
        raise RuntimeError("CLERK_SECRET_KEY must be configured for authenticated routes.")

    authorized_parties = list(settings.clerk_authorized_parties)
    return AuthenticateRequestOptions(
        secret_key=secret_key,
        authorized_parties=authorized_parties or None,
    )


def require_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency that ensures the request is authenticated via Clerk."""

    try:
        options = _build_auth_options()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    request_state = authenticate_request(request, options)
    if not request_state.is_signed_in or request_state.payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    claims = dict(request_state.payload)
    raw_user_id = claims.get("external_id") or claims.get("sub")
    if raw_user_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token is missing a user identifier")

    raw_user_id_str = str(raw_user_id)
    try:
        user_id = UUID(raw_user_id_str)
    except ValueError:
        # Generate a deterministic UUID from any non-UUID Clerk identifier to keep DB schema stable.
        user_id = uuid.uuid5(uuid.NAMESPACE_URL, raw_user_id_str)

    return AuthenticatedUser(
        user_id=user_id,
        session_id=claims.get("sid"),
        claims=claims,
    )
