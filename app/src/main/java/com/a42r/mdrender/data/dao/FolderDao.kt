package com.a42r.mdrender.data.dao

import androidx.room.*
import com.a42r.mdrender.data.entity.FolderEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface FolderDao {
    @Query("SELECT * FROM folders WHERE id = :id")
    suspend fun getById(id: Long): FolderEntity?

    @Query("SELECT * FROM folders WHERE parent_id IS :parentId ORDER BY name ASC")
    fun getChildrenOf(parentId: Long?): Flow<List<FolderEntity>>

    @Query("SELECT * FROM folders ORDER BY name ASC")
    suspend fun getAllFolders(): List<FolderEntity>

    @Query("SELECT * FROM folders WHERE parent_id IS :parentId AND name = :name COLLATE NOCASE LIMIT 1")
    suspend fun findByName(parentId: Long?, name: String): FolderEntity?

    @Query("UPDATE folders SET hidden = :hidden, updated_at = :updatedAt WHERE id = :id")
    suspend fun setHidden(id: Long, hidden: Boolean, updatedAt: Long)

    @Query("UPDATE folders SET parent_id = :parentId, updated_at = :updatedAt WHERE id = :id")
    suspend fun move(id: Long, parentId: Long?, updatedAt: Long)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(folder: FolderEntity): Long

    @Update
    suspend fun update(folder: FolderEntity)

    @Query("DELETE FROM folders WHERE id = :id")
    suspend fun delete(id: Long)
}
