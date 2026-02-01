# Codebase Audit Report - December 2024

**Audit Date:** December 19, 2024
**Status:** In Progress
**Codebase Version:** Commit `cd5fa09` (main branch)
**Last Updated:** December 20, 2024

---

## Executive Summary

This audit examines the StudyBuddy Backend codebase following the previous cleanup cycle documented in `codebase_cleanup_plan.md`. The audit confirms that **all 4 critical security issues have been resolved**. Additional security fixes have been implemented during this audit cycle.

### Issues Fixed This Audit Cycle

| Issue | Status | Commit | Evidence |
|-------|--------|--------|----------|
| HTTP Header Injection in File Download | FIXED | `32690ed` | Uses RFC 5987 encoding with `quote()` for Content-Disposition |
| No File Size Limit on Upload | FIXED | `89f7858` | 50MB limit with early Content-Length check and read limit |
| Dev Routes Lack Defense in Depth | FIXED | `09a02db` | Requires both `DEV_ROUTES_ENABLED` and admin user |

### Previous Critical Issues - RESOLVED

| Issue | Status | Evidence |
|-------|--------|----------|
| Path Traversal in Storage | FIXED | `storage/__init__.py:55-59` - Added `..` check and `is_relative_to()` validation |
| Test Credentials in Production | FIXED | Removed `_TEST_OWNER_ID` and `_TEST_COURSE_ID`; now uses `settings.test_course_id` and `settings.test_owner_id` env vars |
| CORS Wildcard Default | FIXED | `main.py:56-57` - Raises `RuntimeError` if `CORS_ALLOW_ORIGINS` not configured |
| SQL Injection in Schema Creation | FIXED | `knowledge_builder.py:63` - Added regex validation for schema name |

---

## Table of Contents

