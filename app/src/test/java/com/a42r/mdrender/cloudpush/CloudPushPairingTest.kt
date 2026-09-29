package com.a42r.mdrender.cloudpush

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class CloudPushPairingTest {

    private val pairing = CloudPushPairing()

    // A real 3072-bit RSA public key, DER, as the server would send it.
    private val der: ByteArray = run {
        val kp = java.security.KeyPairGenerator.getInstance("RSA")
            .apply { initialize(3072) }
            .generateKeyPair()
        kp.public.encoded
    }

    private fun qr(
        url: String = "https://push.example.com",
        pk: String = Base64.getEncoder().encodeToString(der),
        token: String = "tok-1",
    ) = """{"v":1,"server_url":"$url","pk":"$pk","token":"$token","expires":"2026-01-01T00:00:00+00:00"}"""

    @Test
    fun `parses a valid pairing code`() {
        val info = pairing.parse(qr())

        assertEquals("https://push.example.com", info?.serverUrl)
        assertEquals("tok-1", info?.token)
        assertTrue("key must survive the round trip", der.contentEquals(info!!.serverPublicKeyDer))
    }

    @Test
    fun `strips a trailing slash so paths do not double up`() {
        assertEquals(
            "https://push.example.com",
            pairing.parse(qr(url = "https://push.example.com/"))?.serverUrl,
        )
    }

    @Test
    fun `wraps the same bytes as PEM, which is what signature verification reads`() {
        val pem = pairing.toPem(der)
        val body = pem
            .removePrefix("-----BEGIN PUBLIC KEY-----\n")
            .removeSuffix("-----END PUBLIC KEY-----\n")
            .replace("\n", "")

        assertTrue(der.contentEquals(Base64.getDecoder().decode(body)))
    }

    @Test
    fun `PEM lines are wrapped to 64 characters`() {
        val lines = pairing.toPem(der).trim().lines()
        assertEquals("-----BEGIN PUBLIC KEY-----", lines.first())
        assertEquals("-----END PUBLIC KEY-----", lines.last())
        lines.drop(1).dropLast(1).forEach { assertTrue("line too long: ${it.length}", it.length <= 64) }
    }

    @Test
    fun `rejects a code that is not JSON`() {
        assertNull(pairing.parse("not a qr code"))
        assertNull(pairing.parse(""))
    }

    @Test
    fun `rejects a code missing any required field`() {
        assertNull(pairing.parse("""{"v":1,"pk":"${Base64.getEncoder().encodeToString(der)}"}"""))
        assertNull(pairing.parse("""{"v":1,"server_url":"https://x","token":"t"}"""))
        assertNull(pairing.parse("""{"v":1,"server_url":"https://x","pk":"AAAA"}"""))
        assertNull(pairing.parse("""{"v":1,"server_url":"  ","pk":"AAAA","token":"t"}"""))
    }

    @Test
    fun `rejects a key that is not valid base64`() {
        assertNull(pairing.parse(qr(pk = "not!base64!")))
    }

    @Test
    fun `two different keys do not produce the same PEM`() {
        val other = java.security.KeyPairGenerator.getInstance("RSA")
            .apply { initialize(3072) }
            .generateKeyPair()
            .public
            .encoded

        assertNotEquals(pairing.toPem(der), pairing.toPem(other))
    }
}
