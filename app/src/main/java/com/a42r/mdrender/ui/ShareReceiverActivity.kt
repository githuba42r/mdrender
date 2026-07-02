package com.a42r.mdrender.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.a42r.mdrender.MDRenderApplication
import com.a42r.mdrender.ui.theme.MDRenderTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class ShareReceiverActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        Log.d("ShareReceiver", "onCreate, action=${intent?.action}, type=${intent?.type}")
        val uris = extractUris(intent)
        Log.d("ShareReceiver", "extracted ${uris.size} URIs: $uris")

        setContent {
            MDRenderTheme {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    var completed by remember { mutableIntStateOf(0) }
                    val total = uris.size

                    LaunchedEffect(uris) {
                        if (uris.isNotEmpty()) {
                            withContext(Dispatchers.IO) { importFiles(uris) { completed = it } }
                        }
                        Log.d("ShareReceiver", "import done, $completed/$total files imported, finishing")
                        finish()
                    }

                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center
                    ) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(48.dp),
                            strokeWidth = 4.dp
                        )
                        Spacer(Modifier.height(24.dp))
                        Text(
                            text = "Importing...",
                            fontSize = 16.sp,
                            color = MaterialTheme.colorScheme.onBackground
                        )
                        Spacer(Modifier.height(8.dp))
                        Text(
                            text = "$completed / $total",
                            fontSize = 14.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
                        )
                    }
                }
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun extractUris(intent: Intent?): List<Uri> {
        if (intent == null) return emptyList()
        if (intent.action == Intent.ACTION_SEND) {
            val uri = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
            if (uri != null) return listOf(uri)
            // Text shares come as EXTRA_TEXT
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)
            if (text != null) return emptyList() // text content, not files
        }
        if (intent.action == Intent.ACTION_SEND_MULTIPLE) {
            return intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM) ?: emptyList()
        }
        return emptyList()
    }

    private suspend fun importFiles(uris: List<Uri>, onProgress: (Int) -> Unit) {
        val fileRepo = MDRenderApplication.instance.fileRepository
        var count = 0
        for (uri in uris) {
            try {
                // Take persistable permission so the URI survives beyond this activity
                contentResolver.takePersistableUriPermission(
                    uri,
                    android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            } catch (_: SecurityException) { }

            try {
                val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: continue
                val fileName = getFileName(uri) ?: "shared_${System.currentTimeMillis()}"
                val mimeType = fileRepo.mimeTypeFromExtension(fileName)
                val finalMime = if (mimeType == "application/octet-stream") {
                    contentResolver.getType(uri) ?: mimeType
                } else mimeType
                fileRepo.importFile(fileName, finalMime, bytes, null)
                count++
                onProgress(count)
            } catch (e: Exception) {
                Log.e("ShareReceiver", "import failed for $uri", e)
            }
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
}
