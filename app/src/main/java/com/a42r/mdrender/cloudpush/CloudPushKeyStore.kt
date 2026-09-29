package com.a42r.mdrender.cloudpush

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
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
    companion object {
        const val ALIAS = "mdrender_cloudpush_keypair"
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
