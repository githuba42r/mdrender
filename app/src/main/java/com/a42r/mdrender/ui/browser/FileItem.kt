package com.a42r.mdrender.ui.browser

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
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
    selected: Boolean = false,
    hiddenBadge: Boolean = false,
    encryptedBadge: Boolean = false,
    processing: Boolean = false,
    thumbnail: ByteArray? = null
) {
    val showThumb = thumbnail != null && !selected
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
                Box {
                    if (processing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(24.dp).align(Alignment.Center),
                            strokeWidth = 3.dp
                        )
                    } else if (showThumb) {
                        AsyncImage(
                            model = ImageRequest.Builder(LocalContext.current)
                                .data(thumbnail).crossfade(true).build(),
                            contentDescription = name,
                            modifier = Modifier.fillMaxWidth().aspectRatio(1f)
                                .clip(RoundedCornerShape(6.dp)),
                            contentScale = ContentScale.Crop
                        )
                    } else {
                        Icon(
                            imageVector = if (selected) Icons.Filled.CheckCircle else fileType.icon,
                            contentDescription = if (selected) "Selected" else fileType.label,
                            modifier = Modifier.size(40.dp),
                            tint = MaterialTheme.colorScheme.primary
                        )
                    }
                    if (encryptedBadge) {
                        Icon(
                            Icons.Filled.Lock,
                            contentDescription = "Encrypted",
                            modifier = Modifier.size(16.dp).align(Alignment.BottomStart),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    if (hiddenBadge) {
                        Icon(
                            Icons.Filled.VisibilityOff,
                            contentDescription = "Hidden",
                            modifier = Modifier.size(16.dp).align(Alignment.BottomEnd),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                // Thumbnail tiles show the image only; other files show a name.
                if (!showThumb) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = name,
                        style = MaterialTheme.typography.bodySmall,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    } else {
        ListItem(
            headlineContent = { Text(name, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            leadingContent = {
                if (processing) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(24.dp),
                        strokeWidth = 3.dp
                    )
                } else if (showThumb) {
                    AsyncImage(
                        model = ImageRequest.Builder(LocalContext.current)
                            .data(thumbnail).crossfade(true).build(),
                        contentDescription = fileType.label,
                        modifier = Modifier.size(40.dp).clip(RoundedCornerShape(4.dp)),
                        contentScale = ContentScale.Crop
                    )
                } else {
                    Icon(
                        if (selected) Icons.Filled.CheckCircle else fileType.icon,
                        if (selected) "Selected" else fileType.label,
                        tint = if (selected) MaterialTheme.colorScheme.primary else LocalContentColor.current
                    )
                }
            },
            trailingContent = if (encryptedBadge || hiddenBadge) { {
                Row {
                    if (encryptedBadge) {
                        Icon(Icons.Filled.Lock, "Encrypted", modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(4.dp))
                    }
                    if (hiddenBadge) {
                        Icon(Icons.Filled.VisibilityOff, "Hidden", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            } } else null,
            colors = if (selected) ListItemDefaults.colors(
                containerColor = MaterialTheme.colorScheme.primaryContainer
            ) else ListItemDefaults.colors(),
            modifier = modifier.combinedClickable(onClick = onClick, onLongClick = onLongClick),
            // Swipe-to-delete is handled at the parent list level
        )
    }
}
