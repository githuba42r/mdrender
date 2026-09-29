package com.a42r.mdrender.cloudpush

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import java.security.spec.MGF1ParameterSpec
import javax.crypto.Cipher
import javax.crypto.spec.OAEPParameterSpec
import javax.crypto.spec.PSource
import javax.inject.Inject
import javax.inject.Singleton

/**
 * RSA-3072 keypair in the Android Keystore, used only to prove at registration that this
 * device holds the private key the server will later verify registrations against.
 *
 * Sign-only: the doorbell itself is symmetric AES-256-GCM under the negotiated
 * [PushServerConfig.pushKey], so this key never decrypts anything. An attacker who
 * captures a doorbell cannot use it to recover the private key.
 */
@Singleton
class CloudPushKeyStore @Inject constructor() {
    /**
     * Content decryption keypair: a **separate**, decrypt-capable RSA key used to
     * open the client-sealed content key (design §7a). Distinct from the
     * sign-only pairing key above so the doorbell/registration key never carries
     * a decryption capability.
     */
    fun getOrCreateContentKeyPair(): KeyPair {
        if (keyStore.containsAlias(CONTENT_ALIAS)) {
            return (keyStore.getEntry(CONTENT_ALIAS, null) as KeyStore.PrivateKeyEntry).let {
                KeyPair(it.certificate.publicKey, it.privateKey)
            }
        }
        val generator = KeyPairGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_RSA, PROVIDER
        )
        generator.initialize(
            KeyGenParameterSpec.Builder(
                CONTENT_ALIAS,
                KeyProperties.PURPOSE_DECRYPT or KeyProperties.PURPOSE_ENCRYPT,
            )
                .setKeySize(3072)
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP)
                .build()
        )
        return generator.generateKeyPair()
    }

    /** SPKI DER of the content public key, registered so clients can seal to it. */
    fun getContentPublicKeySpkiDer(): ByteArray =
        getOrCreateContentKeyPair().public.encoded

    /** Unwrap a client-sealed blob (RSA-OAEP, MGF1-SHA256). Null on failure. */
    fun decryptOaep(ciphertext: ByteArray): ByteArray? = try {
        val cipher = Cipher.getInstance("RSA/ECB/OAEPPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            getOrCreateContentKeyPair().private,
            OAEPParameterSpec(
                "SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified.DEFAULT
            ),
        )
        cipher.doFinal(ciphertext)
    } catch (_: Exception) {
        null
    }

    fun deleteContentKeyPair() {
        if (keyStore.containsAlias(CONTENT_ALIAS)) keyStore.deleteEntry(CONTENT_ALIAS)
    }

    companion object {
        const val ALIAS = "mdrender_cloudpush_keypair"
        const val CONTENT_ALIAS = "mdrender_content_keypair"
        private const val PROVIDER = "AndroidKeyStore"
    }

    private val keyStore: KeyStore =
        KeyStore.getInstance(PROVIDER).apply { load(null) }

    fun getOrCreateKeyPair(): KeyPair {
        if (keyStore.containsAlias(ALIAS)) {
            return (keyStore.getEntry(ALIAS, null) as KeyStore.PrivateKeyEntry).let {
                KeyPair(it.certificate.publicKey, it.privateKey)
            }
        }
        val generator = KeyPairGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_RSA, PROVIDER
        )
        generator.initialize(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setKeySize(3072)
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setSignaturePaddings(KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
                .build()
        )
        return generator.generateKeyPair()
    }

    fun getPublicKeySpkiDer(): ByteArray =
        getOrCreateKeyPair().public.encoded // X.509 SubjectPublicKeyInfo (DER)

    fun sign(data: ByteArray): ByteArray {
        val signature = Signature.getInstance("SHA256withRSA")
        signature.initSign(getOrCreateKeyPair().private)
        signature.update(data)
        return signature.sign()
    }

    fun deleteKeyPair() {
        if (keyStore.containsAlias(ALIAS)) keyStore.deleteEntry(ALIAS)
    }
}
