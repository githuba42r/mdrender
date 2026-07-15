package com.a42r.mdrender.ui.settings

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.security.DeviceAuth

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    viewModel: SettingsViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(modifier = Modifier.padding(padding).verticalScroll(rememberScrollState())) {

            // Security
            val context = LocalContext.current
            val activity = context as? FragmentActivity
            val noCredential = remember { DeviceAuth.noCredentialConfigured(context) }
            ListItem(
                headlineContent = { Text("Require unlock authentication") },
                supportingContent = {
                    Text(
                        if (noCredential)
                            "No device PIN, pattern, or biometric is set up — MDRender can't " +
                            "require unlock authentication regardless of this setting."
                        else
                            "Ask for your device biometric or PIN each time MDRender opens or " +
                            "resumes. Hidden folders are unaffected: they're always re-hidden and " +
                            "require the reveal gesture again after every backgrounding, whether " +
                            "this is on or off."
                    )
                },
                trailingContent = {
                    Switch(
                        checked = uiState.requireSystemAuth,
                        enabled = !noCredential,
                        onCheckedChange = { enabling ->
                            if (enabling) {
                                // Tightening security never needs proof of identity.
                                viewModel.setRequireSystemAuth(true)
                            } else if (activity != null) {
                                // Loosening security requires proving identity first.
                                try {
                                    DeviceAuth.authenticate(
                                        activity = activity,
                                        onSuccess = { viewModel.setRequireSystemAuth(false) },
                                        onFailure = { /* leave enabled, no state change */ }
                                    )
                                } catch (_: Exception) {
                                    // Mirrors MainActivity's defensive handling of BiometricPrompt
                                    // throwing on an unstable lifecycle state.
                                }
                            }
                        }
                    )
                }
            )

            HorizontalDivider()

            // LocalSend receiver
            val notificationPermission = rememberLauncherForActivityResult(
                ActivityResultContracts.RequestPermission()
            ) { /* transfer dialog still works in-app without it */ }

            val appLock = com.a42r.mdrender.MDRenderApplication.instance.appLock
            ListItem(
                headlineContent = { Text("LocalSend receiver") },
                supportingContent = {
                    Text(
                        if (uiState.localSendEnabled)
                            "Receiving as \"${uiState.localSendAlias}\""
                        else "Other devices can send files into the encrypted store"
                    )
                },
                trailingContent = {
                    Switch(
                        checked = uiState.localSendEnabled,
                        onCheckedChange = { enabled ->
                            if (enabled && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                                // Suspend next lock so the permission dialog
                                // doesn't trigger finishAndRemoveTask().
                                appLock.suspendNextLock()
                                notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                            }
                            viewModel.setLocalSendEnabled(enabled)
                        }
                    )
                }
            )
            if (uiState.localSendEnabled) {
                ListItem(
                    headlineContent = { Text("Device name") },
                    supportingContent = { Text(uiState.localSendAlias) },
                    trailingContent = {
                        IconButton(onClick = { viewModel.regenerateLocalSendAlias() }) {
                            Icon(Icons.Filled.Refresh, "New name")
                        }
                    }
                )
                var pinText by remember(uiState.localSendPin) { mutableStateOf(uiState.localSendPin) }
                OutlinedTextField(
                    value = pinText,
                    onValueChange = { pinText = it },
                    label = { Text("Transfer PIN (optional)") },
                    supportingText = { Text("Senders must enter this PIN. Leave empty to allow anyone on the network to request a transfer.") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                )
                TextButton(
                    onClick = { viewModel.setLocalSendPin(pinText) },
                    enabled = pinText.trim() != uiState.localSendPin,
                    modifier = Modifier.padding(horizontal = 16.dp)
                ) { Text("Save PIN") }

                // Auto-accept — only meaningful with a PIN set.
                val hasPin = uiState.localSendPin.isNotEmpty()
                ListItem(
                    headlineContent = { Text("Auto-accept with PIN") },
                    supportingContent = {
                        Text(
                            if (hasPin) "Accept transfers automatically when the correct PIN is supplied"
                            else "Set a PIN first to enable auto-accept"
                        )
                    },
                    trailingContent = {
                        Switch(
                            checked = uiState.localSendAutoAccept,
                            enabled = hasPin,
                            onCheckedChange = { viewModel.setLocalSendAutoAccept(it) }
                        )
                    }
                )
            }

            HorizontalDivider()

            // Audio section
            ListItem(
                headlineContent = { Text("Headphones only") },
                supportingContent = {
                    Text("Require headphones or Bluetooth audio to play files. " +
                         "Prevents private audio playing over the device speaker.")
                },
                trailingContent = {
                    Switch(
                        checked = uiState.headphonesOnly,
                        onCheckedChange = { viewModel.setHeadphonesOnly(it) }
                    )
                }
            )
            ListItem(
                headlineContent = { Text("Expanded media notification") },
                supportingContent = {
                    Text("Show full notification with progress bar and " +
                         "extended playback controls in the notification shade.")
                },
                trailingContent = {
                    Switch(
                        checked = uiState.fullNotification,
                        onCheckedChange = { viewModel.setFullNotification(it) }
                    )
                }
            )

            HorizontalDivider()

            // Storage / encryption
            ListItem(
                headlineContent = { Text("Encrypt large files") },
                supportingContent = {
                    Text("Large files are stored unencrypted for instant playback. " +
                         "Only affects new uploads.")
                },
                trailingContent = {
                    Switch(
                        checked = uiState.encryptLargeFiles,
                        onCheckedChange = { viewModel.setEncryptLargeFiles(it) }
                    )
                }
            )

            HorizontalDivider()

            // INDEX.md table of contents
            ListItem(
                headlineContent = { Text("INDEX.md table of contents") },
                supportingContent = {
                    Text("Show INDEX.md files as a tappable chapter list " +
                         "instead of raw markdown")
                },
                trailingContent = {
                    Switch(
                        checked = uiState.indexTocEnabled,
                        onCheckedChange = { viewModel.setIndexTocEnabled(it) }
                    )
                }
            )

            HorizontalDivider()

            // App info
            ListItem(
                headlineContent = { Text("Version") },
                supportingContent = { Text(uiState.appVersion) }
            )
        }
    }
}
