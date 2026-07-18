package com.a42r.mdrender.localsend

import android.util.Log
import com.a42r.mdrender.data.repository.FileBookmarks
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import java.io.File
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/** Emitted when a LocalSend REPLACE conflict replaces an existing file. */
data class FileReplacedEvent(
    val fileName: String,
    val folderId: Long?,
    val newFileId: Long
)

/** Progress of a file upload currently in transit. */
data class TransferProgress(
    val sessionId: String,
    val fileId: String,
    val fileName: String,
    val receivedBytes: Long,
    val totalBytes: Long,
    val fileIndex: Int,
    val totalFiles: Int
)

/** A transfer request waiting for the user's Accept/Reject decision. */
data class PendingTransfer(
    val sessionId: String,
    val senderAlias: String,
    val files: List<IncomingFile>,
    internal val decision: CompletableDeferred<Boolean>
)

/** Result of an accepted prepare-upload: session id plus fileId->token map. */
data class SessionGrant(
    val sessionId: String,
    val tokens: Map<String, String>
)

/** An accepted transfer session: expected files and their upload tokens. */
private class ActiveSession(
    val sessionId: String,
    val tokens: Map<String, String>,          // fileId -> token
    val files: Map<String, IncomingFile>,     // fileId -> metadata
    val options: MDRenderOptions,
    val received: MutableSet<String> = mutableSetOf()
)

/**
 * Tracks LocalSend transfer sessions: parks prepare-upload requests until the
 * user accepts or rejects, then imports uploaded files into the encrypted
 * "LocalSend" folder with auto-rename on name conflicts.
 */
