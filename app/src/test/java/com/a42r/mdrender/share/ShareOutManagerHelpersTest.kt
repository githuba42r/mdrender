package com.a42r.mdrender.share

import org.junit.Assert.assertEquals
import org.junit.Test

class ShareOutManagerHelpersTest {

    @Test
    fun dedupeNames_uniqueNamesUnchanged() {
        assertEquals(
            listOf("a.md", "b.md"),
            ShareOutManager.dedupeNames(listOf("a.md", "b.md"))
        )
    }

    @Test
    fun dedupeNames_duplicatesGetNumericSuffixBeforeExtension() {
        assertEquals(
            listOf("a.md", "a (1).md", "a (2).md"),
            ShareOutManager.dedupeNames(listOf("a.md", "a.md", "a.md"))
        )
    }

    @Test
    fun dedupeNames_extensionlessDuplicates() {
        assertEquals(
            listOf("notes", "notes (1)"),
            ShareOutManager.dedupeNames(listOf("notes", "notes"))
        )
    }

    @Test
    fun dedupeNames_suffixSkipsAlreadyTakenName() {
        assertEquals(
            listOf("a.md", "a (1).md", "a (2).md"),
            ShareOutManager.dedupeNames(listOf("a.md", "a (1).md", "a.md"))
        )
    }

    @Test
    fun commonMimeType_singleTypeIsExact() {
        assertEquals("text/markdown",
            ShareOutManager.commonMimeType(listOf("text/markdown", "text/markdown")))
    }

    @Test
    fun commonMimeType_samePrimaryTypeWildcardsSubtype() {
        assertEquals("image/*",
            ShareOutManager.commonMimeType(listOf("image/png", "image/jpeg")))
    }

    @Test
    fun commonMimeType_mixedPrimaryTypesIsStarStar() {
        assertEquals("*/*",
            ShareOutManager.commonMimeType(listOf("image/png", "text/plain")))
    }

    @Test
    fun commonMimeType_emptyIsStarStar() {
        assertEquals("*/*", ShareOutManager.commonMimeType(emptyList()))
    }
}
