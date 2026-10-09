package com.a42r.mdrender.ui.viewer

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.localsend.LocalSendSessionManager
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.ui.browser.MoveTarget
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class ViewerUiState(
    val fileName: String = "",
    val mimeType: String = "",
    val markdownContent: String = "",
    val textContent: String = "",
    val imageBytes: ByteArray? = null,
    val isLoading: Boolean = true,
    val error: String? = null,
    val initialScrollPosition: Int = 0,
    val isIndexTocEnabled: Boolean = false,
    val fileUpdated: Boolean = false,
    val updatedFileId: Long? = null
)

/** Ordered sibling image ids in the folder plus the index of the opened one. */
data class ImagePagerState(
    val ids: List<Long>,
    val startIndex: Int
)

@HiltViewModel
class ViewerViewModel @Inject constructor(
    private val fileRepository: FileRepository,
    private val folderRepository: FolderRepository,
    private val appLock: AppLock,
    private val viewerPrefs: ViewerPrefs,
    private val localSendSessionManager: LocalSendSessionManager,
    savedStateHandle: SavedStateHandle
) : ViewModel() {

    private val fileId: Long = savedStateHandle.get<Long>("fileId") ?: 0L

    /** The folder containing this file; used to resolve sibling-file links. */
    var folderId: Long? = null

    private val _uiState = MutableStateFlow(ViewerUiState())
    val uiState: StateFlow<ViewerUiState> = _uiState.asStateFlow()

    // Populated for image viewing; null until the sibling list is resolved.
    private val _imagePager = MutableStateFlow<ImagePagerState?>(null)
    val imagePager: StateFlow<ImagePagerState?> = _imagePager.asStateFlow()

    /** One-off notices (delete/move results) shown as snackbars. */
    private val _userMessage = MutableSharedFlow<String>()
    val userMessage: SharedFlow<String> = _userMessage.asSharedFlow()

    /** Flattened folder tree for the Move dialog. */
    private val _moveTargets = MutableStateFlow<List<MoveTarget>>(emptyList())
    val moveTargets: StateFlow<List<MoveTarget>> = _moveTargets.asStateFlow()

    private val currentFileName: String get() = _uiState.value.fileName

    init {
        loadContent()
        loadImageSiblings()
        observeFileReplacements()
    }

    /** Watch for LocalSend REPLACE events targeting the file we're viewing. */
    private fun observeFileReplacements() {
        viewModelScope.launch {
            localSendSessionManager.fileReplaced.collect { event ->
                val fn = currentFileName
                val fid = folderId
                if (fn.isNotEmpty() && event.fileName == fn && event.folderId == fid) {
                    _uiState.update { it.copy(fileUpdated = true, updatedFileId = event.newFileId) }
                }
            }
        }
    }

    /** Resolve a filename (from a markdown link) to a file ID in the same folder, or null. */
    suspend fun resolveFileLink(filename: String): Long? {
        return fileRepository.findByName(folderId, filename)?.id
    }

    private fun loadImageSiblings() {
        viewModelScope.launch {
            val (ids, index) = fileRepository.getImageSiblings(fileId)
            if (ids.isNotEmpty()) _imagePager.value = ImagePagerState(ids, index)
        }
    }

    /** Decrypt a specific image (used per-page by the pager). */
    suspend fun decryptImage(id: Long): ByteArray? =
        fileRepository.getDecryptedContent(id)?.first

    suspend fun fileNameFor(id: Long): String =
        fileRepository.getFileMetadata(id)?.name ?: ""

    private fun loadContent() {
        viewModelScope.launch {
            try {
                val metadata = fileRepository.getFileMetadata(fileId)
                folderId = metadata?.folderId
                val (bytes, mimeType) = fileRepository.getDecryptedContent(fileId)
                    ?: throw Exception("File not found")

                _uiState.update {
                    it.copy(
                        fileName = metadata?.name ?: "Unknown",
                        isIndexTocEnabled = viewerPrefs.indexTocEnabled,
                        mimeType = mimeType,
                        isLoading = false,
                        initialScrollPosition = metadata?.scrollPosition ?: 0
                    )
                }

                when {
                    mimeType.startsWith("text/markdown") || mimeType.startsWith("text/plain") -> {
                        val text = String(bytes, Charsets.UTF_8)
                        if (mimeType.startsWith("text/markdown")) {
                            _uiState.update { it.copy(markdownContent = text) }
                        } else {
                            _uiState.update { it.copy(textContent = text) }
                        }
                    }
                    mimeType.startsWith("image/") -> {
                        _uiState.update { it.copy(imageBytes = bytes) }
                    }
                }

                // Record this as the last opened file
                fileRepository.saveLastOpenedAt(fileId)
            } catch (e: Exception) {
                _uiState.update { it.copy(isLoading = false, error = e.message) }
            }
        }
    }

    fun saveScrollPosition(pos: Int) {
        viewModelScope.launch {
            fileRepository.saveScrollPosition(fileId, pos)
        }
    }

    // --- Delete / move (image viewer context menu) -------------------------

    /** Delete [id] and drop it from the image pager. */
    fun deleteFile(id: Long) {
        viewModelScope.launch {
            val name = fileRepository.getFileMetadata(id)?.name ?: "File"
            fileRepository.deleteFile(id)
            removeImageFromPager(id)
            _userMessage.emit("Deleted \"$name\"")
        }
    }

    /** Move [id] into [targetFolderId] (null = Home). Blocks on name conflict. */
    fun moveFile(id: Long, targetFolderId: Long?) {
        viewModelScope.launch {
            val meta = fileRepository.getFileMetadata(id) ?: return@launch
            val existing = fileRepository.findByName(targetFolderId, meta.name)
            if (existing != null && existing.id != id) {
                val dest = targetFolderId?.let { folderRepository.getFolder(it)?.name } ?: "Home"
                _userMessage.emit("A file named \"${meta.name}\" already exists in \"$dest\"")
                return@launch
            }
            fileRepository.moveFile(id, targetFolderId)
            removeImageFromPager(id)
            val dest = targetFolderId?.let { folderRepository.getFolder(it)?.name } ?: "Home"
            _userMessage.emit("Moved \"${meta.name}\" to \"$dest\"")
        }
    }

    /** Refresh the flattened folder tree for the Move dialog. Hidden folders
     *  (and their subtrees) are omitted unless currently revealed. */
    fun loadMoveTargets() {
        viewModelScope.launch {
            val reveal = appLock.revealHidden.value
            val flattened = mutableListOf<MoveTarget>()
            fun flatten(nodes: List<com.a42r.mdrender.data.repository.FolderNode>, depth: Int) {
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

    private fun removeImageFromPager(id: Long) {
        _imagePager.update { pager ->
            if (pager == null || id !in pager.ids) return@update pager
            val newIds = pager.ids - id
            pager.copy(
                ids = newIds,
                startIndex = pager.startIndex.coerceIn(0, (newIds.size - 1).coerceAtLeast(0))
            )
        }
    }
}
