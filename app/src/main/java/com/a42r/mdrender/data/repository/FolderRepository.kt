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

    suspend fun setFolderHidden(id: Long, hidden: Boolean) =
        folderDao.setHidden(id, hidden, System.currentTimeMillis())

    suspend fun moveFolder(id: Long, parentId: Long?) =
        folderDao.move(id, parentId, System.currentTimeMillis())

    /** A folder plus all its descendants — invalid move destinations for it. */
    suspend fun getSubtreeIds(folderId: Long): Set<Long> {
        val childrenMap = folderDao.getAllFolders().groupBy { it.parentId }
        val result = mutableSetOf(folderId)
        fun collect(id: Long) {
            childrenMap[id]?.forEach { child ->
                if (result.add(child.id)) collect(child.id)
            }
        }
        collect(folderId)
        return result
    }

    /** True if [folderId] or any ancestor is marked hidden. */
    suspend fun isInHiddenTree(folderId: Long?): Boolean {
        var currentId: Long? = folderId
        // Guard against cycles in malformed data.
        var hops = 0
        while (currentId != null && hops < 1000) {
            val folder = folderDao.getById(currentId) ?: return false
            if (folder.hidden) return true
            currentId = folder.parentId
            hops++
        }
        return false
    }

    /** Returns the id of the folder with [name] under [parentId], creating it if needed. */
    suspend fun findOrCreateFolder(name: String, parentId: Long? = null): Long =
        folderDao.findByName(parentId, name)?.id ?: createFolder(name, parentId)

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
