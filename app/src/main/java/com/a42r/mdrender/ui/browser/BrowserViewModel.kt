package com.a42r.mdrender.ui.browser

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.entity.FolderEntity
import android.content.Context
import android.content.Intent
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderNode
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.localsend.LocalSendPrefs
import com.a42r.mdrender.localsend.LocalSendService
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.share.SharePlan
import com.a42r.mdrender.share.ShareOutManager
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class BrowserUiState(
    val currentFolderId: Long? = null,
    val breadcrumbPath: List<FolderEntity> = emptyList(),
    val folders: List<FolderEntity> = emptyList(),
    val files: List<FileEntity> = emptyList(),
    val isGridView: Boolean = true,
    val isLoading: Boolean = false
)

data class UndoDelete(
    val message: String,
    val action: suspend () -> Unit
)

/** A candidate destination for the Move dialog: a folder and its tree depth. */
data class MoveTarget(
    val folder: FolderEntity,
    val depth: Int
)

/** A move name-conflict currently awaiting the user's decision. */
data class MoveConflict(
    val file: FileEntity,
    val targetFolderName: String,
    val remaining: Int
)

@HiltViewModel
class BrowserViewModel @Inject constructor(
    private val folderRepository: FolderRepository,
    private val fileRepository: FileRepository,
    private val browserPrefs: BrowserPreferencesStore,
    private val appLock: AppLock,
    private val localSendPrefs: LocalSendPrefs,
    private val shareOutManager: ShareOutManager,
    @ApplicationContext private val context: Context
) : ViewModel() {

    private val _uiState = MutableStateFlow(BrowserUiState(isGridView = browserPrefs.isGridView))
    val uiState: StateFlow<BrowserUiState> = _uiState.asStateFlow()

    /** Whether hidden folders are currently revealed (drives badges + Unhide). */
    val revealHidden: StateFlow<Boolean> = appLock.revealHidden

    /** Whether the LocalSend receiver is on (drives the top-bar toggle). */
    val localSendEnabled: StateFlow<Boolean> = localSendPrefs.enabledFlow

    fun setLocalSendEnabled(enabled: Boolean) {
        localSendPrefs.enabled = enabled
        if (enabled) LocalSendService.start(context) else LocalSendService.stop(context)
    }

    private val _undoDelete = MutableSharedFlow<UndoDelete>()
    val undoDelete: SharedFlow<UndoDelete> = _undoDelete.asSharedFlow()

    // --- Sharing ---------------------------------------------------------

    /** Non-null while the hidden-item warning dialog should be showing. */
    private val _pendingShare = MutableStateFlow<SharePlan.NeedsConfirmation?>(null)
    val pendingShare: StateFlow<SharePlan.NeedsConfirmation?> = _pendingShare.asStateFlow()

    /** Chooser intents ready to launch. */
    private val _shareIntent = MutableSharedFlow<Intent>()
    val shareIntent: SharedFlow<Intent> = _shareIntent.asSharedFlow()

    /** Share failures, for the snackbar. */
    private val _shareError = MutableSharedFlow<String>()
    val shareError: SharedFlow<String> = _shareError.asSharedFlow()

    /** True while files are being decrypted and staged. */
    private val _shareInProgress = MutableStateFlow(false)
    val shareInProgress: StateFlow<Boolean> = _shareInProgress.asStateFlow()

    /** Entry point for both the top-bar Share action and the file menu.
     *  Hidden items divert to the confirmation dialog via [pendingShare].
     *
     *  [_shareInProgress] is set synchronously here, before the coroutine
     *  even launches, so a second tap arriving while we're still awaiting
     *  the DB lookups below is rejected by the guard above — not just once
     *  [stageAndShareLocked] starts staging. */
    fun requestShare(ids: Collection<Long>) {
        if (_shareInProgress.value || _pendingShare.value != null) return
        _shareInProgress.value = true
        viewModelScope.launch {
            var handedOffToStaging = false
            try {
                val files = ids.mapNotNull { fileRepository.getFileMetadata(it) }
                val hiddenByFolder = files.map { it.folderId }.distinct()
                    .associateWith { folderRepository.isInHiddenTree(it) }
                when (val plan = SharePlan.of(files) { hiddenByFolder[it.folderId] == true }) {
                    is SharePlan.ShareNow -> {
                        handedOffToStaging = true
                        stageAndShareLocked(plan.files)
                    }
                    is SharePlan.NeedsConfirmation -> {
                        _shareInProgress.value = false
                        _pendingShare.value = plan
                    }
                    SharePlan.None -> _shareInProgress.value = false
                }
            } finally {
                // stageAndShareLocked owns clearing the flag on the ShareNow
                // path (including any error inside it). Every other exit —
                // including an exception thrown before a plan was even
                // reached (e.g. in getFileMetadata/isInHiddenTree) — must
                // clear it here so the flag can never get stuck true.
                if (!handedOffToStaging) _shareInProgress.value = false
            }
        }
    }

    fun confirmShareAll() {
        val pending = _pendingShare.value ?: return
        _pendingShare.value = null
        stageAndShare(pending.visible + pending.hidden)
    }

    fun confirmShareVisibleOnly() {
        val pending = _pendingShare.value ?: return
        _pendingShare.value = null
        if (pending.visible.isNotEmpty()) stageAndShare(pending.visible)
    }

    fun cancelShare() {
        _pendingShare.value = null
    }

    private fun stageAndShare(files: List<FileEntity>) {
        if (files.isEmpty()) return
        viewModelScope.launch {
            _shareInProgress.value = true
            stageAndShareLocked(files)
        }
    }

    /** Stages and shares [files], assuming [_shareInProgress] is already
     *  true (set by the caller before entering this coroutine). Always
     *  clears the flag when done — success, staging failure, empty input,
     *  or an exception escaping [ShareOutManager.stage] — so callers can
     *  treat "handed off here" as "the flag will end up false". */
    private suspend fun stageAndShareLocked(files: List<FileEntity>) {
        try {
            if (files.isEmpty()) return
            when (val result = shareOutManager.stage(files)) {
                is ShareOutManager.StageResult.Ready -> _shareIntent.emit(result.intent)
                is ShareOutManager.StageResult.Failed ->
                    _shareError.emit("Couldn't prepare \"${result.fileName}\" — nothing was shared")
            }
        } finally {
            _shareInProgress.value = false
        }
    }

    private var undoJob: Job? = null
    private var contentJob: Job? = null
    private var initialized = false

    /** First-load entry point. Restores the last viewed folder when no
     *  explicit folder was requested; no-op if this ViewModel instance has
     *  already initialized (e.g. returning from the Import screen). */
    fun initialize(requestedFolderId: Long?) {
        if (initialized) return
        initialized = true
        viewModelScope.launch {
            val requested = requestedFolderId
                ?: browserPrefs.lastFolderId?.takeIf { folderRepository.folderExists(it) }
            // Never land inside a hidden tree on (re)start — authentication
            // alone must not expose hidden content. Fall back to root.
            val target = requested?.takeUnless {
                !appLock.revealHidden.value && folderRepository.isInHiddenTree(it)
            }
            navigateToFolder(target)
        }
    }

    fun navigateToFolder(folderId: Long?) {
        browserPrefs.lastFolderId = folderId
        _uiState.update { it.copy(currentFolderId = folderId) }
        loadContent(folderId)
        // Secure the window while inside a hidden tree so the app-switcher
        // snapshot never captures hidden content.
        viewModelScope.launch {
            appLock.setDisplayingHidden(folderId != null && folderRepository.isInHiddenTree(folderId))
        }
    }

    private fun loadContent(folderId: Long?) {
        contentJob?.cancel()
        contentJob = viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true) }
            launch {
                combine(
                    folderRepository.getChildrenOf(folderId),
                    appLock.revealHidden
                ) { folders, reveal ->
                    if (reveal) folders else folders.filter { !it.hidden }
                }.collect { folders ->
                    _uiState.update { it.copy(folders = folders) }
                }
            }
            launch {
                fileRepository.getFilesInFolder(folderId).collect { files ->
                    _uiState.update { it.copy(files = files, isLoading = false) }
                }
            }
            val path = folderId?.let { folderRepository.getPathToFolder(it) } ?: emptyList()
            _uiState.update { it.copy(breadcrumbPath = path) }
        }
    }

    fun createFolder(name: String) {
        viewModelScope.launch {
            folderRepository.createFolder(name, _uiState.value.currentFolderId)
        }
    }

    fun deleteFolder(id: Long) {
        viewModelScope.launch {
            folderRepository.deleteFolder(id)
        }
    }

    /** Blocked when the destination already has a same-named sibling. */
    fun moveFolder(id: Long, targetFolderId: Long?) {
        viewModelScope.launch {
            val folder = folderRepository.getFolder(id) ?: return@launch
            if (folderRepository.siblingNameExists(targetFolderId, folder.name, excludeId = id)) {
                _userMessage.emit(
                    "A folder named \"${folder.name}\" already exists in \"${folderDisplayName(targetFolderId)}\""
                )
                return@launch
            }
            folderRepository.moveFolder(id, targetFolderId)
        }
    }

    /** The folder plus its descendants — excluded as move destinations. */
    suspend fun getSubtreeIds(folderId: Long): Set<Long> =
        folderRepository.getSubtreeIds(folderId)

    fun deleteFile(id: Long) {
        viewModelScope.launch {
            val entity = fileRepository.getFileMetadata(id) ?: return@launch
            fileRepository.deleteFile(id)

            // Offer undo
            val undo = UndoDelete(
                message = "Deleted \"${entity.name}\"",
                action = {
                    // Re-import is hard here; just re-insert the original encrypted entity
                    // For simplicity, we re-insert the entity (Room auto-generates new ID)
                    fileRepository.importFile(entity.name, entity.mimeType,
                        // Can't recover plaintext; skip true undo for now
                        byteArrayOf(), entity.folderId)
                }
            )
            _undoDelete.emit(undo)

            // Auto-commit after 5 seconds
            undoJob?.cancel()
            undoJob = viewModelScope.launch {
                delay(5000)
                // Delete is already committed at this point
            }
        }
    }

    fun renameFile(id: Long, newName: String) {
        viewModelScope.launch {
            fileRepository.renameFile(id, newName)
        }
    }

    // --- Move with conflict resolution ------------------------------------

    /** Non-null while the Replace/Skip dialog should be showing. */
    private val _moveConflict = MutableStateFlow<MoveConflict?>(null)
    val moveConflict: StateFlow<MoveConflict?> = _moveConflict.asStateFlow()

    /** One-off user-facing notices (batch summaries, blocked folder moves). */
    private val _userMessage = MutableSharedFlow<String>()
    val userMessage: SharedFlow<String> = _userMessage.asSharedFlow()

    private var conflictAnswer: CompletableDeferred<ConflictDecision>? = null

    private val walker = MoveConflictWalker(
        findByName = { folderId, name -> fileRepository.findByName(folderId, name) },
        moveFile = { id, folderId -> fileRepository.moveFile(id, folderId) },
        deleteFile = { id -> fileRepository.deleteFile(id) }
    )

    /** Moves [ids] into [targetFolderId], prompting Replace/Skip per name
     *  conflict. Used by both the single-file and multi-select Move dialogs. */
    fun moveFilesResolvingConflicts(ids: Collection<Long>, targetFolderId: Long?) {
        viewModelScope.launch {
            val files = ids.mapNotNull { fileRepository.getFileMetadata(it) }
            val targetName = folderDisplayName(targetFolderId)
            val result = walker.run(files, targetFolderId) { file, remaining ->
                val answer = CompletableDeferred<ConflictDecision>()
                conflictAnswer = answer
                _moveConflict.value = MoveConflict(file, targetName, remaining)
                try {
                    answer.await()
                } finally {
                    _moveConflict.value = null
                }
            }
            val summary = buildString {
                append("Moved ${result.moved}")
                if (result.replaced > 0) append(", replaced ${result.replaced}")
                if (result.skipped > 0) append(", skipped ${result.skipped}")
            }
            _userMessage.emit(summary)
        }
    }

    /** Called by the conflict dialog's buttons (and its dismiss = CancelBatch). */
    fun resolveMoveConflict(decision: ConflictDecision) {
        conflictAnswer?.takeIf { !it.isCompleted }?.complete(decision)
    }

    private suspend fun folderDisplayName(folderId: Long?): String =
        folderId?.let { folderRepository.getFolder(it)?.name } ?: "Home"

    // --- Name availability checks (rename / create dialogs) ---------------

    /** True when another file in [folderId] already uses [name] (exact match). */
    suspend fun fileNameExists(folderId: Long?, name: String, excludeId: Long): Boolean =
        fileRepository.findByName(folderId, name)?.let { it.id != excludeId } ?: false

    /** True when a sibling folder already uses [name] (case-insensitive). */
    suspend fun folderNameExists(parentId: Long?, name: String, excludeId: Long? = null): Boolean =
        folderRepository.siblingNameExists(parentId, name, excludeId)

    fun renameFolder(id: Long, newName: String) {
        viewModelScope.launch {
            folderRepository.renameFolder(id, newName)
        }
    }

    fun deleteFiles(ids: Set<Long>) {
        viewModelScope.launch {
            ids.forEach { fileRepository.deleteFile(it) }
        }
    }

    private val _moveTargets = MutableStateFlow<List<MoveTarget>>(emptyList())
    val moveTargets: StateFlow<List<MoveTarget>> = _moveTargets.asStateFlow()

    /** Refresh the flattened folder tree shown in the Move dialog. Hidden
     *  folders (and their subtrees) are omitted unless currently revealed, so
     *  a move destination can never expose hidden folders. */
    fun loadMoveTargets() {
        viewModelScope.launch {
            val reveal = appLock.revealHidden.value
            val flattened = mutableListOf<MoveTarget>()
            fun flatten(nodes: List<FolderNode>, depth: Int) {
                for (node in nodes) {
                    if (!reveal && node.folder.hidden) continue
                    flattened.add(MoveTarget(node.folder, depth))
                    flatten(node.children, depth + 1)
                }
            }
            flatten(folderRepository.buildTree(), 0)
            _moveTargets.value = flattened
        }
    }

    /** Decrypt a file's thumbnail for browser display (image files only). */
    suspend fun decryptThumbnail(id: Long): ByteArray? =
        fileRepository.getDecryptedThumbnail(id)

    /** Decode an image's pixel dimensions (width to height) for Properties. */
    suspend fun getImageResolution(id: Long): Pair<Int, Int>? {
        val bytes = fileRepository.getDecryptedContent(id)?.first ?: return null
        val opts = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
        android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
        return if (opts.outWidth > 0) opts.outWidth to opts.outHeight else null
    }

    fun setFolderHidden(id: Long, hidden: Boolean) {
        viewModelScope.launch {
            folderRepository.setFolderHidden(id, hidden)
        }
    }

    /** Called by the secret title-tap gesture to reveal hidden folders. */
    fun revealHiddenFolders() = appLock.revealHiddenFolders()

    /** Turn reveal off. If currently inside a hidden tree, return to root
     *  immediately so no hidden content stays visible. */
    fun turnOffReveal() {
        appLock.hideHiddenFolders()
        viewModelScope.launch {
            val current = _uiState.value.currentFolderId
            if (current != null && folderRepository.isInHiddenTree(current)) {
                navigateToRoot()
            }
        }
    }

    fun toggleGridView() {
        _uiState.update { it.copy(isGridView = !it.isGridView) }
        browserPrefs.isGridView = _uiState.value.isGridView
    }

    fun navigateToRoot() = navigateToFolder(null)
}
