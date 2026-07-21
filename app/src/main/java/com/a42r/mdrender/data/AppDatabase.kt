package com.a42r.mdrender.data

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.dao.FolderDao
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.entity.FolderEntity

@Database(entities = [FolderEntity::class, FileEntity::class], version = 7, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun folderDao(): FolderDao
    abstract fun fileDao(): FileDao

    companion object {
        /** v1 → v2: add the folders.hidden flag without dropping data. */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE folders ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
            }
        }

        /** v2 → v3: add files.scroll_position for scroll bookmarking. */
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE files ADD COLUMN scroll_position INTEGER NOT NULL DEFAULT 0")
            }
        }

        /** v3 → v4: add files.playback_position for audio bookmarking. */
        val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE files ADD COLUMN playback_position INTEGER NOT NULL DEFAULT 0")
            }
        }

        /** v4 → v5: add files.storage_type and files.storage_path for file-backed
         *  storage of large files alongside existing BLOBs. */
        val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE files ADD COLUMN storage_type TEXT NOT NULL DEFAULT 'blob'")
                db.execSQL("ALTER TABLE files ADD COLUMN storage_path TEXT")
            }
        }

        /** v5 → v6: add files.last_opened_at for last-opened file shortcut. */
        val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE files ADD COLUMN last_opened_at INTEGER NOT NULL DEFAULT 0")
            }
        }

        /** v6 → v7: clean orphaned disk files (no schema change). Physical file
         *  cleanup runs separately in MDRenderApplication.onCreate(). */
        val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // No schema change — file cleanup is done in application code
            }
        }
    }
}