1. [Remaining Security Issues](#1-remaining-security-issues)
2. [Dead Code and Unused Components](#2-dead-code-and-unused-components)
3. [Architectural Improvements](#3-architectural-improvements)
4. [Code Quality Issues](#4-code-quality-issues)
5. [Database Concerns](#5-database-concerns)
6. [Implementation Plan](#6-implementation-plan)

---

## 1. Remaining Security Issues

### 1.1 MEDIUM: No Database Rollback on Exception

**Location:** `app/database/db.py:16-22`

**Current Code:**
```python
def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**Issue:** On exception, uncommitted changes may persist. No explicit rollback.

**Fix Required:**
```python
def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

---

### 1.2 MEDIUM: Panopto URL Domain Not Validated

**Location:** `app/core/utils.py:6-20`

**Issue:** `extract_panopto_session_id()` accepts any URL without validating it's from Panopto. Could be used to fetch arbitrary URLs.

**Fix Required:**
```python
ALLOWED_PANOPTO_DOMAINS = {"panopto.com", "hosted.panopto.com"}

def extract_panopto_session_id(url: str) -> str:
    parsed = urlparse(url)
    if not any(parsed.netloc.endswith(domain) for domain in ALLOWED_PANOPTO_DOMAINS):
        raise ValueError("URL must be from a Panopto domain")
    # ... rest of function
```

---

## 2. Dead Code and Unused Components

### 2.1 Unused Imports

| File | Unused Import | Action |
|------|---------------|--------|
| `app/services/pdf_slides_service.py:5` | `Iterable` - imported but not used in file | Remove |
| `app/schemas/__init__.py:85` | `session_id` field in `ChatRequest` - never read in `main.py` | Document or remove |

### 2.2 Unused Configuration Variables

| Variable | Location | Usage Status |
|----------|----------|--------------|
| `documents_prefix` | `app/core/config.py:55` | Not used anywhere |
| `audio_tmp_prefix` | `app/core/config.py:56` | Not used anywhere |
| `direct_stream_required` | `app/core/config.py:73` | Not used anywhere |

**Fix Required:** Either use these variables in storage key generation or remove them.

### 2.3 Unused Methods

| Method | Location | Notes |
|--------|----------|-------|
| `chunk_texts()` | `app/services/pdf_slide_chunks_service.py:36-37` | Defined but never called |

### 2.4 Potentially Dead Code

| Code | Location | Notes |
|------|----------|-------|
| `HttpPanoptoDownloader` | `app/services/downloaders/downloader.py:56-84` | Only `PanoptoPackageDownloader` is used in production. This appears to be an alternative implementation but is never instantiated. Could be kept for testing purposes but should be documented. |
| `custom_retriever` | `app/agents/chat_agent.py:151-186` | Exported in `__all__` but only used internally by `_test_retriever`. The main chat endpoint uses `retrieve_documents` directly. |

### 2.5 Unused Database Fields

| Field | Table | Notes |
|-------|-------|-------|
| `description` | `documents` | Always set to `None` in `documents_service.py:63`. Never populated. |
| `page_count` | `documents` | Always set to `None` in `documents_service.py:62`. Never populated during upload. Could be populated during PDF processing. |

---

## 3. Architectural Improvements

### 3.1 MEDIUM: Scattered Storage Key Generation

**Issue:** Storage key patterns are hardcoded throughout:

- `documents_service.py:50`: `f"documents/{document_id}.pdf"`
- `lectures_service.py:157`: `f"audio_tmp/{lecture.id}_source.mp4"`
- `lectures_service.py:164`: `f"audio/{lecture.id}.m4a"`
- `lectures_service.py:178`: `f"transcripts/{lecture.id}.json"`
- `lectures_service.py:191`: `f"transcripts/{lecture.id}.vtt"`
- `document_chunk_pipeline.py:104`: `f"{self.chunk_storage_prefix}/{document_id}.json"`
- `lecture_chunk_pipeline.py:241`: `f"{self.chunk_storage_prefix}/{lecture_id}.json"`

**Fix Required:** Create `app/core/storage_keys.py`:
```python
class StorageKeys:
    @staticmethod
    def document(document_id: UUID) -> str:
        return f"documents/{document_id}.pdf"

    @staticmethod
    def audio_temp(lecture_id: UUID) -> str:
        return f"audio_tmp/{lecture_id}_source.mp4"

    @staticmethod
    def audio(lecture_id: UUID) -> str:
        return f"audio/{lecture_id}.m4a"

    # ... etc
```

---

### 3.2 MEDIUM: Duplicate Definition of RAGSource

**Issue:** `RAGSource` dataclass defined in:
- `app/adapters/vercel_stream.py:46-68` (with full metadata)
- Overlapping concept in `app/agents/context_formatter.py`

These serve different purposes but create confusion. The adapter's `RAGSource` is for SSE transmission; the context formatter builds client sources.

**Fix Required:** Rename adapter's class to `SSESourceEvent` or similar to clarify purpose.

---

### 3.3 LOW: Inconsistent Session Management

**Issue:** Background tasks in `lectures_service.py:146` create `SessionLocal()` directly while route handlers use `Depends(get_db)`.

**Fix Required:** Create a context manager for background task sessions:
```python
@contextmanager
def get_background_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

---

### 3.4 LOW: MCP Server Path Manipulation

**Location:** `app/mcp/server.py:11-16`

**Issue:** Uses `sys.path.append` for standalone execution. This is fragile.

**Fix Required:** Use proper entry point in `pyproject.toml`:
```toml
[project.scripts]
mcp-server = "app.mcp.server:main"
```

---

## 4. Code Quality Issues

### 4.1 Broad Exception Handling

**Locations:**
- `app/agents/chat_agent.py:87-89`: `except Exception:` returns empty list
- `app/services/document_chunk_pipeline.py:137,153`: `except Exception:`
- `app/services/lecture_chunk_pipeline.py:223,238`: `except Exception:`

**Issue:** Silent failures make debugging difficult.

**Fix Required:** Catch specific exceptions; add error classification.

---

### 4.2 Complex Pipeline Method

**Location:** `app/services/lectures_service.py:145-225`

**Issue:** `_run_download_pipeline` is 81 lines with multiple responsibilities.

**Fix Required:** Extract into smaller methods:
- `_download_video()`
- `_extract_audio()`
- `_transcribe_audio()`
- `_process_transcript()`

---

### 4.3 Missing Type Hints

**Location:** `app/database/db.py:16`

**Current:**
```python
def get_db() -> Generator:
```

**Fix:**
```python
def get_db() -> Generator[Session, None, None]:
```

---

### 4.4 Magic Numbers

| Location | Value | Meaning |
|----------|-------|---------|
| `lecture_chunk_pipeline.py:65` | `180.0` | Chunk duration in seconds |
| `storage/__init__.py:75` | `1024 * 1024` | Read chunk size |
| `transcription_service.py:36-37` | `5`, `600` | Poll interval/timeout |

**Fix Required:** Define as named constants.

---

## 5. Database Concerns

### 5.1 Missing Foreign Key Constraint

**Issue:** `Lecture.course_id` references courses but has no FK constraint.

**Fix Required:** Add migration:
```sql
ALTER TABLE lectures
ADD CONSTRAINT fk_lectures_course_id
FOREIGN KEY (course_id) REFERENCES courses(id)
ON DELETE CASCADE;
```

### 5.2 Missing Index

**Issue:** `Document.owner_id` used in queries but no dedicated index (only part of composite unique constraint).

**Fix Required:** Add index for `owner_id` alone for faster user-scoped queries:
```sql
CREATE INDEX idx_documents_owner_id ON documents(owner_id);
```

---

## 6. Implementation Plan

### Phase 1: Security (Priority: CRITICAL) - PARTIALLY COMPLETE

| # | Task | Status |
|---|------|--------|
| 1 | Fix HTTP header injection in file download | DONE |
| 2 | Add file size limit on upload | DONE |
| 3 | Add authentication to dev routes | DONE |
| 4 | Add database rollback handling | TODO |
| 5 | Add Panopto URL domain validation | TODO |

### Phase 2: Dead Code Cleanup (Priority: HIGH)

| # | Task | Status |
|---|------|--------|
| 6 | Remove unused imports (`Iterable` in pdf_slides_service) | TODO |
| 7 | Remove or use unused config variables | TODO |
| 8 | Remove unused `chunk_texts()` method | TODO |
| 9 | Document or remove `HttpPanoptoDownloader` | TODO |
| 10 | Populate or remove unused `description` and `page_count` fields | TODO |

### Phase 3: Architecture (Priority: MEDIUM)

| # | Task | Status |
|---|------|--------|
| 11 | Centralize storage key generation | TODO |
| 12 | Rename `RAGSource` to `SSESourceEvent` for clarity | TODO |
| 13 | Create context manager for background task sessions | TODO |
| 14 | Fix MCP server entry point | TODO |

### Phase 4: Code Quality (Priority: LOW)

| # | Task | Status |
|---|------|--------|
| 15 | Replace broad exception handling | TODO |
| 16 | Refactor `_run_download_pipeline` | TODO |
| 17 | Add missing type hints | TODO |
| 18 | Define magic numbers as constants | TODO |
| 19 | Add FK constraint for course_id | TODO |
| 20 | Add index on owner_id | TODO |

---

## Files Affected Summary

| File | Changes |
|------|---------|
| `app/main.py` | ~~Header injection fix, file size limit, dev routes auth~~ DONE |
| `app/database/db.py` | Rollback handling, type hints |
| `app/core/utils.py` | URL domain validation |
| `app/core/config.py` | Remove unused variables |
| `app/services/pdf_slides_service.py` | Remove unused import |
| `app/services/pdf_slide_chunks_service.py` | Remove unused method |
| `app/services/lectures_service.py` | Refactor pipeline |
| `app/adapters/vercel_stream.py` | Rename RAGSource |
| `app/schemas/__init__.py` | Document/remove session_id |
| `migrations/versions/` | New migration for FK and index |

---

## Verification Checklist

After implementing fixes, verify:

### Security
- [x] File download: Filenames with quotes/newlines properly encoded
- [x] File upload: Files over 50MB rejected
- [x] Dev routes: Require authentication when enabled
- [ ] Database: Transactions rolled back on exception
- [ ] URL validation: Non-Panopto URLs rejected

### Functionality
- [ ] All existing endpoints work correctly
- [ ] Document upload/download flows work
- [ ] Lecture download pipeline completes
- [ ] Chat agent returns relevant results
- [ ] MCP server accessible via proper entry point

### Code Quality
- [ ] No unused imports (run linter)
- [ ] Storage keys generated from centralized module
- [ ] No `except Exception:` without logging/handling
- [ ] Type hints on all public functions

---

## Notes

1. The codebase is generally well-structured with clear layered architecture
2. All critical security issues from previous audit have been resolved
3. 3 additional security fixes implemented in this audit cycle
4. Remaining issues are primarily in areas of defensive hardening and code hygiene
5. Test coverage should be expanded, especially for security-critical paths
6. Consider adding security headers middleware (CSP, X-Frame-Options, etc.)
