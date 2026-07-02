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
    val snackbarHostState = remember { SnackbarHostState() }

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
                            onClick = {
                                val route = when {
                                    file.mimeType.startsWith("text/markdown") -> Routes.MarkdownViewer.createRoute(file.id)
                                    file.mimeType.startsWith("text/plain") -> Routes.TextViewer.createRoute(file.id)
                                    file.mimeType.startsWith("image/") -> Routes.ImageViewer.createRoute(file.id)
                                    else -> Routes.TextViewer.createRoute(file.id)
                                }
                                navController.navigate(route)
                            }
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
                                        viewModel.deleteFile(file.id)
                                        true
                                    } else false
                                }
                            ),
                            backgroundContent = {
                                Box(
                                    Modifier.fillMaxSize().padding(horizontal = 20.dp),
                                    contentAlignment = Alignment.CenterEnd
                                ) { Icon(Icons.Filled.Delete, "Delete", tint = MaterialTheme.colorScheme.error) }
                            },
                            content = {
                                ListItem(
                                    headlineContent = { Text(file.name) },
                                    leadingContent = { Icon(fileType.icon, fileType.label) },
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
