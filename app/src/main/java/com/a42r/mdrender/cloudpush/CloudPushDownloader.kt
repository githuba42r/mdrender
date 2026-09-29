package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.data.repository.FileBookmarks
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.data.repository.PushHistoryRepository
import com.a42r.mdrender.localsend.ConflictStrategy
import java.io.File
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
    private val fileRepository: FileRepository,
    private val folderRepository: FolderRepository,
    private val pushHistory: PushHistoryRepository,
) {

    /**
     * Download, import, and ack every outstanding file for [pushId], calling
     * [onFile] with each file name as it starts. Returns how many were imported.
     */
    suspend fun drain(pushId: String, tempDir: File, onFile: (String) -> Unit): Int {
        var imported = 0
        for (task in manager.state.value.filter { it.pushId == pushId }) {
            val fileId = task.fileId
            if (statusOf(fileId) == CloudPushManager.Status.CANCELLED) {
                // Ack so the server drops its copy; the user declined it.
                client.ackReceived(config, fileId, task.file.retrievalKey)
                continue
            }
            onFile(task.file.name)

            val folderId = resolveFolder(task.targetFolder, task.file.path)
            // SKIP is settled before any bytes move: if the name is already
            // taken there is nothing to download, so do not spend the bandwidth.
            if (task.conflict == ConflictStrategy.SKIP &&
                fileRepository.findByName(folderId, task.file.name) != null
            ) {
                client.ackReceived(config, fileId, task.file.retrievalKey)
                pushHistory.record(ROOT_FOLDER, task.file.name, task.file.size, folderId)
                manager.onFinished(fileId, success = true)
                continue
            }

            val temp = File.createTempFile("cp_", ".tmp", tempDir)
            val ok = runCatching {
                client.downloadFile(config, fileId, task.file.retrievalKey, temp)
                    .getOrThrow()
                // importFileFromTemp consumes and deletes the temp file, so the
                // size has to be read first or every history row would say 0.
                val size = temp.length()
                importHonouringConflict(temp, task, folderId)
                // Ack only once the file is safely in the library. A server that
                // still holds an unacked file can resend it, which beats silently
                // losing one.
                client.ackReceived(config, fileId, task.file.retrievalKey)
                    .getOrThrow()
                pushHistory.record(ROOT_FOLDER, task.file.name, size, folderId)
            }.onFailure { e ->
                // 401/404 means the server has forgotten us. Retrying will not
                // help, so ask for re-pairing instead of failing silently forever.
                if (e is PushHttpException && e.isUnregistered) {
                    manager.setReRegistrationNeeded(true)
                }
            }.isSuccess
            if (ok) imported++
            temp.delete()
            manager.onFinished(fileId, ok)
        }
        return imported
    }

    private fun statusOf(fileId: String): CloudPushManager.Status? =
        manager.state.value.firstOrNull { it.fileId == fileId }?.status

    /**
     * Import [temp] under the conflict strategy the sender chose, behaving the
     * same way a LocalSend receive does so one mental model covers both.
     */
    private suspend fun importHonouringConflict(
        temp: File,
        task: CloudPushManager.DownloadTask,
        folderId: Long?,
    ) {
        val mime = fileRepository.mimeTypeFromExtension(task.file.name)
        when (task.conflict) {
            ConflictStrategy.REPLACE -> {
                val old = fileRepository.findByName(folderId, task.file.name)
                if (old == null) {
                    fileRepository.importFileFromTemp(temp, task.file.name, mime, folderId)
                } else {
                    // Carry the reader's place over to the replacement, exactly
                    // as LocalSend does, so overwriting a file does not silently
                    // reset someone's scroll or playback position.
                    val lastOpened = fileRepository.getLastOpenedAt(old.id)?.coerceAtLeast(0) ?: 0
                    fileRepository.replaceFileFromTemp(
                        temp, task.file.name, mime, folderId,
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
                fileRepository.importFileFromTemp(temp, task.file.name, mime, folderId)
            ConflictStrategy.RENAME -> fileRepository.importFileFromTemp(
                temp,
                fileRepository.uniqueNameInFolder(folderId, task.file.name),
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
        const val ROOT_FOLDER = "Cloud Push"
    }
}
