package com.a42r.mdrender.data.repository

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.entity.FileEntity
import com.a42r.mdrender.security.CryptoEngine
import kotlinx.coroutines.flow.Flow
import java.io.ByteArrayOutputStream
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class FileRepository @Inject constructor(
    private val fileDao: FileDao,
    private val cryptoEngine: CryptoEngine
) {
    fun getFilesInFolder(folderId: Long?): Flow<List<FileEntity>> = fileDao.getFilesInFolder(folderId)

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

    suspend fun deleteFile(id: Long) = fileDao.delete(id)

    suspend fun getDecryptedContent(id: Long): Pair<ByteArray, String>? {
        val entity = fileDao.getById(id) ?: return null
        val decrypted = cryptoEngine.decrypt(entity.encryptedBlob)
        return Pair(decrypted, entity.mimeType)
    }

    suspend fun getDecryptedThumbnail(id: Long): ByteArray? {
        val entity = fileDao.getById(id) ?: return null
        return entity.encryptedThumbnail?.let { cryptoEngine.decrypt(it) }
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

    suspend fun getFileMetadata(id: Long): FileEntity? = fileDao.getById(id)

    fun mimeTypeFromExtension(filename: String): String {
        return when {
            filename.endsWith(".md", ignoreCase = true) -> "text/markdown"
            filename.endsWith(".txt", ignoreCase = true) -> "text/plain"
            filename.endsWith(".png", ignoreCase = true) -> "image/png"
            filename.endsWith(".jpg", ignoreCase = true) || filename.endsWith(".jpeg", ignoreCase = true) -> "image/jpeg"
            filename.endsWith(".gif", ignoreCase = true) -> "image/gif"
            filename.endsWith(".webp", ignoreCase = true) -> "image/webp"
            filename.endsWith(".bmp", ignoreCase = true) -> "image/bmp"
            else -> "application/octet-stream"
        }
    }
}
