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
            val entry = keyStore.getEntry(KEY_ALIAS, null)
            if (entry != null) {
                return (entry as KeyStore.SecretKeyEntry).secretKey
            }
        }
        return generateAesKey()
    }

    private fun generateAesKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE_TYPE).apply { load(null) }
        // Delete any existing key (e.g., from a prior version that used
        // setUserAuthenticationRequired). Existing encrypted files will be
        // lost, but this is acceptable for a v0.1 dev build.
        keyStore.deleteEntry(KEY_ALIAS)

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
            // No auth requirement — app lock screen handles viewing
            // protection. Share/import needs to work without auth.
            .build()
        keyGenerator.init(spec)
        return keyGenerator.generateKey()
    }
}
