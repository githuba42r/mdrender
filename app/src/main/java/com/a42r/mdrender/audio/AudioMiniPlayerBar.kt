package com.a42r.mdrender.audio

import androidx.compose.animation.*
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.a42r.mdrender.ui.navigation.Routes

/** Thin persistent bar at the bottom of every screen when an audio file is loaded. */
@Composable
fun AudioMiniPlayerBar(
    navController: NavController,
    playerState: AudioPlayerState,
    prefs: AudioPlayerPrefs,
    modifier: Modifier = Modifier
) {
    val info by playerState.info.collectAsState()
    val isPlaying by playerState.isPlaying.collectAsState()
    val isLoading by playerState.isLoading.collectAsState()
    val position by playerState.currentPosition.collectAsState()

    AnimatedVisibility(
        visible = info.fileId != 0L,
        modifier = modifier,
        enter = slideInVertically(initialOffsetY = { it }) + fadeIn(),
        exit = slideOutVertically(targetOffsetY = { it }) + fadeOut()
    ) {
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { navController.navigate(Routes.AudioPlayer.createRoute(info.fileId)) },
            tonalElevation = 3.dp,
            shadowElevation = 6.dp
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                val blocked = prefs.headphonesOnly &&
                    !AudioManagerUtil.isHeadphonesConnected(LocalContext.current)

                if (blocked) {
                    Icon(
                        Icons.Filled.HeadsetOff,
                        contentDescription = "No headphones",
                        tint = MaterialTheme.colorScheme.error
                    )
                } else if (isLoading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(24.dp),
                        strokeWidth = 3.dp
                    )
                } else {
                    IconButton(
                        onClick = {
                            if (isPlaying) playerState.pause() else playerState.resume()
                        },
                        modifier = Modifier.size(36.dp)
                    ) {
                        Icon(
                            if (isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                            contentDescription = if (isPlaying) "Pause" else "Play"
                        )
                    }
                }

                Spacer(Modifier.width(8.dp))

                Text(
                    info.fileName,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )

                Spacer(Modifier.width(8.dp))

                Text(
                    formatDuration(position),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                Spacer(Modifier.width(4.dp))

                IconButton(
                    onClick = { playerState.stopPlayback() },
                    modifier = Modifier.size(28.dp)
                ) {
                    Icon(
                        Icons.Filled.Close,
                        contentDescription = "Close",
                        modifier = Modifier.size(18.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
    }
}

private fun formatDuration(ms: Long): String {
    val totalSec = ms / 1000
    val min = totalSec / 60
    val sec = totalSec % 60
    return "%d:%02d".format(min, sec)
}
