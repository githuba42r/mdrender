package com.a42r.mdrender.ui.viewer

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
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
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    // Full-screen by default; tap the image to toggle the title bar.
    var showAppBar by remember { mutableStateOf(false) }
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }

    RegisterViewerZoom { delta ->
        scale = (scale * if (delta > 0) 1.25f else 0.8f).coerceIn(0.5f, 5f)
    }

    if (showAppBar) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = { Text(uiState.fileName) },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                        }
                    }
                )
            }
        ) { padding ->
            ImageContent(
                uiState = uiState,
                padding = padding,
                scale = scale,
                offset = offset,
                onScaleChange = { scale = it },
                onOffsetChange = { offset = it },
                onTap = { showAppBar = !showAppBar }
            )
        }
    } else {
        Box(
            modifier = Modifier.fillMaxSize().background(Color.Black)
        ) {
            ImageContent(
                uiState = uiState,
                padding = PaddingValues(0.dp),
                scale = scale,
                offset = offset,
                onScaleChange = { scale = it },
                onOffsetChange = { offset = it },
                onTap = { showAppBar = !showAppBar }
            )
        }
    }
}

@Composable
private fun ImageContent(
    uiState: ViewerUiState,
    padding: PaddingValues,
    scale: Float,
    offset: Offset,
    onScaleChange: (Float) -> Unit,
    onOffsetChange: (Offset) -> Unit,
    onTap: () -> Unit
) {
    when {
        uiState.isLoading -> Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        uiState.imageBytes != null -> {
            val context = LocalContext.current
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .background(Color.Black)
                    .pointerInput(Unit) {
                        detectTransformGestures { _, pan, zoom, _ ->
                            onScaleChange((scale * zoom).coerceIn(0.5f, 5f))
                            onOffsetChange(Offset(
                                offset.x + pan.x,
                                offset.y + pan.y
                            ))
                        }
                    }
                    .pointerInput(Unit) {
                        detectTapGestures(onTap = { onTap() })
                    },
                contentAlignment = Alignment.Center
            ) {
                AsyncImage(
                    model = ImageRequest.Builder(context)
                        .data(uiState.imageBytes)
                        .crossfade(true)
                        .build(),
                    contentDescription = uiState.fileName,
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer(
                            scaleX = scale,
                            scaleY = scale,
                            translationX = offset.x,
                            translationY = offset.y
                        ),
                    contentScale = ContentScale.Fit
                )
            }
        }
        uiState.error != null -> Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
            Text("Error: ${uiState.error}", color = MaterialTheme.colorScheme.error)
        }
    }
}
