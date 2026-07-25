package com.a42r.mdrender.data.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "push_history",
    indices = [Index(value = ["pushed_at"])]
)
data class PushHistoryEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "file_name") val fileName: String,
    @ColumnInfo(name = "file_size") val fileSize: Long,
    @ColumnInfo(name = "folder_id") val folderId: Long? = null,
    @ColumnInfo(name = "source") val source: String,
    @ColumnInfo(name = "pushed_at") val pushedAt: Long = System.currentTimeMillis()
)
