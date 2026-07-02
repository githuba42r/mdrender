package com.a42r.mdrender.ui.browser

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

@Composable
fun FileItem(
    name: String,
    fileType: FileType,
    isGridView: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    if (isGridView) {
        Card(
            onClick = onClick,
            modifier = modifier.width(120.dp).padding(4.dp)
        ) {
            Column(
                modifier = Modifier.padding(12.dp).fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Icon(
                    imageVector = fileType.icon,
                    contentDescription = fileType.label,
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
            leadingContent = { Icon(fileType.icon, fileType.label) },
            modifier = modifier,
            // Swipe-to-delete is handled at the parent list level
        )
    }
}
