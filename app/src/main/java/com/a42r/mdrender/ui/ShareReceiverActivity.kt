package com.a42r.mdrender.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import com.a42r.mdrender.MDRenderApplication

class ShareReceiverActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Read URIs and log them
        val uris = extractUris(intent)
        Log.d("ShareReceiver", "action=${intent?.action}, type=${intent?.type}, uriCount=${uris.size}")

        // Import synchronously on main thread — these are typically small files
        // from a share intent, so this won't ANR.
        var count = 0
        if (uris.isNotEmpty()) {
            count = importFilesSync(uris)
        }
        Log.d("ShareReceiver", "imported $count of ${uris.size} files")

        val message = when {
            uris.isEmpty() -> "Nothing to import"
            count == uris.size && count == 1 -> "Imported to MDRender"
            count == uris.size -> "Imported $count files to MDRender"
            else -> "Imported $count of ${uris.size} files — see log for errors"
        }
        Toast.makeText(applicationContext, message, Toast.LENGTH_SHORT).show()

        // Set a result so the caller knows we processed the share
        setResult(RESULT_OK)
        finish()
    }

    @Suppress("DEPRECATION")
    private fun extractUris(intent: Intent?): List<Uri> {
        if (intent == null) return emptyList()
        if (intent.action == Intent.ACTION_SEND) {
            val uri = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
            if (uri != null) return listOf(uri)
        }
        if (intent.action == Intent.ACTION_SEND_MULTIPLE) {
            return intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM) ?: emptyList()
        }
        return emptyList()
    }

    private fun importFilesSync(uris: List<Uri>): Int {
        val fileRepo = MDRenderApplication.instance.fileRepository
        var count = 0
        for (uri in uris) {
            try {
                // Read the file contents via content resolver
                val bytes = contentResolver.openInputStream(uri)?.use { stream ->
                    stream.readBytes()
                }
                if (bytes == null) {
                    Log.e("ShareReceiver", "openInputStream returned null for $uri")
                    continue
                }

                val fileName = getFileName(uri) ?: "shared_${System.currentTimeMillis()}"
                val mimeType = fileRepo.mimeTypeFromExtension(fileName)
                val finalMime = if (mimeType == "application/octet-stream") {
                    contentResolver.getType(uri) ?: mimeType
                } else mimeType

                // Room's importFile is suspend — run it synchronously
                kotlinx.coroutines.runBlocking {
                    fileRepo.importFile(fileName, finalMime, bytes, null)
                }
                count++
            } catch (e: SecurityException) {
                Log.e("ShareReceiver", "Permission denied for $uri", e)
            } catch (e: Exception) {
                Log.e("ShareReceiver", "Failed to import $uri: ${e.message}", e)
            }
        }
        return count
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
