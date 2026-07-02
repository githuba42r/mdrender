package com.a42r.mdrender.data.repository

import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.security.AuthMethod
import com.a42r.mdrender.security.AuthPreferencesStore
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec
import javax.inject.Inject
import javax.inject.Singleton
import android.util.Base64

@Singleton
class AuthRepository @Inject constructor(
    private val prefs: AuthPreferencesStore,
    private val appLockManager: AppLockManager
) {
    fun getAuthMethod(): AuthMethod = prefs.authMethod

    fun setAuthMethod(method: AuthMethod) {
        prefs.authMethod = method
    }

    fun verifyPattern(pattern: List<Int>): Boolean {
        val storedHash = prefs.patternHash ?: return false
        val inputHash = sha256(pattern.joinToString(","))
        return inputHash == storedHash
    }

    fun setPattern(pattern: List<Int>) {
        prefs.patternHash = sha256(pattern.joinToString(","))
    }

    fun hasPatternSet(): Boolean = prefs.patternHash != null

    fun verifyPin(pin: String): Boolean {
        val storedHash = prefs.pinHash ?: return false
        val salt = prefs.pinSalt ?: return false
        val inputHash = pbkdf2(pin, Base64.decode(salt, Base64.DEFAULT))
        return inputHash == storedHash
    }

    fun setPin(pin: String) {
        val salt = ByteArray(16).also { SecureRandom().nextBytes(it) }
        prefs.pinSalt = Base64.encodeToString(salt, Base64.DEFAULT)
        prefs.pinHash = pbkdf2(pin, salt)
    }

    fun hasPinSet(): Boolean = prefs.pinHash != null

    fun getIdleTimeoutSeconds(): Int = prefs.idleTimeoutSeconds

    fun setIdleTimeoutSeconds(seconds: Int) {
        prefs.idleTimeoutSeconds = seconds
        appLockManager.setIdleTimeoutSeconds(seconds)
    }

    private fun sha256(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return Base64.encodeToString(digest.digest(input.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
    }

    private fun pbkdf2(input: String, salt: ByteArray): String {
        val spec = PBEKeySpec(input.toCharArray(), salt, 100_000, 256)
        val factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
        return Base64.encodeToString(factory.generateSecret(spec).encoded, Base64.NO_WRAP)
    }
}
