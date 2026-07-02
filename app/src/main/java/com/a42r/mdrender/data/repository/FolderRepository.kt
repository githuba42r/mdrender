package com.a42r.mdrender.data.repository

import com.a42r.mdrender.data.dao.FolderDao
import com.a42r.mdrender.data.entity.FolderEntity
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

data class FolderNode(
    val folder: FolderEntity,
    val children: List<FolderNode>
)

@Singleton
class FolderRepository @Inject constructor(
    private val folderDao: FolderDao
) {
    fun getChildrenOf(parentId: Long?): Flow<List<FolderEntity>> = folderDao.getChildrenOf(parentId)

    suspend fun createFolder(name: String, parentId: Long?): Long {
        val folder = FolderEntity(name = name, parentId = parentId)
        return folderDao.insert(folder)
    }

    suspend fun renameFolder(id: Long, newName: String) {
        val folder = folderDao.getById(id) ?: return
        folderDao.update(folder.copy(name = newName, updatedAt = System.currentTimeMillis()))
    }

    suspend fun deleteFolder(id: Long) {
        folderDao.delete(id) // CASCADE handles children
    }

    suspend fun buildTree(): List<FolderNode> {
        val allFolders = folderDao.getAllFolders()
        val childrenMap = allFolders.groupBy { it.parentId }
        fun buildNode(parentId: Long?): List<FolderNode> {
            return childrenMap[parentId]?.map { folder ->
                FolderNode(folder, buildNode(folder.id))
            } ?: emptyList()
        }
        return buildNode(null)
    }

    suspend fun folderExists(id: Long): Boolean = folderDao.getById(id) != null

    suspend fun getPathToFolder(folderId: Long): List<FolderEntity> {
        val path = mutableListOf<FolderEntity>()
        var currentId: Long? = folderId
        while (currentId != null) {
            val folder = folderDao.getById(currentId) ?: break
            path.add(0, folder)
            currentId = folder.parentId
        }
        return path
    }
}
