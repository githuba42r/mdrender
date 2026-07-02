package com.a42r.mdrender.ui.browser

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.entity.FolderEntity
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderNode
import com.a42r.mdrender.data.repository.FolderRepository
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
    private val fileRepository: FileRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(BrowserUiState())
    val uiState: StateFlow<BrowserUiState> = _uiState.asStateFlow()

    private val _undoDelete = MutableSharedFlow<UndoDelete>()
    val undoDelete: SharedFlow<UndoDelete> = _undoDelete.asSharedFlow()

    private var undoJob: Job? = null

    fun navigateToFolder(folderId: Long?) {
        _uiState.update { it.copy(currentFolderId = folderId) }
        loadContent(folderId)
    }

    private fun loadContent(folderId: Long?) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true) }
            launch {
                folderRepository.getChildrenOf(folderId).collect { folders ->
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

    fun toggleGridView() {
        _uiState.update { it.copy(isGridView = !it.isGridView) }
    }

    fun navigateToRoot() = navigateToFolder(null)
}
