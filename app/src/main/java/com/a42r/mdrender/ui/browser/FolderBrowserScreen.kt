package com.a42r.mdrender.ui.browser

import android.Manifest
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.data.dao.FileListItem
import com.a42r.mdrender.share.SharePlan
import com.a42r.mdrender.ui.navigation.FileType
import com.a42r.mdrender.ui.navigation.Routes
import com.a42r.mdrender.ui.viewer.IndexTocView
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun FolderBrowserScreen(
    navController: androidx.navigation.NavController,
    viewModel: BrowserViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var showNewFolderDialog by remember { mutableStateOf(false) }
    var newFolderName by remember { mutableStateOf("") }
    var showImportSheet by remember { mutableStateOf(false) }
    var menuFile by remember { mutableStateOf<FileListItem?>(null) }
    var renameFile by remember { mutableStateOf<FileListItem?>(null) }
    var renameText by remember { mutableStateOf("") }
    var moveFile by remember { mutableStateOf<FileListItem?>(null) }
    var confirmDeleteFile by remember { mutableStateOf<FileListItem?>(null) }
    var propertiesFile by remember { mutableStateOf<FileListItem?>(null) }
    var folderMenu by remember { mutableStateOf<com.a42r.mdrender.data.entity.FolderEntity?>(null) }
    var moveFolderState by remember { mutableStateOf<com.a42r.mdrender.data.entity.FolderEntity?>(null) }
    var renameFolderState by remember { mutableStateOf<com.a42r.mdrender.data.entity.FolderEntity?>(null) }
    var renameFolderText by remember { mutableStateOf("") }
    var confirmDeleteFolder by remember { mutableStateOf<com.a42r.mdrender.data.entity.FolderEntity?>(null) }
    val revealHidden by viewModel.revealHidden.collectAsStateWithLifecycle()
    val localSendEnabled by viewModel.localSendEnabled.collectAsStateWithLifecycle()
    val pendingShare by viewModel.pendingShare.collectAsStateWithLifecycle()
    val shareInProgress by viewModel.shareInProgress.collectAsStateWithLifecycle()
    val moveConflict by viewModel.moveConflict.collectAsStateWithLifecycle()
    val processingFiles by viewModel.processingFiles.collectAsStateWithLifecycle()
    val lastOpenedFile by viewModel.lastOpenedFile.collectAsStateWithLifecycle()
    val lastOpenedFileHidden by viewModel.lastOpenedFileHidden.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    val notifPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* the service still runs without notification permission */ }
    val toggleLocalSend = {
        val newState = !localSendEnabled
        if (newState && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        viewModel.setLocalSendEnabled(newState)
    }

    // Title gesture: routed through GestureRouter which handles 12-tap,
    // tap-sequence, and multi-touch (multi-touch is its own pointerInput below).
    val onTitleShortTap = { viewModel.onTitleTap(200L) }
    val onTitleLongTap = { viewModel.onTitleTap(800L) }

    var selectedIds by remember { mutableStateOf<Set<Long>>(emptySet()) }
    var confirmDeleteMulti by remember { mutableStateOf(false) }
    var moveMulti by remember { mutableStateOf(false) }
    val selectionMode = selectedIds.isNotEmpty()
    val snackbarHostState = remember { SnackbarHostState() }

    // Selections referencing files no longer present (moved/deleted) are pruned.
    LaunchedEffect(uiState.files) {
        val present = uiState.files.mapTo(mutableSetOf()) { it.id }
        if (selectedIds.any { it !in present }) selectedIds = selectedIds intersect present
    }

    val toggleSelect: (Long) -> Unit = { id ->
        selectedIds = if (id in selectedIds) selectedIds - id else selectedIds + id
    }

    // Back in a subfolder ascends to the parent folder instead of closing
    // the app. When at Home (root) the system back exits normally.
    // Only active when not in selection mode — selection mode takes priority
    // (BackHandler dispatches LIFO, so this one is registered first).
    val canAscend = uiState.currentFolderId != null && !selectionMode
    BackHandler(enabled = canAscend) {
        val parentId = uiState.breadcrumbPath
            .dropLast(1)
            .lastOrNull()
            ?.id
        viewModel.navigateToFolder(parentId)
    }

    // Back in selection mode clears the selection before navigating.
    BackHandler(enabled = selectionMode) { selectedIds = emptySet() }

    val openFile: (FileListItem) -> Unit = { file ->
        if (file.mimeType.startsWith("audio/") && file.folderId != null) {
            scope.launch {
                if (viewModel.isFolderHidden(file.folderId)) {
                    viewModel.playHiddenAudio(file.id)
                } else {
                    navController.navigate(Routes.AudioPlayer.createRoute(file.id))
                }
            }
        } else {
            val route = when {
                file.mimeType.startsWith("text/markdown") -> Routes.MarkdownViewer.createRoute(file.id)
                file.mimeType.startsWith("text/plain") -> Routes.TextViewer.createRoute(file.id)
                file.mimeType.startsWith("image/") -> Routes.ImageViewer.createRoute(file.id)
                else -> Routes.TextViewer.createRoute(file.id)
            }
            navController.navigate(route)
        }
    }

    // Collect undo deletes
    LaunchedEffect(Unit) {
        viewModel.undoDelete.collect { undo ->
            val result = snackbarHostState.showSnackbar(
                message = undo.message,
                actionLabel = "Undo",
                duration = SnackbarDuration.Short
            )
            if (result == SnackbarResult.ActionPerformed) {
                undo.action()
            }
        }
    }

    // Launch the system share sheet when staging completes; leaving selection
    // mode afterwards since the action is done.
    LaunchedEffect(Unit) {
        viewModel.shareIntent.collect { intent ->
            context.startActivity(intent)
            selectedIds = emptySet()
        }
    }
    LaunchedEffect(Unit) {
        viewModel.shareError.collect { snackbarHostState.showSnackbar(it) }
    }
    LaunchedEffect(Unit) {
        viewModel.userMessage.collect { snackbarHostState.showSnackbar(it) }
    }

    Scaffold(
        topBar = {
            if (selectionMode) {
                TopAppBar(
                    title = { Text("${selectedIds.size} selected") },
                    navigationIcon = {
                        IconButton(onClick = { selectedIds = emptySet() }) {
                            Icon(Icons.Filled.Close, contentDescription = "Clear selection")
                        }
                    },
                    actions = {
                        IconButton(onClick = { viewModel.requestShare(selectedIds) }) {
                            Icon(Icons.Filled.Share, contentDescription = "Share")
                        }
                        IconButton(onClick = {
                            viewModel.loadMoveTargets()
                            moveMulti = true
                        }) {
                            Icon(Icons.Filled.DriveFileMove, contentDescription = "Move")
                        }
                        IconButton(onClick = { confirmDeleteMulti = true }) {
                            Icon(Icons.Filled.Delete, contentDescription = "Delete")
                        }
                    }
                )
            } else {
                TopAppBar(
                    title = {
                        Text(
                            "MDRender",
                            modifier = Modifier.combinedClickable(
                                onClick = onTitleShortTap,
                                onLongClick = onTitleLongTap
                            )
                        )
                    },
                    actions = {
                        // Last-opened file shortcut — hidden when the file is in a
                        // hidden folder that is not currently revealed.
                        val lastFile = lastOpenedFile
                        if (lastFile != null && (!lastOpenedFileHidden || revealHidden)) {
                            Box(
                                modifier = Modifier
                                    .clickable(onClick = {
                                        val route = when {
                                            lastFile.mimeType.startsWith("text/markdown") -> Routes.MarkdownViewer.createRoute(lastFile.id)
                                            lastFile.mimeType.startsWith("text/plain") -> Routes.TextViewer.createRoute(lastFile.id)
                                            lastFile.mimeType.startsWith("image/") -> Routes.ImageViewer.createRoute(lastFile.id)
                                            else -> Routes.TextViewer.createRoute(lastFile.id)
                                        }
                                        navController.navigate(route)
                                    })
                                    .padding(8.dp)
                            ) {
                                Icon(
                                    Icons.Filled.History,
                                    contentDescription = "Open last file",
                                    tint = MaterialTheme.colorScheme.primary
                                )
                            }
                        }
                        if (revealHidden) {
                            Box(
                                modifier = Modifier
                                    .clickable(onClick = { viewModel.turnOffReveal() })
                                    .padding(8.dp)
                            ) {
                                Icon(
                                    Icons.Filled.Visibility,
                                    contentDescription = "Hidden folders visible — tap to hide",
                                    tint = MaterialTheme.colorScheme.primary
                                )
                            }
                        }
                        Box(
                            modifier = Modifier
                                .clickable(onClick = { toggleLocalSend() })
                                .padding(8.dp)
                        ) {
                            Icon(
                                if (localSendEnabled) Icons.Filled.Sensors else Icons.Filled.SensorsOff,
                                contentDescription = if (localSendEnabled)
                                    "LocalSend on — tap to turn off"
                                else "LocalSend off — tap to turn on",
                                tint = if (localSendEnabled)
                                    MaterialTheme.colorScheme.primary else LocalContentColor.current
                            )
                        }
                        Box(
                            modifier = Modifier
                                .clickable(onClick = { viewModel.toggleGridView() })
                                .padding(8.dp)
                        ) {
                            Icon(
                                if (uiState.isGridView) Icons.Filled.ViewList else Icons.Filled.GridView,
                                contentDescription = "Toggle view"
                            )
                        }
                        Box(
                            modifier = Modifier
                                .combinedClickable(
                                    onClick = { navController.navigate(Routes.Settings.route) },
                                    onLongClick = {
                                        if (revealHidden) navController.navigate(Routes.UnhideSettings.route)
                                    }
                                )
                                .padding(8.dp)
                        ) {
                            Icon(Icons.Filled.Settings, contentDescription = "Settings")
                        }
                    }
                )
            }
        },
        floatingActionButton = {
            if (!selectionMode) {
                FloatingActionButton(onClick = { showImportSheet = true }) {
                    Icon(Icons.Filled.Add, contentDescription = "Add")
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .pointerInput(Unit) {
                    awaitPointerEventScope {
                        while (true) {
                            val event = awaitPointerEvent(PointerEventPass.Initial)
                            if (!viewModel.isMultiTouchEnabled()) continue
                            for (change in event.changes) {
                                val id = change.id.value
                                when {
                                    change.pressed && !change.previousPressed ->
                                        viewModel.onMultiTouchPointerEvent(id, 0, change.position.x, change.position.y, size.width, size.height)
                                    !change.pressed && change.previousPressed ->
                                        viewModel.onMultiTouchPointerEvent(id, 1, change.position.x, change.position.y, size.width, size.height)
                                    change.pressed && change.position != change.previousPosition ->
                                        viewModel.onMultiTouchPointerEvent(id, 2, change.position.x, change.position.y, size.width, size.height)
                                }
                            }
                        }
                    }
                }
        ) {
            if (shareInProgress) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            }
            // Breadcrumb
            BreadcrumbBar(
                path = uiState.breadcrumbPath,
                onNavigate = { viewModel.navigateToFolder(it) }
            )

            // Content
            val indexToc = uiState.indexTocContent
            if (indexToc != null) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                ) {
                    IndexTocView(
                        markdown = indexToc,
                        onNavigateToFile = { fileId ->
                            navController.navigate(
                                Routes.MarkdownViewer.createRoute(fileId)
                            )
                        },
                        resolveLink = viewModel::resolveFileLink
                    )
                }
            } else if (uiState.isLoading) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else if (uiState.folders.isEmpty() && uiState.files.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("No files yet. Tap + to import.", style = MaterialTheme.typography.bodyLarge)
                }
            } else if (uiState.isGridView) {
                LazyVerticalGrid(columns = GridCells.Adaptive(120.dp)) {
                    // Folders (not selectable; disabled while selecting)
                    items(uiState.folders, key = { "folder_${it.id}" }) { folder ->
                        FileItem(
                            name = folder.name,
                            fileType = FileType.FOLDER,
                            isGridView = true,
                            onClick = { if (!selectionMode) viewModel.navigateToFolder(folder.id) },
                            onLongClick = { if (!selectionMode) folderMenu = folder },
                            hiddenBadge = folder.hidden
                        )
                    }
                    // Files
                    items(uiState.files, key = { "file_${it.id}" }) { file ->
                        val fileType = FileType.fromMimeType(file.mimeType)
                        val thumb by produceState<ByteArray?>(null, file.id) {
                            value = if (file.mimeType.startsWith("image/"))
                                viewModel.decryptThumbnail(file.id) else null
                        }
                        FileItem(
                            name = file.name,
                            fileType = fileType,
                            isGridView = true,
                            selected = file.id in selectedIds,
                            thumbnail = thumb,
                            encryptedBadge = file.storageType != "plain",
                            processing = file.id in processingFiles,
                            onClick = { if (selectionMode) toggleSelect(file.id) else openFile(file) },
                            onLongClick = { if (selectionMode) toggleSelect(file.id) else menuFile = file }
                        )
                    }
                }
            } else {
                LazyColumn {
                    items(uiState.folders, key = { "folder_${it.id}" }) { folder ->
                        FileItem(
                            name = folder.name,
                            fileType = FileType.FOLDER,
                            isGridView = false,
                            onClick = { if (!selectionMode) viewModel.navigateToFolder(folder.id) },
                            onLongClick = { if (!selectionMode) folderMenu = folder },
                            hiddenBadge = folder.hidden,
                            modifier = Modifier.animateItem()
                        )
                    }
                    items(uiState.files, key = { "file_${it.id}" }) { file ->
                        val fileType = FileType.fromMimeType(file.mimeType)
                        val thumb by produceState<ByteArray?>(null, file.id) {
                            value = if (file.mimeType.startsWith("image/"))
                                viewModel.decryptThumbnail(file.id) else null
                        }
                        val fileRow = @Composable {
                            FileItem(
                                name = file.name,
                                fileType = fileType,
                                isGridView = false,
                                selected = file.id in selectedIds,
                                thumbnail = thumb,
                                encryptedBadge = file.storageType != "plain",
                                processing = file.id in processingFiles,
                                onClick = { if (selectionMode) toggleSelect(file.id) else openFile(file) },
                                onLongClick = { if (selectionMode) toggleSelect(file.id) else menuFile = file },
                                modifier = Modifier.animateItem()
                            )
                        }
                        if (selectionMode) {
                            // No swipe-to-delete while selecting — taps toggle selection.
                            fileRow()
                        } else {
                            SwipeToDismissBox(
                                state = rememberSwipeToDismissBoxState(
                                    confirmValueChange = { value ->
                                        if (value == SwipeToDismissBoxValue.EndToStart) {
                                            confirmDeleteFile = file
                                        }
                                        false
                                    }
                                ),
                                backgroundContent = {
                                    Box(
                                        Modifier.fillMaxSize().padding(horizontal = 20.dp),
                                        contentAlignment = Alignment.CenterEnd
                                    ) { Icon(Icons.Filled.Delete, "Delete", tint = MaterialTheme.colorScheme.error) }
                                },
                                content = { fileRow() }
                            )
                        }
                    }
                }
            }
        }
    }

    // Bottom Sheet for FAB
    if (showImportSheet) {
        ModalBottomSheet(onDismissRequest = { showImportSheet = false }) {
            Column(modifier = Modifier.padding(24.dp)) {
                ListItem(
                    headlineContent = { Text("New Folder") },
                    leadingContent = { Icon(Icons.Filled.CreateNewFolder, "New Folder") },
                    modifier = Modifier.clickable {
                        showImportSheet = false
                        showNewFolderDialog = true
                    }
                )
                ListItem(
                    headlineContent = { Text("Import File") },
                    leadingContent = { Icon(Icons.Filled.FileUpload, "Import File") },
                    modifier = Modifier.clickable {
                        showImportSheet = false
                        navController.navigate(Routes.Import.createRoute(uiState.currentFolderId))
                    }
                )
            }
        }
    }

    // File context menu (long-press)
    menuFile?.let { file ->
        ModalBottomSheet(onDismissRequest = { menuFile = null }) {
            Column(modifier = Modifier.padding(bottom = 24.dp)) {
                ListItem(
                    headlineContent = { Text(file.name, maxLines = 1) },
                    supportingContent = { Text(FileType.fromMimeType(file.mimeType).label) }
                )
                HorizontalDivider()
                ListItem(
                    headlineContent = { Text("Open") },
                    leadingContent = { Icon(Icons.Filled.FileOpen, "Open") },
                    modifier = Modifier.clickable {
                        menuFile = null
                        openFile(file)
                    }
                )
                ListItem(
                    headlineContent = { Text("Share") },
                    leadingContent = { Icon(Icons.Filled.Share, "Share") },
                    modifier = Modifier.clickable {
                        menuFile = null
                        viewModel.requestShare(listOf(file.id))
                    }
                )
                ListItem(
                    headlineContent = { Text("Rename") },
                    leadingContent = { Icon(Icons.Filled.Edit, "Rename") },
                    modifier = Modifier.clickable {
                        menuFile = null
                        renameText = file.name
                        renameFile = file
                    }
                )
                ListItem(
                    headlineContent = { Text("Move") },
                    leadingContent = { Icon(Icons.Filled.DriveFileMove, "Move") },
                    modifier = Modifier.clickable {
                        menuFile = null
                        viewModel.loadMoveTargets()
                        moveFile = file
                    }
                )
                ListItem(
                    headlineContent = { Text("Select") },
                    leadingContent = { Icon(Icons.Filled.CheckCircle, "Select") },
                    modifier = Modifier.clickable {
                        selectedIds = selectedIds + file.id
                        menuFile = null
                    }
                )
                if (file.id in processingFiles) {
                    ListItem(
                        headlineContent = { Text("Processing…") },
                        leadingContent = { Icon(Icons.Filled.HourglassEmpty, "Processing") },
                        colors = ListItemDefaults.colors(
                            headlineColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
                            leadingIconColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                        )
                    )
                } else if (file.storageType == "plain") {
                    ListItem(
                        headlineContent = { Text("Encrypt") },
                        leadingContent = { Icon(Icons.Filled.Lock, "Encrypt") },
                        modifier = Modifier.clickable {
                            menuFile = null
                            viewModel.encryptFile(file.id)
                        }
                    )
                } else {
                    ListItem(
                        headlineContent = { Text("Decrypt") },
                        leadingContent = { Icon(Icons.Filled.LockOpen, "Decrypt") },
                        modifier = Modifier.clickable {
                            menuFile = null
                            viewModel.decryptFile(file.id)
                        }
                    )
                }
                ListItem(
                    headlineContent = { Text("Properties") },
                    leadingContent = { Icon(Icons.Filled.Info, "Properties") },
                    modifier = Modifier.clickable {
                        propertiesFile = file
                        menuFile = null
                    }
                )
                ListItem(
                    headlineContent = { Text("Delete") },
                    leadingContent = {
                        Icon(Icons.Filled.Delete, "Delete", tint = MaterialTheme.colorScheme.error)
                    },
                    modifier = Modifier.clickable {
                        menuFile = null
                        confirmDeleteFile = file
                    }
                )
            }
        }
    }

    // Folder context menu (long-press)
    folderMenu?.let { folder ->
        ModalBottomSheet(onDismissRequest = { folderMenu = null }) {
            Column(modifier = Modifier.padding(bottom = 24.dp)) {
                ListItem(
                    headlineContent = { Text(folder.name, maxLines = 1) },
                    supportingContent = { Text("Folder") }
                )
                HorizontalDivider()
                ListItem(
                    headlineContent = { Text("Open") },
                    leadingContent = { Icon(Icons.Filled.FolderOpen, "Open") },
                    modifier = Modifier.clickable {
                        folderMenu = null
                        viewModel.navigateToFolder(folder.id)
                    }
                )
                ListItem(
                    headlineContent = { Text("Rename") },
                    leadingContent = { Icon(Icons.Filled.Edit, "Rename") },
                    modifier = Modifier.clickable {
                        folderMenu = null
                        renameFolderText = folder.name
                        renameFolderState = folder
                    }
                )
                ListItem(
                    headlineContent = { Text("Move") },
                    leadingContent = { Icon(Icons.Filled.DriveFileMove, "Move") },
                    modifier = Modifier.clickable {
                        folderMenu = null
                        viewModel.loadMoveTargets()
                        moveFolderState = folder
                    }
                )
                // Hide/Unhide only appear while hidden folders are revealed —
                // offering "Hide folder" to everyone would leak that the
                // hidden-folders feature exists.
                if (revealHidden) {
                    if (folder.hidden) {
                        ListItem(
                            headlineContent = { Text("Unhide folder") },
                            leadingContent = { Icon(Icons.Filled.Visibility, "Unhide") },
                            modifier = Modifier.clickable {
                                viewModel.setFolderHidden(folder.id, false)
                                folderMenu = null
                            }
                        )
                    } else {
                        ListItem(
                            headlineContent = { Text("Hide folder") },
                            leadingContent = { Icon(Icons.Filled.VisibilityOff, "Hide") },
                            modifier = Modifier.clickable {
                                viewModel.setFolderHidden(folder.id, true)
                                folderMenu = null
                            }
                        )
                    }
                }
                ListItem(
                    headlineContent = { Text("Delete") },
                    leadingContent = {
                        Icon(Icons.Filled.Delete, "Delete", tint = MaterialTheme.colorScheme.error)
                    },
                    modifier = Modifier.clickable {
                        folderMenu = null
                        confirmDeleteFolder = folder
                    }
                )
            }
        }
    }

    // Folder move dialog (excludes the folder's own subtree and hidden folders)
    moveFolderState?.let { folder ->
        val moveTargets by viewModel.moveTargets.collectAsStateWithLifecycle()
        val excluded by produceState(setOf(folder.id), folder.id) {
            value = viewModel.getSubtreeIds(folder.id)
        }
        AlertDialog(
            onDismissRequest = { moveFolderState = null },
            title = { Text("Move \"${folder.name}\" to") },
            text = {
                LazyColumn {
                    item {
                        ListItem(
                            headlineContent = { Text("Home") },
                            leadingContent = { Icon(Icons.Filled.Home, "Home") },
                            modifier = Modifier.clickable(enabled = folder.parentId != null) {
                                viewModel.moveFolder(folder.id, null)
                                moveFolderState = null
                            },
                            colors = ListItemDefaults.colors(
                                headlineColor = if (folder.parentId == null)
                                    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                                else ListItemDefaults.colors().headlineColor
                            )
                        )
                    }
                    items(
                        moveTargets.filter { it.folder.id !in excluded },
                        key = { it.folder.id }
                    ) { target ->
                        val isCurrent = folder.parentId == target.folder.id
                        ListItem(
                            headlineContent = { Text(target.folder.name) },
                            leadingContent = { Icon(Icons.Filled.Folder, "Folder") },
                            modifier = Modifier
                                .padding(start = (target.depth * 16).dp)
                                .clickable(enabled = !isCurrent) {
                                    viewModel.moveFolder(folder.id, target.folder.id)
                                    moveFolderState = null
                                },
                            colors = ListItemDefaults.colors(
                                headlineColor = if (isCurrent)
                                    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                                else ListItemDefaults.colors().headlineColor
                            )
                        )
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { moveFolderState = null }) { Text("Cancel") } }
        )
    }

    // Rename Folder dialog
    renameFolderState?.let { folder ->
        var folderRenameError by remember(folder) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { renameFolderState = null },
            title = { Text("Rename Folder") },
            text = {
                Column {
                    OutlinedTextField(
                        value = renameFolderText,
                        onValueChange = { renameFolderText = it; folderRenameError = false },
                        label = { Text("Folder name") },
                        singleLine = true,
                        isError = folderRenameError
                    )
                    if (folderRenameError) {
                        Text(
                            "A folder with this name already exists",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(
                    enabled = renameFolderText.isNotBlank(),
                    onClick = {
                        val newName = renameFolderText.trim()
                        scope.launch {
                            if (viewModel.folderNameExists(folder.parentId, newName, excludeId = folder.id)) {
                                folderRenameError = true
                            } else {
                                viewModel.renameFolder(folder.id, newName)
                                renameFolderState = null
                            }
                        }
                    }
                ) { Text("Rename") }
            },
            dismissButton = { TextButton(onClick = { renameFolderState = null }) { Text("Cancel") } }
        )
    }

    // Folder delete confirmation (cascades to contents)
    confirmDeleteFolder?.let { folder ->
        AlertDialog(
            onDismissRequest = { confirmDeleteFolder = null },
            title = { Text("Delete Folder?") },
            text = { Text("\"${folder.name}\" and all its contents will be permanently deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.deleteFolder(folder.id)
                    confirmDeleteFolder = null
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmDeleteFolder = null }) { Text("Cancel") } }
        )
    }

    // Delete confirmation dialog
    confirmDeleteFile?.let { file ->
        AlertDialog(
            onDismissRequest = { confirmDeleteFile = null },
            title = { Text("Delete File?") },
            text = { Text("\"${file.name}\" will be permanently deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.deleteFile(file.id)
                    confirmDeleteFile = null
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmDeleteFile = null }) { Text("Cancel") } }
        )
    }

    // Multi-select delete confirmation
    if (confirmDeleteMulti) {
        val count = selectedIds.size
        AlertDialog(
            onDismissRequest = { confirmDeleteMulti = false },
            title = { Text("Delete $count file(s)?") },
            text = { Text("The selected file(s) will be permanently deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.deleteFiles(selectedIds)
                    selectedIds = emptySet()
                    confirmDeleteMulti = false
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmDeleteMulti = false }) { Text("Cancel") } }
        )
    }

    // Hidden-item share warning
    pendingShare?.let { pending ->
        val n = pending.hidden.size
        val warning = if (n == 1)
            "One of the items you are sharing is stored in a hidden folder. " +
            "It is sensitive in nature and is normally hidden for security purposes."
        else
            "$n of the items you are sharing are stored in a hidden folder. " +
            "These items are sensitive in nature and are normally hidden for security purposes."
        AlertDialog(
            onDismissRequest = { viewModel.cancelShare() },
            icon = { Icon(Icons.Filled.Warning, contentDescription = null) },
            title = { Text("Share hidden items?") },
            text = {
                Text(warning + "\n\n" + pending.hidden.joinToString("\n") { "• ${it.name}" })
            },
            confirmButton = {
                TextButton(onClick = { viewModel.confirmShareAll() }) {
                    Text(if (pending.visible.isEmpty()) "Share" else "Share all")
                }
            },
            dismissButton = {
                Row {
                    if (pending.visible.isNotEmpty()) {
                        TextButton(onClick = { viewModel.confirmShareVisibleOnly() }) {
                            Text("Share non-hidden only")
                        }
                    }
                    TextButton(onClick = { viewModel.cancelShare() }) { Text("Cancel") }
                }
            }
        )
    }

    // Move name-conflict dialog (Replace / Skip / Cancel + apply-to-all)
    moveConflict?.let { conflict ->
        var applyToAll by remember(conflict) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { viewModel.resolveMoveConflict(ConflictDecision.CancelBatch) },
            title = { Text("Name conflict") },
            text = {
                Column {
                    Text("\"${conflict.file.name}\" already exists in \"${conflict.targetFolderName}\".")
                    if (conflict.remaining > 1) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.padding(top = 8.dp)
                        ) {
                            Checkbox(checked = applyToAll, onCheckedChange = { applyToAll = it })
                            Text("Apply to all remaining conflicts")
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.resolveMoveConflict(ConflictDecision.Replace(applyToAll))
                }) { Text("Replace") }
            },
            dismissButton = {
                Row {
                    TextButton(onClick = {
                        viewModel.resolveMoveConflict(ConflictDecision.Skip(applyToAll))
                    }) { Text("Skip") }
                    TextButton(onClick = {
                        viewModel.resolveMoveConflict(ConflictDecision.CancelBatch)
                    }) { Text("Cancel") }
                }
            }
        )
    }

    // Multi-select move dialog
    if (moveMulti) {
        val moveTargets by viewModel.moveTargets.collectAsStateWithLifecycle()
        val idsToMove = selectedIds
        AlertDialog(
            onDismissRequest = { moveMulti = false },
            title = { Text("Move ${idsToMove.size} file(s) to") },
            text = {
                LazyColumn {
                    item {
                        ListItem(
                            headlineContent = { Text("Home") },
                            leadingContent = { Icon(Icons.Filled.Home, "Home") },
                            modifier = Modifier.clickable {
                                viewModel.moveFilesResolvingConflicts(idsToMove, null)
                                selectedIds = emptySet()
                                moveMulti = false
                            }
                        )
                    }
                    items(moveTargets, key = { it.folder.id }) { target ->
                        ListItem(
                            headlineContent = { Text(target.folder.name) },
                            leadingContent = { Icon(Icons.Filled.Folder, "Folder") },
                            modifier = Modifier
                                .padding(start = (target.depth * 16).dp)
                                .clickable {
                                    viewModel.moveFilesResolvingConflicts(idsToMove, target.folder.id)
                                    selectedIds = emptySet()
                                    moveMulti = false
                                }
                        )
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { moveMulti = false }) { Text("Cancel") } }
        )
    }

    // Properties dialog
    propertiesFile?.let { file ->
        val isImage = file.mimeType.startsWith("image/")
        val isAudio = file.mimeType.startsWith("audio/")
        val resolution by produceState<Pair<Int, Int>?>(null, file.id) {
            value = if (isImage) viewModel.getImageResolution(file.id) else null
        }
        val audioDuration by produceState<Long?>(null, file.id) {
            value = if (isAudio) viewModel.getAudioDuration(file.id) else null
        }
        AlertDialog(
            onDismissRequest = { propertiesFile = null },
            title = { Text("Properties") },
            text = {
                Column {
                    PropertyRow("Name", file.name)
                    PropertyRow(
                        "Storage",
                        if (file.storageType != "plain") "Encrypted (AES-256-GCM)" else "Plain"
                    )
                    if (isImage) {
                        PropertyRow("Format", file.mimeType.substringAfter('/').uppercase())
                        PropertyRow(
                            "Resolution",
                            resolution?.let { "${it.first} × ${it.second}" } ?: "…"
                        )
                    } else if (isAudio) {
                        PropertyRow("Duration", audioDuration?.let { formatDuration(it) } ?: "…")
                        PropertyRow("Created", formatDateTime(file.createdAt))
                        PropertyRow("Size", formatSize(file.fileSize))
                    } else {
                        PropertyRow("Created", formatDateTime(file.createdAt))
                        PropertyRow("Size", formatSize(file.fileSize))
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { propertiesFile = null }) { Text("Close") }
            },
            dismissButton = if (file.id in processingFiles) {
                { TextButton(onClick = {}, enabled = false) { Text("Processing…") } }
            } else if (file.storageType != "plain") {
                { TextButton(onClick = {
                    propertiesFile = null
                    viewModel.decryptFile(file.id)
                }) { Text("Decrypt", color = MaterialTheme.colorScheme.primary) } }
            } else {
                { TextButton(onClick = {
                    propertiesFile = null
                    viewModel.encryptFile(file.id)
                }) { Text("Encrypt", color = MaterialTheme.colorScheme.primary) } }
            }
        )
    }

    // Rename dialog
    renameFile?.let { file ->
        var renameError by remember(file) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { renameFile = null },
            title = { Text("Rename File") },
            text = {
                Column {
                    OutlinedTextField(
                        value = renameText,
                        onValueChange = { renameText = it; renameError = false },
                        label = { Text("File name") },
                        singleLine = true,
                        isError = renameError
                    )
                    if (renameError) {
                        Text(
                            "A file with this name already exists",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(
                    enabled = renameText.isNotBlank(),
                    onClick = {
                        val newName = renameText.trim()
                        scope.launch {
                            if (viewModel.fileNameExists(file.folderId, newName, excludeId = file.id)) {
                                renameError = true
                            } else {
                                viewModel.renameFile(file.id, newName)
                                renameFile = null
                            }
                        }
                    }
                ) { Text("Rename") }
            },
            dismissButton = { TextButton(onClick = { renameFile = null }) { Text("Cancel") } }
        )
    }

    // Move dialog
    moveFile?.let { file ->
        val moveTargets by viewModel.moveTargets.collectAsStateWithLifecycle()
        AlertDialog(
            onDismissRequest = { moveFile = null },
            title = { Text("Move to") },
            text = {
                LazyColumn {
                    item {
                        ListItem(
                            headlineContent = { Text("Home") },
                            leadingContent = { Icon(Icons.Filled.Home, "Home") },
                            modifier = Modifier.clickable(enabled = file.folderId != null) {
                                viewModel.moveFilesResolvingConflicts(listOf(file.id), null)
                                moveFile = null
                            },
                            colors = ListItemDefaults.colors(
                                headlineColor = if (file.folderId == null)
                                    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                                else ListItemDefaults.colors().headlineColor
                            )
                        )
                    }
                    items(moveTargets, key = { it.folder.id }) { target ->
                        val isCurrent = file.folderId == target.folder.id
                        ListItem(
                            headlineContent = { Text(target.folder.name) },
                            leadingContent = { Icon(Icons.Filled.Folder, "Folder") },
                            modifier = Modifier
                                .padding(start = (target.depth * 16).dp)
                                .clickable(enabled = !isCurrent) {
                                    viewModel.moveFilesResolvingConflicts(listOf(file.id), target.folder.id)
                                    moveFile = null
                                },
                            colors = ListItemDefaults.colors(
                                headlineColor = if (isCurrent)
                                    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                                else ListItemDefaults.colors().headlineColor
                            )
                        )
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { moveFile = null }) { Text("Cancel") } }
        )
    }

    // New Folder Dialog
    if (showNewFolderDialog) {
        var folderError by remember { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { showNewFolderDialog = false },
            title = { Text("New Folder") },
            text = {
                Column {
                    OutlinedTextField(
                        value = newFolderName,
                        onValueChange = { newFolderName = it; folderError = false },
                        label = { Text("Folder name") },
                        singleLine = true,
                        isError = folderError
                    )
                    if (folderError) {
                        Text(
                            "A folder with this name already exists",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val name = newFolderName.trim()
                    if (name.isNotBlank()) {
                        scope.launch {
                            if (viewModel.folderNameExists(uiState.currentFolderId, name)) {
                                folderError = true
                            } else {
                                viewModel.createFolder(name)
                                newFolderName = ""
                                showNewFolderDialog = false
                            }
                        }
                    }
                }) { Text("Create") }
            },
            dismissButton = { TextButton(onClick = { showNewFolderDialog = false }) { Text("Cancel") } }
        )
    }
}

@Composable
private fun PropertyRow(label: String, value: String) {
    Row(modifier = Modifier.padding(vertical = 4.dp)) {
        Text(
            "$label:",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.width(96.dp)
        )
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun formatDateTime(millis: Long): String =
    SimpleDateFormat("d MMM yyyy, h:mm a", Locale.getDefault()).format(Date(millis))

private fun formatSize(bytes: Long): String = when {
    bytes >= 1_048_576 -> String.format(Locale.getDefault(), "%.1f MB", bytes / 1_048_576.0)
    bytes >= 1024 -> String.format(Locale.getDefault(), "%.1f KB", bytes / 1024.0)
    else -> "$bytes B"
}

private fun formatDuration(ms: Long): String {
    val totalSec = ms / 1000
    val min = totalSec / 60
    val sec = totalSec % 60
    return "%d:%02d".format(min, sec)
}
