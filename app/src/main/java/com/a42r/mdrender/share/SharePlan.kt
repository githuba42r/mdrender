package com.a42r.mdrender.share

import com.a42r.mdrender.data.entity.FileEntity

/** Decision for a share request: share immediately, ask about hidden items
 *  first, or nothing to do. Pure logic — see BrowserViewModel for the flow. */
sealed interface SharePlan {
    /** No hidden items involved — open the share sheet directly. */
    data class ShareNow(val files: List<FileEntity>) : SharePlan

    /** At least one item is in a hidden folder — confirm before sharing.
     *  [visible] may be empty (everything selected is hidden). */
    data class NeedsConfirmation(
        val visible: List<FileEntity>,
        val hidden: List<FileEntity>
    ) : SharePlan

    /** Empty selection. */
    object None : SharePlan

    companion object {
        fun of(files: List<FileEntity>, isHidden: (FileEntity) -> Boolean): SharePlan {
            if (files.isEmpty()) return None
            val (hidden, visible) = files.partition(isHidden)
            return if (hidden.isEmpty()) ShareNow(files)
            else NeedsConfirmation(visible = visible, hidden = hidden)
        }
    }
}
