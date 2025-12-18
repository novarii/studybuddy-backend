# Audio-Only Downloads with Authentication - Task

## Problem Statement

We want to download audio-only streams from Panopto to reduce file sizes by 70-90% (from ~460 MB to ~50-100 MB per lecture). However, we've encountered an authentication issue:

### Current Situation:
- **CloudFront URLs** (current implementation):
  - ✅ No authentication required (public CDN URLs)
  - ❌ Ignore `?mediaTargetType=audioPodcast` parameter - still send full video files
  - ❌ Result: Large downloads (~460 MB per lecture)

- **Direct Panopto URLs**:
  - ✅ Respect `?mediaTargetType=audioPodcast` parameter - send audio-only files
  - ✅ Much smaller files (~50-100 MB per lecture)
  - ❌ Require authentication (returns 403 Forbidden without session cookies)
  - ❌ Backend cannot download without Panopto session

### Test Results:

```bash
curl -I 'https://rochester.hosted.panopto.com/Panopto/Podcast/Download/{deliveryId}.mp4?mediaTargetType=audioPodcast'

# Returns:
HTTP/2 403
content-type: text/html; charset=utf-8
```

The direct Panopto audio URLs work in the browser extension (which has session cookies) but fail in the backend (no authentication).

## Objective

Implement one of two solutions to enable audio-only downloads while maintaining reliability and security.

## Solution Options

### Option A: Pass Session Cookies from Extension to Backend (RECOMMENDED TO TRY FIRST)

**Concept:** Browser extension extracts Panopto session cookies and passes them to the backend, which uses them to authenticate download requests.

**Pros:**
- ✅ Enables audio-only downloads (70-90% file size reduction)
- ✅ Uses existing direct Panopto URLs
- ✅ Straightforward implementation
- ✅ Common pattern (similar to OAuth token passing)

**Cons:**
- ⚠️ Session expiration risk (if user's Panopto session expires during download)
- ⚠️ Security consideration (passing user credentials to backend)
- ⚠️ Fails if user logs out during download
- ⚠️ Requires HTTPS between extension and backend

**Risk Mitigation:**
1. Don't store cookies - use them only for the download request
2. Don't log cookie values (security)
3. Implement graceful error handling for 401/403 responses
4. Add retry logic with user notification if session expires
5. Ensure HTTPS is used for all backend communication

### Option B: Investigate DeliveryInfo API for Audio-Only CloudFront URLs

**Concept:** Check if Panopto's DeliveryInfo API response contains separate audio-only CloudFront URLs that don't require authentication.

**Pros:**
- ✅ No authentication issues (CloudFront URLs are pre-signed)
- ✅ No session expiration concerns
- ✅ More robust and reliable
- ✅ Simpler architecture (no cookie passing)

**Cons:**
- ⚠️ Uncertain if such URLs exist in the API response
- ⚠️ Requires investigation of DeliveryInfo API response structure
- ⚠️ Might not be available for all videos

**Investigation Steps:**
1. Open browser DevTools → Network tab
2. Navigate to Panopto viewer page
3. Trigger extension download
4. Find `DeliveryInfo.aspx` API call
5. Examine JSON response for fields like:
   - `AudioStreams`
   - `AudioPodcastStreams`
   - `PodcastStreams[].StreamType === "audio"`
   - Any CloudFront URLs with audio-specific paths

## Implementation Plan (Option A: Cookie Passing)

### Phase 1: Extension Changes

**File:** `studybuddy-extension-plasmo/src/contents/panopto.tsx`

#### 1.1 Add Cookie Permission to Manifest

**File:** `studybuddy-extension-plasmo/package.json` or `manifest.json`

Ensure the extension has permission to read cookies:
```json
{
  "permissions": [
    "cookies",
    "storage"
  ],
  "host_permissions": [
    "https://*.panopto.com/*",
    "https://*.panopto.eu/*"
  ]
}
```

#### 1.2 Modify `sendToBackend` Function

```typescript
const sendToBackend = async ({
  streamUrl,
  title,
  sourceUrl,
  backendUrl,
  courseId,
  apiKey,
  sessionToken
}: BackendPayload): Promise<LectureDownloadResponse | null> => {
  const headers: Record<string, string> = { "Content-Type": "application/json" }

  if (sessionToken) {
    headers.Authorization = `Bearer ${sessionToken}`
  } else if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`
  }

  // Extract Panopto cookies for authentication
  let cookieHeader = ''
  try {
    const panoptoCookies = await chrome.cookies.getAll({
      domain: 'rochester.hosted.panopto.com'
    })

    cookieHeader = panoptoCookies
      .filter(cookie => cookie.name.startsWith('.ASPX') || cookie.name === 'ASP.NET_SessionId')
      .map(cookie => `${cookie.name}=${cookie.value}`)
      .join('; ')

    console.info('Extracted Panopto cookies for backend authentication')
  } catch (error) {
    console.warn('Failed to extract Panopto cookies:', error)
    // Continue without cookies - will fall back to unauthenticated download
  }

  const response = await fetch(`${backendUrl}/api/lectures/download`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      course_id: courseId,
      panopto_url: sourceUrl ?? window.location.href ?? streamUrl,
      stream_url: streamUrl,
      title: title ?? document.title ?? null,
      panopto_cookies: cookieHeader || null
    })
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  return (await response.json().catch(() => null)) as LectureDownloadResponse | null
}
```

### Phase 2: Backend Schema Changes

**File:** `app/schemas/__init__.py`

```python
class LectureDownloadRequest(BaseModel):
    course_id: UUID
    panopto_url: constr(strip_whitespace=True, min_length=1)
    stream_url: constr(strip_whitespace=True, min_length=1)
    title: Optional[str] = None
    panopto_cookies: Optional[str] = None  # Session cookies from browser extension
