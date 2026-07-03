package com.a42r.mdrender.ui.viewer

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.repository.FileRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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
    val initialScrollPosition: Int = 0
)

/** Ordered sibling image ids in the folder plus the index of the opened one. */
data class ImagePagerState(
    val ids: List<Long>,
    val startIndex: Int
)

@HiltViewModel
class ViewerViewModel @Inject constructor(
    private val fileRepository: FileRepository,
    savedStateHandle: SavedStateHandle
) : ViewModel() {

    private val fileId: Long = savedStateHandle.get<Long>("fileId") ?: 0L

    private val _uiState = MutableStateFlow(ViewerUiState())
    val uiState: StateFlow<ViewerUiState> = _uiState.asStateFlow()

    // Populated for image viewing; null until the sibling list is resolved.
    private val _imagePager = MutableStateFlow<ImagePagerState?>(null)
    val imagePager: StateFlow<ImagePagerState?> = _imagePager.asStateFlow()

    init {
        loadContent()
        loadImageSiblings()
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
                val (bytes, mimeType) = fileRepository.getDecryptedContent(fileId)
                    ?: throw Exception("File not found")

                _uiState.update {
                    it.copy(
                        fileName = metadata?.name ?: "Unknown",
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
}
