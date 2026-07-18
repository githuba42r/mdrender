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

/** Minimal controls shown at the bottom of the screen when a hidden audio file is
 *  playing (mini player is hidden to avoid revealing the file). Pause + Stop only. */
@Composable
fun HiddenAudioControls(
    playerState: AudioPlayerState,
    visible: Boolean = false,
    modifier: Modifier = Modifier
) {
    val isPlaying by playerState.isPlaying.collectAsState()
    val isLoading by playerState.isLoading.collectAsState()

    AnimatedVisibility(
        visible = visible,
        modifier = modifier,
        enter = slideInVertically(initialOffsetY = { it }) + fadeIn(),
        exit = slideOutVertically(targetOffsetY = { it }) + fadeOut()
    ) {
        Surface(
            modifier = Modifier.fillMaxWidth(),
            tonalElevation = 3.dp,
            shadowElevation = 6.dp
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Center
            ) {
                if (isLoading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(24.dp),
                        strokeWidth = 3.dp
                    )
                } else {
                    IconButton(onClick = {
                        if (isPlaying) playerState.pause() else playerState.resume()
                    }) {
                        Icon(
                            if (isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                            contentDescription = if (isPlaying) "Pause" else "Play"
                        )
                    }

                    Spacer(Modifier.width(32.dp))

                    IconButton(onClick = { playerState.stopPlayback() }) {
                        Icon(
                            Icons.Filled.Stop,
                            contentDescription = "Stop",
                            tint = MaterialTheme.colorScheme.error
                        )
                    }
                }
            }
        }
    }
}

/** Thin persistent bar at the bottom of every screen when an audio file is loaded.
 *  Visibility is controlled by the caller. */
@Composable
fun AudioMiniPlayerBar(
    navController: NavController,
    playerState: AudioPlayerState,
    prefs: AudioPlayerPrefs,
    visible: Boolean = false,
    modifier: Modifier = Modifier
) {
    val info by playerState.info.collectAsState()
    val isPlaying by playerState.isPlaying.collectAsState()
    val isLoading by playerState.isLoading.collectAsState()
    val position by playerState.currentPosition.collectAsState()

    AnimatedVisibility(
        visible = visible,
        modifier = modifier,
        enter = slideInVertically(initialOffsetY = { it }) + fadeIn(),
        exit = slideOutVertically(targetOffsetY = { it }) + fadeOut()
    ) {
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { navController.navigate(Routes.AudioPlayer.createRoute(playerState.info.value.fileId)) },
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
