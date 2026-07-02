package com.a42r.mdrender.security

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.SecretKey
import javax.crypto.KeyGenerator
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class KeystoreManager @Inject constructor() {
    companion object {
        private const val KEYSTORE_TYPE = "AndroidKeyStore"
        private const val KEY_ALIAS = "mdrender_file_encryption_key"
    }

    fun getOrCreateAesKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE_TYPE).apply { load(null) }
        if (keyStore.containsAlias(KEY_ALIAS)) {
            return (keyStore.getEntry(KEY_ALIAS, null) as KeyStore.SecretKeyEntry).secretKey
        }
        return generateAesKey()
    }

    private fun generateAesKey(): SecretKey {
        val keyGenerator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            KEYSTORE_TYPE
        )
        val spec = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setUserAuthenticationRequired(true)
            .setInvalidatedByBiometricEnrollment(true)
            .build()
        keyGenerator.init(spec)
        return keyGenerator.generateKey()
    }
}
