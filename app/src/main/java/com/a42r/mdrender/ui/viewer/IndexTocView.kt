package com.a42r.mdrender.ui.viewer

import android.util.Log
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

private const val TAG = "IndexToc"
private val linkRegex = Regex("\\[(.+?)\\]\\((.+?)\\)")

/**
 * Parsed chapter entry from INDEX.md.
 */
data class TocEntry(
    val number: String,
    val title: String,
    val link: String?,
    val isCompanion: Boolean = false,
    val companionRef: String? = null
)

/**
 * Split INDEX.md into preamble, TOC entries, and notes.
 */
data class IndexTocData(
    val preamble: String,
    val entries: List<TocEntry>,
    val notes: String
)

// ── Parser ──────────────────────────────────────────────────────────────────

/**
 * Parse INDEX.md. Structure:
 *
 *   [preamble text]
 *   ---
 *   [table rows + headings + more tables]
 *   ---
 *   [notes]
 *
 * The first `---` separates preamble from table content.
 * The last `---` separates table content from notes.
 * If no `---` is found the whole file is treated as preamble.
 * If only one `---` is found, notes are empty.
 */
fun parseIndexToc(markdown: String): IndexTocData {
    val lines = markdown.split("\n")

    // Find ALL separator line indices (lines whose trimmed content is
    // three or more dashes).
    val separatorIndices = lines.indices.filter { i ->
        val t = lines[i].trim()
        t.all { it == '-' } && t.length >= 3
    }

    val preambleEnd = if (separatorIndices.isNotEmpty()) separatorIndices.first() else lines.size
    val notesStart = if (separatorIndices.size >= 2) separatorIndices.last() + 1 else lines.size

    val preambleLines = lines.take(preambleEnd)
    val tableLines = lines.subList(preambleEnd + 1, notesStart - 1).let { sub ->
        if (preambleEnd == lines.size) emptyList() else sub
    }
    // Actually handle the case more carefully:
    val tableContent: List<String>
    val notesLines: List<String>
    when (separatorIndices.size) {
        0 -> {
            tableContent = emptyList()
            notesLines = emptyList()
        }
        1 -> {
            tableContent = lines.subList(separatorIndices[0] + 1, lines.size)
            notesLines = emptyList()
        }
        else -> {
            tableContent = lines.subList(separatorIndices[0] + 1, separatorIndices.last())
            notesLines = lines.subList(separatorIndices.last() + 1, lines.size)
        }
    }

    val preamble = preambleLines.joinToString("\n")
        .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1")
        .replace(Regex("\\*(.+?)\\*"), "$1")
        .replace(Regex("`(.+?)`"), "$1")

    val notes = notesLines.joinToString("\n")
        .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1")
        .replace(Regex("\\*(.+?)\\*"), "$1")
        .replace(Regex("`(.+?)`"), "$1")

    val entries = parseTableRows(tableContent)

    return IndexTocData(preamble = preamble, entries = entries, notes = notes)
}

/**
 * Extract TOC entries from the lines between the `---` separators, which
 * may contain one or more markdown tables.
 */
private fun parseTableRows(lines: List<String>): List<TocEntry> {
    val entries = mutableListOf<TocEntry>()

    // Find all table regions (consecutive lines starting with |)
    var i = 0
    while (i < lines.size) {
        val line = lines[i].trim()
        if (line.startsWith("|")) {
            // Collect all consecutive table rows
            val tableRows = mutableListOf<String>()
            while (i < lines.size && lines[i].trim().startsWith("|")) {
                tableRows.add(lines[i].trim())
                i++
            }
            // Parse this table
            entries.addAll(parseOneTable(tableRows))
        } else {
            i++
        }
    }
    return entries
}

