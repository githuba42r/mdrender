package com.a42r.mdrender.ui

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.fragment.app.FragmentActivity
import androidx.navigation.compose.rememberNavController
import com.a42r.mdrender.audio.AudioMiniPlayerBar
import com.a42r.mdrender.audio.AudioPlayerPrefs
import com.a42r.mdrender.audio.AudioPlayerState
import com.a42r.mdrender.audio.HiddenAudioControls
import com.a42r.mdrender.localsend.LocalSendPrefs
import com.a42r.mdrender.localsend.LocalSendService
import com.a42r.mdrender.localsend.LocalSendSessionManager
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.security.DeviceAuth
import com.a42r.mdrender.security.SecurityPrefs
import com.a42r.mdrender.ui.navigation.MDRenderNavHost
import com.a42r.mdrender.ui.navigation.Routes
import com.a42r.mdrender.ui.theme.MDRenderTheme
import com.a42r.mdrender.ui.viewer.ViewerZoom
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : FragmentActivity() {

    @Inject lateinit var localSendSessionManager: LocalSendSessionManager
    @Inject lateinit var localSendPrefs: LocalSendPrefs
    @Inject lateinit var appLock: AppLock
    @Inject lateinit var securityPrefs: SecurityPrefs
    @Inject lateinit var playerState: AudioPlayerState
    @Inject lateinit var audioPlayerPrefs: AudioPlayerPrefs

    private var authInProgress = false
    private var authRetryCount = 0

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

                val navController = rememberNavController()

                // Gate: when unlocking with audio playing, suppress content
                // until the nav backstack has been cleaned up (no flash).
                val info by playerState.info.collectAsState()
                val needsCleanup = info.fileId != 0L
                var contentReady by remember { mutableStateOf(!needsCleanup) }
                val wasLocked = remember { mutableStateOf(true) }
                LaunchedEffect(locked) {
                    if (!locked && (wasLocked.value || !contentReady)) {
                        if (needsCleanup) {
                            navController.navigate(Routes.FolderBrowser.createRoute(null)) {
                                popUpTo(0) { inclusive = true }
                                launchSingleTop = true
                            }
                        }
                        contentReady = true
                    }
                    wasLocked.value = locked
                }

                val isHiddenFile by playerState.isHiddenFile.collectAsState()
                val revealHidden by appLock.revealHidden.collectAsState()
                val currentBackStackEntry by navController.currentBackStackEntryFlow.collectAsState(null)
                val currentRoute = currentBackStackEntry?.destination?.route
                val showMiniPlayer = info.fileId != 0L &&
                    currentRoute?.startsWith("audio_player/") != true &&
                    (!isHiddenFile || revealHidden)

                val showHiddenAudio = info.fileId != 0L &&
                    currentRoute?.startsWith("audio_player/") != true &&
                    isHiddenFile && !revealHidden

                Box(modifier = Modifier.fillMaxSize()) {
                    if (locked || !contentReady) {
                        LockGate()
                    } else {
                        Surface(modifier = Modifier.fillMaxSize()) {
                            Box(modifier = Modifier.fillMaxSize()) {
                                MDRenderNavHost(navController = navController)
                                AudioMiniPlayerBar(
                                    navController = navController,
                                    playerState = playerState,
                                    prefs = audioPlayerPrefs,
                                    visible = showMiniPlayer,
                                    modifier = Modifier.align(Alignment.BottomCenter)
                                )
                                HiddenAudioControls(
                                    playerState = playerState,
                                    visible = showHiddenAudio,
                                    modifier = Modifier.align(Alignment.BottomCenter)
                                )
                            }
                        }
                        LocalSendOverlays()
                    }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        authInProgress = false
        if (appLock.isLocked.value) {
            promptAuth()
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }

    private fun promptAuth(force: Boolean = false) {
        if (authInProgress && !force) return
        if (!securityPrefs.requireSystemAuth) {
            // User opted out in Settings (opt-out itself required a biometric
            // challenge — see SettingsScreen). Distinct from the credential-less
            // bypass below: here a credential IS configured, it's just not required.
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            appLock.unlock()
            return
        }
        if (DeviceAuth.noCredentialConfigured(this)) {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            appLock.unlock()
            return
        }
        if (!force) authRetryCount = 0
        authInProgress = true
        try {
            DeviceAuth.authenticate(
                activity = this,
                onSuccess = {
                    window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
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
        } catch (_: Exception) {
            // BiometricPrompt can throw (e.g. "Called after onSaveInstanceState")
            // when the activity lifecycle hasn't stabilized after a cold restart
            // following finishAndRemoveTask. Retry with delay instead of crashing.
            authInProgress = false
            if (authRetryCount < 2 && appLock.isLocked.value) {
                authRetryCount++
                Handler(Looper.getMainLooper()).postDelayed({
                    if (appLock.isLocked.value) promptAuth(force = true)
                }, 400)
            }
        }
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
