package com.a42r.mdrender.ui.browser

import com.a42r.mdrender.data.entity.FileEntity

/** User's answer to a move name-conflict prompt. */
sealed interface ConflictDecision {
    data class Replace(val applyToAll: Boolean) : ConflictDecision
    data class Skip(val applyToAll: Boolean) : ConflictDecision
    object CancelBatch : ConflictDecision
}

/** Walks a move batch sequentially against live storage state, asking the
 *  user (via [run]'s askUser) about each name conflict unless a sticky
 *  apply-to-all decision is active. Checking live state means two same-named
 *  files in one batch conflict with each other — intended. */
class MoveConflictWalker(
    private val findByName: suspend (folderId: Long?, name: String) -> FileEntity?,
    private val moveFile: suspend (id: Long, folderId: Long?) -> Unit,
    private val deleteFile: suspend (id: Long) -> Unit,
) {
    data class Result(val moved: Int, val replaced: Int, val skipped: Int, val cancelled: Boolean)

    suspend fun run(
        files: List<FileEntity>,
        targetFolderId: Long?,
        askUser: suspend (file: FileEntity, remaining: Int) -> ConflictDecision
    ): Result {
        var moved = 0
        var replaced = 0
        var skipped = 0
        var sticky: ConflictDecision? = null

        for ((index, file) in files.withIndex()) {
            if (file.folderId == targetFolderId) continue
            val existing = findByName(targetFolderId, file.name)?.takeIf { it.id != file.id }
            if (existing == null) {
                moveFile(file.id, targetFolderId)
                moved++
                continue
            }
            val decision = sticky ?: askUser(file, files.size - index)
            when (decision) {
                is ConflictDecision.Replace -> {
                    if (decision.applyToAll) sticky = decision
                    deleteFile(existing.id)
                    moveFile(file.id, targetFolderId)
                    replaced++
                }
                is ConflictDecision.Skip -> {
                    if (decision.applyToAll) sticky = decision
                    skipped++
                }
                ConflictDecision.CancelBatch ->
                    return Result(moved, replaced, skipped, cancelled = true)
            }
        }
        return Result(moved, replaced, skipped, cancelled = false)
    }
}
