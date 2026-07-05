package com.a42r.mdrender.ui

import android.graphics.Color as AndroidColor
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.fragment.app.FragmentActivity
import com.a42r.mdrender.localsend.LocalSendPrefs
import com.a42r.mdrender.localsend.LocalSendService
import com.a42r.mdrender.localsend.LocalSendSessionManager
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.security.DeviceAuth
import com.a42r.mdrender.ui.navigation.MDRenderNavHost
import com.a42r.mdrender.ui.theme.MDRenderTheme
import com.a42r.mdrender.ui.viewer.ViewerZoom
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : FragmentActivity() {

    @Inject lateinit var localSendSessionManager: LocalSendSessionManager
    @Inject lateinit var localSendPrefs: LocalSendPrefs
    @Inject lateinit var appLock: AppLock

    private var authInProgress = false
    private var authRetryCount = 0
    private var shieldView: View? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        if (localSendPrefs.enabled) {
            runCatching { LocalSendService.start(this) }
        }

        setContent {
            MDRenderTheme {
                val locked by appLock.isLocked.collectAsState()

                val displayingHidden by appLock.displayingHidden.collectAsState()
                LaunchedEffect(displayingHidden) {
                    if (displayingHidden) {
                        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    } else {
                        window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    }
                }

                if (locked) {
                    LockGate()
                } else {
                    Surface(modifier = Modifier.fillMaxSize()) {
                        MDRenderNavHost()
                    }
                    LocalSendOverlays()
                }
            }
        }

        // Add a permanent black overlay on top of the ComposeView.
        val content = window.findViewById<ViewGroup>(android.R.id.content)
        if (content != null && shieldView == null) {
            val shield = View(this).apply {
                setBackgroundColor(AndroidColor.BLACK)
                layoutParams = FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT
                )
                visibility = if (appLock.isLocked.value) View.VISIBLE else View.GONE
            }
            content.addView(shield)
            shieldView = shield
        }
    }

    override fun onResume() {
        super.onResume()
        authInProgress = false
        if (appLock.isLocked.value) {
            // Shield should be visible (set in onPause). Don't touch it
            // until auth succeeds.
            promptAuth()
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            shieldView?.visibility = View.GONE
        }
    }

    override fun onPause() {
        if (appLock.isLocked.value) {
            // Show the shield synchronously so the next layout pass draws it.
            // We cannot synchronously wait for the GPU to render it (blocking
            // the main thread prevents the draw from ever happening), but the
            // visibility change + invalidate ensures the shield appears in the
            // first frame after onPause returns.
            shieldView?.visibility = View.VISIBLE
            shieldView?.invalidate()
        }
        super.onPause()
    }

    private fun hideShieldAfterUnlock() {
        shieldView?.post { shieldView?.visibility = View.GONE }
    }

    private fun promptAuth(force: Boolean = false) {
        if (authInProgress && !force) return
        if (DeviceAuth.noCredentialConfigured(this)) {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            hideShieldAfterUnlock()
            appLock.unlock()
            return
        }
        if (!force) authRetryCount = 0
        authInProgress = true
        DeviceAuth.authenticate(
            activity = this,
            onSuccess = {
                window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                hideShieldAfterUnlock()
                authInProgress = false
                authRetryCount = 0
                appLock.unlock()
            },
            onFailure = {
                authInProgress = false
                if (authRetryCount < 2 && appLock.isLocked.value) {
                    authRetryCount++
                    Handler(Looper.getMainLooper()).postDelayed({
                        if (appLock.isLocked.value) promptAuth(force = true)
                    }, 400)
                }
            }
        )
    }

    @Composable
    private fun LockGate() {
        LaunchedEffect(Unit) { promptAuth(force = true) }
        Surface(modifier = Modifier.fillMaxSize(), color = Color.Black) { }
    }

    @Composable
    private fun LocalSendOverlays() {
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

        val completed by localSendSessionManager.lastCompleted.collectAsState()
        LaunchedEffect(completed) {
            completed?.let {
                Toast.makeText(this@MainActivity, it, Toast.LENGTH_SHORT).show()
                localSendSessionManager.clearCompletedMessage()
            }
        }
    }

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
