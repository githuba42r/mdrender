package com.a42r.mdrender.ui.viewer

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp

private val linkRegex = Regex("\\[(.+?)\\]\\((.+?)\\)")

internal enum class CellAlign { LEFT, CENTER, RIGHT }

internal data class MarkdownTable(
    val header: List<String>,
    val alignments: List<CellAlign>,
    val bodyRows: List<List<String>>
) {
    val columnCount: Int
        get() = maxOf(
            header.size,
            alignments.size,
            bodyRows.maxOfOrNull { it.size } ?: 0
        )

    /** Max display width (in characters) of each column. */
    fun columnWidths(): List<Int> {
        val counts = IntArray(columnCount)
        fun upd(cells: List<String>) {
            cells.forEachIndexed { i, c -> counts[i] = maxOf(counts[i], cellDisplayText(c).length) }
        }
        upd(header)
        bodyRows.forEach { upd(it) }
        return counts.toList()
    }
}

internal fun isTableLine(line: String): Boolean = line.trim().startsWith("|")

/** Split a markdown table row into cells, respecting [] for links. */
internal fun splitTableRow(row: String): List<String> {
    val inner = row.trim().removeSurrounding("|")
    val cells = mutableListOf<String>()
    val buf = StringBuilder()
    var depth = 0
    for (ch in inner) {
        when (ch) {
            '[' -> { buf.append(ch); depth++ }
            ']' -> { buf.append(ch); depth-- }
            '|' -> if (depth == 0) { cells.add(buf.toString().trim()); buf.clear() } else buf.append(ch)
            else -> buf.append(ch)
        }
    }
    if (buf.isNotEmpty()) cells.add(buf.toString().trim())
    return cells
}

private fun isSeparatorRow(cells: List<String>): Boolean =
    cells.isNotEmpty() && cells.all { it.matches(Regex(":?-+:?")) }

private fun parseAlign(cell: String): CellAlign {
    val t = cell.trim()
    return when {
        t.startsWith(":") && t.endsWith(":") -> CellAlign.CENTER
        t.endsWith(":") -> CellAlign.RIGHT
        else -> CellAlign.LEFT
    }
}

/** Parse consecutive trimmed `|`-prefixed lines into a table (row 1 may be the alignment separator). */
internal fun parseMarkdownTable(rawLines: List<String>): MarkdownTable {
    val rows = rawLines.filter { it.isNotBlank() }.map { splitTableRow(it) }
    if (rows.isEmpty()) return MarkdownTable(emptyList(), emptyList(), emptyList())

    val header = rows.first()
    val hasSeparator = rows.size > 1 && isSeparatorRow(rows[1])
    val alignments = if (hasSeparator) {
        rows[1].map { parseAlign(it) }
    } else {
        List(header.size) { CellAlign.LEFT }
    }
    val body = if (hasSeparator) rows.drop(2) else rows.drop(1)
    return MarkdownTable(header = header, alignments = alignments, bodyRows = body)
}

/** Display text of a cell: link syntax reduced to its label, inline markers stripped. */
internal fun cellDisplayText(cell: String): String =
    plainStyled(cell.replace(linkRegex, "$1"))

internal fun plainStyled(text: String): String = text
    .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1")
    .replace(Regex("\\*(.+?)\\*"), "$1")
    .replace(Regex("`(.+?)`"), "$1")

internal fun appendStyled(raw: String, builder: AnnotatedString.Builder, linkColor: Color) {
    var lastEnd = 0
    for (match in linkRegex.findAll(raw)) {
        if (match.range.first > lastEnd) {
            builder.append(plainStyled(raw.substring(lastEnd, match.range.first)))
        }
        val text = match.groupValues[1]
        val url = match.groupValues[2]
        builder.pushStringAnnotation("link", url)
        builder.withStyle(SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline)) {
            builder.append(text)
        }
        builder.pop()
        lastEnd = match.range.last + 1
    }
    if (lastEnd < raw.length) {
        builder.append(plainStyled(raw.substring(lastEnd)))
    }
}

