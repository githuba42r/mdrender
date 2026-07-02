package com.a42r.mdrender.ui.import

import android.app.Application
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.repository.FileRepository
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

data class ImportUiState(
    val isImporting: Boolean = false,
    val completedCount: Int = 0,
    val errorMessage: String? = null
)

@HiltViewModel
class ImportViewModel @Inject constructor(
    private val fileRepository: FileRepository,
    private val application: Application
) : ViewModel() {

    private val _uiState = MutableStateFlow(ImportUiState())
    val uiState: StateFlow<ImportUiState> = _uiState.asStateFlow()

    private val _importComplete = MutableSharedFlow<Boolean>()
    val importComplete: SharedFlow<Boolean> = _importComplete.asSharedFlow()

    fun importFiles(uris: List<Uri>, folderId: Long?) {
        viewModelScope.launch {
            _uiState.update { it.copy(isImporting = true, errorMessage = null, completedCount = 0) }
            var count = 0
            for (uri in uris) {
                try {
                    val bytes = application.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                        ?: throw Exception("Cannot read file")
                    val fileName = getFileName(uri) ?: "unknown_file"
                    // Skip if unsupported
                    val mimeType = fileRepository.mimeTypeFromExtension(fileName)
                    if (mimeType == "application/octet-stream") {
                        // Try content resolver's mime type
                        val resolvedMime = application.contentResolver.getType(uri) ?: mimeType
                        if (!resolvedMime.startsWith("text/") && !resolvedMime.startsWith("image/")) {
                            continue // unsupported
                        }
                        fileRepository.importFile(fileName, resolvedMime, bytes, folderId)
                    } else {
                        fileRepository.importFile(fileName, mimeType, bytes, folderId)
                    }
                    count++
                    _uiState.update { it.copy(completedCount = count) }
                } catch (e: Exception) {
                    _uiState.update { it.copy(errorMessage = "Failed to import a file: ${e.message}") }
                }
            }
            _uiState.update { it.copy(isImporting = false) }
            _importComplete.emit(true)
        }
    }

    private fun getFileName(uri: Uri): String? {
        val cursor = application.contentResolver.query(uri, null, null, null, null)
        return cursor?.use {
            if (it.moveToFirst()) {
                val nameIndex = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                if (nameIndex >= 0) it.getString(nameIndex) else null
            } else null
        }
    }
}
