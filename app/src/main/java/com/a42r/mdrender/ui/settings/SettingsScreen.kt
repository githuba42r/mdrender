package com.a42r.mdrender.ui.settings

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.security.AuthMethod
import com.a42r.mdrender.ui.auth.PinEntryScreen
import com.a42r.mdrender.ui.auth.PatternLockView

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
            // Auth method
            ListItem(
                headlineContent = { Text("Authentication Method") },
                supportingContent = { Text(uiState.authMethod.name.lowercase().replaceFirstChar { it.uppercase() }) }
            )
            Row(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = uiState.authMethod == AuthMethod.BIOMETRIC,
                    onClick = { viewModel.setAuthMethod(AuthMethod.BIOMETRIC) },
                    label = { Text("Biometric") }
                )
                FilterChip(
                    selected = uiState.authMethod == AuthMethod.PATTERN,
                    onClick = { viewModel.setAuthMethod(AuthMethod.PATTERN) },
                    label = { Text("Pattern") }
                )
                FilterChip(
                    selected = uiState.authMethod == AuthMethod.PIN,
                    onClick = { viewModel.setAuthMethod(AuthMethod.PIN) },
                    label = { Text("PIN") }
                )
            }

            HorizontalDivider()

            // Pattern setup
            ListItem(
                headlineContent = { Text("Pattern") },
                supportingContent = { Text(if (uiState.hasPatternSet) "Configured" else "Not set") },
                modifier = Modifier.clickable { viewModel.showPatternSetup() }
            )

            // PIN setup
            ListItem(
                headlineContent = { Text("PIN") },
                supportingContent = { Text(if (uiState.hasPinSet) "Configured" else "Not set") },
                modifier = Modifier.clickable { viewModel.showPinSetup() }
            )

            HorizontalDivider()

            // Idle timeout
            ListItem(
                headlineContent = { Text("Auto-lock timeout") },
                supportingContent = { Text(timeoutLabel(uiState.idleTimeoutSeconds)) }
            )
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                listOf(30, 60, 120, 300, 600, -1).forEach { seconds ->
                    FilterChip(
                        selected = uiState.idleTimeoutSeconds == seconds,
                        onClick = { viewModel.setIdleTimeout(seconds) },
                        label = { Text(timeoutLabel(seconds)) }
                    )
                }
            }

            HorizontalDivider()

            // LocalSend receiver
            val notificationPermission = rememberLauncherForActivityResult(
                ActivityResultContracts.RequestPermission()
            ) { /* transfer dialog still works in-app without it */ }

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
            }

            HorizontalDivider()

            // App info
            ListItem(
                headlineContent = { Text("Version") },
                supportingContent = { Text(uiState.appVersion) }
            )
        }
    }

    // Pattern setup dialog
    if (uiState.showPatternSetup) {
        AlertDialog(
            onDismissRequest = { viewModel.dismissPatternSetup() },
            title = { Text("Set Pattern") },
            text = {
                PatternLockView(
                    onPatternComplete = { pattern ->
                        viewModel.patternSetupComplete(pattern)
                    }
                )
            },
            confirmButton = {},
            dismissButton = {
                TextButton(onClick = { viewModel.dismissPatternSetup() }) { Text("Cancel") }
            }
        )
    }

    // PIN setup dialog
    if (uiState.showPinSetup) {
        AlertDialog(
            onDismissRequest = { viewModel.dismissPinSetup() },
            title = { Text("Set PIN") },
            text = {
                PinEntryScreen(
                    onSubmit = { pin ->
                        viewModel.pinSetupComplete(pin)
                        true
                    }
                )
            },
            confirmButton = {},
            dismissButton = {
                TextButton(onClick = { viewModel.dismissPinSetup() }) { Text("Cancel") }
            }
        )
    }
}

private fun timeoutLabel(seconds: Int): String = when (seconds) {
    30 -> "30 seconds"
    60 -> "1 minute"
    120 -> "2 minutes"
    300 -> "5 minutes"
    600 -> "10 minutes"
    -1 -> "Never"
    else -> "$seconds seconds"
}