@Singleton
class LocalSendSessionManager @Inject constructor(
    private val fileRepository: FileRepository,
    private val folderRepository: FolderRepository
) {
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val _pendingTransfer = MutableStateFlow<PendingTransfer?>(null)
    val pendingTransfer: StateFlow<PendingTransfer?> = _pendingTransfer.asStateFlow()

    /** Emits a user-facing message when a transfer finishes ("3 files received"). */
    private val _lastCompleted = MutableStateFlow<String?>(null)
    val lastCompleted: StateFlow<String?> = _lastCompleted.asStateFlow()

    /** Emits up-to-one event when a REPLACE conflict replaces an existing file. */
    private val _fileReplaced = MutableSharedFlow<FileReplacedEvent>(replay = 1)
    val fileReplaced: SharedFlow<FileReplacedEvent> = _fileReplaced.asSharedFlow()

    /** Emits progress during an active file upload. Null when idle. */
    private val _transferProgress = MutableStateFlow<TransferProgress?>(null)
    val transferProgress: StateFlow<TransferProgress?> = _transferProgress.asStateFlow()

    private val sessions = ConcurrentHashMap<String, ActiveSession>()
    private val sessionWatchdogs = ConcurrentHashMap<String, Job>()

    /** Called by the HTTP server (worker thread). Blocks until the user
     *  decides or the request times out. Returns fileId->token map on accept,
     *  null on reject/timeout. Only one pending/active session at a time.
     *
     *  When [autoAccept] is true (PIN was set and validated), the transfer is
     *  granted immediately without prompting the user. */
    fun requestSession(
        senderAlias: String,
        files: List<IncomingFile>,
        autoAccept: Boolean = false,
        options: MDRenderOptions = MDRenderOptions()
    ): SessionGrant? {
        val sessionId = UUID.randomUUID().toString()

        if (!autoAccept) {
            val decision = CompletableDeferred<Boolean>()
            val pending = PendingTransfer(sessionId, senderAlias, files, decision)
            _pendingTransfer.value = pending
            Log.d(TAG, "awaiting decision for session $sessionId from $senderAlias")

            // Auto-reject if nobody answers.
            scope.launch {
                delay(DECISION_TIMEOUT_MS)
                decision.complete(false)
            }

            val accepted = runBlocking { decision.await() }
            _pendingTransfer.value = null
            if (!accepted) return null
        } else {
            Log.d(TAG, "auto-accepting session $sessionId from $senderAlias")
        }

        val tokens = files.associate { it.id to UUID.randomUUID().toString() }
        sessions[sessionId] = ActiveSession(sessionId, tokens, files.associateBy { it.id }, options)
        resetInactivityWatchdog(sessionId)
        // Safety net: drop the session if the sender never finishes.
        scope.launch {
            delay(SESSION_TIMEOUT_MS)
            sessionWatchdogs.remove(sessionId)?.cancel()
            sessions.remove(sessionId)
        }
        return SessionGrant(sessionId, tokens)
    }

    fun hasBlockingSession(): Boolean = _pendingTransfer.value != null || sessions.isNotEmpty()

    /** Called from the HTTP server worker thread during the chunked body read
     *  of an upload request. Emits progress so the service can update its
     *  notification. */
    fun reportUploadProgress(sessionId: String, fileId: String, bytesRead: Int, contentLength: Int?) {
        val session = sessions[sessionId] ?: return
        resetInactivityWatchdog(sessionId)
        val meta = session.files[fileId] ?: return
        val total = (contentLength?.toLong() ?: meta.size).coerceAtLeast(0L)
        _transferProgress.value = TransferProgress(
            sessionId = sessionId,
            fileId = fileId,
            fileName = meta.fileName,
            receivedBytes = bytesRead.toLong(),
            totalBytes = total,
            fileIndex = session.received.size + 1,
            totalFiles = session.files.size
        )
    }

    /** Reset the inactivity watchdog for a session. The session will be
     *  auto-removed if no progress is reported within [INACTIVITY_TIMEOUT_MS]. */
    private fun resetInactivityWatchdog(sessionId: String) {
        sessionWatchdogs[sessionId]?.cancel()
        sessionWatchdogs[sessionId] = scope.launch {
            delay(INACTIVITY_TIMEOUT_MS)
            Log.w(TAG, "Session $sessionId timed out due to inactivity")
            sessions.remove(sessionId)
            sessionWatchdogs.remove(sessionId)
            _transferProgress.value = null
        }
    }

    fun accept(sessionId: String) {
        _pendingTransfer.value?.takeIf { it.sessionId == sessionId }?.decision?.complete(true)
    }

    fun reject(sessionId: String) {
        _pendingTransfer.value?.takeIf { it.sessionId == sessionId }?.decision?.complete(false)
    }

    /** Resolve a slash-delimited folder path like "Docs/Reports" into a folder
     *  ID, creating missing folders as needed. Empty or blank → root with the
     *  default "LocalSend" folder. */
    private suspend fun resolveFolder(path: String): Long {
        if (path.isBlank()) return folderRepository.findOrCreateFolder(FOLDER_NAME)
        var parentId: Long? = null
        for (segment in path.split("/").filter { it.isNotBlank() }) {
            parentId = folderRepository.findOrCreateFolder(segment, parentId)
        }
        return parentId ?: folderRepository.findOrCreateFolder(FOLDER_NAME)
    }

    /** Validates the upload and stores the file. Called on a server worker
     *  thread after the complete file body has been streamed to [tempFile].
     *  The caller is responsible for deleting [tempFile] after this returns
     *  (importFileFromTemp will have already deleted it on success).
     *  Returns true on success. */
    fun receiveFile(sessionId: String, fileId: String, token: String, tempFile: File): Boolean {
        val session = sessions[sessionId] ?: return false
        if (session.tokens[fileId] != token) return false
        val meta = session.files[fileId] ?: return false

        // --- ConflictStrategy.SKIP early-out ---
        // Check before runBlocking so we can return normally.
        if (session.options.conflict == ConflictStrategy.SKIP && runBlocking {
                fileRepository.findByName(resolveFolder(session.options.folder), meta.fileName) != null
            }) {
            synchronized(session.received) { session.received.add(fileId) }
            if (session.received.size == session.files.size) {
                sessionWatchdogs.remove(sessionId)?.cancel()
                sessions.remove(sessionId)
                _transferProgress.value = null
                _lastCompleted.value = if (session.files.size == 1)
                    "\"${meta.fileName}\" skipped" else "${session.files.size} files received (with skips)"
            }
            return true
        }

        return try {
            var replacedFolderId: Long? = null
            var replacedNewId: Long = 0L
            var wasReplace = false
            runBlocking {
                val folderId = resolveFolder(session.options.folder)
                replacedFolderId = folderId
                val mime = fileRepository.mimeTypeFromExtension(meta.fileName)
                    .takeIf { it != "application/octet-stream" } ?: meta.fileType

                val oldMeta = if (session.options.conflict == ConflictStrategy.REPLACE) {
                    fileRepository.findByName(folderId, meta.fileName)
                } else null

                oldMeta?.let { wasReplace = true }

                val name = when (session.options.conflict) {
                    ConflictStrategy.REPLACE -> meta.fileName
                    ConflictStrategy.SKIP -> meta.fileName
                    ConflictStrategy.RENAME -> fileRepository.uniqueNameInFolder(folderId, meta.fileName)
                }

                if (wasReplace) {
                    val om = oldMeta!!
                    val lastOpened = fileRepository.getLastOpenedAt(om.id)?.coerceAtLeast(0) ?: 0
                    val bookmarks = FileBookmarks(
                        scrollPosition = om.scrollPosition,
                        playbackPosition = om.playbackPosition,
                        lastOpenedAt = lastOpened
                    )
                    replacedNewId = fileRepository.replaceFileFromTemp(
                        tempFile, name, mime, folderId,
                        oldId = om.id, bookmarks = bookmarks
                    )
                } else {
                    replacedNewId = fileRepository.importFileFromTemp(tempFile, name, mime, folderId)
                }
            }
            if (wasReplace) {
                _fileReplaced.tryEmit(FileReplacedEvent(meta.fileName, replacedFolderId, replacedNewId))
            }
            synchronized(session.received) { session.received.add(fileId) }
            if (session.received.size == session.files.size) {
                sessionWatchdogs.remove(sessionId)?.cancel()
                sessions.remove(sessionId)
                _transferProgress.value = null
                _lastCompleted.value = if (session.files.size == 1)
                    "\"${meta.fileName}\" received" else "${session.files.size} files received"
            }
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to store ${meta.fileName}", e)
            false
        }
    }

    fun cancel(sessionId: String) {
        sessions.remove(sessionId)
        sessionWatchdogs.remove(sessionId)?.cancel()
        _pendingTransfer.value?.takeIf { it.sessionId == sessionId }?.decision?.complete(false)
        _transferProgress.value = null
    }

    fun clearCompletedMessage() {
        _lastCompleted.value = null
    }

    companion object {
        private const val TAG = "LocalSendSession"
        private const val FOLDER_NAME = "LocalSend"
        // Long enough to notice the notification, unlock, and tap Accept
        // without the app being open.
        private const val DECISION_TIMEOUT_MS = 3 * 60_000L
        private const val SESSION_TIMEOUT_MS = 30 * 60_000L
        private const val INACTIVITY_TIMEOUT_MS = 5 * 60_000L
    }
}
