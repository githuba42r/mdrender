package com.a42r.mdrender.share

import org.junit.Assert.assertEquals
import org.junit.Test

class ShareOutManagerHelpersTest {

    @Test
    fun sanitizeName_stripsPathTraversalComponents() {
        assertEquals("evil.md", ShareOutManager.sanitizeName("../evil.md"))
    }

    @Test
    fun sanitizeName_plainNamePassesThroughUnchanged() {
        assertEquals("notes.md", ShareOutManager.sanitizeName("notes.md"))
    }

    @Test
    fun sanitizeName_blankAfterSanitizingGetsPlaceholder() {
        assertEquals("file", ShareOutManager.sanitizeName("../"))
    }

    @Test
    fun sanitizeName_dotDotAloneGetsPlaceholder() {
        assertEquals("file", ShareOutManager.sanitizeName(".."))
    }

    @Test
    fun sanitizeName_dotAloneGetsPlaceholder() {
        assertEquals("file", ShareOutManager.sanitizeName("."))
    }

    @Test
    fun sanitizeName_backslashTraversalStripped() {
        assertEquals("evil.md", ShareOutManager.sanitizeName("..\\evil.md"))
    }

    @Test
    fun sanitizeName_embeddedNullByteStripped() {
        assertEquals("ab.md", ShareOutManager.sanitizeName("a\u0000b.md"))
    }

    @Test
    fun sanitizeName_mixedTraversalKeepsOnlyFinalComponent() {
        assertEquals("b.md", ShareOutManager.sanitizeName("a/../../b.md"))
    }

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
