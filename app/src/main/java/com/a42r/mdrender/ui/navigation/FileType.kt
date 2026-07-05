package com.a42r.mdrender.ui.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.*
import androidx.compose.material.icons.filled.*
import androidx.compose.ui.graphics.vector.ImageVector

enum class FileType(val icon: ImageVector, val label: String) {
    FOLDER(Icons.Filled.Folder, "Folder"),
    MARKDOWN(Icons.Filled.Description, "Markdown"),
    TEXT(Icons.AutoMirrored.Filled.TextSnippet, "Text"),
    IMAGE(Icons.Filled.Image, "Image"),
    AUDIO(Icons.Filled.MusicNote, "Audio"),
    UNKNOWN(Icons.AutoMirrored.Filled.InsertDriveFile, "File");

    companion object {
        fun fromMimeType(mimeType: String, isFolder: Boolean = false): FileType = when {
            isFolder -> FOLDER
            mimeType.startsWith("text/markdown") -> MARKDOWN
            mimeType.startsWith("text/plain") -> TEXT
            mimeType.startsWith("image/") -> IMAGE
            mimeType.startsWith("audio/") -> AUDIO
            else -> UNKNOWN
        }
    }
}
