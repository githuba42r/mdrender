package com.a42r.mdrender.ui.settings

import android.Manifest
import android.os.Build
import android.util.Log
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.gesture.settings.UnhideSettingsContent
import com.a42r.mdrender.gesture.settings.UnhideSettingsViewModel
import com.a42r.mdrender.MDRenderApplication
import com.a42r.mdrender.cloudpush.QrScannerScreen
import com.a42r.mdrender.security.DeviceAuth

private const val TAG = "SettingsScreen"

enum class SettingsSection(val label: String) {
    AUTH("Authentication"),
    FOLDERS("Folders"),
    AUDIO("Audio"),
    LOCALSEND("LocalSend"),
    CLOUDPUSH("Cloud Push"),
    PUSHES("Push History"),
    ADVANCED("Advanced"),
    ABOUT("About")
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    viewModel: SettingsViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var currentSection by remember { mutableStateOf<SettingsSection?>(null) }
    // The QR scanner is a full-screen Scaffold, so it cannot live inside the
    // vertically-scrolling section Column (infinite max height => Compose
    // IllegalStateException). It is hosted here, as a sibling of that Column.
    var scanning by remember { mutableStateOf(false) }
    val cloudPushViewModel: CloudPushViewModel = hiltViewModel()
    val context = LocalContext.current
    val activity = context as? FragmentActivity

    // Hoist unhide viewmodel to the SettingsScreen scope to avoid
    // creation issues inside the section-switching content.
    val unhideViewModel: UnhideSettingsViewModel = hiltViewModel()

    val notificationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* transfer dialog still works in-app without it */ }

    BackHandler(enabled = scanning || currentSection != null) {
        if (scanning) scanning = false else currentSection = null
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(currentSection?.label ?: "Settings") },
                navigationIcon = {
                    IconButton(onClick = {
                        if (currentSection != null) currentSection = null
                        else onBack()
                    }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                }
            )
        }
    ) { padding ->
        if (scanning) {
            // Full-screen takeover, hosted outside the scrollable Column so it
            // gets bounded constraints (a nested full-screen Scaffold inside
            // verticalScroll is measured at infinite height and throws).
            Box(modifier = Modifier.padding(padding)) {
                QrScannerScreen(
                    onResult = {
                        scanning = false
                        cloudPushViewModel.pairWithQr(it)
                    },
                    onCancel = { scanning = false },
                )
            }
        } else if (currentSection == SettingsSection.PUSHES) {
            // PushHistoryScreen renders a LazyColumn; nesting it inside the
            // verticalScroll Column below would measure it with infinite max
            // height and throw IllegalStateException. Host it in a plain
            // container as a sibling of the scrollable Column.
            Box(modifier = Modifier.padding(padding)) {
                PushHistoryScreen(
                    revealHidden = MDRenderApplication.instance.appLock.revealHidden
                )
            }
        } else {
            Column(
                modifier = Modifier
                    .padding(padding)
                    .verticalScroll(rememberScrollState())
            ) {
                when (currentSection) {
                    null -> SettingsMenu(
                        uiState = uiState,
                        viewModel = viewModel,
                        activity = activity,
                        onSelectSection = { currentSection = it }
                    )
                    SettingsSection.AUTH -> AuthSettings(uiState = uiState, viewModel = viewModel, context = context)
                    SettingsSection.FOLDERS -> FolderSettings(uiState = uiState, viewModel = viewModel)
                    SettingsSection.AUDIO -> AudioSettings(uiState = uiState, viewModel = viewModel)
                    SettingsSection.LOCALSEND -> LocalSendSettings(
                        uiState = uiState,
                        viewModel = viewModel,
                        context = context,
                        notificationPermission = { notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS) }
                    )
                    SettingsSection.CLOUDPUSH -> CloudPushSettings(
                        viewModel = cloudPushViewModel,
                        onScan = { scanning = true },
                    )
                    SettingsSection.ADVANCED -> AdvancedSettings(unhideViewModel = unhideViewModel)
                    SettingsSection.ABOUT -> AboutSection(uiState = uiState)
                    SettingsSection.PUSHES -> Unit // handled above; unreachable here
                }
            }
        }
    }
}

// ── Menu ──────────────────────────────────────────────────────────────────

