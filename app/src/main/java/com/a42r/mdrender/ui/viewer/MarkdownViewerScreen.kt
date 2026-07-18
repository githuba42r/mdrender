package com.a42r.mdrender.ui.viewer

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.layout.positionInWindow
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.material.icons.filled.KeyboardArrowUp
import android.util.Log
import androidx.compose.ui.draw.alpha
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Size
import kotlin.math.abs
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MarkdownViewerScreen(
    onBack: () -> Unit,
    onNavigateToFile: (Long) -> Unit = {},
    viewModel: ViewerViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    // Full-screen by default; tap the content to toggle the title bar.
    var showAppBar by remember { mutableStateOf(false) }
    var fontScale by remember { mutableFloatStateOf(1f) }

    RegisterViewerZoom { delta ->
        fontScale = (fontScale + delta * 0.1f).coerceIn(0.6f, 3f)
    }

    val snackbarHostState = remember { SnackbarHostState() }
    var selectionMode by remember { mutableStateOf(false) }
    var selectionStart by remember { mutableIntStateOf(-1) }
    var selectionEnd by remember { mutableIntStateOf(-1) }
    val hasSelection = selectionStart >= 0 && selectionEnd >= 0 && selectionStart != selectionEnd
    val clipboard = LocalClipboardManager.current

    // Clear selection when exiting selection mode.
    LaunchedEffect(selectionMode) {
        if (!selectionMode) { selectionStart = -1; selectionEnd = -1 }
    }

    // Show a persistent "Reopen" snackbar when LocalSend replaces the file.
    // Key on updatedFileId so a second replacement while the snackbar is
    // still showing re-triggers the LaunchedEffect (dismissing the old one).
    LaunchedEffect(uiState.updatedFileId) {
        if (uiState.fileUpdated && uiState.updatedFileId != null) {
            val result = snackbarHostState.showSnackbar(
                message = "File updated",
                actionLabel = "Reopen",
                duration = SnackbarDuration.Indefinite
            )
            if (result == SnackbarResult.ActionPerformed) {
                onNavigateToFile(uiState.updatedFileId!!)
            }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            if (showAppBar) {
                TopAppBar(
                    title = { Text(uiState.fileName) },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                        }
                    },
                    actions = {
                        if (selectionMode) {
                            TextButton(onClick = {
                                if (hasSelection) {
                                    val start = minOf(selectionStart, selectionEnd)
                                    val end = maxOf(selectionStart, selectionEnd)
                                    clipboard.setText(AnnotatedString(uiState.markdownContent.substring(start, end)))
                                } else {
                                    clipboard.setText(AnnotatedString(uiState.markdownContent))
                                }
                            }) {
                                Text(if (hasSelection) "Copy" else "Copy all")
                            }
                            IconButton(onClick = { selectionMode = false }) {
                                Icon(Icons.Filled.Close, "Done selecting")
                            }
                        } else {
                            TextButton(onClick = { selectionMode = true }) {
                                Text("Select")
                            }
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
                val isIndex = uiState.fileName.equals("INDEX.md", ignoreCase = true)
                val showIndexAsToc = isIndex && uiState.isIndexTocEnabled

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
                val initialRestoreDone = remember { mutableStateOf(false) }
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
                    initialRestoreDone.value = true
                }

                // Save scroll position when leaving the screen
                val currentScroll by rememberUpdatedState(scrollState.value)
                DisposableEffect(Unit) {
                    onDispose { viewModel.saveScrollPosition(currentScroll) }
                }

                Box(modifier = Modifier.fillMaxSize()) {
                    // Content — either INDEX TOC or normal markdown
                    if (showIndexAsToc) {
                        // TOC view has its own internal scroll
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(padding)
                                .then(if (showAppBar) Modifier else Modifier.statusBarsPadding())
                        ) {
                            IndexTocView(
                                markdown = uiState.markdownContent,
                                onNavigateToFile = onNavigateToFile,
                                resolveLink = viewModel::resolveFileLink
                            )
                        }
                    } else {
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(padding)
                                .then(if (showAppBar) Modifier else Modifier.statusBarsPadding())
                                .verticalScroll(scrollState)
                                .padding(start = 16.dp, end = 4.dp, top = 16.dp, bottom = 16.dp)
                        ) {
                            MarkdownText(
                                markdown = uiState.markdownContent,
                                headings = headings,
                                fontScale = fontScale,
                                selectionMode = selectionMode,
                                selectionStart = selectionStart,
                                selectionEnd = selectionEnd,
                                onSelectionChanged = { start, end ->
                                    selectionStart = start
                                    selectionEnd = end
                                },
                                scrollState = scrollState,
                                onTap = { if (!selectionMode) showAppBar = !showAppBar },
                                onLongPressAt = { offset ->
                                    selectionStart = offset
                                    selectionEnd = offset
                                    selectionMode = true
                                },
                                onLinkTap = { link ->
                                    if (link.startsWith("#")) {
                                        val target = link.removePrefix("#").lowercase()
                                        val idx = headings.indexOfFirst {
                                            it.text.lowercase().replace(" ", "-") == target
                                        }
                                        if (idx >= 0) {
                                            val ratio = headings[idx].lineIndex.toFloat() / totalLines.coerceAtLeast(1)
                                            coroutineScope.launch {
                                                scrollState.animateScrollTo(
                                                    (ratio * scrollState.maxValue).roundToInt()
                                                )
                                            }
                                        }
                                    } else if (!link.contains("://") && !link.startsWith("//")) {
                                        // Local file link — resolve relative to the current file's folder.
                                        Log.d("MarkdownLink", "Tap on local link: \"$link\"")
                                        coroutineScope.launch {
                                            val fileId = viewModel.resolveFileLink(link)
                                            Log.d("MarkdownLink", "resolveFileLink(\"$link\") returned $fileId (folderId=${viewModel.folderId})")
                                            if (fileId != null) {
                                                onNavigateToFile(fileId)
                                            } else {
                                                Log.w("MarkdownLink", "No file found for link \"$link\" in folder ${viewModel.folderId}")
                                            }
                                        }
                                    }
                                }
                            )
                        }
                    }

                    // Scroll position indicator — heading scrollbar if sections exist, plain otherwise
                    if (!showIndexAsToc) {
                        var dragTargetIdx by remember { mutableIntStateOf(-1) }
                        val thumbIndex = if (dragTargetIdx >= 0) dragTargetIdx else activeHeadingIdx
                        val label = if (thumbIndex in headings.indices) headings[thumbIndex].text else ""

                        if (headings.size >= 2) {
                            Box(modifier = Modifier.align(Alignment.CenterEnd)) {
                                HeadingScrollbar(
                                    headings = headings,
                                    totalLines = totalLines,
                                    scrollState = scrollState,
                                    activeIndex = activeHeadingIdx,
                                    dragTargetIdx = dragTargetIdx,
                                    onDragChange = { idx -> dragTargetIdx = idx },
                                    onDragEnd = { dragTargetIdx = -1 },
                                    coroutineScope = coroutineScope,
                                    initialRestoreDone = initialRestoreDone
                                )
                            }
                            // Label overlay — only visible while dragging
                            if (label.isNotEmpty() && dragTargetIdx >= 0) {
                                Surface(
                                    modifier = Modifier
                                        .align(Alignment.CenterEnd)
                                        .padding(end = 44.dp)
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
                        } else {
                            // Plain scroll position indicator for files without headings
                            Box(modifier = Modifier.align(Alignment.CenterEnd)) {
                                PositionScrollbar(scrollState = scrollState, initialRestoreDone = initialRestoreDone)
                            }
                        }
                    }

                    // Jump-to-top FAB
                    AnimatedVisibility(
                        visible = showTopButton && !selectionMode,
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

                    // Selection mode bottom bar — scroll is disabled so selection
                    // handle drags aren't stolen by the scroll container.
                    if (selectionMode) {
                        Surface(
                            modifier = Modifier
                                .align(Alignment.BottomCenter)
                                .fillMaxWidth(),
                            tonalElevation = 3.dp,
                            shadowElevation = 6.dp
                        ) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(horizontal = 16.dp, vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceEvenly
                            ) {
                                TextButton(onClick = {
                                    if (hasSelection) {
                                        val start = minOf(selectionStart, selectionEnd)
                                        val end = maxOf(selectionStart, selectionEnd)
                                        clipboard.setText(AnnotatedString(uiState.markdownContent.substring(start, end)))
                                    } else {
                                        clipboard.setText(AnnotatedString(uiState.markdownContent))
                                    }
                                }) {
                                    Icon(Icons.Filled.ContentCopy, null, modifier = Modifier.size(18.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text(if (hasSelection) "Copy" else "Copy all")
                                }
                                TextButton(onClick = { selectionMode = false }) {
                                    Icon(Icons.Filled.Close, null, modifier = Modifier.size(18.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text("Done")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun MarkdownText(
    markdown: String,
    headings: List<HeadingPos>,
    fontScale: Float = 1f,
    selectionMode: Boolean = false,
    selectionStart: Int = -1,
    selectionEnd: Int = -1,
    onSelectionChanged: (Int, Int) -> Unit = { _, _ -> },
    scrollState: androidx.compose.foundation.ScrollState? = null,
    onLinkTap: ((String) -> Unit)? = null,
    onTap: (() -> Unit)? = null,
    onLongPressAt: ((Int) -> Unit)? = null
) {
    val linkColor = MaterialTheme.colorScheme.primary
    val bodySize = (16 * fontScale).sp
    val bodyLineHeight = (24 * fontScale).sp
    val h1Size = (24 * fontScale).sp
    val h2Size = (20 * fontScale).sp
    val h3Size = (18 * fontScale).sp
    val codeBg = Color(0xFFEEEEEE)
    val onSurfaceVariant = MaterialTheme.colorScheme.onSurfaceVariant

    val annotatedString = remember(markdown, fontScale) {
        buildAnnotatedString {
            val lines = markdown.split("\n")
            for (line in lines) {
                when {
                    line.startsWith("# ") -> withStyle(SpanStyle(fontSize = h1Size, fontWeight = FontWeight.Bold)) {
                        append(line.removePrefix("# "))
                    }
                    line.startsWith("## ") -> withStyle(SpanStyle(fontSize = h2Size, fontWeight = FontWeight.Bold)) {
                        append(line.removePrefix("## "))
                    }
                    line.startsWith("### ") -> withStyle(SpanStyle(fontSize = h3Size, fontWeight = FontWeight.Bold)) {
                        append(line.removePrefix("### "))
                    }
                    line.startsWith("- ") || line.startsWith("* ") -> {
                        append("  \u2022 ")
                        appendStyled(line.removePrefix("- ").removePrefix("* "), this, linkColor)
                    }
                    line.startsWith("`") && line.endsWith("`") -> {
                        withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeBg)) {
                            append(line.removeSurrounding("`", "`"))
                        }
                    }
                    line.startsWith("> ") -> withStyle(SpanStyle(fontStyle = FontStyle.Italic, color = onSurfaceVariant)) {
                        appendStyled(line.removePrefix("> "), this, linkColor)
                    }
                    else -> appendStyled(line, this, linkColor)
                }
                append("\n")
            }
        }
    }

    var textLayoutResult by remember(annotatedString) { mutableStateOf<TextLayoutResult?>(null) }

    if (selectionMode) {
        val selStart = minOf(selectionStart, selectionEnd).coerceAtLeast(0)
        val selEnd = maxOf(selectionStart, selectionEnd)
        val hasActiveSelection = selectionStart >= 0 && selectionEnd >= 0 && selStart != selEnd

        val highlightColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.3f)
        val handleColor = MaterialTheme.colorScheme.primary

        val displayString = remember(annotatedString, selectionStart, selectionEnd) {
            if (hasActiveSelection) {
                buildAnnotatedString {
                    append(annotatedString)
                    addStyle(SpanStyle(background = highlightColor), selStart, selEnd)
                }
            } else {
                annotatedString
            }
        }

        var boxWindowPos by remember { mutableStateOf(Offset.Zero) }

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .onGloballyPositioned { boxWindowPos = it.positionInWindow() }
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { offset ->
                            val layout = textLayoutResult
                            if (layout != null) {
                                val pos = layout.getOffsetForPosition(offset)
                                    .coerceIn(0, annotatedString.length)
                                onSelectionChanged(pos, pos)
                            }
                        },
                        onDrag = { change, _ ->
                            change.consume()
                            val layout = textLayoutResult
                            if (layout != null) {
                                val pos = layout.getOffsetForPosition(change.position)
                                    .coerceIn(0, annotatedString.length)
                                onSelectionChanged(selStart, pos)
                            }
                        }
                    )
                }
        ) {
            Text(
                text = displayString,
                fontSize = bodySize,
                lineHeight = bodyLineHeight,
                onTextLayout = { textLayoutResult = it }
            )

            // Draggable selection handles — each converts drag coordinates from its
            // own window-space to the parent Box's local space via boxWindowPos.
            val layout = textLayoutResult
            if (layout != null && hasActiveSelection) {
                val startBox = layout.getBoundingBox(selStart)
                HandleMarker(
                    boxWindowPos = boxWindowPos,
                    color = handleColor,
                    modifier = Modifier.offset {
                        IntOffset(startBox.left.roundToInt() - 12, startBox.top.roundToInt() - 12)
                    },
                    onDragTo = { localPos ->
                        val off = layout.getOffsetForPosition(localPos).coerceIn(0, annotatedString.length)
                        onSelectionChanged(off, selectionEnd)
                    }
                )

                val endChar = (selEnd - 1).coerceAtLeast(selStart)
                val endBox = layout.getBoundingBox(endChar)
                HandleMarker(
                    boxWindowPos = boxWindowPos,
                    color = handleColor,
                    modifier = Modifier.offset {
                        IntOffset(endBox.right.roundToInt() - 12, endBox.bottom.roundToInt() - 12)
                    },
                    onDragTo = { localPos ->
                        val off = layout.getOffsetForPosition(localPos).coerceIn(0, annotatedString.length)
                        onSelectionChanged(selectionStart, off)
                    }
                )
            }
        }
    } else {
        // Use plain Text for reliable height rendering inside scroll containers
        // (ClickableText truncates height). Tap detection is on the Text itself
        // rather than a matchParentSize() overlay Box — matchParentSize forces
        // Compose to build fixed Constraints matching the Text's exact measured
        // size, which throws for very large documents whose pixel height exceeds
        // what Constraints can represent.
        Text(
            text = annotatedString,
            fontSize = bodySize,
            lineHeight = bodyLineHeight,
            onTextLayout = { textLayoutResult = it },
            modifier = Modifier.pointerInput(onLinkTap, onTap, onLongPressAt) {
                detectTapGestures(
                    onTap = { offset ->
                        val layout = textLayoutResult
                        if (layout != null) {
                            val textOffset = layout.getOffsetForPosition(offset)
                            Log.v("MarkdownLink", "Tap at offset=$offset -> textOffset=$textOffset, annotatedString length=${annotatedString.length}")
                            val link = annotatedString.getStringAnnotations("link", textOffset, textOffset)
                                .firstOrNull()
                            if (link != null) {
                                Log.d("MarkdownLink", "Link annotation found: \"${link.item}\"")
                                onLinkTap?.invoke(link.item)
                                return@detectTapGestures
                            } else {
                                Log.v("MarkdownLink", "No link annotation at offset=$textOffset")
                            }
                        } else {
                            Log.w("MarkdownLink", "textLayoutResult is null on tap")
                        }
                        onTap?.invoke()
                    },
                    onLongPress = { offset ->
                        val layout = textLayoutResult
                        val textOffset = layout?.getOffsetForPosition(offset)?.coerceIn(0, annotatedString.length)
                        if (textOffset != null) onLongPressAt?.invoke(textOffset)
                    }
                )
            }
        )
    }
}

/** Small draggable circle overlay positioned at one end of a text selection.
 *  Uses positionInWindow on both itself and the parent Box to convert drag
 *  coordinates into the parent Box's local space for getOffsetForPosition. */
@Composable
private fun HandleMarker(
    boxWindowPos: Offset,
    color: Color,
    modifier: Modifier = Modifier,
    onDragTo: (Offset) -> Unit = {}
) {
    var myWindowPos by remember { mutableStateOf(Offset.Zero) }

    Box(
        modifier = modifier
            .size(24.dp)
            .onGloballyPositioned { myWindowPos = it.positionInWindow() }
            .pointerInput(Unit) {
                detectDragGestures { change, _ ->
                    change.consume()
                    val dragWindowPos = myWindowPos + change.position
                    val parentLocalPos = dragWindowPos - boxWindowPos
                    onDragTo(parentLocalPos)
                }
            }
    ) {
        Canvas(Modifier.fillMaxSize()) {
            val r = size.width / 2
            drawCircle(color = color, radius = r)
            drawCircle(color = Color.White, radius = r * 0.35f)
        }
    }
}

data class HeadingPos(
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

/** An overlay scrollbar on the right side showing document headings as draggable markers
 * and continuous scroll position. */
@Composable
private fun HeadingScrollbar(
    headings: List<HeadingPos>,
    totalLines: Int,
    scrollState: androidx.compose.foundation.ScrollState,
    activeIndex: Int,
    dragTargetIdx: Int,
    onDragChange: (Int) -> Unit,
    onDragEnd: () -> Unit,
    coroutineScope: kotlinx.coroutines.CoroutineScope,
    initialRestoreDone: androidx.compose.runtime.MutableState<Boolean>
) {
    val headingCount = headings.size
    val trackColor = MaterialTheme.colorScheme.surfaceVariant
    val thumbColor = MaterialTheme.colorScheme.primary
    val inactiveColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.35f)
    val surfaceColor = MaterialTheme.colorScheme.surface

    val thumbIndex = if (dragTargetIdx >= 0) dragTargetIdx else activeIndex

    // Only show on sustained long-distance scroll or direct scrollbar interaction
    var showScrollbar by remember { mutableStateOf(false) }
    var accumulatedDist by remember { mutableIntStateOf(0) }
    var lastPos by remember { mutableIntStateOf(scrollState.value) }
    var lastTimeMs by remember { mutableLongStateOf(0L) }
    // Trigger after scrolling roughly 3 viewport heights in under 1 second
    val screenH = androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp
    val threeViewportsPx = with(androidx.compose.ui.platform.LocalDensity.current) { (screenH * 3).dp.toPx().toInt() }

    LaunchedEffect(scrollState.value) {
        if (scrollState.maxValue > 0 && initialRestoreDone.value) {
            val now = System.currentTimeMillis()
            val dt = lastTimeMs.takeIf { it > 0 }?.let { now - it } ?: 0L
            val dist = abs(scrollState.value - lastPos)
            if (dt > 500) accumulatedDist = 0  // fresh gesture window
            accumulatedDist += dist
            if (accumulatedDist > threeViewportsPx && dt <= 1000) showScrollbar = true
            lastPos = scrollState.value
            lastTimeMs = now

            // Keep visible while dragging the scrollbar thumb directly
            if (dragTargetIdx >= 0) showScrollbar = true

            delay(2500L)
            showScrollbar = false
        }
    }

    val alpha by animateFloatAsState(
        targetValue = if (showScrollbar || dragTargetIdx >= 0) 1f else 0f,
        animationSpec = tween(durationMillis = 200),
        label = "scrollbarAlpha"
    )

    val scrollbarVisible = showScrollbar || dragTargetIdx >= 0

    Box(
        modifier = Modifier
            .fillMaxHeight()
            .width(40.dp)
            .alpha(alpha)
    ) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .then(if (scrollbarVisible) Modifier.pointerInput(headingCount) {
                    detectTapGestures { offset ->
                        val canvasH = size.height.toFloat()
                        val topM = canvasH * 0.10f
                        val trackH = canvasH * 0.85f
                        val ratio = ((offset.y - topM) / trackH).coerceIn(0f, 1f)
                        val idx = (ratio * (headingCount - 1).coerceAtLeast(0)).roundToInt().coerceIn(0, headingCount - 1)
                        val scrollRatio = headings[idx].lineIndex.toFloat() / totalLines.coerceAtLeast(1)
                        coroutineScope.launch { scrollState.animateScrollTo((scrollRatio * scrollState.maxValue).roundToInt()) }
                    }
                } else Modifier)
                .then(if (scrollbarVisible) Modifier.pointerInput(headingCount) {
                    detectDragGestures(
                        onDragStart = { offset ->
                            val canvasH = size.height.toFloat()
                            val topM = canvasH * 0.10f
                            val trackH = canvasH * 0.85f
                            val ratio = ((offset.y - topM) / trackH).coerceIn(0f, 1f)
                            onDragChange((ratio * (headingCount - 1).coerceAtLeast(0)).roundToInt().coerceIn(0, headingCount - 1))
                        },
                        onDrag = { change, _ ->
                            change.consume()
                            val canvasH = size.height.toFloat()
                            val topM = canvasH * 0.10f
                            val trackH = canvasH * 0.85f
                            val ratio = ((change.position.y - topM) / trackH).coerceIn(0f, 1f)
                            val idx = (ratio * (headingCount - 1).coerceAtLeast(0)).roundToInt().coerceIn(0, headingCount - 1)
                            onDragChange(idx)
                            coroutineScope.launch { scrollState.scrollTo((ratio * scrollState.maxValue).roundToInt()) }
                        },
                        onDragEnd = onDragEnd,
                        onDragCancel = onDragEnd
                    )
                } else Modifier)
        ) {
            val h = size.height
            val topMargin = h * 0.10f
            val bottomMargin = h * 0.05f
            val trackH = h - topMargin - bottomMargin

            // Track line
            drawLine(trackColor, Offset(size.width / 2, topMargin), Offset(size.width / 2, h - bottomMargin), 5f)

            // Section marker dots
            headings.forEachIndexed { i, _ ->
                val t = if (headingCount > 1) i.toFloat() / (headingCount - 1) else 0.5f
                val y = topMargin + t * trackH
                drawCircle(color = inactiveColor, radius = 12f, center = Offset(size.width / 2, y))
            }

            // Scroll progress pill — thick, easy to grab
            val scrollProgress = if (scrollState.maxValue > 0)
                scrollState.value.toFloat() / scrollState.maxValue else 0f
            val thumbY = topMargin + scrollProgress * trackH
            val thumbWidth = 48f
            val thumbHeight = 120f
            val left = (size.width - thumbWidth) / 2f
            val top = (thumbY - thumbHeight / 2f).coerceIn(topMargin, h - bottomMargin - thumbHeight)
            val cornerR = CornerRadius(thumbWidth / 2f)

            // Shadow ring
            drawRoundRect(
                color = thumbColor.copy(alpha = 0.3f),
                topLeft = Offset(left - 1f, top - 1f),
                size = Size(thumbWidth + 2f, thumbHeight + 2f),
                cornerRadius = cornerR
            )
            // Main pill
            drawRoundRect(
                color = thumbColor,
                topLeft = Offset(left, top),
                size = Size(thumbWidth, thumbHeight),
                cornerRadius = cornerR
            )
            // Inner accent line
            drawRoundRect(
                color = surfaceColor,
                topLeft = Offset(left + thumbWidth * 0.3f, top + thumbHeight * 0.2f),
                size = Size(thumbWidth * 0.4f, thumbHeight * 0.6f),
                cornerRadius = CornerRadius(2f)
            )
        }
    }
}

/** Plain scroll position indicator with drag-to-scroll and wide touch target. */
@Composable
private fun PositionScrollbar(scrollState: androidx.compose.foundation.ScrollState, initialRestoreDone: androidx.compose.runtime.MutableState<Boolean>) {
    if (scrollState.maxValue > 0) {
        val trackColor = MaterialTheme.colorScheme.surfaceVariant
        val thumbColor = MaterialTheme.colorScheme.primary
        val coroutineScope = rememberCoroutineScope()
        val h = scrollState.maxValue.toFloat()

        var showScrollbar by remember { mutableStateOf(false) }
        var accumulatedDist by remember { mutableIntStateOf(0) }
        var lastPos by remember { mutableIntStateOf(scrollState.value) }
        var lastTimeMs by remember { mutableLongStateOf(0L) }
        val threeViewportsPx = with(androidx.compose.ui.platform.LocalDensity.current) {
            (androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp * 3).dp.toPx().toInt()
        }

        LaunchedEffect(scrollState.value) {
            if (scrollState.maxValue > 0 && initialRestoreDone.value) {
                val now = System.currentTimeMillis()
                val dt = lastTimeMs.takeIf { it > 0 }?.let { now - it } ?: 0L
                val dist = abs(scrollState.value - lastPos)
                if (dt > 500) accumulatedDist = 0
                accumulatedDist += dist
                if (accumulatedDist > threeViewportsPx && dt <= 1000) showScrollbar = true
                lastPos = scrollState.value
                lastTimeMs = now

                delay(2500L)
                showScrollbar = false
            }
        }

        val alpha by animateFloatAsState(
            targetValue = if (showScrollbar) 1f else 0f,
            animationSpec = tween(durationMillis = 200),
            label = "scrollbarAlpha"
        )

        Box(
            modifier = Modifier
                .fillMaxHeight()
                .width(72.dp)
                .alpha(alpha)
        ) {
            Canvas(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(start = 32.dp)
                    .then(if (showScrollbar) Modifier.pointerInput(Unit) {
                        detectTapGestures { offset ->
                            val canvasH = size.height
                            val topM = canvasH * 0.10f
                            val trackH = canvasH * 0.85f
                            val ratio = ((offset.y - topM) / trackH).coerceIn(0f, 1f)
                            coroutineScope.launch {
                                scrollState.animateScrollTo((ratio * h).roundToInt())
                            }
                        }
                    } else Modifier)
                    .then(if (showScrollbar) Modifier.pointerInput(Unit) {
                        detectDragGestures(
                            onDragStart = { _ -> },
                            onDrag = { change, _ ->
                                change.consume()
                                val canvasH = size.height
                                val topM = canvasH * 0.10f
                                val trackH = canvasH * 0.85f
                                val ratio = ((change.position.y - topM) / trackH).coerceIn(0f, 1f)
                                coroutineScope.launch {
                                    scrollState.scrollTo((ratio * h).roundToInt())
                                }
                            }
                        )
                    } else Modifier)
            ) {
                val canvasH = size.height
                val topM = canvasH * 0.10f
                val bottomM = canvasH * 0.05f
                val trackH = canvasH - topM - bottomM
                val centerX = size.width / 2
                val tw = 40f
                val trackWidth = 5f

                // Track line
                drawLine(trackColor, Offset(centerX, topM), Offset(centerX, canvasH - bottomM), trackWidth)

                // Thumb
                val visibleRatio = canvasH / (canvasH + h)
                val thumbHeight = (canvasH * visibleRatio).coerceAtLeast(80f)
                val scrollProgress = (scrollState.value.toFloat() / h).coerceIn(0f, 1f)
                val thumbTop = topM + (trackH - thumbHeight) * scrollProgress

                drawRoundRect(
                    color = thumbColor,
                    topLeft = Offset(centerX - tw / 2, thumbTop),
                    size = Size(tw, thumbHeight),
                    cornerRadius = CornerRadius(tw / 2)
                )
            }
        }
    }
}

private fun appendStyled(raw: String, builder: AnnotatedString.Builder, linkColor: Color) {
    val linkRegex = Regex("\\[(.+?)\\]\\((.+?)\\)")
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

private fun plainStyled(text: String): String = text
    .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1")
    .replace(Regex("\\*(.+?)\\*"), "$1")
    .replace(Regex("`(.+?)`"), "$1")

