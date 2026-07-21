package com.a42r.mdrender.gesture.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import com.a42r.mdrender.gesture.GestureAction
import com.a42r.mdrender.gesture.MultiTouchAction
import kotlinx.coroutines.delay

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UnhideSettingsScreen(
    onBack: () -> Unit,
    viewModel: UnhideSettingsViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Unhide Gestures") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .verticalScroll(rememberScrollState())
        ) {
            UnhideSettingsContent(
                uiState = uiState,
                viewModel = viewModel
            )
        }
    }
}

@Composable
fun UnhideSettingsContent(
    uiState: UnhideSettingsUiState,
    viewModel: UnhideSettingsViewModel,
    modifier: Modifier = Modifier
) {
    Column(modifier = modifier) {
        // --- Info header ---
        Text(
            "Customise how hidden files and folders are revealed. " +
            "These settings are not documented in the app.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
        )

        HorizontalDivider()

        // --- SECTION 1: 12-Tap Toggle ---
        ListItem(
            headlineContent = { Text("12-tap title gesture") },
            supportingContent = {
                Text(
                    "Warning: disabling this may leave you unable to reveal " +
                    "hidden folders if no other method works reliably"
                )
            },
            trailingContent = {
                Switch(
                    checked = uiState.twelveTapEnabled,
                    onCheckedChange = { viewModel.setTwelveTapEnabled(it) }
                )
            }
        )

        if (uiState.showDisableWarning) {
            AlertDialog(
                onDismissRequest = { viewModel.cancelDisableWarning() },
                title = { Text("Disable 12-tap?") },
                text = {
                    Text(
                        "If no other method is working reliably, you won't " +
                        "be able to reveal hidden folders. Make sure you have " +
                        "tested at least one alternative gesture before disabling."
                    )
                },
                confirmButton = {
                    TextButton(onClick = { viewModel.confirmDisableTwelveTap() }) {
                        Text("Disable", color = MaterialTheme.colorScheme.error)
                    }
                },
                dismissButton = {
                    TextButton(onClick = { viewModel.cancelDisableWarning() }) { Text("Cancel") }
                }
            )
        }

        HorizontalDivider()

        // --- SECTION 2: Tap Sequence ---
        Text(
            "Tap Sequence",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
        )

        ListItem(
            headlineContent = { Text("Enabled") },
            trailingContent = {
                Switch(
                    checked = uiState.tapSequenceEnabled,
                    onCheckedChange = { viewModel.setTapSequenceEnabled(it) }
                )
            }
        )

        if (uiState.tapSequenceEnabled) {
            // Explanation
            Text(
                "Tap the title bar in this sequence to unhide. Each step is " +
                "either a quick tap (TAP) or a long press (HOLD). " +
                "Tap a chip below to toggle between TAP and HOLD.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
            )
            // Pattern editor
            Text(
                "Pattern:",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(start = 16.dp, top = 4.dp)
            )
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                uiState.tapPattern.forEachIndexed { index, action ->
                    val tap = action as? GestureAction.Tap ?: return@forEachIndexed
                    val label = if (tap.isLongPress) "HOLD" else "TAP"
                    val isSelected = tap.isLongPress
                    FilterChip(
                        selected = isSelected,
                        onClick = { viewModel.toggleTapStepLongPress(index) },
                        label = { Text(label, fontSize = 11.sp) },
                        modifier = Modifier.height(32.dp)
                    )
                }
            }
            Text(
                "Tap a chip to switch between quick tap (TAP) and long press (HOLD)",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 16.dp, top = 0.dp)
            )

            Row(
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                IconButton(onClick = { viewModel.addTapStep() }) {
                    Icon(Icons.Filled.Add, "Add step")
                }
                IconButton(
                    onClick = { viewModel.removeTapStep() },
                    enabled = uiState.tapPattern.size > 1
                ) {
                    Icon(Icons.Filled.Remove, "Remove step")
                }
            }

            // Test button
            TapTestButton(viewModel)
        }

        HorizontalDivider()

        // --- SECTION 3: Multi-Touch ---
        Text(
            "Multi-Touch",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
        )

        ListItem(
            headlineContent = { Text("Enabled") },
            trailingContent = {
                Switch(
                    checked = uiState.multiTouchEnabled,
                    onCheckedChange = { viewModel.setMultiTouchEnabled(it) }
                )
            }
        )

        if (uiState.multiTouchEnabled) {
            // Explanation
            Text(
                "Place your fingers on the content area (below the title bar) in " +
                "the zones shown in the grid below. Build a sequence of actions — " +
                "finger placement, rotation, sliding, or lift — then the app will " +
                "watch for that exact sequence to unhide.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
            )
            // Sequence editor
            Text(
                "Sequence steps:",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(start = 16.dp, top = 4.dp)
            )

            uiState.multiTouchSequence.forEachIndexed { index, action ->
                ListItem(
                    headlineContent = { Text(describeAction(action)) },
                    trailingContent = {
                        IconButton(onClick = { viewModel.removeMultiTouchAction(index) }) {
                            Icon(Icons.Filled.Delete, "Remove", tint = MaterialTheme.colorScheme.error)
                        }
                    }
                )
            }

            // Add action buttons
            MultiTouchActionAdder(viewModel)

            // Debug overlay toggle
            ListItem(
                headlineContent = { Text("Debug overlay") },
                supportingContent = { Text("Show zone grid and finger tracking on screen") },
                trailingContent = {
                    Switch(
                        checked = uiState.multiTouchDebugOverlay,
                        onCheckedChange = { viewModel.toggleDebugOverlay() }
                    )
                }
            )

            // Zone grid preview
            if (uiState.multiTouchSequence.isNotEmpty()) {
                ZoneGridPreview()
            }

            // Test button
            MultiTouchTestButton(viewModel)
        }

        Spacer(Modifier.height(32.dp))
    }
}

