package com.a42r.mdrender.data.repository

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.util.Log
import com.a42r.mdrender.MDRenderApplication
import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.dao.FileListItem
import com.a42r.mdrender.data.dao.FileMetadata
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.security.CryptoEngine
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class FileRepository @Inject constructor(
    private val fileDao: FileDao,
    private val cryptoEngine: CryptoEngine,
    private val storagePrefs: StoragePrefs,
    @ApplicationContext private val context: Context
) {
    /** Files above this threshold use file-backed storage instead of SQLite BLOB. */
    private val FILE_STORAGE_THRESHOLD = 1L * 1024 * 1024 // 1 MB

    /** Directory for file-backed encrypted blobs. */
    private val encryptedDir: File = File(context.filesDir, "encrypted").also { it.mkdirs() }

    /** Directory for file-backed unencrypted files. */
    private val plainDir: File = File(context.filesDir, "plain").also { it.mkdirs() }

    fun getFilesInFolder(folderId: Long?): Flow<List<FileListItem>> = fileDao.getFilesInFolder(folderId)

    suspend fun importFile(name: String, mimeType: String, rawBytes: ByteArray, folderId: Long? = null): Long {
        val encryptedBlob = cryptoEngine.encrypt(rawBytes)
        val thumbnail: ByteArray? = if (mimeType.startsWith("image/")) {
            generateEncryptedThumbnail(rawBytes)
        } else null

        val entity = FileEntity(
            folderId = folderId,
            name = name,
            mimeType = mimeType,
            encryptedBlob = encryptedBlob,
            encryptedThumbnail = thumbnail,
            fileSize = rawBytes.size.toLong()
        )
        return fileDao.insert(entity)
    }

    /** Import a file from a temp file (typically from a network upload). Reads it
     *  into memory for BLOB storage when the file is small; streams it to an
     *  encrypted file on disk when it exceeds [FILE_STORAGE_THRESHOLD]. The
     *  temp file is deleted after the import. */
    suspend fun importFileFromTemp(
        tempFile: File, name: String, mimeType: String, folderId: Long? = null
    ): Long {
        val fileSize = tempFile.length()
        return if (fileSize <= FILE_STORAGE_THRESHOLD) {
            // Small file — read into memory, encrypt as BLOB (existing path).
            val rawBytes = tempFile.readBytes()
            tempFile.delete()
            importFile(name, mimeType, rawBytes, folderId)
        } else if (!storagePrefs.encryptLargeFiles) {
            // Large file with encryption disabled — store unencrypted on disk.
            val storageName = UUID.randomUUID().toString()
            val dest = File(plainDir, storageName)
            tempFile.copyTo(dest, overwrite = true)
            tempFile.delete()
            val entity = FileEntity(
                folderId = folderId,
                name = name,
                mimeType = mimeType,
                encryptedBlob = ByteArray(0),
                fileSize = fileSize,
                storageType = "plain",
                storagePath = storageName
            )
            fileDao.insert(entity)
        } else {
            // Large file — stream encrypt to file-backed storage.
            val storageName = UUID.randomUUID().toString()
            val dest = File(encryptedDir, storageName)
            tempFile.inputStream().use { input ->
                cryptoEngine.encryptStream(input, dest.outputStream())
            }
            tempFile.delete()
            val entity = FileEntity(
                folderId = folderId,
                name = name,
                mimeType = mimeType,
                encryptedBlob = ByteArray(0),
                fileSize = fileSize,
                storageType = "file",
                storagePath = storageName
            )
            fileDao.insert(entity)
        }
    }

    /** Returns the on-disk File for a plain-stored file, or null if not applicable. */
    suspend fun getPlainFile(id: Long): File? {
        val meta = fileDao.getFileMetadata(id) ?: return null
        if (meta.storageType != "plain" || meta.storagePath == null) return null
        val file = File(plainDir, meta.storagePath)
        return if (file.exists()) file else null
    }

    /** Convert a plain file to encrypted file-backed storage. */
    suspend fun encryptFile(id: Long) = withContext(Dispatchers.IO) {
        val meta = fileDao.getFileMetadata(id) ?: return@withContext
        if (meta.storageType != "plain" || meta.storagePath == null) return@withContext
        val src = File(plainDir, meta.storagePath)
        if (!src.exists()) return@withContext
        val storageName = UUID.randomUUID().toString()
        val dest = File(encryptedDir, storageName)
        src.inputStream().use { input ->
            cryptoEngine.encryptStream(input, dest.outputStream())
        }
        src.delete()
        fileDao.updateStorageInfo(id, "file", storageName, meta.fileSize, System.currentTimeMillis())
    }

    /** Convert a file-backed encrypted file or BLOB to plain (unencrypted) storage.
     *  Uses streaming decrypt to avoid loading the entire file into memory. */
    suspend fun decryptFile(id: Long) = withContext(Dispatchers.IO) {
        val meta = fileDao.getFileMetadata(id) ?: return@withContext
        if (meta.storageType == "plain") return@withContext
        val storageName = UUID.randomUUID().toString()
        val dest = File(plainDir, storageName)
        try {
            when (meta.storageType) {
                "file" -> {
                    val src = meta.storagePath?.let { File(encryptedDir, it) } ?: return@withContext
                    if (!src.exists()) return@withContext
                    cryptoEngine.decryptChunked(src.inputStream(), dest.outputStream())
                    src.delete()
                }
                else -> {
                    val encrypted = fileDao.getEncryptedBlob(id) ?: return@withContext
                    cryptoEngine.decryptChunked(ByteArrayInputStream(encrypted), dest.outputStream())
                }
            }
            fileDao.updateStorageInfo(id, "plain", storageName, meta.fileSize, System.currentTimeMillis())
        } catch (e: Exception) {
            Log.e(TAG, "decryptFile id=$id", e)
        }
    }

    suspend fun deleteFile(id: Long) {
        val meta = fileDao.getFileMetadata(id)
        if (meta?.storageType == "file" && meta.storagePath != null) {
            File(encryptedDir, meta.storagePath).delete()
        } else if (meta?.storageType == "plain" && meta.storagePath != null) {
            File(plainDir, meta.storagePath).delete()
        }
        fileDao.delete(id)
    }

    /** Returns [desired] or, when taken in [folderId], "name (1).ext"-style variant. */
    suspend fun uniqueNameInFolder(folderId: Long?, desired: String): String {
        val taken = fileDao.getNamesInFolder(folderId).toSet()
        if (desired !in taken) return desired
        val dot = desired.lastIndexOf('.')
        val base = if (dot > 0) desired.substring(0, dot) else desired
        val ext = if (dot > 0) desired.substring(dot) else ""
        var n = 1
        while ("$base ($n)$ext" in taken) n++
        return "$base ($n)$ext"
    }

    suspend fun renameFile(id: Long, newName: String) =
        fileDao.rename(id, newName, System.currentTimeMillis())

    suspend fun moveFile(id: Long, folderId: Long?) =
        fileDao.move(id, folderId, System.currentTimeMillis())

    companion object {
        private const val TAG = "FileRepository"
    }

    /** Read encrypted blob by chunking via SQL [substr], avoiding the ~2MB
     *  CursorWindow per-row limit. Each chunk is 1MB. */
    private fun readLargeBlob(id: Long): ByteArray? {
        try {
            val dbPath = MDRenderApplication.instance.getDatabasePath("mdrender.db").absolutePath
            val rawDb = android.database.sqlite.SQLiteDatabase.openDatabase(
                dbPath, null, android.database.sqlite.SQLiteDatabase.OPEN_READONLY
            )
            rawDb.use { db ->
                val sizeStmt = db.compileStatement("SELECT length(encrypted_blob) FROM files WHERE id = ?")
                sizeStmt.bindLong(1, id)
                val totalSize = sizeStmt.simpleQueryForLong()
                if (totalSize <= 0L) {
                    Log.w(TAG, "readLargeBlob: id=$id encrypted_blob length=$totalSize")
                    return null
                }

                val out = ByteArrayOutputStream(totalSize.toInt().coerceAtMost(256 * 1024 * 1024))
                val chunk = 1_048_576 // 1MB
                var offset = 1
                while (offset <= totalSize) {
                    val cursor = db.rawQuery(
                        "SELECT substr(encrypted_blob, ?, ?) FROM files WHERE id = ?",
                        arrayOf(offset.toString(), chunk.toString(), id.toString())
                    )
                    cursor.use { if (it.moveToFirst()) {
                        val data = it.getBlob(0)
                        if (data == null) { Log.w(TAG, "readLargeBlob: null chunk at offset=$offset id=$id") }
                        else out.write(data)
                    } else {
                        Log.w(TAG, "readLargeBlob: no cursor at offset=$offset id=$id")
                    } }
                    offset += chunk
                }
                val result = out.toByteArray()
                Log.d(TAG, "readLargeBlob: id=$id totalSize=$totalSize read=${result.size}")
                return result
            }
        } catch (e: Exception) {
            Log.e(TAG, "readLargeBlob id=$id", e)
            return null
        }
    }

    /** Returns the full decrypted content as a ByteArray. For file-stored files,
     *  the encrypted file is read and decrypted in a single doFinal call. */
    suspend fun getDecryptedContent(id: Long): Pair<ByteArray, String>? {
        val meta = fileDao.getFileMetadata(id) ?: return null
        return when (meta.storageType) {
            "file" -> {
                val storageFile = meta.storagePath?.let { File(encryptedDir, it) }
                    ?: return null
                if (!storageFile.exists()) return null
                try {
                    val bytes = cryptoEngine.decryptFile(storageFile)
                    Pair(bytes, meta.mimeType)
                } catch (e: Exception) {
                    Log.e(TAG, "getDecryptedContent id=$id", e)
                    null
                }
            }
            "plain" -> {
                val storageFile = meta.storagePath?.let { File(plainDir, it) }
                    ?: return null
                if (!storageFile.exists()) return null
                Pair(storageFile.readBytes(), meta.mimeType)
            }
            else -> {
                val bytes = readLargeBlob(id) ?: return null
                Pair(cryptoEngine.decrypt(bytes), meta.mimeType)
            }
        }
    }

    /** Streaming read — returns a decrypted InputStream. For BLOB storage the
     *  full blob is still read into memory then wrapped; for file-backed storage
     *  the encrypted file is stream-decrypted without holding the full plaintext
     *  in memory. */
    suspend fun getDecryptedStream(id: Long): InputStream? {
        val meta = fileDao.getFileMetadata(id) ?: return null.also {
            Log.w(TAG, "getDecryptedStream: no metadata for id=$id")
        }
        return when (meta.storageType) {
            "file" -> {
                val storageFile = meta.storagePath?.let { File(encryptedDir, it) }
                    ?: return null.also { Log.w(TAG, "getDecryptedStream: no storagePath for id=$id") }
                if (!storageFile.exists()) {
                    Log.w(TAG, "getDecryptedStream: storageFile missing for id=$id path=$storageFile")
                    return null
                }
                try {
                    cryptoEngine.decryptStream(storageFile.inputStream())
                } catch (e: Exception) {
                    Log.e(TAG, "getDecryptedStream id=$id decryptStream failed", e)
                    null
                }
            }
            "plain" -> {
                val storageFile = meta.storagePath?.let { File(plainDir, it) }
                    ?: return null.also { Log.w(TAG, "getDecryptedStream: no storagePath for id=$id") }
                if (!storageFile.exists()) {
                    Log.w(TAG, "getDecryptedStream: storageFile missing for id=$id path=$storageFile")
                    return null
                }
                storageFile.inputStream()
            }
            else -> {
                val bytes = readLargeBlob(id) ?: return null.also {
                    Log.w(TAG, "getDecryptedStream: readLargeBlob returned null for id=$id")
                }
                try {
                    ByteArrayInputStream(cryptoEngine.decrypt(bytes))
                } catch (e: Exception) {
                    Log.e(TAG, "getDecryptedStream id=$id decrypt failed", e)
                    null
                }
            }
        }
    }

    suspend fun getDecryptedThumbnail(id: Long): ByteArray? {
        val meta = fileDao.getFileMetadata(id) ?: return null
        return meta.encryptedThumbnail?.let { if (it.isNotEmpty()) cryptoEngine.decrypt(it) else null }
    }

    /** Ordered image files in the same folder as [fileId], with the index of
     *  [fileId] itself. Used to page between sibling images in the viewer. */
    suspend fun getImageSiblings(fileId: Long): Pair<List<Long>, Int> {
        val meta = fileDao.getFileMetadata(fileId) ?: return emptyList<Long>() to 0
        val images = fileDao.getFilesInFolderList(meta.folderId)
            .filter { it.mimeType.startsWith("image/") }
            .map { it.id }
        val index = images.indexOf(fileId).coerceAtLeast(0)
        return images to index
    }

    private fun generateEncryptedThumbnail(rawBytes: ByteArray): ByteArray? {
        return try {
            val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size, opts)
            val scaleFactor = maxOf(opts.outWidth / 256, opts.outHeight / 256, 1)
            val decodeOpts = BitmapFactory.Options().apply { inSampleSize = scaleFactor }
            val bitmap = BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size, decodeOpts) ?: return null
            val stream = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, 70, stream)
            bitmap.recycle()
            cryptoEngine.encrypt(stream.toByteArray())
        } catch (e: Exception) {
            null
        }
    }

    suspend fun saveScrollPosition(id: Long, pos: Int) = fileDao.updateScrollPosition(id, pos)

    suspend fun savePlaybackPosition(id: Long, pos: Long) = fileDao.updatePlaybackPosition(id, pos)

    fun getLastOpenedFile(): Flow<FileMetadata?> = fileDao.getLastOpenedFile()

    suspend fun saveLastOpenedAt(id: Long) = fileDao.updateLastOpenedAt(id, System.currentTimeMillis())

    suspend fun getLastOpenedAt(id: Long): Long? = fileDao.getLastOpenedAt(id)

    /** Restore an explicit last-opened timestamp — used when a file is replaced
     *  (delete + reinsert under a new id) so the "last opened" status carries over. */
    suspend fun restoreLastOpenedAt(id: Long, timestamp: Long) = fileDao.updateLastOpenedAt(id, timestamp)

    suspend fun getFileMetadata(id: Long): FileMetadata? = fileDao.getFileMetadata(id)

    /** The file named [name] in [folderId] (exact match), or null. */
    suspend fun findByName(folderId: Long?, name: String): FileMetadata? =
        fileDao.findByName(folderId, name)

    /** Extract audio duration in milliseconds. Returns null for non-audio or on failure. */
    suspend fun getAudioDuration(id: Long): Long? {
        val meta = fileDao.getFileMetadata(id) ?: return null
        if (!meta.mimeType.startsWith("audio/")) return null
        return withContext(Dispatchers.IO) {
            try {
                val retriever = MediaMetadataRetriever()
                when (meta.storageType) {
                    "plain" -> {
                        val f = meta.storagePath?.let { File(plainDir, it) } ?: return@withContext null
                        if (!f.exists()) return@withContext null
                        retriever.setDataSource(f.absolutePath)
                    }
                    else -> {
                        val tmp = File.createTempFile("dur_", ".tmp", context.cacheDir)
                        try {
                            when (meta.storageType) {
                                "file" -> {
                                    val src = meta.storagePath?.let { File(encryptedDir, it) }
                                        ?: return@withContext null
                                    if (!src.exists()) return@withContext null
                                    cryptoEngine.decryptChunked(src.inputStream(), tmp.outputStream())
                                }
                                else -> {
                                    val encrypted = fileDao.getEncryptedBlob(id)
                                        ?: return@withContext null
                                    cryptoEngine.decryptChunked(ByteArrayInputStream(encrypted), tmp.outputStream())
                                }
                            }
                            retriever.setDataSource(tmp.absolutePath)
                        } finally {
                            tmp.delete()
                        }
                    }
                }
                val dur = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
                retriever.release()
                dur?.coerceAtLeast(0)
            } catch (e: Exception) {
                Log.w(TAG, "getAudioDuration id=$id", e)
                null
            }
        }
    }

    fun mimeTypeFromExtension(filename: String): String {
        return when {
            filename.endsWith(".md", ignoreCase = true) -> "text/markdown"
            filename.endsWith(".txt", ignoreCase = true) -> "text/plain"
            filename.endsWith(".png", ignoreCase = true) -> "image/png"
            filename.endsWith(".jpg", ignoreCase = true) || filename.endsWith(".jpeg", ignoreCase = true) -> "image/jpeg"
            filename.endsWith(".gif", ignoreCase = true) -> "image/gif"
            filename.endsWith(".webp", ignoreCase = true) -> "image/webp"
            filename.endsWith(".bmp", ignoreCase = true) -> "image/bmp"
            filename.endsWith(".mp3", ignoreCase = true) -> "audio/mpeg"
            filename.endsWith(".aac", ignoreCase = true) -> "audio/aac"
            filename.endsWith(".ogg", ignoreCase = true) -> "audio/ogg"
            filename.endsWith(".wav", ignoreCase = true) -> "audio/wav"
            filename.endsWith(".flac", ignoreCase = true) -> "audio/flac"
            else -> "application/octet-stream"
        }
    }
}
