package com.a42r.mdrender.ui.viewer

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class IndexTocParseTest {

    private val realIndex = File("/home/philg/tank/stories-authoring/stories/other-stories/INDEX.md")

    @Test
    fun parsesAllThreeStoriesFromRealIndex() {
        if (!realIndex.exists()) return // device env without the stories tree
        val parsed = parseIndexToc(realIndex.readText())
        parsed.stories.forEach { story ->
            println("  story '${story.title}' indexLink=${story.indexLink} chapters=${story.chapters.size}")
        }
        // The author adds stories over time — assert the three known ones
        // by name rather than any total.
        val byTitle = parsed.stories.associateBy { it.title }
        val expected = mapOf("Sharing Ryan" to 16, "She Decides" to 2, "The Late Train" to 1)
        assertEquals(expected.keys, expected.keys.intersect(byTitle.keys))
        expected.forEach { (title, chapterCount) ->
            val story = byTitle.getValue(title)
            assertEquals("story '$title'", chapterCount, story.chapters.size)
            assertNotNull("story '$title' should have a folder-index link", story.indexLink)
            assertTrue(story.indexLink!!.endsWith("INDEX.md"))
            assertTrue(story.chapters.all { it.link != null })
        }
    }

    @Test
    fun groupsThreeTablesUnderStoryHeadings() {
        val md = """
            # Index
            intro
            ---
            ## Story A
            Folder index: [INDEX.md](a/INDEX.md)
            | # | Chapter | File |
            |---|---------|------|
            | 1 | One | [01.md](a/01.md) |
            ## Story B
            Folder index: [INDEX.md](b/INDEX.md)
            | # | Chapter | File |
            |---|---------|------|
            | 1 | Two | [01.md](b/01.md) |
            ## Story C
            | # | Chapter | File |
            |---|---------|------|
            | 1 | Three | [01.md](c/01.md) |
            ---
            notes
        """.trimIndent()
        val parsed = parseIndexToc(md)
        assertEquals(3, parsed.stories.size)
        assertEquals(listOf("Story A", "Story B", "Story C"), parsed.stories.map { it.title })
        assertEquals(
            listOf("a/01.md", "b/01.md", "c/01.md"),
            parsed.stories.flatMap { s -> s.chapters.map { it.link } }
        )
        assertEquals("a/INDEX.md", parsed.stories[0].indexLink)
        assertEquals("b/INDEX.md", parsed.stories[1].indexLink)
        assertNull("story with no Folder index line", parsed.stories[2].indexLink)
        assertEquals("notes", parsed.notes)
    }

    @Test
    fun legacySingleTableWithoutHeadingsBecomesLooseEntries() {
        val md = """
            # Index
            intro
            ---
            | # | Title | File |
            |---|-------|------|
            | 1 | Sharing Ryan | [Chapters/sharing-ryan/INDEX.md](Chapters/sharing-ryan/INDEX.md) |
            ---
            notes
        """.trimIndent()
        val parsed = parseIndexToc(md)
        assertTrue(parsed.stories.isEmpty())
        assertEquals(1, parsed.looseEntries.size)
        assertEquals("Chapters/sharing-ryan/INDEX.md", parsed.looseEntries[0].link)
    }
}
