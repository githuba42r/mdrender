package com.a42r.mdrender.ui.viewer

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.material.icons.filled.KeyboardArrowUp
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import androidx.compose.runtime.snapshotFlow
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MarkdownViewerScreen(
    onBack: () -> Unit,
    viewModel: ViewerViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    // Full-screen by default; tap the content to toggle the title bar.
    var showAppBar by remember { mutableStateOf(false) }
    var fontScale by remember { mutableFloatStateOf(1f) }

    RegisterViewerZoom { delta ->
        fontScale = (fontScale + delta * 0.1f).coerceIn(0.6f, 3f)
    }

    Scaffold(
        topBar = {
            if (showAppBar) {
                TopAppBar(
                    title = { Text(uiState.fileName) },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                        }
                    }
                )
            }
        }
    ) { padding ->
        when {
            uiState.isLoading -> Box(Modifier.fillMaxSize().padding(padding)) { CircularProgressIndicator() }
            uiState.error != null -> Box(Modifier.fillMaxSize().padding(padding)) {
                Text("Error: ${uiState.error}", color = MaterialTheme.colorScheme.error)
            }
            else -> {
                val scrollState = rememberScrollState()
                val coroutineScope = rememberCoroutineScope()
                val totalLines = remember(uiState.markdownContent) {
                    uiState.markdownContent.count { it == '\n' } + 1
                }
                val headings = remember(uiState.markdownContent) {
                    parseHeadings(uiState.markdownContent)
                }

                val showTopButton by remember {
                    derivedStateOf { scrollState.value > 200 }
                }

                val activeHeadingIdx by remember {
                    derivedStateOf {
                        if (headings.isEmpty()) -1 else {
                            val scroll = scrollState.value.toFloat()
                            val max = scrollState.maxValue.toFloat().coerceAtLeast(1f)
                            val ratio = (scroll / max).coerceIn(0f, 1f)
                            val idx = (ratio * headings.lastIndex).roundToInt()
                            idx.coerceIn(0, headings.lastIndex)
                        }
                    }
                }

                // Restore saved scroll position once content is loaded and
                // layout has completed (maxValue > 0 reflects real content height).
                LaunchedEffect(uiState.isLoading) {
                    if (!uiState.isLoading && uiState.initialScrollPosition > 0) {
                        if (scrollState.maxValue <= 0) {
                            withTimeoutOrNull(500) {
                                snapshotFlow { scrollState.maxValue }
                                    .first { it > 0 }
                            }
                        }
                        scrollState.scrollTo(
                            uiState.initialScrollPosition.coerceAtMost(scrollState.maxValue)
                        )
                    }
                }

                // Save scroll position when leaving the screen
                val currentScroll by rememberUpdatedState(scrollState.value)
                DisposableEffect(Unit) {
                    onDispose { viewModel.saveScrollPosition(currentScroll) }
                }

                Box(modifier = Modifier.fillMaxSize()) {
                    SelectionContainer {
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(padding)
                                .then(if (showAppBar) Modifier else Modifier.statusBarsPadding())
                                .verticalScroll(scrollState)
                                .pointerInput(Unit) {
                                    detectTapGestures(onTap = { showAppBar = !showAppBar })
                                }
                                .padding(start = 16.dp, end = 4.dp, top = 16.dp, bottom = 16.dp)
                        ) {
                            MarkdownText(uiState.markdownContent, fontScale)
                        }
                    }

                    // Heading annotation scrollbar with label
                    if (headings.size >= 2) {
                        var dragTargetIdx by remember { mutableIntStateOf(-1) }
                        val thumbIndex = if (dragTargetIdx >= 0) dragTargetIdx else activeHeadingIdx
                        val label = if (thumbIndex in headings.indices) headings[thumbIndex].text else ""

                        Box(modifier = Modifier.align(Alignment.CenterEnd)) {
                            HeadingScrollbar(
                                headings = headings,
                                totalLines = totalLines,
                                scrollState = scrollState,
                                activeIndex = activeHeadingIdx,
                                dragTargetIdx = dragTargetIdx,
                                onDragChange = { idx -> dragTargetIdx = idx },
                                onDragEnd = { dragTargetIdx = -1 },
                                coroutineScope = coroutineScope
                            )
                        }
                        // Label overlay — only visible while dragging
                        if (label.isNotEmpty() && dragTargetIdx >= 0) {
                            Surface(
                                modifier = Modifier
                                    .align(Alignment.CenterEnd)
                                    .padding(end = 24.dp)
                                    .widthIn(max = 200.dp),
                                shape = MaterialTheme.shapes.small,
                                color = MaterialTheme.colorScheme.inverseSurface
                            ) {
                                Text(
                                    text = label,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.inverseOnSurface,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                                )
                            }
                        }
                    }

                    // Jump-to-top FAB
                    AnimatedVisibility(
                        visible = showTopButton,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(16.dp),
                        enter = fadeIn(),
                        exit = fadeOut()
                    ) {
                        SmallFloatingActionButton(
                            onClick = { coroutineScope.launch { scrollState.animateScrollTo(0) } },
                            containerColor = MaterialTheme.colorScheme.surfaceVariant
                                .copy(alpha = 0.85f)
                        ) {
                            Icon(
                                Icons.Default.KeyboardArrowUp,
                                contentDescription = "Jump to top"
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun MarkdownText(markdown: String, fontScale: Float = 1f) {
    // Simple MD renderer — headings, bold, italic, code, lists, links
    val annotatedString = buildAnnotatedString {
        val lines = markdown.split("\n")
        for (line in lines) {
            when {
                line.startsWith("# ") -> withStyle(SpanStyle(fontSize = (24 * fontScale).sp, fontWeight = FontWeight.Bold)) {
                    append(line.removePrefix("# "))
                }
                line.startsWith("## ") -> withStyle(SpanStyle(fontSize = (20 * fontScale).sp, fontWeight = FontWeight.Bold)) {
                    append(line.removePrefix("## "))
                }
                line.startsWith("### ") -> withStyle(SpanStyle(fontSize = (18 * fontScale).sp, fontWeight = FontWeight.Bold)) {
                    append(line.removePrefix("### "))
                }
                line.startsWith("- ") || line.startsWith("* ") -> {
                    append("  • ")
                    append(renderInlineMarkdown(line.removePrefix("- ").removePrefix("* ")))
                }
                line.startsWith("`") && line.endsWith("`") -> {
                    withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = androidx.compose.ui.graphics.Color(0xFFEEEEEE))) {
                        append(line.removeSurrounding("`", "`"))
                    }
                }
                line.startsWith("> ") -> withStyle(SpanStyle(fontStyle = FontStyle.Italic, color = MaterialTheme.colorScheme.onSurfaceVariant)) {
                    append(line.removePrefix("> "))
                }
                else -> append(renderInlineMarkdown(line))
            }
            append("\n")
        }
    }
    Text(text = annotatedString, fontSize = (16 * fontScale).sp, lineHeight = (24 * fontScale).sp)
}

/** A heading extracted from the markdown text. */
private data class HeadingPos(
    val text: String,
    val lineIndex: Int,
    val level: Int  // 2 for ##, 3 for ###
)

/** Extract ## and ### headings from markdown source. */
private fun parseHeadings(markdown: String): List<HeadingPos> {
    val lines = markdown.split("\n")
    return lines.mapIndexedNotNull { index, line ->
        when {
            line.startsWith("## ") -> HeadingPos(line.removePrefix("## ").trim(), index, 2)
            line.startsWith("### ") -> HeadingPos(line.removePrefix("### ").trim(), index, 3)
            else -> null
        }
    }
}

/** An overlay scrollbar on the right side showing document headings as draggable markers. */
@Composable
private fun HeadingScrollbar(
    headings: List<HeadingPos>,
    totalLines: Int,
    scrollState: androidx.compose.foundation.ScrollState,
    activeIndex: Int,
    dragTargetIdx: Int,
    onDragChange: (Int) -> Unit,
    onDragEnd: () -> Unit,
    coroutineScope: kotlinx.coroutines.CoroutineScope
) {
    val headingCount = headings.size
    val trackColor = MaterialTheme.colorScheme.surfaceVariant
    val thumbColor = MaterialTheme.colorScheme.primary
    val inactiveColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.35f)
    val surfaceColor = MaterialTheme.colorScheme.surface

    val thumbIndex = if (dragTargetIdx >= 0) dragTargetIdx else activeIndex

    Box(
        modifier = Modifier
            .fillMaxHeight()
            .width(20.dp)
            .padding(vertical = 16.dp)
    ) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(headingCount) {
                    detectTapGestures { offset ->
                        val ratio = (offset.y / size.height).coerceIn(0f, 1f)
                        val idx = (ratio * (headingCount - 1).coerceAtLeast(0)).roundToInt().coerceIn(0, headingCount - 1)
                        val scrollRatio = headings[idx].lineIndex.toFloat() / totalLines.coerceAtLeast(1)
                        coroutineScope.launch { scrollState.animateScrollTo((scrollRatio * scrollState.maxValue).roundToInt()) }
                    }
                }
                .pointerInput(headingCount) {
                    detectDragGestures(
                        onDragStart = { offset ->
                            val ratio = (offset.y / size.height).coerceIn(0f, 1f)
                            onDragChange((ratio * (headingCount - 1).coerceAtLeast(0)).roundToInt().coerceIn(0, headingCount - 1))
                        },
                        onDrag = { change, _ ->
                            change.consume()
                            val ratio = (change.position.y / size.height).coerceIn(0f, 1f)
                            val idx = (ratio * (headingCount - 1).coerceAtLeast(0)).roundToInt().coerceIn(0, headingCount - 1)
                            onDragChange(idx)
                            val scrollRatio = headings[idx].lineIndex.toFloat() / totalLines.coerceAtLeast(1)
                            coroutineScope.launch { scrollState.scrollTo((scrollRatio * scrollState.maxValue).roundToInt()) }
                        },
                        onDragEnd = onDragEnd,
                        onDragCancel = onDragEnd
                    )
                }
        ) {
            val h = size.height
            val dotSpacing = if (headingCount > 1) h / (headingCount - 1) else h / 2f

            // Track line
            drawLine(trackColor, Offset(size.width / 2, 0f), Offset(size.width / 2, h), 2f)

            // Dots for each heading
            headings.forEachIndexed { i, _ ->
                val y = if (headingCount > 1) i * dotSpacing else h / 2f
                drawCircle(color = inactiveColor, radius = 3f, center = Offset(size.width / 2, y))
            }

            // Drag thumb
            val thumbY = if (headingCount > 1) thumbIndex * dotSpacing else h / 2f
            drawCircle(color = thumbColor, radius = 6f, center = Offset(size.width / 2, thumbY))
            drawCircle(color = surfaceColor, radius = 2.5f, center = Offset(size.width / 2, thumbY))
        }
    }
}

private fun renderInlineMarkdown(text: String): String {
    // For the viewer, simply strip common inline markers and return plain text.
    // A full inline parser can be added later. This keeps the initial viewer simple
    // and functional for the common case.
    return text
        .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1") // bold
        .replace(Regex("\\*(.+?)\\*"), "$1")       // italic
        .replace(Regex("`(.+?)`"), "$1")            // inline code
        .replace(Regex("\\[(.+?)\\]\\(.+?\\)"), "$1") // links
}
