package com.a42r.mdrender.ui.navigation

sealed class Routes(val route: String) {
    data object FolderBrowser : Routes("folder_browser/{folderId}") {
        fun createRoute(folderId: Long?): String = "folder_browser/${folderId ?: "root"}"
    }
    data object MarkdownViewer : Routes("markdown_viewer/{fileId}") {
        fun createRoute(fileId: Long): String = "markdown_viewer/$fileId"
    }
    data object TextViewer : Routes("text_viewer/{fileId}") {
        fun createRoute(fileId: Long): String = "text_viewer/$fileId"
    }
    data object ImageViewer : Routes("image_viewer/{fileId}") {
        fun createRoute(fileId: Long): String = "image_viewer/$fileId"
    }
    data object Settings : Routes("settings")
    data object Import : Routes("import/{folderId}") {
        fun createRoute(folderId: Long?): String = "import/${folderId ?: "root"}"
    }
    data object AudioPlayer : Routes("audio_player/{fileId}") {
        fun createRoute(fileId: Long): String = "audio_player/$fileId"
    }
    data object UnhideSettings : Routes("unhide_settings")
}
