package com.a42r.mdrender.share

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.core.content.FileProvider
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.data.repository.FileRepository
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/** Stages decrypted copies of files for outbound sharing.
 *
 *  Plaintext only ever exists under cacheDir/share/ — wiped on every app
 *  start (crash-safe) and again before each share. */
@Singleton
class ShareOutManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val fileRepository: FileRepository
) {
    /** Outcome of staging: a launchable chooser intent, or the name of the
     *  file whose decryption failed (nothing is shared partially). */
    sealed interface StageResult {
        data class Ready(val intent: Intent) : StageResult
        data class Failed(val fileName: String) : StageResult
    }

    private val shareDir get() = File(context.cacheDir, SHARE_DIR)

    /** Remove all staged plaintext. Called on app start and before staging. */
    fun clearShareCache() {
        shareDir.deleteRecursively()
    }

    /** Decrypts [files] into cacheDir/share/ and builds a chooser intent.
     *  Any decryption or write failure — including a missing DB row, a
     *  CryptoEngine SecurityException, or an IOException writing the staged
     *  file — wipes the cache again and aborts with no partial share. */
    suspend fun stage(files: List<FileEntity>): StageResult = withContext(Dispatchers.IO) {
        clearShareCache()
        shareDir.mkdirs()
        val names = dedupeNames(files.map { sanitizeName(it.name) })
        val uris = ArrayList<Uri>(files.size)
        for ((i, file) in files.withIndex()) {
            try {
                val bytes = fileRepository.getDecryptedContent(file.id)?.first
                    ?: throw IllegalStateException("No decrypted content for ${file.id}")
                val staged = File(shareDir, names[i])
                if (staged.canonicalFile.parentFile != shareDir.canonicalFile) {
                    throw IllegalStateException("Staged path escapes share dir: ${staged.name}")
                }
                staged.writeBytes(bytes)
                uris += FileProvider.getUriForFile(
                    context, "${context.packageName}.fileprovider", staged
                )
            } catch (e: Exception) {
                clearShareCache()
                if (e is CancellationException) throw e
                Log.e(TAG, "Failed to stage ${file.name}", e)
                return@withContext StageResult.Failed(file.name)
            }
        }
        val send = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).apply { putExtra(Intent.EXTRA_STREAM, uris[0]) }
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).apply {
                putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
            }
        }
        send.type = commonMimeType(files.map { it.mimeType })
        // ClipData so the chooser target gets read grants on every URI.
        send.clipData = ClipData.newUri(context.contentResolver, "share", uris[0]).apply {
            for (j in 1 until uris.size) addItem(ClipData.Item(uris[j]))
        }
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val chooser = Intent.createChooser(send, null)
        chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        StageResult.Ready(chooser)
    }

    companion object {
        const val SHARE_DIR = "share"
        private const val TAG = "ShareOutManager"

        /** Strips path components and null chars from an externally-influenced
         *  file name (LocalSend network names, inbound share DISPLAY_NAME) so
         *  it can't escape shareDir via "../" or embedded separators. */
        fun sanitizeName(name: String): String {
            val stripped = name
                .substringAfterLast('/')
                .substringAfterLast('\\')
                .filter { it != '\u0000' }
            return stripped.ifBlank { "file" }
        }

        /** On-disk names for staged files: duplicates get "name (1).ext". */
        fun dedupeNames(names: List<String>): List<String> {
            val used = mutableSetOf<String>()
            return names.map { name ->
                if (used.add(name)) return@map name
                val dot = name.lastIndexOf('.')
                val base = if (dot > 0) name.substring(0, dot) else name
                val ext = if (dot > 0) name.substring(dot) else ""
                var n = 1
                var candidate = "$base ($n)$ext"
                while (!used.add(candidate)) {
                    n++
                    candidate = "$base ($n)$ext"
                }
                candidate
            }
        }

        /** Narrowest MIME type covering [mimes]: exact match, "type/*", or "*/*". */
        fun commonMimeType(mimes: List<String>): String {
            if (mimes.isEmpty()) return "*/*"
            if (mimes.distinct().size == 1) return mimes[0]
            val primaries = mimes.map { it.substringBefore('/') }.distinct()
            return if (primaries.size == 1) "${primaries[0]}/*" else "*/*"
        }
    }
}
