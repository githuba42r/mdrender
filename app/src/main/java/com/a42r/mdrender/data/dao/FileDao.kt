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

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(file: FileEntity): Long

    @Update
    suspend fun update(file: FileEntity)

    @Query("DELETE FROM files WHERE id = :id")
    suspend fun delete(id: Long)

    @Query("DELETE FROM files WHERE folder_id = :folderId")
    suspend fun deleteByFolderId(folderId: Long)
}