// --- Tap test ------------------------------------------------------------

@Composable
private fun TapTestButton(viewModel: UnhideSettingsViewModel) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    if (uiState.tapTestStepIndex < 0 && !uiState.tapTestStatus.startsWith("✓") && uiState.tapTestStatus == "Idle") {
        // Not testing — show the start button
        Button(
            onClick = { viewModel.startTapTest() },
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
        ) {
            Icon(Icons.Filled.PlayArrow, "Test", modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(4.dp))
            Text("Test tap sequence")
        }
    } else {
        // Testing or showing result — show target + progress
        TapTestUi(viewModel)
    }
}

@Composable
private fun TapTestUi(viewModel: UnhideSettingsViewModel) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    // Step progress dots
    val total = uiState.tapTestTotalSteps
    if (total > 0) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically
        ) {
            for (i in 0 until total) {
                val color = when {
                    uiState.tapTestStatus.startsWith("✓") -> MaterialTheme.colorScheme.primary
                    uiState.tapTestStatus.startsWith("✗") -> MaterialTheme.colorScheme.error
                    i < uiState.tapTestStepIndex -> MaterialTheme.colorScheme.primary
                    i == uiState.tapTestStepIndex -> MaterialTheme.colorScheme.tertiary
                    else -> MaterialTheme.colorScheme.surfaceVariant
                }
                Box(
                    modifier = Modifier
                        .size(if (i == uiState.tapTestStepIndex && !uiState.tapTestStatus.startsWith("✗") && !uiState.tapTestStatus.startsWith("✓")) 16.dp else 12.dp)
                        .padding(2.dp)
                        .background(color, CircleShape)
                )
                if (i < total - 1) {
                    Box(
                        modifier = Modifier
                            .width(12.dp)
                            .height(2.dp)
                            .background(
                                if (i < uiState.tapTestStepIndex) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.surfaceVariant
                            )
                    )
                }
            }
        }
    }

    // Status text
    Text(
        uiState.tapTestStatus,
        style = MaterialTheme.typography.bodySmall,
        color = when {
            uiState.tapTestStatus.startsWith("✓") -> MaterialTheme.colorScheme.primary
            uiState.tapTestStatus.startsWith("✗") -> MaterialTheme.colorScheme.error
            else -> MaterialTheme.colorScheme.onSurface
        },
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp)
    )

    if (uiState.tapTestStatus.startsWith("✓") || uiState.tapTestStatus.startsWith("✗")) {
        // Show result with retry + done buttons
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            OutlinedButton(onClick = { viewModel.startTapTest() }) {
                Icon(Icons.Filled.Refresh, "Retry", modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(4.dp))
                Text("Retry")
            }
            Button(
                onClick = { viewModel.stopTapTest() },
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.errorContainer
                )
            ) { Text("Done") }
        }
    } else {
        // Show tappable test target + stop button
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp)
                .height(120.dp)
                .background(
                    MaterialTheme.colorScheme.surfaceVariant,
                    RoundedCornerShape(12.dp)
                )
                .pointerInput(Unit) {
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        val up = waitForUpOrCancellation()
                        if (up != null) {
                            val duration = up.uptimeMillis - down.uptimeMillis
                            viewModel.feedTestTap(duration)
                        }
                    }
                },
            contentAlignment = Alignment.Center
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(
                    Icons.Filled.TouchApp,
                    "Tap here",
                    modifier = Modifier.size(32.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "Tap or hold here",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
        Button(
            onClick = { viewModel.stopTapTest() },
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.errorContainer
            ),
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
        ) { Text("Stop test") }
    }
}

