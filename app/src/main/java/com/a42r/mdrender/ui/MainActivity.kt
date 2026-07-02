package com.a42r.mdrender.ui

import android.os.Bundle
import android.view.KeyEvent
import android.widget.Toast
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.a42r.mdrender.localsend.LocalSendPrefs
import com.a42r.mdrender.localsend.LocalSendService
import com.a42r.mdrender.localsend.LocalSendSessionManager
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.security.DeviceAuth
import com.a42r.mdrender.ui.navigation.MDRenderNavHost
import com.a42r.mdrender.ui.theme.MDRenderTheme
import com.a42r.mdrender.ui.viewer.ViewerZoom
import androidx.fragment.app.FragmentActivity
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : FragmentActivity() {

    @Inject lateinit var localSendSessionManager: LocalSendSessionManager
    @Inject lateinit var localSendPrefs: LocalSendPrefs
    @Inject lateinit var appLock: AppLock

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Foreground-safe place to start the receiver (an activity is starting).
        if (localSendPrefs.enabled) {
            runCatching { LocalSendService.start(this) }
        }

        setContent {
            MDRenderTheme {
                val locked by appLock.isLocked.collectAsState()

                if (locked) {
                    LockGate(
                        onUnlock = { appLock.unlock() }
                    )
                } else {
                    Surface(modifier = Modifier.fillMaxSize()) {
                        MDRenderNavHost()
                    }
                    LocalSendOverlays()
                }
            }
        }
    }

    /** Full-screen gate shown while locked. Triggers the OS auth prompt on
     *  appearance and offers a retry if the user dismisses it. */
    @androidx.compose.runtime.Composable
    private fun LockGate(onUnlock: () -> Unit) {
        var prompting by remember { mutableStateOf(false) }

        fun prompt() {
            if (prompting) return
            // Only bypass when the device has no lock at all (can't gate).
            // Transient failures keep us locked and let the user retry.
            if (DeviceAuth.noCredentialConfigured(this@MainActivity)) {
                onUnlock()
                return
            }
            prompting = true
            DeviceAuth.authenticate(
                activity = this@MainActivity,
                onSuccess = { prompting = false; onUnlock() },
                onFailure = { prompting = false }
            )
        }

        LaunchedEffect(Unit) { prompt() }

        Surface(modifier = Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier.fillMaxSize().padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Icon(
                    Icons.Filled.Lock,
                    contentDescription = null,
                    modifier = Modifier.padding(bottom = 16.dp),
                    tint = MaterialTheme.colorScheme.primary
                )
                Text("MDRender is locked", style = MaterialTheme.typography.titleMedium)
                Button(onClick = { prompt() }, modifier = Modifier.padding(top = 24.dp)) {
                    Text("Unlock")
                }
            }
        }
    }

    @androidx.compose.runtime.Composable
    private fun LocalSendOverlays() {
        // Incoming LocalSend transfer: in-app Accept/Reject dialog.
        val pending by localSendSessionManager.pendingTransfer.collectAsState()
        pending?.let { transfer ->
            AlertDialog(
                onDismissRequest = { localSendSessionManager.reject(transfer.sessionId) },
                title = { Text("Incoming files from ${transfer.senderAlias}") },
                text = {
                    Column {
                        transfer.files.take(10).forEach { Text(it.fileName) }
                        if (transfer.files.size > 10) {
                            Text("…and ${transfer.files.size - 10} more")
                        }
                    }
                },
                confirmButton = {
                    TextButton(onClick = { localSendSessionManager.accept(transfer.sessionId) }) {
                        Text("Accept")
                    }
                },
                dismissButton = {
                    TextButton(onClick = { localSendSessionManager.reject(transfer.sessionId) }) {
                        Text("Reject")
                    }
                }
            )
        }

        // Completion toast while the app is open.
        val completed by localSendSessionManager.lastCompleted.collectAsState()
        LaunchedEffect(completed) {
            completed?.let {
                Toast.makeText(this@MainActivity, it, Toast.LENGTH_SHORT).show()
                localSendSessionManager.clearCompletedMessage()
            }
        }
    }

    /** Volume keys adjust font size / zoom while a viewer screen is open. */
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        val delta = when (keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> 1
            KeyEvent.KEYCODE_VOLUME_DOWN -> -1
            else -> 0
        }
        if (delta != 0 && ViewerZoom.onVolumeKey(delta)) return true
        return super.onKeyDown(keyCode, event)
    }
}
