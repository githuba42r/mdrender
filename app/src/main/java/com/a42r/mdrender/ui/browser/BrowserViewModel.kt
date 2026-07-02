package com.a42r.mdrender.ui.browser

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.entity.FolderEntity
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderNode
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.security.AppLock
import dagger.hilt.android.lifecycle.HiltViewModel
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

@HiltViewModel
class BrowserViewModel @Inject constructor(
    private val folderRepository: FolderRepository,
    private val fileRepository: FileRepository,
    private val browserPrefs: BrowserPreferencesStore,
    private val appLock: AppLock
) : ViewModel() {

    private val _uiState = MutableStateFlow(BrowserUiState())
    val uiState: StateFlow<BrowserUiState> = _uiState.asStateFlow()

    /** Whether hidden folders are currently revealed (drives badges + Unhide). */
    val revealHidden: StateFlow<Boolean> = appLock.revealHidden

    private val _undoDelete = MutableSharedFlow<UndoDelete>()
    val undoDelete: SharedFlow<UndoDelete> = _undoDelete.asSharedFlow()

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

    fun moveFile(id: Long, targetFolderId: Long?) {
        viewModelScope.launch {
            fileRepository.moveFile(id, targetFolderId)
        }
    }

    fun deleteFiles(ids: Set<Long>) {
        viewModelScope.launch {
            ids.forEach { fileRepository.deleteFile(it) }
        }
    }

    fun moveFiles(ids: Set<Long>, targetFolderId: Long?) {
        viewModelScope.launch {
            ids.forEach { fileRepository.moveFile(it, targetFolderId) }
        }
    }

    private val _moveTargets = MutableStateFlow<List<MoveTarget>>(emptyList())
    val moveTargets: StateFlow<List<MoveTarget>> = _moveTargets.asStateFlow()

    /** Refresh the flattened folder tree shown in the Move dialog. */
    fun loadMoveTargets() {
        viewModelScope.launch {
            val flattened = mutableListOf<MoveTarget>()
            fun flatten(nodes: List<FolderNode>, depth: Int) {
                for (node in nodes) {
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

    fun toggleGridView() {
        _uiState.update { it.copy(isGridView = !it.isGridView) }
    }

    fun navigateToRoot() = navigateToFolder(null)
}
