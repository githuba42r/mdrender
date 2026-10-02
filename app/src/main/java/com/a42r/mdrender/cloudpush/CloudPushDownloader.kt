package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.data.repository.FileBookmarks
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.data.repository.PushHistoryRepository
import com.a42r.mdrender.localsend.ConflictStrategy
import java.io.File
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Drains a verified push into the user's library.
 *
 * Kept apart from [CloudPushDownloadService] because this is where the
 * awkward parts live — reading the size before the temp file is consumed,
 * acking a cancelled file so the server stops offering it, and not acking a
 * failed one so the server can retry — and none of that is worth testing
 * through a Service.
 */
@Singleton
class CloudPushDownloader @Inject constructor(
    private val manager: CloudPushManager,
    private val client: PushClient,
    private val config: PushServerConfig,
    private val crypto: PushCrypto,
    private val fileRepository: FileRepository,
    private val folderRepository: FolderRepository,
    private val pushHistory: PushHistoryRepository,
) {

    /** The unwrapped content key, cached for the session (never persisted). */
    private var cek: ByteArray? = null

    /**
     * Download, import, and ack every outstanding file for [pushId], calling
     * [onFile] with each file name as it starts. Returns how many were imported.
     */
    suspend fun drain(pushId: String, tempDir: File, onFile: (String) -> Unit): Int {
        var imported = 0
        // Plaintext pushes carry name/path in the signed manifest; encrypted
        // ones carry them inside the envelope and can only be resolved after
        // download + decrypt (design §7a), so nothing name-based is settled
        // before bytes move in that mode.
        val encrypted = config.encryptionMode == "on"
        val matched = manager.state.value.filter { it.pushId == pushId }
        android.util.Log.d(
            "CloudPushDownload",
            "drain pushId=$pushId encrypted=$encrypted known=${manager.state.value.size} matched=${matched.size}",
        )
        for (task in matched) {
            val fileId = task.fileId
            if (statusOf(fileId) == CloudPushManager.Status.CANCELLED) {
                // Ack so the server drops its copy; the user declined it.
                client.ackReceived(config, fileId, task.file.retrievalKey)
                continue
            }
            onFile(if (encrypted) "encrypted file" else task.file.name)

            // Plaintext resolves its folder up front so SKIP can settle before
            // any bytes move; encrypted folders only exist after decrypt.
            val plainFolder = if (!encrypted) {
                val preFolder = resolveFolder(task.targetFolder, task.file.path)
                if (task.conflict == ConflictStrategy.SKIP &&
                    fileRepository.findByName(preFolder, task.file.name) != null
                ) {
                    client.ackReceived(config, fileId, task.file.retrievalKey)
                    pushHistory.record(ROOT_FOLDER, task.file.name, task.file.size, preFolder)
                    manager.onFinished(fileId, success = true)
                    continue
                }
                preFolder
            } else {
                null
            }

            val temp = File.createTempFile("cp_", ".tmp", tempDir)
            var name = task.file.name
            var skipped = false
            val ok = runCatching {
                client.downloadFile(config, fileId, task.file.retrievalKey, temp)
                    .getOrThrow()
                val target: Long
                if (encrypted) {
                    // Server-enforced encryption (design §7b): the bytes are
                    // opaque ciphertext; decrypt, then read the name/folder out
                    // of the envelope — the server holds neither (D13).
                    val key = cek ?: refreshCek()
                        ?: throw IOException("no content key for an encrypted push")
                    val plain = crypto.decryptFile(temp.readBytes(), key)
                    if (plain == null) {
                        android.util.Log.w("CloudPushDownload", "decryptFile returned null")
                        throw IOException("encrypted file did not authenticate")
                    }
                    val env = crypto.parseEnvelope(plain)
                    if (env == null) {
                        android.util.Log.w(
                            "CloudPushDownload",
                            "parseEnvelope returned null (len=${plain.size})",
                        )
                        throw IOException("encrypted payload is not a valid envelope")
                    }
                    temp.writeBytes(env.bytes)
                    name = env.name.ifBlank { task.file.name }
                    target = resolveFolder(task.targetFolder, env.path)
                } else {
                    target = plainFolder ?: throw IOException("no destination folder")
                }
                if (task.conflict == ConflictStrategy.SKIP &&
                    fileRepository.findByName(target, name) != null
                ) {
                    // Taken after all (or, encrypted, only knowable now):
                    // ack without importing so the server stops offering it.
                    client.ackReceived(config, fileId, task.file.retrievalKey)
                    pushHistory.record(ROOT_FOLDER, name, temp.length(), target)
                    skipped = true
                } else {
                    // importFileFromTemp consumes and deletes the temp file, so the
                    // size has to be read first or every history row would say 0.
                    val size = temp.length()
                    importHonouringConflict(temp, task, target, name)
                    // Ack only once the file is safely in the library. A server that
                    // still holds an unacked file can resend it, which beats silently
                    // losing one.
                    client.ackReceived(config, fileId, task.file.retrievalKey)
                        .getOrThrow()
                    pushHistory.record(ROOT_FOLDER, name, size, target)
                }
            }.onFailure { e ->
                android.util.Log.w(
                    "CloudPushDownload",
                    "task ${task.file.fileId} failed: ${e.javaClass.simpleName}: ${e.message}",
                )
                // 401/404 means the server has forgotten us. Retrying will not
                // help, so ask for re-pairing instead of failing silently forever.
                if (e is PushHttpException && e.isUnregistered) {
                    manager.setReRegistrationNeeded(true)
                }
            }.isSuccess
            if (ok && !skipped) imported++
            temp.delete()
            manager.onFinished(fileId, ok)
        }
        return imported
    }

    private fun statusOf(fileId: String): CloudPushManager.Status? =
        manager.state.value.firstOrNull { it.fileId == fileId }?.status

    /**
     * Fetch the sealed content key from the server and unwrap it. The server
     * holds only the sealed blob, so it cannot do this itself (design §7c).
     */
    private suspend fun refreshCek(): ByteArray? {
        val sealed = client.fetchSealedCek(config).getOrElse {
            android.util.Log.w(
                "CloudPushDownload",
                "fetchSealedCek failed: ${it.javaClass.simpleName}: ${it.message}",
            )
            return null
        }
        val key = crypto.decryptSealedCek(sealed)
        android.util.Log.d(
            "CloudPushDownload",
            "refreshCek sealed=${sealed.length} unwrapped=${key?.size}",
        )
        return key?.also { cek = it }
    }

    /**
     * Import [temp] under the conflict strategy the sender chose, behaving the
     * same way a LocalSend receive does so one mental model covers both.
     * [name] is the real filename — for encrypted pushes it came from the
     * envelope, not the manifest's opaque id.
     */
    private suspend fun importHonouringConflict(
        temp: File,
        task: CloudPushManager.DownloadTask,
        folderId: Long,
        name: String,
    ) {
        val mime = fileRepository.mimeTypeFromExtension(name)
        when (task.conflict) {
            ConflictStrategy.REPLACE -> {
                val old = fileRepository.findByName(folderId, name)
                if (old == null) {
                    fileRepository.importFileFromTemp(temp, name, mime, folderId)
                } else {
                    // Carry the reader's place over to the replacement, exactly
                    // as LocalSend does, so overwriting a file does not silently
                    // reset someone's scroll or playback position.
                    val lastOpened = fileRepository.getLastOpenedAt(old.id)?.coerceAtLeast(0) ?: 0
                    fileRepository.replaceFileFromTemp(
                        temp, name, mime, folderId,
                        oldId = old.id,
                        bookmarks = FileBookmarks(
                            scrollPosition = old.scrollPosition,
                            playbackPosition = old.playbackPosition,
                            lastOpenedAt = lastOpened,
                        ),
                    )
                }
            }
            // Already dealt with before the download; if we got here the name
            // was free after all, so a plain import is the right outcome.
            ConflictStrategy.SKIP ->
                fileRepository.importFileFromTemp(temp, name, mime, folderId)
            ConflictStrategy.RENAME -> fileRepository.importFileFromTemp(
                temp,
                fileRepository.uniqueNameInFolder(folderId, name),
                mime,
                folderId,
            )
        }
    }

    /**
     * Resolve a slash-delimited folder path like "Story/cloud-send-images" into
     * a folder ID, creating missing folders as needed. Blank falls back to the
     * app's Cloud Push root, matching how LocalSend resolves its own option.
     *
     * A per-file path wins over the push-wide [targetFolder] when present.
     */
    private suspend fun resolveFolder(targetFolder: String, filePath: String): Long {
        val path = filePath.ifBlank { targetFolder }.trim('/')
        if (path.isBlank()) return folderRepository.findOrCreateFolder(ROOT_FOLDER)
        var parent: Long? = null
        for (segment in path.split('/').filter { it.isNotBlank() }) {
            parent = folderRepository.findOrCreateFolder(segment, parent)
        }
        return parent ?: folderRepository.findOrCreateFolder(ROOT_FOLDER)
    }

    companion object {
        // Cloud push is the same receive experience as LocalSend, so files land
        // in the same root folder rather than a separate "Cloud Push" tree.
        const val ROOT_FOLDER = com.a42r.mdrender.localsend.LocalSendSessionManager.FOLDER_NAME
    }
}
