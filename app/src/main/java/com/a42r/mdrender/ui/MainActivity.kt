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
import com.a42r.mdrender.MDRenderApplication
import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.ui.navigation.MDRenderNavHost
import com.a42r.mdrender.ui.theme.MDRenderTheme
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject lateinit var appLockManager: AppLockManager

    private var pendingShareIntent: Intent? = null
    private var pendingImportUris: List<Uri>? = null
    private var navigateToImport = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        handleIntent(intent)

        // Share intents bypass the lock screen — sharing is an explicit user
        // action from an already-authenticated device.
        if (pendingShareIntent != null) {
            processShareIntent(pendingShareIntent!!)
            pendingShareIntent = null
            appLockManager.unlock()
            pendingImportUris?.let { uris ->
                CoroutineScope(Dispatchers.IO).launch { doImport(uris) }
            }
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
        processShareIntent(pendingShareIntent ?: return)
        pendingShareIntent = null
        appLockManager.unlock()
        pendingImportUris?.let { uris ->
            CoroutineScope(Dispatchers.IO).launch { doImport(uris) }
        }
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
        if (uris.isNotEmpty()) {
            pendingImportUris = uris
            // Navigate to import screen after content is set
            navigateToImport = true
        }
    }

    @Suppress("DEPRECATION")
    private suspend fun doImport(uris: List<Uri>) {
        val fileRepo = MDRenderApplication.instance.fileRepository
        var count = 0
        for (uri in uris) {
            try {
                val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: continue
                val fileName = getFileName(uri) ?: "unknown_${count}"
                val mimeType = fileRepo.mimeTypeFromExtension(fileName)
                if (mimeType == "application/octet-stream") {
                    val resolved = contentResolver.getType(uri) ?: continue
                    if (!resolved.startsWith("text/") && !resolved.startsWith("image/")) continue
                    fileRepo.importFile(fileName, resolved, bytes, null)
                } else {
                    fileRepo.importFile(fileName, mimeType, bytes, null)
                }
                count++
            } catch (_: Exception) { }
        }
    }

    @Suppress("DEPRECATION")
    private fun getFileName(uri: Uri): String? {
        val cursor = contentResolver.query(uri, null, null, null, null)
        return cursor?.use {
            if (it.moveToFirst()) {
                val nameIndex = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                if (nameIndex >= 0) it.getString(nameIndex) else null
            } else null
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
