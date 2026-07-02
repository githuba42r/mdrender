package com.a42r.mdrender.data.dao

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.a42r.mdrender.data.AppDatabase
import com.a42r.mdrender.data.entity.FolderEntity
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class FolderDaoTest {
    private lateinit var db: AppDatabase
    private lateinit var dao: FolderDao

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(),
            AppDatabase::class.java
        ).build()
        dao = db.folderDao()
    }

    @After
    fun tearDown() = db.close()

    @Test
    fun insertAndRetrieve() = runTest {
        val id = dao.insert(FolderEntity(name = "Test Folder"))
        val retrieved = dao.getById(id)
        assertNotNull(retrieved)
        assertEquals("Test Folder", retrieved?.name)
    }

    @Test
    fun childrenOf_returnsCorrectFolders() = runTest {
        val parentId = dao.insert(FolderEntity(name = "Parent"))
        dao.insert(FolderEntity(name = "Child 1", parentId = parentId))
        dao.insert(FolderEntity(name = "Child 2", parentId = parentId))
        val children = dao.getChildrenOf(parentId).first()
        assertEquals(2, children.size)
    }

    @Test
    fun cascadeDelete_removesChildren() = runTest {
        val parentId = dao.insert(FolderEntity(name = "Parent"))
        dao.insert(FolderEntity(name = "Child", parentId = parentId))
        dao.delete(parentId)
        val children = dao.getChildrenOf(parentId).first()
        assertTrue(children.isEmpty())
        assertNull(dao.getById(parentId))
    }
}