/**
 * Append a table to an AnnotatedString as monospace, column-aligned text.
 * Link annotations in cells are preserved so taps still work.
 */
internal fun appendTable(
    table: MarkdownTable,
    builder: AnnotatedString.Builder,
    linkColor: Color,
    monoBase: SpanStyle
) {
    val colCount = table.columnCount
    if (colCount == 0) return
    val widths = table.columnWidths()

    fun paddingFor(textLen: Int, width: Int, align: CellAlign): Pair<String, String> {
        val padTotal = (width - textLen).coerceAtLeast(0)
        return when (align) {
            CellAlign.LEFT -> "" to " ".repeat(padTotal)
            CellAlign.RIGHT -> " ".repeat(padTotal) to ""
            CellAlign.CENTER -> " ".repeat(padTotal / 2) to " ".repeat(padTotal - padTotal / 2)
        }
    }

    fun emitRow(cells: List<String>, bold: Boolean) {
        builder.withStyle(
            if (bold) monoBase.copy(fontWeight = FontWeight.Bold) else monoBase
        ) {
            for (i in 0 until colCount) {
                if (i > 0) append(" │ ")
                val cell = cells.getOrElse(i) { "" }
                val align = table.alignments.getOrElse(i) { CellAlign.LEFT }
                val (lead, trail) = paddingFor(cellDisplayText(cell).length, widths[i], align)
                append(lead)
                appendStyled(cell, this, linkColor)
                append(trail)
            }
        }
    }

    emitRow(table.header, bold = true)
    for (row in table.bodyRows) emitRow(row, bold = false)
}

private fun CellAlign.toTextAlign(): TextAlign = when (this) {
    CellAlign.LEFT -> TextAlign.Start
    CellAlign.CENTER -> TextAlign.Center
    CellAlign.RIGHT -> TextAlign.End
}

/** Block-style table composable for Column-based markdown renderers. */
@Composable
internal fun MarkdownTableBlock(
    table: MarkdownTable,
    modifier: Modifier = Modifier
) {
    val colCount = table.columnCount
    if (colCount == 0) return

    val widths = table.columnWidths()
    val totalWidth = widths.sum().coerceAtLeast(1)
    val weights = widths.map { (it.toFloat() / totalWidth).coerceAtLeast(0.1f) }
    val outline = MaterialTheme.colorScheme.outlineVariant
    val onSurface = MaterialTheme.colorScheme.onSurface

    fun cellFor(row: List<String>, i: Int): String =
        cellDisplayText(row.getOrElse(i) { "" })

    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp)
            .border(1.dp, outline, RoundedCornerShape(4.dp))
    ) {
        if (table.header.isNotEmpty()) {
            Row(Modifier.fillMaxWidth()) {
                for (i in 0 until colCount) {
                    Text(
                        text = cellFor(table.header, i),
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        color = onSurface,
                        textAlign = table.alignments.getOrElse(i) { CellAlign.LEFT }.toTextAlign(),
                        modifier = Modifier
                            .weight(weights.getOrElse(i) { 1f })
                            .padding(horizontal = 8.dp, vertical = 6.dp)
                    )
                }
            }
            HorizontalDivider(color = outline)
        }
        table.bodyRows.forEachIndexed { r, row ->
            if (r > 0) HorizontalDivider(color = outline.copy(alpha = 0.5f))
            Row(Modifier.fillMaxWidth()) {
                for (i in 0 until colCount) {
                    Text(
                        text = cellFor(row, i),
                        style = MaterialTheme.typography.bodySmall,
                        color = onSurface,
                        textAlign = table.alignments.getOrElse(i) { CellAlign.LEFT }.toTextAlign(),
                        modifier = Modifier
                            .weight(weights.getOrElse(i) { 1f })
                            .padding(horizontal = 8.dp, vertical = 6.dp)
                    )
                }
            }
        }
    }
}