/** Parse a single markdown table into TOC entries. */
private fun parseOneTable(rows: List<String>): List<TocEntry> {
    val result = mutableListOf<TocEntry>()
    if (rows.isEmpty()) return result

    // Determine column layout from the first row
    val headerCells = splitTableRow(rows[0])
    if (headerCells.isEmpty()) return result

    val isMainTable = headerCells.size >= 3 && headerCells[0] == "#"
    val isCompanionTable = headerCells.size >= 3 &&
        (headerCells[0].equals("Companion to", ignoreCase = true) ||
         headerCells[0].contains("Companion", ignoreCase = true))

    // Skip header row (row 0) and the separator row (row 1 if it's all dashes)
    val dataStart = if (rows.size > 1 && rows[1].all { c -> c == '|' || c == '-' || c == ':' || c == ' ' }) 2 else 1

    for (idx in dataStart until rows.size) {
        val cells = splitTableRow(rows[idx])
        if (cells.size < 2) continue

        if (isMainTable && cells.size >= 3) {
            // | # | Chapter | File |
            val number = cells[0].trim()
            val title = cells[1].trim()
                .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1")
                .replace(Regex("\\*(.+?)\\*"), "$1")
                .replace(Regex("`(.+?)`"), "$1")
            val fileCell = cells[2].trim()
            val link = linkRegex.find(fileCell)?.groupValues?.get(2)

            // Detect companion data row (first col starts with "Ch ")
            if (number.startsWith("Ch ", ignoreCase = true) && link != null) {
                result.add(TocEntry(
                    number = "", title = title, link = link,
                    isCompanion = true, companionRef = number
                ))
            } else {
                result.add(TocEntry(number = number, title = title, link = link))
            }
        } else if (isCompanionTable && cells.size >= 3) {
            // | Companion to | Title | File |
            val companionRef = cells[0].trim()
            val title = cells[1].trim()
                .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1")
                .replace(Regex("\\*(.+?)\\*"), "$1")
                .replace(Regex("`(.+?)`"), "$1")
            val fileCell = cells[2].trim()
            val link = linkRegex.find(fileCell)?.groupValues?.get(2)
            if (link != null) {
                result.add(TocEntry(
                    number = "", title = title, link = link,
                    isCompanion = true, companionRef = companionRef
                ))
            }
        } else if (cells.size >= 2) {
            // Fallback: try to find a link in any cell
            var link: String? = null
            var title = ""
            val number = cells[0].trim()
            for (cell in cells) {
                val m = linkRegex.find(cell)
                if (m != null) {
                    link = m.groupValues[2]
                    title = m.groupValues[1]
                    break
                }
            }
            if (link != null) {
                result.add(TocEntry(number = number, title = title, link = link))
            }
        }
    }
    return result
}

/** Split a markdown table row into cells, respecting [] for links. */
private fun splitTableRow(row: String): List<String> {
    val inner = row.trim().removeSurrounding("|")
    val cells = mutableListOf<String>()
    var buf = StringBuilder()
    var depth = 0
    for (ch in inner) {
        when (ch) {
            '[' -> { buf.append(ch); depth++ }
            ']' -> { buf.append(ch); depth-- }
            '|' -> if (depth == 0) { cells.add(buf.toString().trim()); buf = StringBuilder() } else buf.append(ch)
            else -> buf.append(ch)
        }
    }
    if (buf.isNotEmpty()) cells.add(buf.toString().trim())
    return cells
}

// ── Composable ──────────────────────────────────────────────────────────────

/**
 * Render INDEX.md as a rich table of contents with tappable chapter entries.
 *
 * Each linked row is displayed as "Chapter N -- Title" styled as a link.
 *
 * @param resolveLink function to resolve a filename to a file ID. Called in
 *   a coroutine when a TOC entry is tapped.
 */
