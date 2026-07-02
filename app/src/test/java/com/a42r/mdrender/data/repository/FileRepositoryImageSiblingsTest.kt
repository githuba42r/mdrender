package com.a42r.mdrender.data.repository

import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.security.CryptoEngine
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever

class FileRepositoryImageSiblingsTest {

    private fun file(id: Long, folder: Long?, name: String, mime: String) =
        FileEntity(id = id, folderId = folder, name = name, mimeType = mime,
            encryptedBlob = ByteArray(0), fileSize = 0)

    @Test
    fun `getImageSiblings returns only images ordered with the correct index`() = runTest {
        val dao = mock<FileDao>()
        val crypto = mock<CryptoEngine>()
        whenever(dao.getById(2)).thenReturn(file(2, 5, "b.png", "image/png"))
        whenever(dao.getFilesInFolderList(5)).thenReturn(
            listOf(
                file(1, 5, "a.jpg", "image/jpeg"),
                file(9, 5, "notes.md", "text/markdown"), // filtered out
                file(2, 5, "b.png", "image/png"),
                file(3, 5, "c.webp", "image/webp"),
            )
        )
        val repo = FileRepository(dao, crypto)

        val (ids, index) = repo.getImageSiblings(2)

        assertEquals(listOf(1L, 2L, 3L), ids)
        assertEquals(1, index) // id 2 is the second image
    }

    @Test
    fun `getImageSiblings on missing file returns empty`() = runTest {
        val dao = mock<FileDao>()
        val crypto = mock<CryptoEngine>()
        whenever(dao.getById(99)).thenReturn(null)
        val repo = FileRepository(dao, crypto)

        val (ids, index) = repo.getImageSiblings(99)

        assertEquals(emptyList<Long>(), ids)
        assertEquals(0, index)
    }
}
