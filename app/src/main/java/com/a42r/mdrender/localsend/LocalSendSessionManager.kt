package com.a42r.mdrender.localsend

import android.util.Log
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

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

    private val sessions = ConcurrentHashMap<String, ActiveSession>()

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
        // Safety net: drop the session if the sender never finishes.
        scope.launch {
            delay(SESSION_TIMEOUT_MS)
            sessions.remove(sessionId)
        }
        return SessionGrant(sessionId, tokens)
    }

    fun hasBlockingSession(): Boolean = _pendingTransfer.value != null || sessions.isNotEmpty()

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
     *  thread with the complete file body. Returns true on success. */
    fun receiveFile(sessionId: String, fileId: String, token: String, bytes: ByteArray): Boolean {
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
                sessions.remove(sessionId)
                _lastCompleted.value = if (session.files.size == 1)
                    "\"${meta.fileName}\" skipped" else "${session.files.size} files received (with skips)"
            }
            return true
        }

        return try {
            runBlocking {
                val folderId = resolveFolder(session.options.folder)
                val mime = fileRepository.mimeTypeFromExtension(meta.fileName)
                    .takeIf { it != "application/octet-stream" } ?: meta.fileType

                val name = when (session.options.conflict) {
                    ConflictStrategy.REPLACE -> meta.fileName
                    ConflictStrategy.SKIP -> meta.fileName // early-out above already handled this
                    ConflictStrategy.RENAME -> fileRepository.uniqueNameInFolder(folderId, meta.fileName)
                }
                fileRepository.importFile(name, mime, bytes, folderId)
            }
            synchronized(session.received) { session.received.add(fileId) }
            if (session.received.size == session.files.size) {
                sessions.remove(sessionId)
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
        _pendingTransfer.value?.takeIf { it.sessionId == sessionId }?.decision?.complete(false)
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
    }
}
