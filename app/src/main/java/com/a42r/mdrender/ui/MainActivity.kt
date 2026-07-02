package com.a42r.mdrender.ui

import android.os.Bundle
import android.view.KeyEvent
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.a42r.mdrender.localsend.LocalSendSessionManager
import com.a42r.mdrender.ui.navigation.MDRenderNavHost
import com.a42r.mdrender.ui.theme.MDRenderTheme
import com.a42r.mdrender.ui.viewer.ViewerZoom
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject lateinit var localSendSessionManager: LocalSendSessionManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            MDRenderTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    MDRenderNavHost()
                }

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
