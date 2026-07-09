package com.a42r.mdrender.data.repository

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import com.a42r.mdrender.MDRenderApplication
import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.dao.FileListItem
import com.a42r.mdrender.data.dao.FileMetadata
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.security.CryptoEngine
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
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
    @ApplicationContext private val context: Context
) {
    /** Files above this threshold use file-backed storage instead of SQLite BLOB. */
    private val FILE_STORAGE_THRESHOLD = 10L * 1024 * 1024 // 10 MB

    /** Directory for file-backed encrypted blobs. */
    private val encryptedDir: File = File(context.filesDir, "encrypted").also { it.mkdirs() }

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

    suspend fun deleteFile(id: Long) {
        val meta = fileDao.getFileMetadata(id)
        if (meta?.storageType == "file" && meta.storagePath != null) {
            File(encryptedDir, meta.storagePath).delete()
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
                if (totalSize <= 0L) return null

                val out = ByteArrayOutputStream(totalSize.toInt().coerceAtMost(256 * 1024 * 1024))
                val chunk = 1_048_576 // 1MB
                var offset = 1
                while (offset <= totalSize) {
                    val cursor = db.rawQuery(
                        "SELECT substr(encrypted_blob, ?, ?) FROM files WHERE id = ?",
                        arrayOf(offset.toString(), chunk.toString(), id.toString())
                    )
                    cursor.use { if (it.moveToFirst()) out.write(it.getBlob(0)) }
                    offset += chunk
                }
                return out.toByteArray()
            }
        } catch (e: Exception) {
            Log.e(TAG, "readLargeBlob id=$id", e)
            return null
        }
    }

    /** Returns the full decrypted content as a ByteArray. For file-stored files,
     *  the encrypted file is read and decrypted in a single pass. */
    suspend fun getDecryptedContent(id: Long): Pair<ByteArray, String>? {
        val meta = fileDao.getFileMetadata(id) ?: return null
        return when (meta.storageType) {
            "file" -> {
                val storageFile = meta.storagePath?.let { File(encryptedDir, it) }
                    ?: return null
                if (!storageFile.exists()) return null
                val bytes = storageFile.inputStream().use { input ->
                    cryptoEngine.decryptStream(input).use { it.readBytes() }
                }
                Pair(bytes, meta.mimeType)
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
        val meta = fileDao.getFileMetadata(id) ?: return null
        return when (meta.storageType) {
            "file" -> {
                val storageFile = meta.storagePath?.let { File(encryptedDir, it) }
                    ?: return null
                if (!storageFile.exists()) return null
                try {
                    cryptoEngine.decryptStream(storageFile.inputStream())
                } catch (e: Exception) {
                    Log.e(TAG, "getDecryptedStream id=$id", e)
                    null
                }
            }
            else -> {
                val bytes = readLargeBlob(id) ?: return null
                try {
                    ByteArrayInputStream(cryptoEngine.decrypt(bytes))
                } catch (e: Exception) {
                    Log.e(TAG, "getDecryptedStream id=$id", e)
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

    suspend fun getFileMetadata(id: Long): FileMetadata? = fileDao.getFileMetadata(id)

    /** The file named [name] in [folderId] (exact match), or null. */
    suspend fun findByName(folderId: Long?, name: String): FileMetadata? =
        fileDao.findByName(folderId, name)

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
