package com.a42r.mdrender.ui.settings

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.cloudpush.CloudPushManager

/**
 * Cloud Push settings section.
 *
 * The QR scanner is NOT rendered here: it is a full-screen Scaffold, and this
 * section is hosted inside a vertically-scrolling Column. A nested full-screen
 * Scaffold there is measured with infinite max height and Compose throws
 * IllegalStateException (Size(w x Int.MAX_VALUE)). Instead this section just
 * signals [onScan]; the host (SettingsScreen) shows the scanner as a sibling of
 * the scrollable content, with bounded constraints.
 */
@Composable
fun CloudPushSettings(
    viewModel: CloudPushViewModel = hiltViewModel(),
    onScan: () -> Unit = {},
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var confirmRotate by remember { mutableStateOf(false) }

    // On opening the screen, confirm the server still knows this device; if it
    // doesn't, clear the pairing so the app stops pretending to be paired.
    LaunchedEffect(Unit) { viewModel.verifyRegistrationOnOpen() }

    val cameraPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted) onScan() }

    Column(
        modifier = Modifier.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (uiState.needsReRegistration) {
            Text(
                "This server no longer recognises the app. Pair again to keep receiving files.",
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        if (uiState.isPaired) {
            Text(
                "Paired with ${uiState.serverUrl}",
                style = MaterialTheme.typography.bodyMedium,
            )
        } else {
            Text("Not paired with a server yet.", style = MaterialTheme.typography.bodyMedium)
        }

        uiState.message?.let { message ->
            Text(message, style = MaterialTheme.typography.bodySmall)
        }

        if (uiState.isPaired) {
            ListItem(
                headlineContent = { Text("Device name") },
                supportingContent = { Text(uiState.deviceName.ifBlank { "—" }) },
            )
            ListItem(
                headlineContent = { Text("Device ID") },
                supportingContent = {
                    Text(
                        uiState.deviceSecret,
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                    )
                },
            )
        }

        // Only meaningful once pairing has succeeded. Before that there is no
        // server to point at, and an editable box would suggest otherwise; the
        // URL is set by scanning the pairing code, not typed.
        if (uiState.isPaired) {
            OutlinedTextField(
                value = uiState.serverUrl,
                onValueChange = {},
                readOnly = true,
                label = { Text("Server URL") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }

        Button(
            onClick = {
                val granted = ContextCompat.checkSelfPermission(
                    context, Manifest.permission.CAMERA
                ) == PackageManager.PERMISSION_GRANTED
                if (granted) onScan()
                else cameraPermission.launch(Manifest.permission.CAMERA)
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Icon(Icons.Filled.QrCodeScanner, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text(if (uiState.isPaired) "Pair with another server" else "Pair with server")
        }

        if (uiState.isPaired) {
            OutlinedButton(
                onClick = viewModel::checkRegistration,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Check registration") }
            uiState.registrationKnown?.let {
                Text(
                    if (it) "Server still knows this device." else "Server does not know this device.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            TextButton(
                onClick = { confirmRotate = true },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Unpair and rotate keys") }
        }

        if (uiState.downloads.isNotEmpty()) {
            Text("Transfers", style = MaterialTheme.typography.titleMedium)
            uiState.downloads.forEach { task -> DownloadRow(task, viewModel::cancelDownload) }
        }
    }

    if (confirmRotate) {
        AlertDialog(
            onDismissRequest = { confirmRotate = false },
            title = { Text("Unpair this server?") },
            text = {
                Text(
                    "The app will forget the server and its key. Existing received files " +
                        "are not deleted."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmRotate = false
                    viewModel.rotateKeys()
                }) { Text("Unpair") }
            },
            dismissButton = {
                TextButton(onClick = { confirmRotate = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun DownloadRow(task: CloudPushManager.DownloadTask, onCancel: (String) -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(task.file.name, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
            if (task.status == CloudPushManager.Status.QUEUED ||
                task.status == CloudPushManager.Status.DOWNLOADING
            ) {
                IconButton(onClick = { onCancel(task.fileId) }) {
                    Icon(Icons.Filled.Close, contentDescription = "Cancel download")
                }
            }
        }
        Text(
            task.status.name.lowercase().replaceFirstChar { it.uppercase() },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (task.status == CloudPushManager.Status.DOWNLOADING) {
            LinearProgressIndicator(
                progress = { task.progress },
                modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
            )
        }
    }
}
