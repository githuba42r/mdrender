package com.a42r.mdrender.share

import com.a42r.mdrender.data.entity.FileEntity
import org.junit.Assert.*
import org.junit.Test

class SharePlanTest {

    private fun file(id: Long, folderId: Long?) = FileEntity(
        id = id, folderId = folderId, name = "f$id.md",
        mimeType = "text/markdown", encryptedBlob = byteArrayOf(), fileSize = 0L
    )

    @Test
    fun emptySelection_isNone() {
        assertEquals(SharePlan.None, SharePlan.of(emptyList()) { false })
    }

    @Test
    fun noHiddenItems_sharesImmediately() {
        val files = listOf(file(1, null), file(2, 10))
        val plan = SharePlan.of(files) { false }
        assertEquals(SharePlan.ShareNow(files), plan)
    }

    @Test
    fun mixedSelection_needsConfirmationWithCorrectPartition() {
        val visible = file(1, null)
        val hidden = file(2, 20)
        val plan = SharePlan.of(listOf(visible, hidden)) { it.folderId == 20L }
        assertEquals(
            SharePlan.NeedsConfirmation(visible = listOf(visible), hidden = listOf(hidden)),
            plan
        )
    }

    @Test
    fun allHidden_needsConfirmationWithEmptyVisible() {
        val files = listOf(file(1, 20), file(2, 20))
        val plan = SharePlan.of(files) { true }
        assertEquals(
            SharePlan.NeedsConfirmation(visible = emptyList(), hidden = files),
            plan
        )
    }
}