// --- Multi-touch helpers -------------------------------------------------

@Composable
private fun MultiTouchActionAdder(viewModel: UnhideSettingsViewModel) {
    var expanded by remember { mutableStateOf(false) }
    var showZonePicker by remember { mutableStateOf(false) }
    var showLiftPicker by remember { mutableStateOf(false) }
    var showSlideFromPicker by remember { mutableStateOf(false) }
    var slideFromZone by remember { mutableStateOf(-1) }

    // Zone picker dialog for FingerDown
    if (showZonePicker) {
        ZonePickerDialog(
            title = "Select zones for Finger Down",
            multiSelect = true,
            onConfirm = { zones ->
                if (zones.isNotEmpty()) {
                    viewModel.addMultiTouchAction(MultiTouchAction.FingerDown(zones))
                }
                showZonePicker = false
            },
            onDismiss = { showZonePicker = false }
        )
    }

    // Zone picker dialog for LiftFinger
    if (showLiftPicker) {
        ZonePickerDialog(
            title = "Select zone to lift from",
            multiSelect = false,
            onConfirm = { zones ->
                if (zones.isNotEmpty()) {
                    viewModel.addMultiTouchAction(MultiTouchAction.LiftFinger(zones.first()))
                }
                showLiftPicker = false
            },
            onDismiss = { showLiftPicker = false }
        )
    }

    // Zone picker for Slide — from zone
    if (showSlideFromPicker) {
        ZonePickerDialog(
            title = "Slide from zone…",
            multiSelect = false,
            onConfirm = { zones ->
                slideFromZone = zones.first()
                showSlideFromPicker = false
            },
            onDismiss = { showSlideFromPicker = false }
        )
    }

    // Slide — to zone picker (shown after from-zone is selected)
    val showSlideToPicker = slideFromZone >= 0
    if (showSlideToPicker) {
        val fromZone = slideFromZone
        ZonePickerDialog(
            title = "…to zone",
            multiSelect = false,
            onConfirm = { zones ->
                if (zones.isNotEmpty()) {
                    viewModel.addMultiTouchAction(MultiTouchAction.Slide(fromZone, zones.first()))
                }
                slideFromZone = -1
            },
            onDismiss = { slideFromZone = -1 }
        )
    }

    Box(modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
        OutlinedButton(onClick = { expanded = true }) {
            Icon(Icons.Filled.Add, "Add action")
            Spacer(Modifier.width(4.dp))
            Text("Add action")
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text("Finger Down") },
                onClick = {
                    showZonePicker = true
                    expanded = false
                }
            )
            DropdownMenuItem(
                text = { Text("Rotate CW") },
                onClick = {
                    viewModel.addMultiTouchAction(MultiTouchAction.RotateCW(1))
                    expanded = false
                }
            )
            DropdownMenuItem(
                text = { Text("Rotate CCW") },
                onClick = {
                    viewModel.addMultiTouchAction(MultiTouchAction.RotateCCW(1))
                    expanded = false
                }
            )
            DropdownMenuItem(
                text = { Text("Lift Finger") },
                onClick = {
                    showLiftPicker = true
                    expanded = false
                }
            )
            DropdownMenuItem(
                text = { Text("Slide") },
                onClick = {
                    showSlideFromPicker = true
                    expanded = false
                }
            )
        }
    }
}

