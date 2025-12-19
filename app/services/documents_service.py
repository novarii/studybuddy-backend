from __future__ import annotations

import hashlib
import io
import os
from typing import Optional, Tuple
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from ..database.models import Document, DocumentStatus
from ..storage import StorageBackend
from .users_service import ensure_user_exists


class DocumentsService:
    """Handle PDF upload persistence and linking."""

    def __init__(self, storage: StorageBackend) -> None:
        self.storage = storage

    def upload_document(
        self,
        db: Session,
        *,
        course_id: UUID,
        user_id: UUID,
        filename: str,
        content_type: Optional[str],
        file_bytes: bytes,
    ) -> Tuple[Document, bool]:
        checksum = hashlib.sha256(file_bytes).hexdigest()
        document = (
            db.execute(
                select(Document).where(
                    Document.owner_id == user_id,
                    Document.course_id == course_id,
                    Document.checksum == checksum,
                )
            )
            .scalars()
            .first()
        )
        created = False
        if document is None:
            ensure_user_exists(db, user_id)
            document_id = uuid4()
            storage_key = f"documents/{document_id}.pdf"
            file_stream = io.BytesIO(file_bytes)
            meta = self.storage.store_file(storage_key, file_stream, mime_type=content_type)
            document = Document(
                id=document_id,
                owner_id=user_id,
                course_id=course_id,
                filename=os.path.basename(filename) or "document.pdf",
                storage_key=storage_key,
                checksum=checksum,
                mime_type=content_type or "application/pdf",
                size_bytes=meta.size_bytes,
                page_count=None,
                description=None,
                status=DocumentStatus.uploaded,
            )
            db.add(document)
            created = True

        db.commit()
        return document, created

    def list_documents_for_course(self, db: Session, course_id: UUID, user_id: UUID) -> list[Document]:
        """List all documents for a course owned by the user."""
        stmt = select(Document).where(
            Document.owner_id == user_id,
            Document.course_id == course_id,
        ).order_by(Document.created_at.desc())
        return list(db.execute(stmt).scalars().all())

    def fetch_document_for_user(self, db: Session, document_id: UUID, user_id: UUID) -> Document:
        stmt = (
            select(Document)
            .where(Document.owner_id == user_id, Document.id == document_id)
        )
        document = db.execute(stmt).scalars().first()
        if document is None:
            raise NoResultFound("Document not found for user")
        return document

    def remove_user_from_document(self, db: Session, document_id: UUID, user_id: UUID) -> None:
        document = (
            db.execute(
                select(Document).where(
                    Document.owner_id == user_id,
                    Document.id == document_id,
                )
            )
            .scalars()
            .first()
        )
        if document is None:
            return
        self.storage.delete_file(document.storage_key)
        db.delete(document)
        db.commit()

    def delete_document(self, db: Session, document_id: UUID) -> None:
        document = db.get(Document, document_id)
        if document is None:
            raise NoResultFound("Document not found")
        self.storage.delete_file(document.storage_key)
        db.delete(document)
        db.commit()
