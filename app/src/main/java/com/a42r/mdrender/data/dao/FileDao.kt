package com.a42r.mdrender.data.dao

import androidx.room.*
import com.a42r.mdrender.data.entity.FileEntity
import kotlinx.coroutines.flow.Flow

data class FileListItem(
    val id: Long,
    @ColumnInfo(name = "folder_id") val folderId: Long? = null,
    val name: String,
    @ColumnInfo(name = "mime_type") val mimeType: String,
    @ColumnInfo(name = "file_size") val fileSize: Long,
    @ColumnInfo(name = "storage_type") val storageType: String = "blob",
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "updated_at") val updatedAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "scroll_position") val scrollPosition: Int = 0,
    @ColumnInfo(name = "playback_position") val playbackPosition: Long = 0
)

data class FileMetadata(
    val id: Long,
    @ColumnInfo(name = "folder_id") val folderId: Long? = null,
    val name: String,
    @ColumnInfo(name = "mime_type") val mimeType: String,
    @ColumnInfo(name = "encrypted_blob") val encryptedBlob: ByteArray? = null,
    @ColumnInfo(name = "encrypted_thumbnail") val encryptedThumbnail: ByteArray? = null,
    @ColumnInfo(name = "file_size") val fileSize: Long,
    @ColumnInfo(name = "storage_type") val storageType: String = "blob",
    @ColumnInfo(name = "storage_path") val storagePath: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "updated_at") val updatedAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "scroll_position") val scrollPosition: Int = 0,
    @ColumnInfo(name = "playback_position") val playbackPosition: Long = 0
)

@Dao
interface FileDao {
    /** Metadata plus encrypted_thumbnail, without the (potentially large) encrypted_blob. */
    @Query("SELECT id, folder_id, name, mime_type, encrypted_thumbnail, file_size, storage_type, storage_path, created_at, updated_at, scroll_position, playback_position FROM files WHERE id = :id")
    suspend fun getById(id: Long): FileMetadata?

    /** Encrypted blob bytes only — avoids CursorWindow limit on large files. */
    @Query("SELECT encrypted_blob FROM files WHERE id = :id")
    suspend fun getEncryptedBlob(id: Long): ByteArray?

    /** Metadata without encrypted blob — safe for large files. */
    @Query("SELECT id, folder_id, name, mime_type, file_size, storage_type, storage_path, created_at, updated_at, scroll_position, playback_position FROM files WHERE id = :id")
    suspend fun getFileMetadata(id: Long): FileMetadata?

    @Query("SELECT id, folder_id, name, mime_type, file_size, storage_type, created_at, updated_at, scroll_position, playback_position FROM files WHERE folder_id IS :folderId ORDER BY name ASC")
    fun getFilesInFolder(folderId: Long?): Flow<List<FileListItem>>

    @Query("SELECT name FROM files WHERE folder_id IS :folderId")
    suspend fun getNamesInFolder(folderId: Long?): List<String>

    @Query("SELECT id, folder_id, name, mime_type, file_size, storage_type, storage_path, created_at, updated_at, scroll_position, playback_position FROM files WHERE folder_id IS :folderId AND name = :name LIMIT 1")
    suspend fun findByName(folderId: Long?, name: String): FileMetadata?

    /** Full rows for image sibling detection. Separate from listing to keep listing lightweight. */
    @Query("SELECT id, folder_id, name, mime_type, file_size, storage_type, storage_path, created_at, updated_at, scroll_position, playback_position FROM files WHERE folder_id IS :folderId ORDER BY name ASC")
    suspend fun getFilesInFolderList(folderId: Long?): List<FileMetadata>

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

    @Query("UPDATE files SET storage_type = :storageType, storage_path = :storagePath, file_size = :fileSize, updated_at = :updatedAt WHERE id = :id")
    suspend fun updateStorageInfo(id: Long, storageType: String, storagePath: String?, fileSize: Long, updatedAt: Long)

    @Query("UPDATE files SET scroll_position = :pos WHERE id = :id")
    suspend fun updateScrollPosition(id: Long, pos: Int)

    @Query("UPDATE files SET playback_position = :pos WHERE id = :id")
    suspend fun updatePlaybackPosition(id: Long, pos: Long)

    /** Guarded by last_opened_at > 0 so files that were never opened (default 0,
     *  including freshly re-imported/replaced files) never tie for "most recent".
     *  A live Flow (not a one-shot suspend query) so it re-emits automatically on
     *  any write to the files table — including a background LocalSend replace
     *  that deletes and reinserts the currently-last-opened file under a new id. */
    @Query("SELECT id, folder_id, name, mime_type, file_size, storage_type, storage_path, created_at, updated_at, scroll_position, playback_position FROM files WHERE last_opened_at > 0 ORDER BY last_opened_at DESC LIMIT 1")
    fun getLastOpenedFile(): Flow<FileMetadata?>

    @Query("UPDATE files SET last_opened_at = :timestamp WHERE id = :id")
    suspend fun updateLastOpenedAt(id: Long, timestamp: Long)

    @Query("SELECT last_opened_at FROM files WHERE id = :id")
    suspend fun getLastOpenedAt(id: Long): Long?

    /** Delete the old file and insert a new one atomically in a single Room
     *  transaction, so invalidation-tracked Flows (notably getLastOpenedFile)
     *  only emit once with the final state — no transient wrong-file race. */
    @Transaction
    suspend fun replaceWithEntity(oldId: Long, entity: FileEntity): Long {
        delete(oldId)
        return insert(entity)
    }
}
