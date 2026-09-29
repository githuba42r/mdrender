package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.data.repository.PushHistoryRepository
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
            val fileId = task.file.fileId
            if (statusOf(fileId) == CloudPushManager.Status.CANCELLED) {
                // Ack so the server drops its copy; the user declined it.
                client.ackReceived(config, fileId, task.file.retrievalKey)
                continue
            }
            onFile(task.file.name)

            val temp = File.createTempFile("cp_", ".tmp", tempDir)
            val ok = runCatching {
                client.downloadFile(config, fileId, task.file.retrievalKey, temp)
                    .getOrThrow()
                // importFileFromTemp consumes and deletes the temp file, so the
                // size has to be read first or every history row would say 0.
                val size = temp.length()
                val folderId = resolveFolder(task.file.path)
                fileRepository.importFileFromTemp(
                    temp,
                    task.file.name,
                    fileRepository.mimeTypeFromExtension(task.file.name),
                    folderId,
                )
                // Ack only once the file is safely in the library. A server that
                // still holds an unacked file can resend it, which beats silently
                // losing one.
                client.ackReceived(config, fileId, task.file.retrievalKey)
                    .getOrThrow()
                pushHistory.record(ROOT_FOLDER, task.file.name, size, folderId)
            }.isSuccess
            if (ok) imported++
            temp.delete()
            manager.onFinished(fileId, ok)
        }
        return imported
    }

    private fun statusOf(fileId: String): CloudPushManager.Status? =
        manager.state.value.firstOrNull { it.fileId == fileId }?.status

    private suspend fun resolveFolder(path: String): Long? {
        var parent = folderRepository.findOrCreateFolder(ROOT_FOLDER, null)
        for (segment in path.split('/').filter { it.isNotBlank() }) {
            parent = folderRepository.findOrCreateFolder(segment, parent)
        }
        return parent
    }

    companion object {
        const val ROOT_FOLDER = "Cloud Push"
    }
}
