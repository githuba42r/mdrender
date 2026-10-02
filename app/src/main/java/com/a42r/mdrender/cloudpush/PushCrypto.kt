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
        /**
         * Folder path relative to root, e.g. "Story/cloud-send-images".
         * Blank means the app's default Cloud Push root. Defaults keep a
         * manifest from a server that predates these options parseable.
         */
        val targetFolder: String = "",
        /** One of ConflictStrategy's wire values; anything else falls back to RENAME. */
        val conflict: String = "rename",
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
     * Verify [manifestJson] against [sigB64] using the server key the phone pinned at
     * pairing, and return the whole manifest only if that check passes.
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
    ): Manifest? {
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
            if (!ok) null else WIRE.decodeFromString<Manifest>(manifestJson)
        } catch (_: Exception) {
            null
        }
    }

    /** Base64 RSA-SHA256 signature over [data], using the Keystore key. */
    fun signRegistration(data: ByteArray): String =
        Base64.getEncoder().encodeToString(keyStore.sign(data))

    /**
     * Proof that this device owns its content public key: the pairing key signs
     * `content:<pubkey>`. A client verifies this against the device's pairing
     * public key before sealing the content key, so the server cannot substitute
     * a content key (design §7c).
     */
    fun signContentKey(contentPublicKeyB64: String): String =
        Base64.getEncoder().encodeToString(
            keyStore.sign("content:$contentPublicKeyB64".toByteArray(Charsets.UTF_8))
        )

    /**
     * Open the client-sealed content key with the content private key. The server
     * only ever held the sealed blob, so it could not open it. Null if it is not
     * addressed to this device's content key.
     */
    fun decryptSealedCek(sealedB64: String): ByteArray? = try {
        keyStore.decryptOaep(Base64.getDecoder().decode(sealedB64))
    } catch (_: Exception) {
        null
    }

    /**
     * Decrypt one file. The client stores `nonce(12) || ciphertext||tag` as an
     * opaque blob, so the nonce travels with the bytes and the server never sees
     * it (design §7a). Null if it does not authenticate.
     */
    fun decryptFile(blob: ByteArray, cek: ByteArray): ByteArray? = try {
        if (blob.size <= CONTENT_NONCE_LENGTH) {
            null
        } else {
            val nonce = blob.copyOfRange(0, CONTENT_NONCE_LENGTH)
            val ciphertext = blob.copyOfRange(CONTENT_NONCE_LENGTH, blob.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(
                Cipher.DECRYPT_MODE,
                SecretKeySpec(cek, "AES"),
                GCMParameterSpec(GCM_TAG_LENGTH_BITS, nonce),
            )
            cipher.doFinal(ciphertext)
        }
    } catch (_: Exception) {
        null
    }

    /** Metadata header of a decrypted push payload (design §7a/D13). */
    @Serializable
    data class EnvelopeHeader(val name: String, val path: String = "")

    /** A decrypted push payload: the real filename, folder, and file bytes. */
    data class PushEnvelope(val name: String, val path: String, val bytes: ByteArray)

    /**
     * Split [plain] — the output of [decryptFile] — into header and file bytes:
     * `u32be(len(header)) || header_json || file_bytes`, where the header
     * carries the original filename and destination folder. They exist only
     * inside the ciphertext: the server stores the blob under an opaque id and
     * never sees them (design §7a, D13). Null when the payload is not a
     * well-formed envelope (truncated length, header past the end, bad JSON).
     */
    fun parseEnvelope(plain: ByteArray): PushEnvelope? = try {
        if (plain.size < 4) {
            null
        } else {
            val headerLen = ((plain[0].toInt() and 0xff) shl 24) or
                ((plain[1].toInt() and 0xff) shl 16) or
                ((plain[2].toInt() and 0xff) shl 8) or
                (plain[3].toInt() and 0xff)
            if (headerLen < 0 || headerLen > plain.size - 4) {
                null
            } else {
                val header = WIRE.decodeFromString<EnvelopeHeader>(
                    String(plain, 4, headerLen, Charsets.UTF_8))
                PushEnvelope(
                    header.name,
                    header.path,
                    plain.copyOfRange(4 + headerLen, plain.size),
                )
            }
        }
    } catch (_: Exception) {
        null
    }

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
        private const val CONTENT_NONCE_LENGTH = 12
        private const val GCM_TAG_LENGTH = 16
        private const val GCM_TAG_LENGTH_BITS = 128
    }
}
