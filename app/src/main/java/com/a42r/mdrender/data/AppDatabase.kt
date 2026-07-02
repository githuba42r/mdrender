package com.a42r.mdrender.data

import androidx.room.Database
import androidx.room.RoomDatabase
import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.dao.FolderDao
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.entity.FolderEntity

@Database(entities = [FolderEntity::class, FileEntity::class], version = 1, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun folderDao(): FolderDao
    abstract fun fileDao(): FileDao
}
