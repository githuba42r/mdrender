package com.a42r.mdrender.data.repository

import com.a42r.mdrender.data.dao.FolderDao
import com.a42r.mdrender.data.entity.FolderEntity
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever

class FolderRepositoryHiddenTest {

    private fun folder(id: Long, parent: Long?, hidden: Boolean) =
        FolderEntity(id = id, name = "f$id", parentId = parent, hidden = hidden)

    @Test
    fun `isInHiddenTree true when the folder itself is hidden`() = runTest {
        val dao = mock<FolderDao>()
        whenever(dao.getById(1)).thenReturn(folder(1, null, hidden = true))
        val repo = FolderRepository(dao)
        assertTrue(repo.isInHiddenTree(1))
    }

    @Test
    fun `isInHiddenTree true when an ancestor is hidden`() = runTest {
        val dao = mock<FolderDao>()
        whenever(dao.getById(3)).thenReturn(folder(3, 2, hidden = false))
        whenever(dao.getById(2)).thenReturn(folder(2, 1, hidden = true))
        whenever(dao.getById(1)).thenReturn(folder(1, null, hidden = false))
        val repo = FolderRepository(dao)
        assertTrue(repo.isInHiddenTree(3))
    }

    @Test
    fun `isInHiddenTree false when no ancestor is hidden`() = runTest {
        val dao = mock<FolderDao>()
        whenever(dao.getById(2)).thenReturn(folder(2, 1, hidden = false))
        whenever(dao.getById(1)).thenReturn(folder(1, null, hidden = false))
        val repo = FolderRepository(dao)
        assertFalse(repo.isInHiddenTree(2))
    }

    @Test
    fun `isInHiddenTree false for root (null)`() = runTest {
        val dao = mock<FolderDao>()
        val repo = FolderRepository(dao)
        assertFalse(repo.isInHiddenTree(null))
    }
}