@Composable
private fun SettingsMenu(
    uiState: SettingsUiState,
    viewModel: SettingsViewModel,
    activity: FragmentActivity?,
    onSelectSection: (SettingsSection) -> Unit
) {
    ListItem(
        headlineContent = { Text("Authentication") },
        supportingContent = { Text("Require unlock, face unlock") },
        leadingContent = { Icon(Icons.Filled.Lock, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable { onSelectSection(SettingsSection.AUTH) }
    )
    ListItem(
        headlineContent = { Text("Folders") },
        supportingContent = { Text("INDEX.md, thumbnails, file encryption") },
        leadingContent = { Icon(Icons.Filled.Folder, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable { onSelectSection(SettingsSection.FOLDERS) }
    )
    ListItem(
        headlineContent = { Text("Audio") },
        supportingContent = { Text("Headphones only, notification controls") },
        leadingContent = { Icon(Icons.Filled.Headphones, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable { onSelectSection(SettingsSection.AUDIO) }
    )
    ListItem(
        headlineContent = { Text("LocalSend") },
        supportingContent = { Text("Receiver, device name, transfer PIN") },
        leadingContent = { Icon(Icons.Filled.Sensors, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable { onSelectSection(SettingsSection.LOCALSEND) }
    )
    ListItem(
        headlineContent = { Text("Cloud Push") },
        supportingContent = { Text("Pair a push server, view transfers") },
        leadingContent = { Icon(Icons.Filled.CloudQueue, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable { onSelectSection(SettingsSection.CLOUDPUSH) }
    )
    ListItem(
        headlineContent = { Text("Push History") },
        supportingContent = { Text("Received files from LAN or cloud") },
        leadingContent = { Icon(Icons.Filled.Download, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable { onSelectSection(SettingsSection.PUSHES) }
    )
    ListItem(
        headlineContent = { Text("Advanced") },
        supportingContent = { Text("Hidden folder reveal gestures") },
        leadingContent = { Icon(Icons.Filled.Tune, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable {
            val navigate = { onSelectSection(SettingsSection.ADVANCED) }
            if (uiState.requireSystemAuth && activity != null) {
                try {
                    DeviceAuth.authenticate(
                        activity = activity,
                        onSuccess = navigate,
                        onFailure = { /* stay on menu */ }
                    )
                } catch (e: Exception) {
                    Log.e(TAG, "Device auth failed for advanced section", e)
                }
            } else {
                navigate()
            }
        }
    )
    ListItem(
        headlineContent = { Text("About") },
        supportingContent = { Text("App version") },
        leadingContent = { Icon(Icons.Filled.Info, null) },
        trailingContent = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null) },
        modifier = Modifier.clickable { onSelectSection(SettingsSection.ABOUT) }
    )
}

// ── Authentication ────────────────────────────────────────────────────────

@Composable
private fun AuthSettings(
    uiState: SettingsUiState,
    viewModel: SettingsViewModel,
    context: android.content.Context
) {
    val activity = context as? FragmentActivity
    val noCredential = remember { DeviceAuth.noCredentialConfigured(context, uiState.allowWeakBiometric) }
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
                        viewModel.setRequireSystemAuth(true)
                    } else if (activity != null) {
                        try {
                            DeviceAuth.authenticate(
                                activity = activity,
                                allowWeak = uiState.allowWeakBiometric,
                                onSuccess = { viewModel.setRequireSystemAuth(false) },
                                onFailure = { /* leave enabled */ }
                            )
                        } catch (e: Exception) {
                            Log.e(TAG, "Device auth failed for disable", e)
                        }
                    }
                }
            )
        }
    )
    if (uiState.requireSystemAuth) {
        ListItem(
            headlineContent = { Text("Allow face unlock") },
            supportingContent = {
                Text("Also accept face recognition (BIOMETRIC_WEAK) " +
                     "when unlocking. Turning this off restricts unlock to " +
                     "strong biometrics (e.g. fingerprint) and device PIN/pattern.")
            },
            trailingContent = {
                Switch(
                    checked = uiState.allowWeakBiometric,
                    onCheckedChange = { viewModel.setAllowWeakBiometric(it) }
                )
            }
        )
    }
}

// ── Folders ───────────────────────────────────────────────────────────────

@Composable
private fun FolderSettings(
    uiState: SettingsUiState,
    viewModel: SettingsViewModel
) {
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
    ListItem(
        headlineContent = { Text("Show image thumbnails") },
        supportingContent = {
            Text("Generate and display thumbnail previews for image " +
                 "files in the folder browser.")
        },
        trailingContent = {
            Switch(
                checked = uiState.showThumbnails,
                onCheckedChange = { viewModel.setShowThumbnails(it) }
            )
        }
    )
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
}

// ── Audio ─────────────────────────────────────────────────────────────────

@Composable
private fun AudioSettings(
    uiState: SettingsUiState,
    viewModel: SettingsViewModel
) {
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
}

// ── LocalSend ─────────────────────────────────────────────────────────────

@Composable
private fun LocalSendSettings(
    uiState: SettingsUiState,
    viewModel: SettingsViewModel,
    context: android.content.Context,
    notificationPermission: () -> Unit
) {
    val appLock = com.a42r.mdrender.MDRenderApplication.instance.appLock
    ListItem(
        headlineContent = { Text("Enable receiver") },
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
                        appLock.suspendNextLock()
                        notificationPermission()
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
}

// ── Advanced ──────────────────────────────────────────────────────────────

@Composable
private fun AdvancedSettings(unhideViewModel: UnhideSettingsViewModel) {
    val unhideState by unhideViewModel.uiState.collectAsStateWithLifecycle()
    UnhideSettingsContent(
        uiState = unhideState,
        viewModel = unhideViewModel
    )
}

// ── About ─────────────────────────────────────────────────────────────────

@Composable
private fun AboutSection(uiState: SettingsUiState) {
    ListItem(
        headlineContent = { Text("App version") },
        supportingContent = { Text(uiState.appVersion) }
    )
}
