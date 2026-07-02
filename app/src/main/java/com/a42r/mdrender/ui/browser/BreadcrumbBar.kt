package com.a42r.mdrender.ui.browser

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Home
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.a42r.mdrender.data.entity.FolderEntity

@Composable
fun BreadcrumbBar(
    path: List<FolderEntity>,
    onNavigate: (Long?) -> Unit,
    modifier: Modifier = Modifier
) {
    Row(modifier = modifier.horizontalScroll(rememberScrollState())) {
        // Home
        TextButton(onClick = { onNavigate(null) }) {
            Icon(Icons.Filled.Home, contentDescription = "Home", modifier = Modifier.size(18.dp))
        }
        for (folder in path) {
            Text(" / ", style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = { onNavigate(folder.id) }) {
                Text(folder.name, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
