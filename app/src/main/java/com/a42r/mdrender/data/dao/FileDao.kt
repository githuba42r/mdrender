package com.a42r.mdrender.data.dao

import androidx.room.*
import com.a42r.mdrender.data.entity.FileEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface FileDao {
    @Query("SELECT * FROM files WHERE id = :id")
    suspend fun getById(id: Long): FileEntity?

    @Query("SELECT * FROM files WHERE folder_id IS :folderId ORDER BY name ASC")
    fun getFilesInFolder(folderId: Long?): Flow<List<FileEntity>>

    @Query("SELECT name FROM files WHERE folder_id IS :folderId")
    suspend fun getNamesInFolder(folderId: Long?): List<String>

    @Query("SELECT * FROM files WHERE folder_id IS :folderId AND name = :name LIMIT 1")
    suspend fun findByName(folderId: Long?, name: String): FileEntity?

    @Query("SELECT * FROM files WHERE folder_id IS :folderId ORDER BY name ASC")
    suspend fun getFilesInFolderList(folderId: Long?): List<FileEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(file: FileEntity): Long

    @Update
    suspend fun update(file: FileEntity)

    @Query("UPDATE files SET name = :name, updated_at = :updatedAt WHERE id = :id")
    suspend fun rename(id: Long, name: String, updatedAt: Long)

    @Query("UPDATE files SET folder_id = :folderId, updated_at = :updatedAt WHERE id = :id")
    suspend fun move(id: Long, folderId: Long?, updatedAt: Long)

    @Query("DELETE FROM files WHERE id = :id")
    suspend fun delete(id: Long)

    @Query("DELETE FROM files WHERE folder_id = :folderId")
    suspend fun deleteByFolderId(folderId: Long)
}
