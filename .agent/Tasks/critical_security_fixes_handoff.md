# Critical Security Fixes Handoff

**For:** Codebase Audit Expert Agent
**Priority:** CRITICAL - Fix Immediately
**Codebase:** StudyBuddy Backend (FastAPI)

---

## Summary

4 critical security vulnerabilities were identified that require immediate fixes. This document provides exact locations, current code, and required fixes for each.

---

## Issue 1: Path Traversal Vulnerability

**Severity:** CRITICAL
**File:** `app/storage/__init__.py`
**Lines:** 72-74

### Current Code
```python
def _resolve_path(self, storage_key: str) -> Path:
    clean_key = storage_key.lstrip("/")
    return self.root.joinpath(clean_key)
```

### Problem
Only strips leading `/` but doesn't prevent `..` sequences. An attacker could access files outside storage root with `storage_key = "documents/../../../etc/passwd"`.

### Required Fix
```python
def _resolve_path(self, storage_key: str) -> Path:
    clean_key = storage_key.lstrip("/")
    if ".." in clean_key:
        raise ValueError("Invalid storage key: path traversal not allowed")
    resolved = self.root.joinpath(clean_key).resolve()
    if not resolved.is_relative_to(self.root.resolve()):
        raise ValueError("Invalid storage key: path traversal detected")
    return resolved
```

### Verification
- Test that `"../etc/passwd"` raises ValueError
- Test that `"documents/test.pdf"` works normally

---

## Issue 2: Test Credentials in Production Code

**Severity:** CRITICAL
**Files:**
- `app/agents/chat_agent.py` (lines 23-25, 102-104, 174-175)
- `app/mcp/server.py` (lines 19-20, 44-45, 61)

### Current Code (chat_agent.py)
```python
# Lines 23-25
_TEST_OWNER_ID = "48d245df-a01f-5247-b84d-3ff890373545"
_TEST_COURSE_ID = "c5b51d23-d26f-4b7e-900a-44c8e738011c"

# Lines 102-104 in retrieve_documents()
if use_test_defaults:
    owner_lookup = owner_lookup or _TEST_OWNER_ID
    course_lookup = course_lookup or _TEST_COURSE_ID

# Lines 174-175 in custom_retriever()
owner_lookup = owner_id or agent.user_id or _TEST_OWNER_ID
course_lookup = course_id or _TEST_COURSE_ID
```

### Current Code (mcp/server.py)
```python
# Lines 44-45 - default parameters
owner_id: Optional[str] = _TEST_OWNER_ID,
course_id: Optional[str] = _TEST_COURSE_ID,

# Line 61 - forces test defaults
use_test_defaults=True,
```

### Problem
Hardcoded test UUIDs used as fallbacks. If auth context isn't passed, queries silently fall back to test data, potentially exposing wrong course materials.

### Required Fix

**chat_agent.py:**
1. Remove `_TEST_OWNER_ID` and `_TEST_COURSE_ID` constants entirely
2. In `retrieve_documents()`: Remove the `use_test_defaults` parameter and its logic
3. In `custom_retriever()`: Change to require explicit context:
```python
owner_lookup = owner_id or agent.user_id
course_lookup = course_id
if not owner_lookup:
    logger.warning("No owner context provided for retrieval")
    return []
```

**mcp/server.py:**
1. Remove imports of test constants
2. Change default parameters to `None` (no defaults)
3. Remove `use_test_defaults=True` from the call
4. Add validation that required params are provided

### Verification
- Grep for `_TEST_OWNER_ID` and `_TEST_COURSE_ID` should return 0 results
- MCP tool should fail gracefully when owner_id not provided

---

## Issue 3: CORS Wildcard Default

**Severity:** CRITICAL
**File:** `app/main.py`
**Lines:** 51-58

### Current Code
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

### Problem
Defaults to `["*"]` when `CORS_ALLOW_ORIGINS` not configured. Combined with `allow_credentials=True`, this violates CORS security model and allows cross-origin attacks.

### Required Fix
```python
if not settings.cors_allow_origins:
    raise RuntimeError("CORS_ALLOW_ORIGINS must be configured")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allow_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### Verification
- App should fail to start without `CORS_ALLOW_ORIGINS` set
- Only specified methods/headers should be allowed

---

## Issue 4: SQL Injection in Schema Creation

**Severity:** CRITICAL (mitigated by config-only input)
**File:** `app/agents/knowledge_builder.py`
**Lines:** 59-63

### Current Code
```python
@lru_cache(maxsize=1)
def _ensure_schema_exists(schema_name: str) -> None:
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
```

### Problem
F-string interpolation in `text()` bypasses SQL parameterization. While `schema_name` comes from config (not user input), this is a dangerous pattern.

### Required Fix
```python
import re

@lru_cache(maxsize=1)
def _ensure_schema_exists(schema_name: str) -> None:
    # Validate schema name format (alphanumeric and underscore only)
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema_name):
        raise ValueError(f"Invalid schema name: {schema_name}")

    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        # Safe after validation - PostgreSQL identifiers can't be parameterized
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
```

### Verification
- Schema names with special characters should raise ValueError
- Normal schema name "ai" should work

---

## Execution Order

1. **Fix Issue 1** (Path Traversal) - Single file, isolated fix
2. **Fix Issue 3** (CORS) - Single file, isolated fix
3. **Fix Issue 4** (SQL Injection) - Single file, isolated fix
4. **Fix Issue 2** (Test Credentials) - Multiple files, most complex

---

## Testing After Fixes

```bash
# Start the server (should fail without CORS config)
uv run uvicorn app.main:app --reload

# Set CORS and restart
export CORS_ALLOW_ORIGINS="http://localhost:3000"
uv run uvicorn app.main:app --reload

# Verify health endpoint works
curl http://localhost:8000/api/health
```

---

## Files Modified

| File | Issue |
|------|-------|
| `app/storage/__init__.py` | #1 Path Traversal |
| `app/main.py` | #3 CORS |
| `app/agents/knowledge_builder.py` | #4 SQL Injection |
| `app/agents/chat_agent.py` | #2 Test Credentials |
| `app/mcp/server.py` | #2 Test Credentials |

---

## Context Notes

- Codebase uses FastAPI with SQLAlchemy 2.0+
- Authentication via Clerk JWT tokens
- Storage backend abstracts file persistence
- MCP server exposes retrieval as LLM tool
- Full audit report at `.agent/Tasks/codebase_cleanup_plan.md`