@Composable
private fun ZonePickerDialog(
    title: String,
    multiSelect: Boolean,
    onConfirm: (List<Int>) -> Unit,
    onDismiss: () -> Unit
) {
    val config = LocalConfiguration.current
    val cols = 3
    val rows = 4

    var selected by remember { mutableStateOf<Set<Int>>(emptySet()) }
    val toggleZone: (Int) -> Unit = { zone ->
        selected = if (multiSelect) {
            if (zone in selected) selected - zone else selected + zone
        } else {
            setOf(zone)
        }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.85f))
        ) {
            // Title bar at top
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.Black.copy(alpha = 0.6f))
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(title, color = Color.White, fontWeight = FontWeight.Bold)
            }

            // Zone grid fills remaining space
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(0.dp)
            ) {
                for (row in 0 until rows) {
                    Row(
                        modifier = Modifier.fillMaxWidth().weight(1f),
                        horizontalArrangement = Arrangement.spacedBy(0.dp)
                    ) {
                        for (col in 0 until cols) {
                            val zoneId = row * cols + col + 1
                            val isSel = zoneId in selected
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .fillMaxHeight()
                                    .background(
                                        if (isSel) MaterialTheme.colorScheme.primary.copy(alpha = 0.6f)
                                        else Color.Transparent
                                    )
                                    .border(
                                        width = 0.5.dp,
                                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.3f)
                                    )
                                    .clickable { toggleZone(zoneId) },
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    "$zoneId",
                                    fontSize = if (isSel) 28.sp else 20.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = if (isSel) MaterialTheme.colorScheme.onPrimary
                                    else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f)
                                )
                            }
                        }
                    }
                }
            }

            // Bottom action bar
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.Black.copy(alpha = 0.7f))
                    .padding(16.dp)
                    .navigationBarsPadding(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                if (selected.isNotEmpty()) {
                    Text(
                        if (multiSelect) "${selected.size} zone(s) selected"
                        else "Zone ${selected.first()} selected",
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                        modifier = Modifier.padding(bottom = 8.dp)
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterHorizontally)
                ) {
                    OutlinedButton(
                        onClick = onDismiss,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White)
                    ) { Text("Cancel") }
                    Button(
                        onClick = { onConfirm(selected.toList()) },
                        enabled = selected.isNotEmpty()
                    ) {
                        Text(if (multiSelect) "Add (${selected.size})" else "Select")
                    }
                }
            }
        }
    }
}

// --- Multi-touch test (full-screen overlay) -----------------------------------