```

### Phase 3: Backend Service Changes

**File:** `app/services/lectures_service.py`

#### 3.1 Store Cookies Temporarily

```python
def request_download(
    self,
    db: Session,
    payload: LectureDownloadRequest,
    user_id: UUID,
    background_tasks: Optional[BackgroundTasks] = None,
) -> tuple[Lecture, bool]:
    session_id = extract_panopto_session_id(payload.panopto_url)
    # ... existing lecture creation/lookup code ...

    if created and background_tasks is not None:
        background_tasks.add_task(
            self._run_download_pipeline,
            lecture.id,
            payload.panopto_cookies  # Pass cookies to background task
        )

    return lecture, created
```

#### 3.2 Update Pipeline to Use Cookies

```python
def _run_download_pipeline(self, lecture_id: UUID, panopto_cookies: Optional[str] = None) -> None:
    db = SessionLocal()
    temp_keys: list[str] = []
    try:
        lecture = db.get(Lecture, lecture_id)
        if lecture is None:
            return

        lecture.status = LectureStatus.downloading
        lecture.error_message = None
        db.commit()

        logger.info(
            "Starting download pipeline for lecture %s (stream_url=%s, authenticated=%s)",
            lecture.id,
            lecture.stream_url,
            bool(panopto_cookies)
        )

        video_storage_key = f"audio_tmp/{lecture.id}_source.mp4"
        download_result = self.downloader.download_video(
            lecture.stream_url,
            self.storage,
            video_storage_key,
            cookies=panopto_cookies  # Pass cookies to downloader
        )

        # ... rest of pipeline code ...
    except DownloadError as exc:
        # Check if error is authentication-related
        if "403" in str(exc) or "401" in str(exc) or "Forbidden" in str(exc):
            error_msg = "Authentication failed - Panopto session may have expired"
        else:
            error_msg = str(exc)
        logger.exception("Panopto download failed for lecture %s", lecture_id)
        self._handle_pipeline_failure(db, lecture_id, error_msg, temp_keys=temp_keys)
    # ... rest of exception handlers ...
```

### Phase 4: Downloader Adapter Changes

**File:** `app/services/downloaders/panopto_downloader.py`

```python
class PanoptoPackageDownloader(DownloaderInterface):
    """Panopto downloader powered by the external PanoptoDownloader package."""

    def __init__(self, progress_callback: Callable[[int], None] | None = None) -> None:
        self.progress_callback = progress_callback or (lambda _progress: None)

    def download_video(
        self,
        panopto_url: str,
        storage: StorageBackend,
        destination_key: str,
        cookies: Optional[str] = None
    ) -> DownloadResult:
        temp_path = self._build_temp_path()
        try:
            panopto_download(
                panopto_url,
                str(temp_path),
                self.progress_callback,
                cookies=cookies
            )
        except (
            panopto_exceptions.RegexNotMatch,
            panopto_exceptions.NotExist,
            panopto_exceptions.NotSupported,
            panopto_exceptions.NotAVideo,
            panopto_exceptions.NotAFile,
            panopto_exceptions.AlreadyExists,
        ) as exc:
            temp_path.unlink(missing_ok=True)
            raise DownloadError(str(exc)) from exc
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            logger.exception("Panopto package download failed")
            raise DownloadError(str(exc)) from exc

        try:
            with open(temp_path, "rb") as payload:
                meta = storage.store_file(destination_key, payload, mime_type="video/mp4")
        finally:
            temp_path.unlink(missing_ok=True)

        return DownloadResult(
            storage_key=meta.storage_key,
            size_bytes=meta.size_bytes,
            mime_type=meta.mime_type
        )

    def _build_temp_path(self) -> Path:
        temp_dir = Path(tempfile.gettempdir())
        candidate = temp_dir / f"panopto_{uuid.uuid4()}.mp4"
        if candidate.exists():
            candidate.unlink()
        return candidate
