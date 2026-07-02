package com.a42r.mdrender.ui.browser

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.ui.navigation.FileType
import com.a42r.mdrender.ui.navigation.Routes

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FolderBrowserScreen(
    navController: androidx.navigation.NavController,
    viewModel: BrowserViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var showNewFolderDialog by remember { mutableStateOf(false) }
    var newFolderName by remember { mutableStateOf("") }
    var showImportSheet by remember { mutableStateOf(false) }
    var menuFile by remember { mutableStateOf<FileEntity?>(null) }
    var renameFile by remember { mutableStateOf<FileEntity?>(null) }
    var renameText by remember { mutableStateOf("") }
    var moveFile by remember { mutableStateOf<FileEntity?>(null) }
    var confirmDeleteFile by remember { mutableStateOf<FileEntity?>(null) }
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

    // Back exits selection mode before leaving the screen.
    BackHandler(enabled = selectionMode) { selectedIds = emptySet() }

    val openFile: (FileEntity) -> Unit = { file ->
        val route = when {
            file.mimeType.startsWith("text/markdown") -> Routes.MarkdownViewer.createRoute(file.id)
            file.mimeType.startsWith("text/plain") -> Routes.TextViewer.createRoute(file.id)
            file.mimeType.startsWith("image/") -> Routes.ImageViewer.createRoute(file.id)
            else -> Routes.TextViewer.createRoute(file.id)
        }
        navController.navigate(route)
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
                    title = { Text("MDRender") },
                    actions = {
                        IconButton(onClick = { viewModel.toggleGridView() }) {
                            Icon(
                                if (uiState.isGridView) Icons.Filled.ViewList else Icons.Filled.GridView,
                                contentDescription = "Toggle view"
                            )
                        }
                        IconButton(onClick = { navController.navigate(Routes.Settings.route) }) {
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
        Column(modifier = Modifier.padding(padding)) {
            // Breadcrumb
            BreadcrumbBar(
                path = uiState.breadcrumbPath,
                onNavigate = { viewModel.navigateToFolder(it) }
            )

            // Content
            if (uiState.isLoading) {
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
                            onClick = { if (!selectionMode) viewModel.navigateToFolder(folder.id) }
                        )
                    }
                    // Files
                    items(uiState.files, key = { "file_${it.id}" }) { file ->
                        val fileType = FileType.fromMimeType(file.mimeType)
                        FileItem(
                            name = file.name,
                            fileType = fileType,
                            isGridView = true,
                            selected = file.id in selectedIds,
                            onClick = { if (selectionMode) toggleSelect(file.id) else openFile(file) },
                            onLongClick = { if (selectionMode) toggleSelect(file.id) else menuFile = file }
                        )
                    }
                }
            } else {
                LazyColumn {
                    items(uiState.folders, key = { "folder_${it.id}" }) { folder ->
                        ListItem(
                            headlineContent = { Text(folder.name) },
                            leadingContent = { Icon(Icons.Filled.Folder, "Folder") },
                            modifier = Modifier.animateItem()
                        ) // No click support needed for list view in this iteration
                    }
                    items(uiState.files, key = { "file_${it.id}" }) { file ->
                        val fileType = FileType.fromMimeType(file.mimeType)
                        val fileRow = @Composable {
                            FileItem(
                                name = file.name,
                                fileType = fileType,
                                isGridView = false,
                                selected = file.id in selectedIds,
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
                            headlineContent = { Text("Root") },
                            leadingContent = { Icon(Icons.Filled.Home, "Root") },
                            modifier = Modifier.clickable {
                                viewModel.moveFiles(idsToMove, null)
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
                                    viewModel.moveFiles(idsToMove, target.folder.id)
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

    // Rename dialog
    renameFile?.let { file ->
        AlertDialog(
            onDismissRequest = { renameFile = null },
            title = { Text("Rename File") },
            text = {
                OutlinedTextField(
                    value = renameText,
                    onValueChange = { renameText = it },
                    label = { Text("File name") },
                    singleLine = true
                )
            },
            confirmButton = {
                TextButton(
                    enabled = renameText.isNotBlank(),
                    onClick = {
                        viewModel.renameFile(file.id, renameText.trim())
                        renameFile = null
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
                            headlineContent = { Text("Root") },
                            leadingContent = { Icon(Icons.Filled.Home, "Root") },
                            modifier = Modifier.clickable(enabled = file.folderId != null) {
                                viewModel.moveFile(file.id, null)
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
                                    viewModel.moveFile(file.id, target.folder.id)
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
        AlertDialog(
            onDismissRequest = { showNewFolderDialog = false },
            title = { Text("New Folder") },
            text = {
                OutlinedTextField(
                    value = newFolderName,
                    onValueChange = { newFolderName = it },
                    label = { Text("Folder name") },
                    singleLine = true
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    if (newFolderName.isNotBlank()) {
                        viewModel.createFolder(newFolderName.trim())
                        newFolderName = ""
                        showNewFolderDialog = false
                    }
                }) { Text("Create") }
            },
            dismissButton = { TextButton(onClick = { showNewFolderDialog = false }) { Text("Cancel") } }
        )
    }
}
