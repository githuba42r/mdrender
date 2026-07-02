package com.a42r.mdrender.security

import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.mock
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

class CryptoEngineTest {
    private lateinit var testKey: SecretKey
    private lateinit var cryptoEngine: CryptoEngine

    @Before
    fun setUp() {
        testKey = KeyGenerator.getInstance("AES").run {
            init(256)
            generateKey()
        }
        cryptoEngine = CryptoEngine(mock()) // mock KeystoreManager, won't be used due to setTestKey
        cryptoEngine.setTestKey(testKey)
    }

    @Test
    fun encryptThenDecrypt_returnsOriginalPlaintext() {
        val plaintext = "Hello, secure world!".toByteArray(Charsets.UTF_8)
        val encrypted = cryptoEngine.encrypt(plaintext)
        assertFalse(encrypted.contentEquals(plaintext))
        val decrypted = cryptoEngine.decrypt(encrypted)
        assertArrayEquals(plaintext, decrypted)
    }

    @Test
    fun encrypt_producesDifferentOutputEachTime() {
        val plaintext = "test data".toByteArray()
        val enc1 = cryptoEngine.encrypt(plaintext)
        val enc2 = cryptoEngine.encrypt(plaintext)
        assertFalse(enc1.contentEquals(enc2))
    }

    @Test(expected = SecurityException::class)
    fun decrypt_withWrongKey_throwsSecurityException() {
        val wrongKey = KeyGenerator.getInstance("AES").run {
            init(256)
            generateKey()
        }
        val wrongEngine = CryptoEngine(mock())
        wrongEngine.setTestKey(wrongKey)
        val encrypted = cryptoEngine.encrypt("secret".toByteArray())
        wrongEngine.decrypt(encrypted) // should throw
    }
}
