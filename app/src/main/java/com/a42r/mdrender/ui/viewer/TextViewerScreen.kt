package com.a42r.mdrender.ui.viewer

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
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontFamily
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import androidx.compose.foundation.Canvas
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.runtime.snapshotFlow
import kotlin.math.abs
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TextViewerScreen(
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

                val showTopButton by remember {
                    derivedStateOf { scrollState.value > 200 }
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
                                .padding(16.dp)
                        ) {
                            Text(
                                text = uiState.textContent,
                                fontFamily = FontFamily.Monospace,
                                fontSize = (14 * fontScale).sp,
                                lineHeight = (20 * fontScale).sp
                            )
                        }
                    }

                    // Scroll position indicator — fades in on fast scroll, fades out after pause
                    if (scrollState.maxValue > 0) {
                        val trackColor = MaterialTheme.colorScheme.surfaceVariant
                        val thumbColor = MaterialTheme.colorScheme.primary

                        var showScrollbar by remember { mutableStateOf(false) }
                        var accumulatedDist by remember { mutableIntStateOf(0) }
                        var lastPos by remember { mutableIntStateOf(scrollState.value) }
                        var lastTimeMs by remember { mutableLongStateOf(0L) }
                        val threeViewportsPx = with(androidx.compose.ui.platform.LocalDensity.current) {
                            (androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp * 3).dp.toPx().toInt()
                        }

                        LaunchedEffect(scrollState.value) {
                            if (scrollState.maxValue > 0) {
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
                                .align(Alignment.CenterEnd)
                                .fillMaxHeight()
                                .width(72.dp)
                                .alpha(alpha)
                        ) {
                            Canvas(
                                modifier = Modifier
                                    .fillMaxSize()
                                    .padding(start = 32.dp)
                                    .pointerInput(Unit) {
                                        detectTapGestures { offset ->
                                            val canvasH = size.height
                                            val topM = canvasH * 0.10f
                                            val trackH = canvasH * 0.85f
                                            val ratio = ((offset.y - topM) / trackH).coerceIn(0f, 1f)
                                            coroutineScope.launch {
                                                scrollState.animateScrollTo((ratio * scrollState.maxValue).roundToInt())
                                            }
                                        }
                                    }
                                    .pointerInput(Unit) {
                                        detectDragGestures(
                                            onDragStart = { _ -> },
                                            onDrag = { change, _ ->
                                                change.consume()
                                                val canvasH = size.height
                                                val topM = canvasH * 0.10f
                                                val trackH = canvasH * 0.85f
                                                val ratio = ((change.position.y - topM) / trackH).coerceIn(0f, 1f)
                                                coroutineScope.launch {
                                                    scrollState.scrollTo((ratio * scrollState.maxValue).roundToInt())
                                                }
                                            }
                                        )
                                    }
                            ) {
                                val canvasH = size.height
                                val topM = canvasH * 0.10f
                                val bottomM = canvasH * 0.05f
                                val trackH = canvasH - topM - bottomM
                                val centerX = size.width / 2
                                val tw = 40f
                                val trackWidth = 5f
                                val h = scrollState.maxValue.toFloat()

                                drawLine(trackColor, Offset(centerX, topM), Offset(centerX, canvasH - bottomM), trackWidth)

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
