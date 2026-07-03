package com.a42r.mdrender.data.repository

import com.a42r.mdrender.data.dao.FolderDao
import com.a42r.mdrender.data.entity.FolderEntity
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever

class FolderRepositoryNamesTest {

    private val dao: FolderDao = mock()
    private val repo = FolderRepository(dao)

    private val existing = FolderEntity(id = 5, parentId = 1, name = "Work")

    @Test
    fun siblingNameExists_hit() = runTest {
        whenever(dao.findByName(1, "Work")).thenReturn(existing)
        assertTrue(repo.siblingNameExists(1, "Work"))
    }

    @Test
    fun siblingNameExists_miss() = runTest {
        whenever(dao.findByName(1, "Play")).thenReturn(null)
        assertFalse(repo.siblingNameExists(1, "Play"))
    }

    @Test
    fun siblingNameExists_excludesSelf() = runTest {
        // Renaming folder 5 to a different casing of its own name is allowed.
        whenever(dao.findByName(1, "WORK")).thenReturn(existing)
        assertFalse(repo.siblingNameExists(1, "WORK", excludeId = 5))
        assertTrue(repo.siblingNameExists(1, "WORK", excludeId = 99))
    }

    @Test
    fun getFolder_delegatesToDao() = runTest {
        whenever(dao.getById(5)).thenReturn(existing)
        assertEquals(existing, repo.getFolder(5))
    }
}
