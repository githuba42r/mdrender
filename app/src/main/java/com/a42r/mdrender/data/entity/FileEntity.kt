package com.a42r.mdrender.data.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "files",
    foreignKeys = [ForeignKey(
        entity = FolderEntity::class,
        parentColumns = ["id"],
        childColumns = ["folder_id"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index(value = ["folder_id"])]
)
data class FileEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "folder_id") val folderId: Long? = null,
    @ColumnInfo(name = "name") val name: String,
    @ColumnInfo(name = "mime_type") val mimeType: String,
    @ColumnInfo(name = "encrypted_blob") val encryptedBlob: ByteArray,
    @ColumnInfo(name = "encrypted_thumbnail") val encryptedThumbnail: ByteArray? = null,
    @ColumnInfo(name = "file_size") val fileSize: Long,
    @ColumnInfo(name = "storage_type") val storageType: String = "blob",
    @ColumnInfo(name = "storage_path") val storagePath: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "updated_at") val updatedAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "scroll_position") val scrollPosition: Int = 0,
    @ColumnInfo(name = "playback_position") val playbackPosition: Long = 0,
    @ColumnInfo(name = "last_opened_at") val lastOpenedAt: Long = 0
) {
    override fun equals(other: Any?): Boolean = this === other || (other is FileEntity && id == other.id)
    override fun hashCode(): Int = id.hashCode()
}
