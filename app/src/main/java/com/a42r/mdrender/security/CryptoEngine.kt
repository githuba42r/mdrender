package com.a42r.mdrender.security

import java.io.DataInputStream
import java.io.InputStream
import java.io.OutputStream
import javax.crypto.Cipher
import javax.crypto.CipherInputStream
import javax.crypto.CipherOutputStream
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.inject.Inject
import javax.inject.Singleton
import java.security.GeneralSecurityException

@Singleton
class CryptoEngine @Inject constructor(
    private val keystoreManager: KeystoreManager
) {
    companion object {
        private const val GCM_TAG_LENGTH = 128 // bits
        private const val GCM_IV_LENGTH = 12   // bytes
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }

    @Volatile
    private var testKey: SecretKey? = null

    fun setTestKey(key: SecretKey) { testKey = key }

    private fun getKey(): SecretKey = testKey ?: keystoreManager.getOrCreateAesKey()

    fun encrypt(plaintext: ByteArray): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getKey())
        val iv = cipher.iv
        val ciphertext = cipher.doFinal(plaintext)
        return iv + ciphertext // IV(12) + ciphertext+GCM-tag
    }

    fun decrypt(ciphertext: ByteArray): ByteArray {
        val iv = ciphertext.copyOfRange(0, GCM_IV_LENGTH)
        val encryptedData = ciphertext.copyOfRange(GCM_IV_LENGTH, ciphertext.size)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        val spec = GCMParameterSpec(GCM_TAG_LENGTH, iv)
        cipher.init(Cipher.DECRYPT_MODE, getKey(), spec)
        return try {
            cipher.doFinal(encryptedData)
        } catch (e: GeneralSecurityException) {
            throw SecurityException("Decryption failed — invalid key or tampered data", e)
        }
    }

    /** Streaming encrypt: reads [input], writes IV + ciphertext to [output].
     *  Both streams are closed after the operation. */
    fun encryptStream(input: InputStream, output: OutputStream) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getKey())
        output.write(cipher.iv) // IV first (12 bytes), matching existing format
        CipherOutputStream(output, cipher).use { cipherOut ->
            input.use { it.copyTo(cipherOut) }
        }
    }

    /** Streaming decrypt: returns an InputStream that reads IV from [input],
     *  then lazily decrypts the remaining bytes. The caller must close the
     *  returned stream. GCM tag verification happens on close. */
    fun decryptStream(input: InputStream): InputStream {
        val iv = ByteArray(GCM_IV_LENGTH)
        DataInputStream(input).readFully(iv)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        val spec = GCMParameterSpec(GCM_TAG_LENGTH, iv)
        cipher.init(Cipher.DECRYPT_MODE, getKey(), spec)
        return CipherInputStream(input, cipher)
    }
}
