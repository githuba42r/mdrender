package com.a42r.mdrender.audio

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AudioPlayerScreen(
    onBack: () -> Unit,
    viewModel: AudioPlayerViewModel = hiltViewModel()
) {
    val state = viewModel.playerState
    val info by state.info.collectAsState()
    val isPlaying by state.isPlaying.collectAsState()
    val position by state.currentPosition.collectAsState()
    val duration by state.duration.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Now Playing") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Collapse to mini player")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Icon(
                Icons.Filled.MusicNote,
                contentDescription = null,
                modifier = Modifier.size(96.dp),
                tint = MaterialTheme.colorScheme.primary
            )

            Spacer(Modifier.height(24.dp))

            Text(
                info.fileName,
                style = MaterialTheme.typography.headlineSmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )

            Spacer(Modifier.height(4.dp))

            Text(
                formatDuration(position) + " / " + formatDuration(duration),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(Modifier.height(16.dp))

            // Progress slider
            Slider(
                value = if (duration > 0) position.toFloat().coerceAtMost(duration.toFloat()) else 0f,
                onValueChange = { state.seekTo(it.toLong()) },
                valueRange = 0f..if (duration > 0) duration.toFloat() else 1f,
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(16.dp))

            // Controls
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceEvenly,
                modifier = Modifier.fillMaxWidth()
            ) {
                // Skip back 15s
                IconButton(onClick = {
                    val newPos = (position - 15_000).coerceAtLeast(0)
                    state.seekTo(newPos)
                }) {
                    Icon(Icons.Filled.Replay10, "Skip back 10s", modifier = Modifier.size(48.dp))
                }

                IconButton(
                    onClick = {
                        if (isPlaying) state.pause() else state.resume()
                    },
                    modifier = Modifier.size(72.dp)
                ) {
                    Icon(
                        if (isPlaying) Icons.Filled.PauseCircle else Icons.Filled.PlayCircle,
                        contentDescription = if (isPlaying) "Pause" else "Play",
                        modifier = Modifier.size(64.dp),
                        tint = MaterialTheme.colorScheme.primary
                    )
                }

                IconButton(onClick = {
                    state.seekTo((position + 10_000).coerceAtMost(duration))
                }) {
                    Icon(Icons.Filled.Forward10, "Skip forward 10s", modifier = Modifier.size(48.dp))
                }
            }

            Spacer(Modifier.height(32.dp))

            // Collapse to mini player
            OutlinedButton(onClick = onBack) {
                Icon(Icons.Filled.Minimize, "Mini player")
                Spacer(Modifier.width(8.dp))
                Text("Mini Player")
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
