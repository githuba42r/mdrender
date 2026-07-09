# LocalSend 500 Error & Stuck Sessions Handover — 2026-07-09

## Background

Sending large MP3 files (~37–117 MB) from a Python CLI sender to MDRender's
LocalSend receiver produces two related bugs:

1. **HTTP 500 during upload** — the file body is streamed (chunked or
   Content-Length) but the server returns 500 instead of 200.
2. **Stuck session** — after the 500, the session stays in the in-memory
   `ConcurrentHashMap` for 30 minutes (SESSION_TIMEOUT_MS), rejecting all
   further transfers with 409 Conflict until the app is force-stopped.

Once a session is stuck, the only recovery is force-stop (which kills the
entire `LocalSendService`). The sender retries with backoff for minutes, but
the stale session persists and every further retry gets 409.

## Files involved

| File | Role |
|------|------|
| `app/.../localsend/LocalSendServer.kt` | NanoHTTPD server: routes to `handleUpload` |
| `app/.../localsend/LocalSendSessionManager.kt` | Session lifecycle: create, track, cancel, cleanup |
| `app/.../localsend/LocalSendService.kt` | Foreground service hosting the server |
| `app/.../localsend/LocalSendProtocol.kt` | Protocol constants and data classes |
| `app/.../data/repository/FileRepository.kt` | Encrypted file storage, threshold at 10 MB |
| `tools/localsend-send/localsend-send.py` | CLI sender (already fixed: chunked→Content-Length) |

## Issue 1: HTTP 500 on upload

### Observed behaviour

After the CLI sender's `prepare-upload` is accepted, the `upload` endpoint
returns HTTP 500. The error is caught by the catch-all in `LocalSendServer.serve()`
(around line 47):

```kotlin
} catch (e: Exception) {
    Log.e(TAG, "Error serving ${session.uri}", e)
    error(Response.Status.INTERNAL_ERROR, "Internal error")
}
```

The logcat `TAG = "LocalSendServer"` will contain the stack trace of whatever
exception is thrown.

### Possible root causes (investigate in order)

**1. NanoHTTPD chunked transfer encoding bug**

NanoHTTPD's built-in chunked TE handling may break on large payloads
(> 100 MB). The CLI sender originally used `Transfer-Encoding: chunked` and
got the 500. On 2026-07-09 the sender was changed to `Content-Length` instead
but this hasn't been tested yet (every upload attempt since then hit the
"busy" state from a prior stuck session, so the fix landed without validation).

**To test:** force-stop MDRender, send a *small* file (a few KB) first to
verify Content-Length works, then escalate to 37 MB, then 117 MB.

**2. InputStream read loop in handleUpload**

`handleUpload` (lines 100–137) reads the body via `session.inputStream`.
If NanoHTTPD buffers the full body before handing it to the handler, a 117 MB
body could OOM the heap. If it streams, the read loop should be fine.

Check NanoHTTPD's body buffering behaviour — it may cache the body in memory
when using the default `serve(IHTTPSession)` signature.

**3. CryptoEngine.encryptStream failure**

For files > 10 MB, `FileRepository.importFileFromTemp` calls
`cryptoEngine.encryptStream(input, output)`. If the crypto layer has a bug
with large streams (e.g., buffer limits, AES-GCM chunking), it would throw
and propagate up as a 500. Check `LogCat` for any exception from this path.

**4. fileRepository.uniqueNameInFolder takes too long**

The `RENAME` conflict strategy calls `fileRepository.uniqueNameInFolder()`
which runs a `getNamesInFolder` query. On a folder with thousands of files
this could be slow but should not 500.

## Issue 2: Stuck sessions (critical)

### Root cause

When `handleUpload` fails (whether 500 or 403):

1. The `finally` block deletes the temp file — good.
2. But `sessions` in `LocalSendSessionManager` still holds the
   `ActiveSession` entry — **nothing removes it on failure**.
3. The 30-minute SESSION_TIMEOUT_MS coroutine (launched in `requestSession`
   line 117) will eventually clean it up, but:
   - The sender retries every few seconds, so by the time 30 minutes pass
     the user has long given up.
   - Force-stop kills the service + scope, wiping the map — then the user
     tries again, hits the same 500, and gets stuck again.

The `sessions` map is also checked by `hasBlockingSession()` which gates
`prepare-upload`. Any stale entry there means the phone rejects new
transfers for 30 minutes.

### Required fixes

**Fix A: Remove session on upload failure in `handleUpload`**

In `LocalSendServer.handleUpload`, after a failed body read or when
`receiveFile` returns false, call `sessionManager.cancel(sessionId)`.

```kotlin
// Pseudo-code — wrap the body-read block in try/catch:
try {
    // ... read body stream to tempFile ...
} catch (e: Exception) {
    sessionManager.cancel(sessionId)
    throw e  // re-throw so serve() still returns 500
}
```

And after `receiveFile` returns false:
```kotlin
if (!sessionManager.receiveFile(sessionId, fileId, token, tempFile)) {
    sessionManager.cancel(sessionId)
    return error(Response.Status.FORBIDDEN, "Invalid session/token")
}
```

**Fix B: Guard against duplicate sessions in `requestSession`**

`requestSession` currently only checks `hasBlockingSession()` at the top
(a pending transfer OR any active session). This produces the 409 correctly,
but doesn't solve the "session is stuck" problem — Fix A above addresses
the root cause by cleaning up on failure.

Consider also: what happens if `handleUpload` is called for a session that
doesn't exist in the map (e.g., after timeout). Currently it returns a
command-response without the session existing, which will cause `receiveFile`
to return false immediately. Fix A handles this by calling `cancel` (which is
a no-op for unknown session IDs, safe).

**Fix C: Session inactivity watchdog**

Add a secondary timeout coroutine per session that fires after, say,
5 minutes of inactivity (no bytes received). This prevents a failed upload
from blocking the receiver for the full 30 minutes. Wire it to
`reportUploadProgress` — reset the timer every time progress is reported.
When it fires, remove the session so new transfers can come in.

```kotlin
// In ActiveSession or a companion watchdog map:
private val sessionWatchdogs = ConcurrentHashMap<String, Job>()

private fun resetInactivityWatchdog(sessionId: String) {
    sessionWatchdogs[sessionId]?.cancel()
    sessionWatchdogs[sessionId] = scope.launch {
        delay(INACTIVITY_TIMEOUT_MS)  // 5 minutes
        sessions.remove(sessionId)
        sessionWatchdogs.remove(sessionId)
    }
}
```

Call `resetInactivityWatchdog(sessionId)` from `reportUploadProgress` and
when the session is created. Cancel it from `cancel()` and when the session
completes normally.

### Acceptance criteria

1. Send a 37 MB MP3 to MDRender — completes with HTTP 200, file is
   importable in the app's file browser.
2. Kill the sender mid-upload (e.g., Ctrl-C the Python script) — the
   session is cleaned up within 5 minutes (inactivity watchdog) or
   immediately (if the connection drops).
3. After a failed upload, immediately send another file — the second
   attempt gets through without "busy" error.
4. Send a 117 MB MP3 — completes successfully.

## Appendix: Player notification

There was also a reference to a persistent transfer notification that stayed
on the phone even after the session expired. This is likely the progress
notification (NOTIF_ID_PROGRESS = 103) at
`LocalSendService.updateTransferProgressNotification`. When the session
drops out of the map, `_transferProgress` is set to null by the normal
completion path in `receiveFile`, but NOT by the session timeout or cancel
path. Ensure `cancel()` also sets `_transferProgress.value = null` and
`_lastCompleted.value = ...` so the notification is dismissed.