```

### Phase 5: Library Changes

**File:** `/Users/novari/Repos/Panopto-Video-DL-lib/PanoptoDownloader/PanoptoDownloader.py`

```python
import os.path
import re
import requests
import urllib.request
from shutil import which
from ffmpeg_progress_yield import FfmpegProgress

from .exceptions import *
from .hls_downloader import hls_downloader


SUPPORTED_FORMATS = ['.mp4', '.mkv', '.flv', '.avi']
REGEX = re.compile(
    r'^(http)s?://'
    r'(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)
PANOBF_FILE_REGEX = re.compile(r'.*\.panobf(\d+)$', re.IGNORECASE)


def download(uri: str, output: str, callback: callable, cookies: str = None) -> None:
    """
    Download video/audio from Panopto
    :param uri: video/audio URI
    :param output: downloaded file path
    :param callback: function to be called during downloading
    :param cookies: optional cookie header string for authentication
    """
    if not REGEX.match(uri):
        raise RegexNotMatch('Doesn\'t seem to be a URL')
    if os.path.isdir(output):
        raise NotAFile('Cannot be a folder')
    if not os.path.isdir(os.path.split(output)[0] or './'):
        raise NotExist('Folder does not exist')
    if os.path.exists(output):
        raise AlreadyExists('File already exists')

    if uri.endswith('master.m3u8') or PANOBF_FILE_REGEX.match(uri):
        use_ffmpeg = which('ffmpeg') is not None
        if use_ffmpeg:
            command = ['ffmpeg', '-f', 'hls', '-i', uri, '-c', 'copy', output]
            ff = FfmpegProgress(command)
            for progress in ff.run_command_with_progress():
                callback(progress)
        else:
            hls_downloader(uri, output, callback=callback)

    else:
        def _format(block_num, block_size, total_size):
            callback(int(block_num * block_size / total_size * 100))

        # Prepare headers with optional cookies
        headers = {}
        if cookies:
            headers['Cookie'] = cookies

        response = requests.head(uri, headers=headers)

        # Check for authentication errors
        if response.status_code in [401, 403]:
            raise NotAVideo(f'Authentication failed (HTTP {response.status_code}). Session may have expired.')

        content_type = response.headers.get('Content-Type', '')
        if 'video/' in content_type or 'audio/' in content_type:
            # Use custom opener with cookies if provided
            if cookies:
                opener = urllib.request.build_opener()
                opener.addheaders = [('Cookie', cookies)]
                urllib.request.install_opener(opener)
            urllib.request.urlretrieve(uri, output, _format)
        else:
            raise NotAVideo(f'Doesn\'t seem to be a video or audio file (Content-Type: {content_type})')
```

**Commit this change to the library:**
```bash
cd /Users/novari/Repos/Panopto-Video-DL-lib
git checkout relax-yarl
git add PanoptoDownloader/PanoptoDownloader.py
git commit -m "Add cookie authentication support for authenticated downloads

- Added optional cookies parameter to download() function
- Pass cookies in request headers for authentication
- Handle 401/403 authentication errors explicitly
- Support authenticated Panopto direct URLs"
git push origin relax-yarl

# Update backend dependency
cd /Users/novari/Repos/studybuddy-backend
uv lock --upgrade-package panoptodownloader
uv sync
```

## Testing Strategy

### Test Case 1: Successful Audio Download with Authentication
1. User logged into Panopto in browser
2. Trigger download via extension
3. Verify backend logs show `authenticated=True`
4. Verify file size is ~50-100 MB (audio-only)
5. Verify download completes successfully
6. Verify MIME type is `audio/mp4` or similar

**Expected logs:**
```
INFO - Starting download pipeline for lecture {id} (stream_url=https://rochester.hosted.panopto.com/Panopto/Podcast/Download/..., authenticated=True)
INFO - Panopto download stored at audio_tmp/{id}_source.mp4 (bytes=~50000000, mime=audio/mp4)
```

### Test Case 2: Session Expiration During Download
1. Mock/simulate expired session (manually invalidate cookies)
2. Trigger download
3. Verify graceful error handling
4. Verify error message mentions "session expired" or "authentication failed"
5. Verify lecture status is set to `failed` with appropriate error message

**Expected behavior:**
- Lecture deleted from database
- Temporary files cleaned up
- User can retry download after re-authenticating

### Test Case 3: Missing Cookies (Fallback)
1. Extension fails to extract cookies (permission denied, etc.)
2. Verify download still attempts with no cookies
3. May fail with 403, but should fail gracefully

### Test Case 4: Long Download with Valid Session
1. Trigger download of large lecture (~1 hour)
2. Verify session stays valid throughout download
3. Verify successful completion

### Test Case 5: User Logs Out During Download
1. Trigger download
2. User logs out of Panopto mid-download
3. Verify download fails with appropriate error
4. Verify cleanup happens correctly

## Security Considerations

### ✅ Security Best Practices:

1. **Don't Log Cookies:**
   ```python
   # BAD
   logger.info(f"Using cookies: {panopto_cookies}")

   # GOOD
   logger.info(f"Using cookies: {bool(panopto_cookies)}")
   ```

2. **Don't Store Cookies:**
   - Never save cookies to database
   - Only pass them through background task in memory
   - Let them be garbage collected after use

3. **Use HTTPS:**
   - Ensure backend URL uses HTTPS in production
   - Add validation in extension to warn if HTTP is used

4. **Minimal Cookie Exposure:**
   - Only extract necessary cookies (`.ASPXAUTH`, `ASP.NET_SessionId`)
   - Don't send all cookies indiscriminately

5. **Error Messages:**
   - Don't expose cookie values in error messages
   - Generic "authentication failed" messages

### Implementation:

```python
# In lectures_service.py, ensure cookies aren't logged
logger.info(
    "Starting download pipeline for lecture %s (stream_url=%s, authenticated=%s)",
    lecture.id,
    lecture.stream_url,
    bool(panopto_cookies)  # Log only boolean, not actual cookie value
)
```

## Rollback Plan

If cookie authentication causes issues:

### Quick Rollback (Backend Only):
1. Revert `lectures_service.py` to not pass cookies
2. Revert `panopto_downloader.py` to not accept cookies parameter
3. Extension will still send cookies but backend ignores them
4. Falls back to CloudFront URLs with larger file sizes

### Full Rollback (Extension + Backend):
1. Revert extension to remove cookie extraction
2. Revert backend schema to remove `panopto_cookies` field
3. Revert library to remove cookie parameter
4. Confirm CloudFront URLs still work

## Alternative: Investigate DeliveryInfo API First

**RECOMMENDED:** Before implementing cookie passing, investigate if audio-only CloudFront URLs exist.

### Investigation Steps:

1. Open Chrome DevTools → Network tab
2. Navigate to: `https://rochester.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id={session-id}`
3. Trigger extension download
4. Find `DeliveryInfo.aspx` request in Network tab
5. Copy full JSON response
6. Search for fields containing:
   - "audio"
   - "podcast"
   - Different `StreamUrl` values
   - `StreamType` fields

### What to Look For:

```json
{
  "Delivery": {
    "Id": "...",
    "Streams": [...],
    "PodcastStreams": [
      {
        "StreamUrl": "https://d2y36twrtb17ty.cloudfront.net/...",
        "StreamType": "???",  // Check if "audio" or "podcast"
        "MimeType": "???"     // Check for audio MIME types
      }
    ],
    "AudioStreams": [...],  // Check if this exists
    "AudioPodcastStreams": [...]  // Check if this exists
  }
}
```

If audio-only CloudFront URLs exist in the response, we can:
1. Extract those URLs in the extension
2. Send to backend (no authentication needed)
3. Avoid all cookie complexity
4. Get small audio files without authentication issues

## Success Metrics

After implementation:

### File Size Reduction:
- **Before:** ~460 MB per lecture (video)
- **After:** ~50-100 MB per lecture (audio)
- **Reduction:** 70-90%

### Bandwidth Savings:
- Average lecture: 400 MB saved
- 100 lectures/month: 40 GB saved
- 1000 lectures/semester: 400 GB saved

### Download Time Reduction:
- **Before:** ~5-10 minutes per lecture (video)
- **After:** ~30-60 seconds per lecture (audio)
- **Improvement:** 80-90% faster

### Storage Savings:
- Temporary storage during processing: 90% reduction
- Faster processing → less concurrent storage usage

## Related Files

- Extension: `/Users/novari/Repos/studybuddy-extension-plasmo/src/contents/panopto.tsx`
- Backend schema: `/Users/novari/Repos/studybuddy-backend/app/schemas/__init__.py`
- Backend service: `/Users/novari/Repos/studybuddy-backend/app/services/lectures_service.py`
- Backend downloader: `/Users/novari/Repos/studybuddy-backend/app/services/downloaders/panopto_downloader.py`
- Library: `/Users/novari/Repos/Panopto-Video-DL-lib/PanoptoDownloader/PanoptoDownloader.py`
- Related docs:
  - `.agent/Tasks/extension_audio_optimization.md`
  - `.agent/System/project_architecture.md`

## Questions to Resolve Before Implementation

- [ ] Does the DeliveryInfo API provide audio-only CloudFront URLs? (Check this FIRST)
- [ ] What is Panopto's typical session timeout duration?
- [ ] Should we implement automatic retry on authentication failure?
- [ ] Should we add a feature flag to disable cookie authentication if issues arise?
- [ ] Do we need to handle multiple Panopto domains (rochester.hosted vs. others)?
- [ ] Should we implement cookie refresh mechanism for long downloads?

## Implementation Checklist

### Phase 0: Investigation (DO THIS FIRST)
- [ ] Capture full DeliveryInfo API response
- [ ] Search for audio-only CloudFront URLs
- [ ] Decide: Use cookies OR use audio CloudFront URLs (if available)

### Phase 1: Extension
- [ ] Add cookie permissions to manifest
- [ ] Implement cookie extraction in `sendToBackend`
- [ ] Add error handling for permission denied
- [ ] Test cookie extraction locally
- [ ] Build and test extension

### Phase 2: Backend Schema
- [ ] Add `panopto_cookies` to `LectureDownloadRequest`
- [ ] Update API documentation

### Phase 3: Backend Service
- [ ] Update `request_download` to pass cookies to background task
- [ ] Update `_run_download_pipeline` to accept cookies parameter
- [ ] Add authentication status logging (boolean only, not values)
- [ ] Improve error messages for auth failures

### Phase 4: Backend Downloader
- [ ] Update `PanoptoPackageDownloader.download_video` signature
- [ ] Pass cookies to library

### Phase 5: Library
- [ ] Checkout `relax-yarl` branch
- [ ] Add `cookies` parameter to `download()` function
- [ ] Implement cookie header passing
- [ ] Add auth error handling (401/403)
- [ ] Commit and push changes
- [ ] Merge to master if needed

### Phase 6: Backend Integration
- [ ] Update lock file: `uv lock --upgrade-package panoptodownloader`
- [ ] Sync dependencies: `uv sync`
- [ ] Verify library changes are installed

### Phase 7: Testing
- [ ] Test successful download with valid session
- [ ] Test session expiration scenario
- [ ] Test missing cookies fallback
- [ ] Test error message clarity
- [ ] Monitor file sizes (should be ~90% smaller)
- [ ] Test concurrent downloads
- [ ] Test long download stability

### Phase 8: Monitoring
- [ ] Monitor authentication failure rate
- [ ] Monitor file size reduction
- [ ] Monitor download success rate
- [ ] Track any session expiration issues

### Phase 9: Documentation
- [ ] Update project architecture docs
- [ ] Document cookie security best practices
- [ ] Add troubleshooting guide for auth errors
- [ ] Update API documentation

## Timeline Estimate

- **Investigation (Option B):** 30 minutes - 1 hour
- **Implementation (Option A):** 4-6 hours
  - Extension: 1 hour
  - Backend: 2 hours
  - Library: 1 hour
  - Testing: 1-2 hours
- **Documentation:** 1 hour

**Total:** 5-8 hours depending on complexity and issues encountered

---

**Status:** Not Started
**Priority:** Medium (Significant bandwidth/storage savings, but system works without it)
**Assigned To:** TBD
**Last Updated:** 2025-12-16
