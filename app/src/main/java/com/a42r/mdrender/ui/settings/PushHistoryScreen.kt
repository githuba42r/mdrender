package com.a42r.mdrender.ui.settings

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.data.repository.PushHistoryEntry
import com.a42r.mdrender.data.repository.PushHistoryRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.launch
import javax.inject.Inject
import kotlin.math.roundToLong

@Composable
fun PushHistoryScreen(
    revealHidden: StateFlow<Boolean>,
    viewModel: PushHistoryViewModel = hiltViewModel()
) {
    val entries by viewModel.entries.collectAsStateWithLifecycle()
    val hiddenRevealed by revealHidden.collectAsStateWithLifecycle()
    var showClearConfirm by remember { mutableStateOf(false) }

    val filtered = remember(entries, hiddenRevealed) {
        if (hiddenRevealed) entries
        else entries.filter { !it.isHidden }
    }.filter { it.fileExists }

    if (filtered.isEmpty()) {
        Box(
            modifier = Modifier.fillMaxSize().padding(32.dp),
            contentAlignment = Alignment.Center
        ) {
            Text("No pushes yet", style = MaterialTheme.typography.bodyLarge)
        }
    } else {
        Column(modifier = Modifier.fillMaxSize()) {
            LazyColumn(
                modifier = Modifier.weight(1f),
                contentPadding = PaddingValues(vertical = 8.dp)
            ) {
                items(filtered, key = { it.entity.id }) { entry ->
                    PushHistoryRow(entry)
                }
            }
            HorizontalDivider()
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.End
            ) {
                TextButton(onClick = { showClearConfirm = true }) {
                    Text("Clear history")
                }
            }
        }
    }

    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text("Clear push history?") },
            text = { Text("This removes the record of all received files. The files themselves are not deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    showClearConfirm = false
                    viewModel.clearAll()
                }) { Text("Clear") }
            },
            dismissButton = {
                TextButton(onClick = { showClearConfirm = false }) { Text("Cancel") }
            }
        )
    }
}

@Composable
private fun PushHistoryRow(entry: PushHistoryEntry) {
    val entity = entry.entity
    ListItem(
        headlineContent = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = entity.fileName,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )
                if (entry.isHidden) {
                    Spacer(Modifier.width(4.dp))
                    Icon(
                        Icons.Filled.VisibilityOff,
                        contentDescription = "Hidden",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(16.dp)
                    )
                }
            }
        },
        supportingContent = {
            Text(formatDateTime(entity.pushedAt), style = MaterialTheme.typography.bodySmall)
        },
        leadingContent = {
            Icon(Icons.Filled.Download, contentDescription = null)
        },
        trailingContent = {
            Text(
                text = formatSize(entity.fileSize),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    )
    HorizontalDivider()
}

private fun formatSize(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    val units = arrayOf("KB", "MB", "GB")
    var value = bytes.toDouble() / 1024
    for (unit in units) {
        if (value < 1024) return "%.1f %s".format(value, unit)
        value /= 1024
    }
    return "%.1f TB".format(value)
}

private fun formatDateTime(millis: Long): String {
    val sdf = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", java.util.Locale.getDefault())
    return sdf.format(java.util.Date(millis))
}

@HiltViewModel
class PushHistoryViewModel @Inject constructor(
    private val pushHistoryRepository: PushHistoryRepository
) : androidx.lifecycle.ViewModel() {
    val entries = pushHistoryRepository.getAnnotatedFlow()
        .map { list -> list.sortedByDescending { it.entity.pushedAt } }
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5_000),
            initialValue = emptyList()
        )

    fun clearAll() {
        viewModelScope.launch { pushHistoryRepository.deleteAll() }
    }
}
