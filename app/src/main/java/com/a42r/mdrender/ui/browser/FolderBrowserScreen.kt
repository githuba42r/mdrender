package com.a42r.mdrender.ui.browser

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
    val snackbarHostState = remember { SnackbarHostState() }

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
        },
        floatingActionButton = {
            FloatingActionButton(onClick = { showImportSheet = true }) {
                Icon(Icons.Filled.Add, contentDescription = "Add")
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
                    // Folders
                    items(uiState.folders, key = { "folder_${it.id}" }) { folder ->
                        FileItem(
                            name = folder.name,
                            fileType = FileType.FOLDER,
                            isGridView = true,
                            onClick = { viewModel.navigateToFolder(folder.id) }
                        )
                    }
                    // Files
                    items(uiState.files, key = { "file_${it.id}" }) { file ->
                        val fileType = FileType.fromMimeType(file.mimeType)
                        FileItem(
                            name = file.name,
                            fileType = fileType,
                            isGridView = true,
                            onClick = { openFile(file) },
                            onLongClick = { menuFile = file }
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
                        SwipeToDismissBox(
                            state = rememberSwipeToDismissBoxState(
                                confirmValueChange = { value ->
                                    if (value == SwipeToDismissBoxValue.EndToStart) {
                                        // Ask for confirmation; row snaps back until confirmed
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
                            content = {
                                FileItem(
                                    name = file.name,
                                    fileType = fileType,
                                    isGridView = false,
                                    onClick = { openFile(file) },
                                    onLongClick = { menuFile = file },
                                    modifier = Modifier.animateItem()
                                )
                            }
                        )
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
