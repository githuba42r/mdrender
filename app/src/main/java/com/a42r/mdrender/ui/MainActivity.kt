package com.a42r.mdrender.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.MotionEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.ui.navigation.MDRenderNavHost
import com.a42r.mdrender.ui.theme.MDRenderTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject lateinit var appLockManager: AppLockManager

    private var pendingShareIntent: Intent? = null
    private var pendingImportUris: List<Uri>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        handleIntent(intent)

        // Process share intent if present, importing to root folder
        pendingShareIntent?.let { intent ->
            processShareIntent(intent)
            pendingShareIntent = null
        }

        setContent {
            MDRenderTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    MDRenderNavHost()
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND || intent?.action == Intent.ACTION_SEND_MULTIPLE) {
            pendingShareIntent = intent
        }
    }

    private fun processShareIntent(intent: Intent) {
        // Extract URIs from ACTION_SEND or ACTION_SEND_MULTIPLE
        val uris = when {
            intent.action == Intent.ACTION_SEND_MULTIPLE ->
                intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM) ?: emptyList()
            intent.action == Intent.ACTION_SEND ->
                listOfNotNull(intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))
            else -> emptyList()
        }
        // For v0.1, share intents are stored for future processing
        if (uris.isNotEmpty()) {
            pendingImportUris = uris
        }
    }

    /** Reset idle timer on every touch — dispatched to AppLockManager. */
    override fun dispatchTouchEvent(ev: MotionEvent?): Boolean {
        if (ev?.action == MotionEvent.ACTION_DOWN) {
            appLockManager.onUserInteraction()
        }
        return super.dispatchTouchEvent(ev)
    }
}
