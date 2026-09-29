package com.a42r.mdrender.cloudpush

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNamingStrategy
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The client half of the cloud-push crypto: opening the FCM doorbell and
 * checking the manifest signature.
 */
@Singleton
class PushCrypto @Inject constructor(private val keyStore: CloudPushKeyStore) {

    @Serializable
    data class Doorbell(
        val serverUrl: String,
        val pushId: String,
        val challengeKey: String,
    )

    @Serializable
    data class ManifestFile(
        val fileId: String,
        val name: String,
        val path: String,
        val size: Long,
        val retrievalKey: String,
    )

    @Serializable
    data class Manifest(
        val pushId: String,
        val date: String,
        val files: List<ManifestFile>,
    )

    @Serializable
    private data class DoorbellPayload(
        val v: Int = 1,
        val pushId: String,
        val serverUrl: String,
        val challengeKey: String,
    )

    /**
     * Open the doorbell. Returns null for anything that does not authenticate
     * under [pushKey]: a doorbell for another device, a tampered one, or plain
     * garbage. There is nothing to report in those cases, so the caller drops it.
     *
     * The IV arrives as an argument because the server derives it from a
     * `push_id` that is still inside the ciphertext — the app cannot recompute
     * the value the server used, it can only be told.
     */
    fun decryptTrigger(ctB64: String, ivB64: String, pushKey: ByteArray): Doorbell? {
        val payload = try {
            val iv = Base64.getDecoder().decode(ivB64)
            val raw = Base64.getDecoder().decode(ctB64)
            if (iv.size != IV_LENGTH || raw.size <= GCM_TAG_LENGTH) return null
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(
                Cipher.DECRYPT_MODE,
                SecretKeySpec(pushKey, "AES"),
                GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv),
            )
            // The server sends ciphertext||tag, which is exactly what GCM expects.
            WIRE.decodeFromString<DoorbellPayload>(String(cipher.doFinal(raw), Charsets.UTF_8))
        } catch (_: Exception) {
            return null
        }
        return Doorbell(payload.serverUrl, payload.pushId, payload.challengeKey)
    }

    /**
     * Verify [manifestJson] against [sigB64] using the key the QR code pinned at
     * pairing, and return the file list only if that check passes.
     *
     * The signature is checked over the exact bytes passed in, never over a
     * re-serialisation of the parsed object: the server signs one canonical
     * encoding, and re-encoding would reorder keys and fail the check. The
     * server returns those signed bytes as the whole response body for
     * precisely this reason.
     */
    fun verifyManifest(
        manifestJson: String,
        sigB64: String,
        serverPublicKeyPem: String,
    ): List<ManifestFile>? {
        return try {
            val pem = serverPublicKeyPem
                .replace("-----BEGIN PUBLIC KEY-----", "")
                .replace("-----END PUBLIC KEY-----", "")
                .replace("\\s".toRegex(), "")
            val key = KeyFactory.getInstance("RSA")
                .generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(pem)))
            val ok = Signature.getInstance("SHA256withRSA").run {
                initVerify(key)
                update(manifestJson.toByteArray(Charsets.UTF_8))
                verify(Base64.getDecoder().decode(sigB64))
            }
            if (!ok) null else WIRE.decodeFromString<Manifest>(manifestJson).files
        } catch (_: Exception) {
            null
        }
    }

    /** Base64 RSA-SHA256 signature over [data], using the Keystore key. */
    fun signRegistration(data: ByteArray): String =
        Base64.getEncoder().encodeToString(keyStore.sign(data))

    companion object {
        /**
         * Every cloud-push payload is snake_case on the wire (`push_id`,
         * `file_id`, `retrieval_key`). Declaring the mapping once here means a
         * future field cannot silently stop matching the server just because
         * Kotlin and JSON disagree about naming.
         *
         * Unknown keys are ignored so a later format version that adds a field
         * still opens: the doorbell is already authenticated by the GCM tag, so
         * an unrecognised field is trustworthy-but-newer rather than hostile.
         */
        private val WIRE = Json {
            namingStrategy = JsonNamingStrategy.SnakeCase
            ignoreUnknownKeys = true
        }

        private const val IV_LENGTH = 12
        private const val GCM_TAG_LENGTH = 16
        private const val GCM_TAG_LENGTH_BITS = 128
    }
}
