package com.a42r.mdrender.cloudpush

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The download queue for cloud push.
 *
 * A manifest is complete and authoritative, so there is nothing to reconcile
 * and no timer to wait on: [enqueue] takes the whole file list and hands the
 * service everything not already known for that push. Dedup is by fileId
 * because a re-rung doorbell re-fetches a manifest that may still list a file
 * whose download is queued or in flight.
 */
@Singleton
class CloudPushManager @Inject constructor() {

    enum class Status { QUEUED, DOWNLOADING, DONE, FAILED, CANCELLED }

    data class DownloadTask(
        val pushId: String,
        val file: PushCrypto.ManifestFile,
        val serverUrl: String,
        val status: Status = Status.QUEUED,
        val progress: Float = 0f,
    ) {
        val fileId: String get() = file.fileId
    }

    private val _state = MutableStateFlow<List<DownloadTask>>(emptyList())
    val state: StateFlow<List<DownloadTask>> = _state.asStateFlow()

    private val readyCallbacks = mutableListOf<(String) -> Unit>()

    private val _needsReRegistration = MutableStateFlow(false)

    /** Set when the server no longer knows this device, or rejects its credentials. */
    val needsReRegistration: StateFlow<Boolean> = _needsReRegistration.asStateFlow()

    fun setReRegistrationNeeded(needed: Boolean) {
        _needsReRegistration.value = needed
    }

    /** Register a listener woken when a push has work to do. */
    fun onPushReady(callback: (String) -> Unit) {
        readyCallbacks.add(callback)
    }

    /**
     * Queue every file in [files] that this push has not been told about yet,
     * then wake the service — but only if there is genuinely new work. Waking on
     * a fully-known manifest would restart downloads that are already running.
     */
    fun enqueue(doorbell: PushCrypto.Doorbell, files: List<PushCrypto.ManifestFile>) {
        val existing = _state.value
            .filter { it.pushId == doorbell.pushId }
            .map { it.file.fileId }
            .toSet()
        val newTasks = files
            .filter { it.fileId !in existing }
            .map { DownloadTask(doorbell.pushId, it, doorbell.serverUrl) }
        if (newTasks.isEmpty()) return
        _state.update { it + newTasks }
        readyCallbacks.toList().forEach { it(doorbell.pushId) }
    }

    /** Tasks still worth acting on for [pushId]. */
    fun pending(pushId: String): List<DownloadTask> = _state.value.filter {
        it.pushId == pushId &&
            (it.status == Status.QUEUED || it.status == Status.DOWNLOADING)
    }

    fun cancel(fileId: String) {
        _state.update { list ->
            list.map { if (it.fileId == fileId) it.copy(status = Status.CANCELLED) else it }
        }
    }

    fun onFinished(fileId: String, success: Boolean) {
        _state.update { list ->
            list.map {
                if (it.fileId == fileId) {
                    it.copy(
                        status = if (success) Status.DONE else Status.FAILED,
                        progress = if (success) 1f else it.progress,
                    )
                } else {
                    it
                }
            }
        }
    }
}
