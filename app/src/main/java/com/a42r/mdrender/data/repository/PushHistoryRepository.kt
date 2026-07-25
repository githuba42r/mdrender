package com.a42r.mdrender.data.repository

import com.a42r.mdrender.data.dao.PushHistoryDao
import com.a42r.mdrender.data.entity.PushHistoryEntity
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

/** Annotated entry combining entity data with its current hidden folder state. */
data class PushHistoryEntry(
    val entity: PushHistoryEntity,
    val isHidden: Boolean
)

@Singleton
class PushHistoryRepository @Inject constructor(
    private val dao: PushHistoryDao,
    private val folderRepository: FolderRepository
) {
    suspend fun record(source: String, fileName: String, fileSize: Long, folderId: Long?) {
        dao.insert(PushHistoryEntity(
            fileName = fileName,
            fileSize = fileSize,
            folderId = folderId,
            source = source
        ))
    }

    /** Returns all push history entries without hidden filtering. */
    fun getAllFlow(): Flow<List<PushHistoryEntity>> = dao.getAllFlow()

    /**
     * Returns a Flow of annotated entries. Entries whose target folder is in a
     * hidden tree have [PushHistoryEntry.isHidden] set and are included regardless —
     * callers should filter with [PushHistoryEntry.isHidden] based on their reveal
     * state.
     */
    fun getAnnotatedFlow(): Flow<List<PushHistoryEntry>> {
        return dao.getAllFlow().map { entities ->
            val hiddenFolderIds = folderRepository.getHiddenTreeFolderIds()
            entities.map { entity ->
                PushHistoryEntry(
                    entity = entity,
                    isHidden = entity.folderId != null && entity.folderId in hiddenFolderIds
                )
            }
        }
    }

    suspend fun deleteAll() = dao.deleteAll()
}
