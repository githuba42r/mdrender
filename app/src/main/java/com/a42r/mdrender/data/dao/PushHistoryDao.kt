package com.a42r.mdrender.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.a42r.mdrender.data.entity.PushHistoryEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PushHistoryDao {
    @Insert
    suspend fun insert(entry: PushHistoryEntity)

    @Query("SELECT * FROM push_history ORDER BY pushed_at DESC")
    fun getAllFlow(): Flow<List<PushHistoryEntity>>

    @Query("SELECT * FROM push_history ORDER BY pushed_at DESC")
    suspend fun getAll(): List<PushHistoryEntity>

    @Query("DELETE FROM push_history")
    suspend fun deleteAll()
}
