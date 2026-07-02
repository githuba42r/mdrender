package com.a42r.mdrender.ui.browser

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.a42r.mdrender.ui.navigation.FileType

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun FileItem(
    name: String,
    fileType: FileType,
    isGridView: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onLongClick: (() -> Unit)? = null,
    selected: Boolean = false
) {
    if (isGridView) {
        Card(
            modifier = modifier
                .width(120.dp)
                .padding(4.dp)
                .combinedClickable(onClick = onClick, onLongClick = onLongClick),
            colors = if (selected) CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.primaryContainer
            ) else CardDefaults.cardColors()
        ) {
            Column(
                modifier = Modifier.padding(12.dp).fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Icon(
                    imageVector = if (selected) Icons.Filled.CheckCircle else fileType.icon,
                    contentDescription = if (selected) "Selected" else fileType.label,
                    modifier = Modifier.size(40.dp),
                    tint = MaterialTheme.colorScheme.primary
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = name,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }
    } else {
        ListItem(
            headlineContent = { Text(name, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            leadingContent = {
                Icon(
                    if (selected) Icons.Filled.CheckCircle else fileType.icon,
                    if (selected) "Selected" else fileType.label,
                    tint = if (selected) MaterialTheme.colorScheme.primary else LocalContentColor.current
                )
            },
            colors = if (selected) ListItemDefaults.colors(
                containerColor = MaterialTheme.colorScheme.primaryContainer
            ) else ListItemDefaults.colors(),
            modifier = modifier.combinedClickable(onClick = onClick, onLongClick = onLongClick),
            // Swipe-to-delete is handled at the parent list level
        )
    }
}
