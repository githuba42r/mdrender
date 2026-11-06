package com.a42r.mdrender.data.repository

import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.dao.PushHistoryDao
import com.a42r.mdrender.data.entity.PushHistoryEntity
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import javax.inject.Inject
import javax.inject.Singleton

/** Annotated entry combining entity data with its current file/folder state. */
data class PushHistoryEntry(
    val entity: PushHistoryEntity,
    val isHidden: Boolean,
    val fileExists: Boolean
)

@Singleton
class PushHistoryRepository @Inject constructor(
    private val dao: PushHistoryDao,
    private val folderRepository: FolderRepository,
    private val fileDao: FileDao
) {
    suspend fun record(source: String, fileName: String, fileSize: Long, folderId: Long?) {
        pruneExpired()
        dao.insert(PushHistoryEntity(
            fileName = fileName,
            fileSize = fileSize,
            folderId = folderId,
            source = source
        ))
    }

    /** Delete history entries older than [RETENTION_DAYS]. */
    suspend fun pruneExpired() {
        dao.deleteBefore(System.currentTimeMillis() - RETENTION_DAYS * 86_400_000L)
    }

    /** Returns all push history entries without hidden filtering. */
    fun getAllFlow(): Flow<List<PushHistoryEntity>> = dao.getAllFlow()

    /**
     * Returns a Flow of annotated entries. Entries whose target folder is in a
     * hidden tree have [PushHistoryEntry.isHidden] set, and entries whose file no
     * longer exists (deleted since the push) have [PushHistoryEntry.fileExists]
     * false — both are included regardless; callers filter on those flags.
     */
    fun getAnnotatedFlow(): Flow<List<PushHistoryEntry>> {
        return dao.getAllFlow()
            .onStart { pruneExpired() }
            .map { entities ->
                val hiddenFolderIds = folderRepository.getHiddenTreeFolderIds()
                entities.map { entity ->
                    PushHistoryEntry(
                        entity = entity,
                        isHidden = entity.folderId != null && entity.folderId in hiddenFolderIds,
                        fileExists = fileDao.findByName(entity.folderId, entity.fileName) != null
                    )
                }
            }
    }

    suspend fun deleteAll() = dao.deleteAll()

    companion object {
        /** Push history entries older than this are deleted on record and on read. */
        const val RETENTION_DAYS = 7
    }
}
