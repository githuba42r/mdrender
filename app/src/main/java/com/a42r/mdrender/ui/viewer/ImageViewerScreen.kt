package com.a42r.mdrender.ui.viewer

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.PagerDefaults
import androidx.compose.foundation.pager.PagerSnapDistance
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
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

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun ImageViewerScreen(
    onBack: () -> Unit,
    onNavigateToFile: (Long) -> Unit = {},
    viewModel: ViewerViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
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

    val snackbarHostState = remember { SnackbarHostState() }

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

    // Delete/move notices from the context menu actions.
    LaunchedEffect(Unit) {
        viewModel.userMessage.collect { snackbarHostState.showSnackbar(it) }
    }

    var showActionsMenu by remember { mutableStateOf(false) }
    var showMoveDialog by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }

    val state = pager
    if (state == null) {
        Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        return
    }

    // All images deleted or moved out of the folder — leave the viewer.
    if (state.ids.isEmpty()) {
        LaunchedEffect(Unit) { onBack() }
        return
    }

    val pagerState = rememberPagerState(initialPage = state.startIndex) { state.ids.size }

    // After a delete/move removes pages, clamp the current index into range.
    LaunchedEffect(state.ids) {
        if (pagerState.currentPage > state.ids.lastIndex) {
            pagerState.scrollToPage(state.ids.lastIndex)
        }
    }

    // Reset zoom when the settled page changes; also update the title source.
    LaunchedEffect(pagerState.currentPage) {
        scale = 1f
        offset = Offset.Zero
    }

    // Clamp here too: composition can run before the LaunchedEffect below
    // scrolls the pager back into range after a delete/move.
    val currentId = state.ids[pagerState.currentPage.coerceIn(0, state.ids.lastIndex)]
    val currentName by produceState("", currentId) { value = viewModel.fileNameFor(currentId) }
    val currentFolderId = viewModel.folderId

    Scaffold(
        containerColor = Color.Black,
        snackbarHost = {
            // Match Scaffold's background so the snackbar blends with the content area.
            SnackbarHost(snackbarHostState)
        },
        topBar = {
            if (showAppBar) {
                TopAppBar(
                    title = { Text("$currentName  (${pagerState.currentPage + 1}/${state.ids.size})") },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                        }
                    },
                    actions = {
                        var menuExpanded by remember { mutableStateOf(false) }
                        IconButton(onClick = { menuExpanded = true }) {
                            Icon(Icons.Filled.MoreVert, contentDescription = "More options")
                        }
                        DropdownMenu(
                            expanded = menuExpanded,
                            onDismissRequest = { menuExpanded = false }
                        ) {
                            DropdownMenuItem(
                                text = { Text("Move") },
                                leadingIcon = { Icon(Icons.Filled.DriveFileMove, null) },
                                onClick = {
                                    menuExpanded = false
                                    viewModel.loadMoveTargets()
                                    showMoveDialog = true
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("Delete") },
                                leadingIcon = {
                                    Icon(
                                        Icons.Filled.Delete,
                                        null,
                                        tint = MaterialTheme.colorScheme.error
                                    )
                                },
                                onClick = {
                                    menuExpanded = false
                                    confirmDelete = true
                                }
                            )
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
            // One image per swipe — a fast fling can't skip past the next image.
            flingBehavior = PagerDefaults.flingBehavior(
                state = pagerState,
                pagerSnapDistance = PagerSnapDistance.atMost(1)
            ),
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
                    .combinedClickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onClick = { showAppBar = !showAppBar },
                        onLongClick = { showActionsMenu = true }
                    )
                    .pointerInput(id) {
                        // Only handle (and consume) the gesture when it's a
                        // pinch or the image is already zoomed. A one-finger
                        // drag at 1x is left unconsumed so the VerticalPager
                        // can page to the next/previous image.
                        awaitEachGesture {
                            awaitFirstDown(requireUnconsumed = false)
                            do {
                                val event = awaitPointerEvent()
                                val pressed = event.changes.count { it.pressed }
                                if (pressed >= 2 || scale > 1f) {
                                    val zoom = event.calculateZoom()
                                    val pan = event.calculatePan()
                                    val newScale = (scale * zoom).coerceIn(1f, 5f)
                                    scale = newScale
                                    offset = if (newScale > 1f) offset + pan else Offset.Zero
                                    event.changes.forEach { it.consume() }
                                }
                            } while (event.changes.any { it.pressed })
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

    // Long-press context menu for the current image.
    if (showActionsMenu) {
        ModalBottomSheet(onDismissRequest = { showActionsMenu = false }) {
            Column(modifier = Modifier.padding(bottom = 24.dp)) {
                ListItem(
                    headlineContent = { Text(currentName, maxLines = 1) },
                    supportingContent = { Text("Image") }
                )
                HorizontalDivider()
                ListItem(
                    headlineContent = { Text("Move") },
                    leadingContent = { Icon(Icons.Filled.DriveFileMove, "Move") },
                    modifier = Modifier.clickable {
                        showActionsMenu = false
                        viewModel.loadMoveTargets()
                        showMoveDialog = true
                    }
                )
                ListItem(
                    headlineContent = { Text("Delete") },
                    leadingContent = {
                        Icon(Icons.Filled.Delete, "Delete", tint = MaterialTheme.colorScheme.error)
                    },
                    modifier = Modifier.clickable {
                        showActionsMenu = false
                        confirmDelete = true
                    }
                )
            }
        }
    }

    // Move destination picker
    if (showMoveDialog) {
        val moveTargets by viewModel.moveTargets.collectAsStateWithLifecycle()
        AlertDialog(
            onDismissRequest = { showMoveDialog = false },
            title = { Text("Move \"$currentName\" to") },
            text = {
                LazyColumn {
                    item {
                        ListItem(
                            headlineContent = { Text("Home") },
                            leadingContent = { Icon(Icons.Filled.Home, "Home") },
                            modifier = Modifier.clickable(enabled = currentFolderId != null) {
                                viewModel.moveFile(currentId, null)
                                showMoveDialog = false
                            },
                            colors = ListItemDefaults.colors(
                                headlineColor = if (currentFolderId == null)
                                    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                                else ListItemDefaults.colors().headlineColor
                            )
                        )
                    }
                    items(
                        moveTargets.filter { it.folder.id != currentFolderId },
                        key = { it.folder.id }
                    ) { target ->
                        ListItem(
                            headlineContent = { Text(target.folder.name) },
                            leadingContent = { Icon(Icons.Filled.Folder, "Folder") },
                            modifier = Modifier
                                .padding(start = (target.depth * 16).dp)
                                .clickable {
                                    viewModel.moveFile(currentId, target.folder.id)
                                    showMoveDialog = false
                                }
                        )
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { showMoveDialog = false }) { Text("Cancel") } }
        )
    }

    // Delete confirmation
    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Delete File?") },
            text = { Text("\"$currentName\" will be permanently deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.deleteFile(currentId)
                    confirmDelete = false
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } }
        )
    }
}
