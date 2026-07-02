package com.a42r.mdrender.ui.import

import android.app.Application
import android.net.Uri
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.repository.FileRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ImportUiState(
    val isImporting: Boolean = false,
    val completedCount: Int = 0,
    val skippedCount: Int = 0,
    val errorMessage: String? = null
)

class ImportViewModel(application: Application) : AndroidViewModel(application) {

    private val fileRepository: FileRepository by lazy {
        com.a42r.mdrender.MDRenderApplication.instance.fileRepository
    }

    private val _uiState = MutableStateFlow(ImportUiState())
    val uiState: StateFlow<ImportUiState> = _uiState.asStateFlow()

    private val _importComplete = MutableStateFlow(false)
    val importComplete: StateFlow<Boolean> = _importComplete.asStateFlow()

    fun importFiles(uris: List<Uri>, folderId: Long?) {
        _importComplete.value = false
        viewModelScope.launch {
            _uiState.update { it.copy(isImporting = true, errorMessage = null, completedCount = 0, skippedCount = 0) }
            var count = 0
            var skipped = 0
            var lastError: String? = null
            val contentResolver = getApplication<Application>().contentResolver
            for (uri in uris) {
                try {
                    val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                        ?: throw Exception("Cannot read file")
                    val fileName = getFileName(uri) ?: "unknown_file"
                    val mimeType = fileRepository.mimeTypeFromExtension(fileName)
                    if (mimeType == "application/octet-stream") {
                        val resolvedMime = contentResolver.getType(uri) ?: mimeType
                        if (!resolvedMime.startsWith("text/") && !resolvedMime.startsWith("image/")) {
                            Log.w(TAG, "Skipping unsupported type $resolvedMime for $uri")
                            skipped++
                            _uiState.update { it.copy(skippedCount = skipped) }
                            continue
                        }
                        fileRepository.importFile(fileName, resolvedMime, bytes, folderId)
                    } else {
                        fileRepository.importFile(fileName, mimeType, bytes, folderId)
                    }
                    count++
                    _uiState.update { it.copy(completedCount = count) }
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to import $uri", e)
                    lastError = e.message ?: e.javaClass.simpleName
                }
            }
            val failed = uris.size - count - skipped
            val errorMessage = when {
                failed > 0 -> "Imported $count of ${uris.size} file(s). $failed failed: $lastError"
                skipped > 0 && count == 0 -> "No files imported — $skipped unsupported file type(s). Only text and image files are supported."
                else -> null
            }
            _uiState.update { it.copy(isImporting = false, errorMessage = errorMessage) }
            // Only auto-navigate back when everything the user picked was imported;
            // otherwise stay on screen so the error is visible.
            if (errorMessage == null && count > 0) {
                _importComplete.value = true
            }
        }
    }

    companion object {
        private const val TAG = "ImportViewModel"
    }

    private fun getFileName(uri: Uri): String? {
        val cursor = getApplication<Application>().contentResolver.query(uri, null, null, null, null)
        return cursor?.use {
            if (it.moveToFirst()) {
                val nameIndex = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                if (nameIndex >= 0) it.getString(nameIndex) else null
            } else null
        }
    }
}
