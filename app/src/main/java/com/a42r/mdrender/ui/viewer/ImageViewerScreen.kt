package com.a42r.mdrender.ui.viewer

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import coil.request.ImageRequest

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ImageViewerScreen(
    onBack: () -> Unit,
    viewModel: ViewerViewModel = hiltViewModel()
) {
    val pager by viewModel.imagePager.collectAsStateWithLifecycle()
    var showAppBar by remember { mutableStateOf(false) }

    // Zoom/pan hoisted to the viewer: only the current page is interactive and
    // paging is disabled while zoomed, so a single scale/offset is sufficient.
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }

    RegisterViewerZoom { delta ->
        scale = (scale * if (delta > 0) 1.25f else 0.8f).coerceIn(1f, 5f)
        if (scale == 1f) offset = Offset.Zero
    }

    val state = pager
    if (state == null || state.ids.isEmpty()) {
        Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        return
    }

    val pagerState = rememberPagerState(initialPage = state.startIndex) { state.ids.size }

    // Reset zoom when the settled page changes; also update the title source.
    LaunchedEffect(pagerState.currentPage) {
        scale = 1f
        offset = Offset.Zero
    }

    val currentId = state.ids[pagerState.currentPage]
    val currentName by produceState("", currentId) { value = viewModel.fileNameFor(currentId) }

    Scaffold(
        containerColor = Color.Black,
        topBar = {
            if (showAppBar) {
                TopAppBar(
                    title = { Text("$currentName  (${pagerState.currentPage + 1}/${state.ids.size})") },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                        }
                    }
                )
            }
        }
    ) { padding ->
        VerticalPager(
            state = pagerState,
            // Swipe pages only when not zoomed; otherwise a drag pans the image.
            userScrollEnabled = scale == 1f,
            modifier = Modifier
                .fillMaxSize()
                .then(if (showAppBar) Modifier.padding(padding) else Modifier)
        ) { page ->
            val id = state.ids[page]
            val isCurrent = page == pagerState.currentPage
            val bytes by produceState<ByteArray?>(null, id) { value = viewModel.decryptImage(id) }
            val context = LocalContext.current

            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color.Black)
                    .pointerInput(id) {
                        detectTapGestures(onTap = { showAppBar = !showAppBar })
                    }
                    .pointerInput(id) {
                        detectTransformGestures { _, pan, zoom, _ ->
                            val newScale = (scale * zoom).coerceIn(1f, 5f)
                            scale = newScale
                            offset = if (newScale > 1f) offset + pan else Offset.Zero
                        }
                    },
                contentAlignment = Alignment.Center
            ) {
                if (bytes != null) {
                    AsyncImage(
                        model = ImageRequest.Builder(context).data(bytes).crossfade(true).build(),
                        contentDescription = null,
                        modifier = Modifier
                            .fillMaxSize()
                            .then(
                                if (isCurrent) Modifier.graphicsLayer(
                                    scaleX = scale,
                                    scaleY = scale,
                                    translationX = offset.x,
                                    translationY = offset.y
                                ) else Modifier
                            ),
                        contentScale = ContentScale.Fit
                    )
                } else {
                    CircularProgressIndicator()
                }
            }
        }
    }
}
