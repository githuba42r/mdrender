package com.a42r.mdrender.ui.navigation

import org.junit.Assert.assertEquals
import org.junit.Test

class FileTypeTest {

    @Test
    fun mapsHtmlAndVideoMimeTypes() {
        assertEquals(FileType.HTML, FileType.fromMimeType("text/html"))
        assertEquals(FileType.HTML, FileType.fromMimeType("text/html; charset=utf-8"))
        assertEquals(FileType.VIDEO, FileType.fromMimeType("video/mp4"))
        assertEquals(FileType.VIDEO, FileType.fromMimeType("video/webm"))
        assertEquals(FileType.VIDEO, FileType.fromMimeType("video/x-matroska"))
    }

    @Test
    fun keepsExistingMappings() {
        assertEquals(FileType.MARKDOWN, FileType.fromMimeType("text/markdown"))
        assertEquals(FileType.TEXT, FileType.fromMimeType("text/plain"))
        assertEquals(FileType.IMAGE, FileType.fromMimeType("image/png"))
        assertEquals(FileType.AUDIO, FileType.fromMimeType("audio/mpeg"))
        assertEquals(FileType.UNKNOWN, FileType.fromMimeType("application/pdf"))
        assertEquals(FileType.FOLDER, FileType.fromMimeType("application/octet-stream", isFolder = true))
    }
}