@Composable
fun IndexTocView(
    markdown: String,
    onNavigateToFile: (Long) -> Unit,
    resolveLink: suspend (String) -> Long?
) {
    val parsed = remember(markdown) { parseIndexToc(markdown) }
    val coroutineScope = rememberCoroutineScope()
    val primary = MaterialTheme.colorScheme.primary
    val onSurface = MaterialTheme.colorScheme.onSurface
    val onSurfaceVariant = MaterialTheme.colorScheme.onSurfaceVariant

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 16.dp)
    ) {
        // ── Preamble ──
        renderMarkdownText(parsed.preamble, onSurface, onSurfaceVariant)

        // ── TOC entries ──
        Spacer(Modifier.height(8.dp))

        val mainEntries = parsed.entries.filter { !it.isCompanion }
        val companionEntries = parsed.entries.filter { it.isCompanion }

        // Main chapter entries
        if (mainEntries.isNotEmpty()) {
            for (entry in mainEntries) {
                if (entry.link != null) {
                    val display = if (entry.number.all { it.isDigit() }) {
                        "Chapter ${entry.number} — ${entry.title}"
                    } else {
                        "${entry.number} — ${entry.title}"
                    }
                    LinkText(
                        text = display,
                        color = primary,
                        onClick = {
                            coroutineScope.launch {
                                val fileId = resolveLink(entry.link)
                                if (fileId != null) {
                                    onNavigateToFile(fileId)
                                } else {
                                    Log.w(TAG, "TOC link not resolved: ${entry.link}")
                                }
                            }
                        }
                    )
                } else {
                    Text(
                        text = entry.title.ifBlank { entry.number },
                        style = MaterialTheme.typography.bodyMedium,
                        color = onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 4.dp)
                    )
                }
            }
        }

        // Companion entries
        if (companionEntries.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            Text(
                text = "Companion Chapters",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(vertical = 8.dp)
            )
            for (entry in companionEntries) {
                if (entry.link != null) {
                    LinkText(
                        text = entry.title,
                        color = primary,
                        onClick = {
                            coroutineScope.launch {
                                val fileId = resolveLink(entry.link)
                                if (fileId != null) onNavigateToFile(fileId)
                            }
                        }
                    )
                    if (entry.companionRef != null) {
                        Text(
                            text = "Companion to: ${entry.companionRef}",
                            style = MaterialTheme.typography.bodySmall,
                            color = onSurfaceVariant,
                            modifier = Modifier.padding(start = 8.dp, bottom = 4.dp)
                        )
                    }
                }
            }
        }

        // ── Notes ──
        if (parsed.notes.isNotBlank()) {
            Spacer(Modifier.height(24.dp))
            HorizontalDivider()
            Spacer(Modifier.height(16.dp))
            renderMarkdownText(parsed.notes, onSurface, onSurfaceVariant)
        }
    }
}

/** Styled clickable link text. */
@Composable
private fun LinkText(
    text: String,
    color: androidx.compose.ui.graphics.Color,
    onClick: () -> Unit
) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyLarge,
        color = color,
        textDecoration = TextDecoration.Underline,
        fontWeight = FontWeight.Medium,
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
            .clickable(onClick = onClick),
        maxLines = 1,
        overflow = TextOverflow.Ellipsis
    )
}

/** Render simple markdown as styled text blocks. */
@Composable
private fun renderMarkdownText(
    text: String,
    onSurface: androidx.compose.ui.graphics.Color,
    onSurfaceVariant: androidx.compose.ui.graphics.Color
) {
    val lines = text.split("\n")
    for (line in lines) {
        val trimmed = line.trim()
        when {
            trimmed.startsWith("# ") -> Text(
                text = trimmed.removePrefix("# ").trim(),
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 8.dp)
            )
            trimmed.startsWith("## ") -> Text(
                text = trimmed.removePrefix("## ").trim(),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 16.dp, bottom = 8.dp)
            )
            trimmed.startsWith("### ") -> Text(
                text = trimmed.removePrefix("### ").trim(),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)
            )
            trimmed.startsWith("> ") -> Text(
                text = trimmed.removePrefix("> ").trim(),
                style = MaterialTheme.typography.bodyMedium,
                fontStyle = FontStyle.Italic,
                color = onSurfaceVariant,
                modifier = Modifier.padding(vertical = 4.dp)
            )
            trimmed.startsWith("- ") || trimmed.startsWith("* ") -> Text(
                text = "• ${trimmed.removePrefix("- ").removePrefix("* ")}",
                style = MaterialTheme.typography.bodyMedium,
                color = onSurface,
                modifier = Modifier.padding(start = 8.dp, top = 1.dp, bottom = 1.dp)
            )
            trimmed.isBlank() -> Spacer(Modifier.height(8.dp))
            else -> Text(
                text = trimmed,
                style = MaterialTheme.typography.bodyMedium,
                color = onSurface,
                modifier = Modifier.padding(vertical = 2.dp)
            )
        }
    }
}