@Composable
private fun MultiTouchTestOverlay(
    viewModel: UnhideSettingsViewModel,
    onStop: () -> Unit
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val cols = 3
    val rows = 4

    Dialog(
        onDismissRequest = onStop,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.88f))
                .pointerInput(Unit) {
                    val areaWidth = size.width
                    val areaHeight = size.height
                    awaitPointerEventScope {
                        while (true) {
                            val event = awaitPointerEvent(PointerEventPass.Initial)
                            for (change in event.changes) {
                                val pointerId = change.id.value.toLong()
                                val x = change.position.x
                                val y = change.position.y
                                if (change.pressed && !change.previousPressed) {
                                    viewModel.feedTestPointerEvent(pointerId, 0, x, y, areaWidth, areaHeight)
                                } else if (!change.pressed && change.previousPressed) {
                                    viewModel.feedTestPointerEvent(pointerId, 1, x, y, areaWidth, areaHeight)
                                } else if (change.pressed && change.position != change.previousPosition) {
                                    viewModel.feedTestPointerEvent(pointerId, 2, x, y, areaWidth, areaHeight)
                                }
                            }
                        }
                    }
                }
        ) {
            // Step progress at top
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.Black.copy(alpha = 0.6f))
                    .padding(horizontal = 16.dp, vertical = 10.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically
            ) {
                val total = uiState.multiTouchTestTotalSteps
                if (total > 0) {
                    Text(
                        "Steps: ",
                        color = Color.White.copy(alpha = 0.6f),
                        fontSize = 12.sp
                    )
                    for (i in 0 until total) {
                        val color = when {
                            uiState.multiTouchTestStatus.startsWith("✓") -> MaterialTheme.colorScheme.primary
                            i < uiState.multiTouchTestStepIndex -> MaterialTheme.colorScheme.primary
                            i == uiState.multiTouchTestStepIndex -> MaterialTheme.colorScheme.tertiary
                            else -> Color.Gray.copy(alpha = 0.4f)
                        }
                        Box(
                            modifier = Modifier
                                .size(if (i == uiState.multiTouchTestStepIndex && !uiState.multiTouchTestStatus.startsWith("✓")) 14.dp else 10.dp)
                                .padding(2.dp)
                                .background(color, CircleShape)
                        )
                        if (i < total - 1) {
                            Box(
                                modifier = Modifier
                                    .width(10.dp)
                                    .height(2.dp)
                                    .background(
                                        if (i < uiState.multiTouchTestStepIndex) MaterialTheme.colorScheme.primary
                                        else Color.Gray.copy(alpha = 0.4f)
                                    )
                            )
                        }
                    }
                }
            }

            // Faint zone grid overlay — fills remaining space
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
            ) {
                for (row in 0 until rows) {
                    Row(
                        modifier = Modifier.fillMaxWidth().weight(1f),
                        horizontalArrangement = Arrangement.spacedBy(0.dp)
                    ) {
                        for (col in 0 until cols) {
                            val zoneId = row * cols + col + 1
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .fillMaxHeight()
                                    .border(
                                        width = 0.5.dp,
                                        color = Color.White.copy(alpha = 0.15f)
                                    ),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    "$zoneId",
                                    fontSize = 16.sp,
                                    color = Color.White.copy(alpha = 0.25f)
                                )
                            }
                        }
                    }
                }
            }

            // Status + stop button at bottom
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.Black.copy(alpha = 0.7f))
                    .padding(16.dp)
                    .navigationBarsPadding(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    uiState.multiTouchTestStatus,
                    color = if (uiState.multiTouchTestStatus.startsWith("✓")) MaterialTheme.colorScheme.primary
                    else Color.White.copy(alpha = 0.7f),
                    fontSize = 14.sp,
                    modifier = Modifier.padding(bottom = 8.dp)
                )
                if (uiState.multiTouchTestStatus.startsWith("✓")) {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        OutlinedButton(
                            onClick = { viewModel.startMultiTouchTest() },
                            colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White)
                        ) { Text("Retry") }
                        Button(onClick = onStop) { Text("Done") }
                    }
                } else {
                    Button(
                        onClick = onStop,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer
                        )
                    ) { Text("Stop test") }
                }
            }
        }
    }
}

@Composable
private fun ZoneGridPreview() {
    val config = LocalConfiguration.current
    val cols = 3
    val rows = 4
    val cellWidth = (config.screenWidthDp.dp / cols) * 0.6f
    val cellHeight = 20.dp

    Column(
        modifier = Modifier
            .padding(16.dp)
            .clip(RoundedCornerShape(8.dp))
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(8.dp))
            .padding(4.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp)
    ) {
        for (row in 0 until rows) {
            Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                for (col in 0 until cols) {
                    val zoneId = row * cols + col + 1
                    Box(
                        modifier = Modifier
                            .size(cellWidth, cellHeight)
                            .background(
                                MaterialTheme.colorScheme.surfaceVariant,
                                RoundedCornerShape(2.dp)
                            ),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "$zoneId",
                            fontSize = 9.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            textAlign = TextAlign.Center
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun MultiTouchTestButton(viewModel: UnhideSettingsViewModel) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var showOverlay by remember { mutableStateOf(false) }

    if (showOverlay) {
        MultiTouchTestOverlay(
            viewModel = viewModel,
            onStop = {
                viewModel.stopMultiTouchTest()
                showOverlay = false
            }
        )
    }

    Button(
        onClick = { viewModel.startMultiTouchTest(); showOverlay = true },
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
    ) {
        Icon(Icons.Filled.PlayArrow, "Test", modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(4.dp))
        Text("Test multi-touch")
    }
}

// --- Utilities -----------------------------------------------------------

private fun describeAction(action: MultiTouchAction): String = when (action) {
    is MultiTouchAction.FingerDown -> "Finger Down in zones ${action.zoneIds.joinToString(",")}"
    is MultiTouchAction.RotateCW -> "Rotate ${action.steps} step(s) clockwise"
    is MultiTouchAction.RotateCCW -> "Rotate ${action.steps} step(s) counter-clockwise"
    is MultiTouchAction.LiftFinger -> "Lift finger from zone ${action.zoneId}"
    is MultiTouchAction.Slide -> "Slide from zone ${action.fromZoneId} to ${action.toZoneId}"
}
