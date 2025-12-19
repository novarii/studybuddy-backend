from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Course(Base):
    __tablename__ = "courses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(Text, nullable=False, unique=True)
    title = Column(Text, nullable=False)
    instructor = Column(Text, nullable=True)
    is_official = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LectureStatus(str, enum.Enum):
    pending = "pending"
    downloading = "downloading"
    completed = "completed"
    failed = "failed"


class DocumentStatus(str, enum.Enum):
    uploaded = "uploaded"
    failed = "failed"


class Lecture(Base):
    __tablename__ = "lectures"
    __table_args__ = (
        UniqueConstraint("course_id", "panopto_session_id", name="uq_course_session"),
        Index("idx_lectures_course_id", "course_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id = Column(UUID(as_uuid=True), nullable=False)
    panopto_session_id = Column(String, nullable=True)
    panopto_url = Column(Text, nullable=False)
    stream_url = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    audio_storage_key = Column(String, nullable=True)
    transcript_storage_key = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    status = Column(
        Enum(LectureStatus, name="lecture_status", create_constraint=False),
        nullable=False,
        default=LectureStatus.pending,
    )
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user_links = relationship("UserLecture", back_populates="lecture", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("owner_id", "course_id", "checksum", name="uq_owner_course_checksum"),
        Index("idx_documents_course_id", "course_id"),
        Index("idx_documents_checksum", "checksum"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id = Column(UUID(as_uuid=True), nullable=False)
    filename = Column(Text, nullable=False)
    storage_key = Column(Text, nullable=False)
    checksum = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    page_count = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(
        Enum(DocumentStatus, name="document_status", create_constraint=False),
        nullable=False,
        default=DocumentStatus.uploaded,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserLecture(Base):
    __tablename__ = "user_lectures"
    __table_args__ = (Index("idx_user_lectures_lecture_id", "lecture_id"),)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lecture_id = Column(
        UUID(as_uuid=True),
        ForeignKey("lectures.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lecture = relationship("Lecture", back_populates="user_links")


class UserCourse(Base):
    __tablename__ = "user_courses"
    __table_args__ = (Index("idx_user_courses_course_id", "course_id"),)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    course_id = Column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    course = relationship("Course")

