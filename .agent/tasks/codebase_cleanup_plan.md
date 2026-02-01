# Codebase Cleanup Plan

**Generated:** December 2024
**Status:** Ready for Review
**Audit Type:** Deep codebase audit covering security, architecture, code quality, and maintainability

---

## Executive Summary

This document outlines a comprehensive cleanup plan based on a thorough audit of the StudyBuddy Backend codebase. The audit identified **4 critical issues**, **8 high-priority issues**, **7 medium-priority issues**, and several low-priority improvements. The codebase is generally well-structured with good separation of concerns, but has several security vulnerabilities and code quality issues that should be addressed.

---

## Table of Contents

1. [Critical Security Issues](#1-critical-security-issues)
2. [High Priority Issues](#2-high-priority-issues)
3. [Medium Priority Issues](#3-medium-priority-issues)
4. [Low Priority Improvements](#4-low-priority-improvements)
5. [Implementation Order](#5-implementation-order)
6. [Verification Checklist](#6-verification-checklist)

---

## 1. Critical Security Issues

### 1.1 Path Traversal Vulnerability in Storage Backend

**Location:** `app/storage/__init__.py:72-74`

**Current Code:**
```python
def _resolve_path(self, storage_key: str) -> Path:
    clean_key = storage_key.lstrip("/")
    return self.root.joinpath(clean_key)
```

**Issue:** The `_resolve_path` method only strips leading slashes but doesn't prevent path traversal attacks. An attacker could pass `storage_key = "documents/../../../etc/passwd"` to access files outside the storage root.

**Impact:** High - Arbitrary file read/write outside storage directory

**Fix Required:**
```python
def _resolve_path(self, storage_key: str) -> Path:
    clean_key = storage_key.lstrip("/")
    # Reject path traversal attempts
    if ".." in clean_key:
        raise ValueError("Invalid storage key: path traversal not allowed")
    resolved = self.root.joinpath(clean_key).resolve()
    # Verify resolved path is within root
    if not resolved.is_relative_to(self.root.resolve()):
        raise ValueError("Invalid storage key: path traversal detected")
    return resolved
```

**Data Flow Evidence:**
- `storage_key` originates from `documents_service.py:50` as `f"documents/{document_id}.pdf"` (safe)
- `storage_key` originates from `lectures_service.py:157,164,178,191` as controlled strings (safe)
- However, the storage backend interface is public and could be misused

---

### 1.2 Test Credentials in Production Code

**Location:** `app/agents/chat_agent.py:23-25`

**Current Code:**
```python
# TODO: remove once the frontend passes authenticated context down to the agent layer.
_TEST_OWNER_ID = "48d245df-a01f-5247-b84d-3ff890373545"
_TEST_COURSE_ID = "c5b51d23-d26f-4b7e-900a-44c8e738011c"
```

**Issue:** Hardcoded test UUIDs are used as fallbacks in production query paths:
- `chat_agent.py:103-104`: `owner_lookup = owner_lookup or _TEST_OWNER_ID`
- `chat_agent.py:174-175`: Used in `custom_retriever()`
- `mcp/server.py:44-45`: Used as default parameters

**Impact:** High - If authentication context is not properly passed, queries fall back to test data, potentially exposing course materials

**Fix Required:**
1. Remove test defaults from production code
2. Make `owner_id` and `course_id` required parameters (no defaults)
3. Raise explicit errors when context is missing instead of falling back

---

### 1.3 CORS Wildcard Default

**Location:** `app/main.py:51-58`

**Current Code:**
```python
allow_origins = settings.cors_allow_origins or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allow_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Issue:** If `CORS_ALLOW_ORIGINS` is not set, the application defaults to allowing all origins with credentials enabled, which violates CORS security model.

**Impact:** High - Cross-origin attacks from any domain when misconfigured

**Fix Required:**
1. Fail fast if `CORS_ALLOW_ORIGINS` is not configured (no default)
2. Validate CORS configuration at startup
3. Explicitly list allowed methods instead of wildcard

---

### 1.4 SQL Injection in Schema Creation

**Location:** `app/agents/knowledge_builder.py:60-63`

**Current Code:**
```python
@lru_cache(maxsize=1)
def _ensure_schema_exists(schema_name: str) -> None:
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
```

**Issue:** Uses f-string interpolation with `text()`, bypassing SQL parameterization. While the schema name comes from configuration, this is still a code smell and potential vulnerability if environment variables are compromised.

**Impact:** Medium (configuration-controlled, not user-input)

**Fix Required:**
- Validate schema name format (alphanumeric + underscore only)
- Or use SQLAlchemy's DDL construct instead of raw SQL

---

## 2. High Priority Issues

### 2.1 HTTP Header Injection in File Download

**Location:** `app/main.py:317-318`

**Current Code:**
```python
headers = {
    "Content-Disposition": f'attachment; filename="{document.filename}"'
}
```

**Issue:** User-controlled filename is directly interpolated into HTTP header without escaping. Filenames with quotes or newlines could inject additional headers.

**Fix Required:**
- Use RFC 5987 encoding for Content-Disposition filename
- Or sanitize filename to remove special characters

---

### 2.2 No File Size Limit on Upload

**Location:** `app/main.py:253`

**Current Code:**
```python
file_bytes = await file.read()  # No max_size!
```

**Issue:** Entire file is read into memory without size validation, enabling denial-of-service via large file uploads.

**Fix Required:**
- Implement streaming upload with size cap
- Add FastAPI body size limit configuration

---

### 2.3 Dev Routes Without Authentication

**Location:** `app/main.py:115-118`

**Current Code:**
```python
@app.get("/api/dev/lectures", response_model=list[LectureStatusListItem])
async def list_dev_lectures(db: Session = Depends(get_db)):
    if not settings.dev_routes_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
```

**Issue:** Development endpoint returns all lectures without authentication. Only protected by environment variable flag.

**Fix Required:**
- Add `Depends(require_user)` with admin check for defense-in-depth
- Or completely remove dev routes in production builds

---

### 2.4 Overly Complex Pipeline Method

**Location:** `app/services/lectures_service.py:145-225`

**Issue:** `_run_download_pipeline` is 81 lines with:
- 7 nested try/except blocks
- Multiple concerns (session management, pipeline orchestration, error handling, cleanup)
- Complex control flow with multiple return points

**Fix Required:**
- Extract into smaller, testable methods
- Use a context manager for session management
- Separate orchestration from error handling

---

### 2.5 Duplicate Filter Construction Logic

**Location:** `app/agents/chat_agent.py`

**Issue:** Filter building logic is duplicated between:
- `retrieve_documents()` function (lines 106-122)
- `custom_retriever()` function (lines 177-193)

The `custom_retriever()` builds filters then passes them to `retrieve_documents()` which rebuilds the same filters again.

**Fix Required:**
- Remove duplicate filter construction from `custom_retriever()`
- Consolidate filter building into a single utility function

---

### 2.6 Exception Details Exposed in HTTP Responses

**Location:** Multiple files

**Instances:**
- `app/api/auth.py:41`: `detail=str(exc)` in 500 response
- `app/main.py:170`: `detail=str(exc)` in 400 response

**Issue:** Exception messages returned to client may leak implementation details.

**Fix Required:**
- Log exceptions server-side with full details
- Return generic error messages to client

---

### 2.7 MCP Server Tool Uses Test Defaults

**Location:** `app/mcp/server.py:40-68`

**Current Code:**
```python
def retrieve_course_material(
    query: str,
    *,
    owner_id: Optional[str] = _TEST_OWNER_ID,
    course_id: Optional[str] = _TEST_COURSE_ID,
    ...
```

**Issue:** MCP tool has test credentials as default parameters and forces `use_test_defaults=True` on line 61.

**Fix Required:**
- Remove default values for `owner_id` and `course_id`
- Require explicit authentication context from MCP callers

---

### 2.8 No Rollback in Database Dependency

**Location:** `app/database/db.py:15-21`

**Current Code:**
```python
def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**Issue:** On exception, changes may be partially committed. No explicit rollback handling.

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

## 3. Medium Priority Issues

### 3.1 Broad Exception Handling

**Locations:**
- `app/agents/chat_agent.py:84-86`: `except Exception:` returns empty list
- `app/services/document_chunk_pipeline.py:137,153`: `except Exception:`
- `app/services/lecture_chunk_pipeline.py:223,238`: `except Exception:`

**Issue:** Generic exception catching masks error types; silent failures in knowledge search make debugging difficult.

**Fix Required:**
- Catch specific exceptions
- Add proper error classification and handling

---

### 3.2 Inconsistent Session Management Patterns

**Locations:**
- Background tasks create `SessionLocal()` directly
- Route handlers use `Depends(get_db)`
- Different cleanup patterns

**Fix Required:**
- Create a context manager for background task sessions
- Standardize session creation pattern

---

### 3.3 Audio Codec Not Validated

**Location:** `app/services/downloaders/downloader.py:107`

**Issue:** `audio_codec` parameter passed to ffmpeg without validation.

**Fix Required:**
- Validate against whitelist of allowed codecs: `{"aac", "mp3", "opus", "flac"}`

---

### 3.4 Panopto URL Domain Not Validated

**Location:** `app/core/utils.py:6-20`

**Issue:** No validation that URL actually comes from Panopto domain.

**Fix Required:**
- Validate URL hostname against allowed domains

---

### 3.5 Missing Foreign Key Between Lecture and Course

**Issue:** `Lecture.course_id` references courses but no FK constraint in database.

**Fix Required:**
- Add migration to create FK constraint

---

### 3.6 Knowledge Removal Failures Silent

**Locations:**
- `app/services/lecture_chunk_pipeline.py:236-239`
- `app/services/document_chunk_pipeline.py:151-154`

**Issue:** Exceptions during knowledge removal are logged but don't fail the operation. Could lead to orphaned vectors.

**Fix Required:**
- At minimum, add monitoring for these failures
- Consider retry logic

---

### 3.7 Scattered Storage Key Generation

**Locations:**
- `documents_service.py:50`: `f"documents/{document_id}.pdf"`
- `lectures_service.py:157,164,178,191`: Various formats
- `document_chunk_pipeline.py:105`
- `lecture_chunk_pipeline.py:242`

**Issue:** Storage key formats scattered across codebase.

**Fix Required:**
- Create `StorageKeyFactory` or constants module
- Centralize all storage key generation

---

## 4. Low Priority Improvements

### 4.1 Configuration Parsing Duplication

**Location:** `app/core/config.py:59-80`

**Issue:** Three environment variables use identical split/strip/filter pattern.

**Fix:** Extract to helper function.

---

### 4.2 No Logging of Active LLM Model

**Location:** `app/agents/chat_agent.py:211-214`

**Issue:** Silent fallback to Gemini if OpenRouter key missing; no log of which model is active.

**Fix:** Add info-level log indicating active model.

---

### 4.3 Magic Numbers Scattered

**Locations:**
- `lecture_chunk_pipeline.py:65`: `180.0` seconds chunk duration
- `storage/__init__.py:51`: `1024 * 1024` chunk size
- `transcription_service.py:92`: Status string literals

**Fix:** Define as named constants.

---

### 4.4 Exported Functions Could Be Private

**Location:** `app/agents/chat_agent.py:227`

**Issue:** `custom_retriever` and `retrieve_documents` exported but only used internally or by MCP.

**Fix:** Consider making private with `_` prefix if not part of public API.

---

### 4.5 Circular Dependency Risk in MCP Server

**Location:** `app/mcp/server.py:11-29`

**Issue:** Dynamic import path manipulation for standalone execution.

**Fix:** Use proper entry point configuration instead of path manipulation.

---

## 5. Implementation Order

### Phase 1: Critical Security (Immediate)
1. Fix path traversal vulnerability in storage backend
2. Remove test credentials from production code
3. Fix CORS wildcard default
4. Validate schema name in knowledge_builder

### Phase 2: High Priority Security & Quality
5. Fix HTTP header injection in file download
6. Add file size limit on upload
7. Secure dev routes with authentication
8. Add rollback handling to database dependency
9. Fix exception details in HTTP responses
10. Secure MCP server tool parameters

### Phase 3: Code Quality
11. Refactor `_run_download_pipeline` into smaller methods
12. Remove duplicate filter construction in chat_agent
13. Standardize session management patterns
14. Centralize storage key generation

### Phase 4: Maintenance
15. Add specific exception handling
16. Add URL domain validation
17. Add audio codec validation
18. Add foreign key constraint for course_id
19. Extract configuration parsing helper
20. Define magic numbers as constants

---

## 6. Verification Checklist

After implementing fixes, verify:

### Security
- [ ] Path traversal test: Attempt to access files outside storage root fails
- [ ] Test credentials removed: Grep for `_TEST_OWNER_ID` returns no results in production code
- [ ] CORS test: Application fails to start without `CORS_ALLOW_ORIGINS` configured
- [ ] Schema validation: Invalid schema names rejected
- [ ] Header injection test: Filenames with special characters properly encoded
- [ ] File size test: Uploads over limit are rejected

### Functionality
- [ ] All existing tests pass
- [ ] Document upload/download works correctly
- [ ] Lecture download pipeline completes successfully
- [ ] Chat agent returns relevant results
- [ ] MCP server tools work with explicit parameters

### Code Quality
- [ ] No `except Exception:` without specific handling
- [ ] All storage keys generated from centralized location
- [ ] Session management consistent across codebase
- [ ] No duplicate code blocks

---

## Files Affected

| File | Changes |
|------|---------|
| `app/storage/__init__.py` | Path traversal fix |
| `app/agents/chat_agent.py` | Remove test defaults, deduplicate filters |
| `app/main.py` | CORS fix, file size limit, header injection fix, dev routes auth |
| `app/agents/knowledge_builder.py` | Schema name validation |
| `app/mcp/server.py` | Remove test defaults |
| `app/database/db.py` | Add rollback handling |
| `app/api/auth.py` | Generic error messages |
| `app/services/lectures_service.py` | Refactor pipeline method |
| `app/services/downloaders/downloader.py` | Codec validation |
| `app/core/utils.py` | URL domain validation |
| `app/core/config.py` | Extract parsing helper |

---

## Notes

- The codebase is well-organized with clear separation of concerns
- No major architectural issues; fixes are mostly localized
- Test coverage should be added for security-critical paths
- Consider adding rate limiting for upload endpoints
- Consider adding security headers middleware (X-Frame-Options, X-Content-Type-Options)
