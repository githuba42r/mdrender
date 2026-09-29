package com.a42r.mdrender.cloudpush

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import org.mockito.kotlin.mock
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.SecureRandom
import java.security.Signature
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

class PushCryptoTest {
    private val pushKey = ByteArray(32).also { SecureRandom().nextBytes(it) }
    private val crypto = PushCrypto(mock())

    /**
     * Build a doorbell exactly the way the server does: IV = HMAC(push_key,
     * push_id)[:12], plaintext is the canonical JSON, ciphertext||tag is the
     * base64 payload. Reproducing the server's construction here means the test
     * breaks if the wire format changes on either side.
     */
    private fun seal(
        serverUrl: String = "https://push.example.com",
        pushId: String = "push-1",
        challengeKey: String = "ck-abc",
        key: ByteArray = pushKey,
    ): Pair<String, String> {
        val iv = Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(key, "HmacSHA256"))
            doFinal(pushId.toByteArray()).copyOfRange(0, 12)
        }
        // Canonical plaintext: sorted keys, no whitespace, v travels inside.
        val plaintext = """{"challenge_key":"$challengeKey","push_id":"$pushId",""" +
            """"server_url":"$serverUrl","v":1}"""
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, iv))
            doFinal(plaintext.toByteArray())
        }
        return Base64.getEncoder().encodeToString(cipher) to
            Base64.getEncoder().encodeToString(iv)
    }

    @Test
    fun `decryptTrigger returns the doorbell the server sealed`() {
        val (ct, iv) = seal()

        val doorbell = crypto.decryptTrigger(ct, iv, pushKey)

        assertNotNull(doorbell)
        assertEquals("https://push.example.com", doorbell!!.serverUrl)
        assertEquals("push-1", doorbell.pushId)
        assertEquals("ck-abc", doorbell.challengeKey)
    }

    @Test
    fun `decryptTrigger rejects a wrong push key`() {
        val (ct, iv) = seal()

        assertNull(crypto.decryptTrigger(ct, iv, ByteArray(32)))
    }

    @Test
    fun `decryptTrigger rejects a tampered ciphertext`() {
        val (ct, iv) = seal()
        val raw = Base64.getDecoder().decode(ct)
        raw[0] = (raw[0].toInt() xor 0x01).toByte()

        assertNull(crypto.decryptTrigger(Base64.getEncoder().encodeToString(raw), iv, pushKey))
    }

    @Test
    fun `decryptTrigger rejects a tampered IV`() {
        val (ct, iv) = seal()
        val rawIv = Base64.getDecoder().decode(iv)
        rawIv[0] = (rawIv[0].toInt() xor 0x01).toByte()

        assertNull(crypto.decryptTrigger(ct, Base64.getEncoder().encodeToString(rawIv), pushKey))
    }

    @Test
    fun `decryptTrigger returns null rather than throwing on garbage`() {
        assertNull(crypto.decryptTrigger("not-base64!!", "also-not", pushKey))
        assertNull(crypto.decryptTrigger("", "", pushKey))
    }

    @Test
    fun `verifyManifest accepts a validly signed manifest`() {
        val rsa = rsaKeyPair()
        val body = manifestJson()

        val manifest = crypto.verifyManifest(body, sign(rsa, body), pemEncode(rsa.public.encoded))

        assertNotNull(manifest)
        val files = manifest!!.files
        assertEquals(2, files.size)
        assertEquals("notes.md", files[0].name)
        assertEquals("Docs/Reports", files[0].path)
        assertEquals(1234L, files[0].size)
        assertEquals("rk-1", files[0].retrievalKey)
        assertEquals("f1", files[0].fileId)
        assertEquals("other.md", files[1].name)
        // A manifest that predates the options still opens, and lands on the
        // documented defaults rather than on an empty folder.
        assertEquals("", manifest.targetFolder)
        assertEquals("rename", manifest.conflict)
    }

    @Test
    fun `verifyManifest returns the target folder and conflict the sender chose`() {
        val rsa = rsaKeyPair()
        val body = manifestJsonWithOptions("Story/cloud-send-images", "replace")

        val manifest = crypto.verifyManifest(body, sign(rsa, body), pemEncode(rsa.public.encoded))

        assertNotNull(manifest)
        assertEquals("Story/cloud-send-images", manifest!!.targetFolder)
        assertEquals("replace", manifest.conflict)
    }

    @Test
    fun `verifyManifest rejects a retargeted folder`() {
        val rsa = rsaKeyPair()
        val body = manifestJsonWithOptions("Story/cloud-send-images", "replace")
        // The folder is inside the signed bytes, so moving the file elsewhere is
        // exactly as detectable as renaming it.
        val moved = body.replace("Story/cloud-send-images", "Story/cloud-send-images/..")

        assertNull(crypto.verifyManifest(moved, sign(rsa, body), pemEncode(rsa.public.encoded)))
    }

    @Test
    fun `verifyManifest rejects a tampered body`() {
        val rsa = rsaKeyPair()
        val body = manifestJson()
        val sig = sign(rsa, body)

        assertNull(crypto.verifyManifest(body.replace("notes.md", "evil.sh"), sig, pemEncode(rsa.public.encoded)))
    }

    @Test
    fun `verifyManifest rejects a body re-serialised with different key order`() {
        // This is the bug the manifest contract exists to prevent: if the client
        // parsed and re-encoded the manifest, the bytes would differ from the
        // signed ones and verification must fail rather than silently pass.
        val rsa = rsaKeyPair()
        val sig = sign(rsa, manifestJson())
        val reordered = """{"push_id":"push-1","date":"2026-08-29T00:00:00+00:00","files":[""" +
            """{"file_id":"f1","name":"notes.md","path":"Docs/Reports","size":1234,"retrieval_key":"rk-1"},""" +
            """{"file_id":"f2","name":"other.md","path":"","size":10,"retrieval_key":"rk-2"}]}"""

        assertNull(crypto.verifyManifest(reordered, sig, pemEncode(rsa.public.encoded)))
    }

    @Test
    fun `verifyManifest rejects a signature from a different key`() {
        val signer = rsaKeyPair()
        val other = rsaKeyPair()
        val body = manifestJson()

        assertNull(crypto.verifyManifest(body, sign(signer, body), pemEncode(other.public.encoded)))
    }

    @Test
    fun `verifyManifest returns null rather than throwing on malformed input`() {
        val rsa = rsaKeyPair()
        val pem = pemEncode(rsa.public.encoded)
        val body = manifestJson()
        val sig = sign(rsa, body)

        assertNull(crypto.verifyManifest("{not json", sig, pem))
        assertNull(crypto.verifyManifest(body, "!!!", pem))
        assertNull(crypto.verifyManifest(body, sig, "not a pem"))
    }

    private fun rsaKeyPair(): KeyPair =
        KeyPairGenerator.getInstance("RSA").apply { initialize(3072) }.generateKeyPair()

    private fun sign(kp: KeyPair, body: String): String {
        val sig = Signature.getInstance("SHA256withRSA").run {
            initSign(kp.private)
            update(body.toByteArray())
            sign()
        }
        return Base64.getEncoder().encodeToString(sig)
    }

    /** Canonical manifest bytes: sorted keys, no whitespace, exactly as the server emits. */
    private fun manifestJson(): String =
        """{"date":"2026-08-29T00:00:00+00:00","files":[""" +
            """{"file_id":"f1","name":"notes.md","path":"Docs/Reports","retrieval_key":"rk-1","size":1234},""" +
            """{"file_id":"f2","name":"other.md","path":"","retrieval_key":"rk-2","size":10}""" +
            """],"push_id":"push-1"}"""

    /**
     * The same manifest, but carrying the push-level folder and conflict options
     * a sender can now choose. These two tests pin the fact that the options are
     * part of the signed body: a server that added them is signing over them.
     */
    private fun manifestJsonWithOptions(targetFolder: String, conflict: String): String =
        """{"conflict":"$conflict","date":"2026-08-29T00:00:00+00:00","files":[""" +
            """{"file_id":"f1","name":"notes.md","path":"","retrieval_key":"rk-1","size":1234}""" +
            """],"push_id":"push-1","target_folder":"$targetFolder"}"""

    private fun pemEncode(der: ByteArray): String {
        val b64 = Base64.getEncoder().encodeToString(der)
        return buildString {
            append("-----BEGIN PUBLIC KEY-----\n")
            b64.chunked(64).forEach { append(it).append("\n") }
            append("-----END PUBLIC KEY-----\n")
        }
    }
}
