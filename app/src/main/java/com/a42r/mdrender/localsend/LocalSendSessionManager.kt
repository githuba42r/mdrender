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
     *  null on reject/timeout. Only one pending/active session at a time. */
    fun requestSession(senderAlias: String, files: List<IncomingFile>): SessionGrant? {
        val sessionId = UUID.randomUUID().toString()
        val decision = CompletableDeferred<Boolean>()
        val pending = PendingTransfer(sessionId, senderAlias, files, decision)
        _pendingTransfer.value = pending

        // Auto-reject if nobody answers.
        scope.launch {
            delay(DECISION_TIMEOUT_MS)
            decision.complete(false)
        }

        val accepted = runBlocking { decision.await() }
        _pendingTransfer.value = null
        if (!accepted) return null

        val tokens = files.associate { it.id to UUID.randomUUID().toString() }
        sessions[sessionId] = ActiveSession(sessionId, tokens, files.associateBy { it.id })
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

    /** Validates the upload and stores the file. Called on a server worker
     *  thread with the complete file body. Returns true on success. */
    fun receiveFile(sessionId: String, fileId: String, token: String, bytes: ByteArray): Boolean {
        val session = sessions[sessionId] ?: return false
        if (session.tokens[fileId] != token) return false
        val meta = session.files[fileId] ?: return false

        return try {
            runBlocking {
                val folderId = folderRepository.findOrCreateFolder(FOLDER_NAME)
                val mime = fileRepository.mimeTypeFromExtension(meta.fileName)
                    .takeIf { it != "application/octet-stream" } ?: meta.fileType
                val name = fileRepository.uniqueNameInFolder(folderId, meta.fileName)
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
