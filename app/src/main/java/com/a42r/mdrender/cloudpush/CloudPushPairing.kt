package com.a42r.mdrender.cloudpush

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNamingStrategy
import java.util.Base64
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Reading the server's pairing QR code.
 *
 * Split out from the ViewModel because this is the one part of pairing that
 * can be wrong in a way the user only discovers much later as a bad-signature
 * failure, so it gets tested directly.
 */
@Singleton
class CloudPushPairing @Inject constructor() {

    @Serializable
    private data class PairingQr(
        val serverUrl: String,
        val token: String,
    )

    data class PairingInfo(
        val serverUrl: String,
        val token: String,
    )

    /**
     * Parse the JSON the server's `/pair` page encodes. Null if unusable.
     *
     * The code carries only the server URL and a one-time token. The server
     * public key is deliberately absent: at ~740 base64 characters for a
     * 3072-bit key it dominated the payload and made the QR too dense to scan
     * comfortably. The token proves the user stood at this server's
     * authenticated pairing page, so the key is fetched from that same server
     * during registration instead, over the TLS connection to the URL named
     * here.
     */
    fun parse(qrText: String): PairingInfo? = try {
        val qr = WIRE.decodeFromString<PairingQr>(qrText)
        PairingInfo(
            serverUrl = qr.serverUrl.trimEnd('/'),
            token = qr.token,
        ).takeIf { it.serverUrl.isNotBlank() && it.token.isNotBlank() }
    } catch (_: Exception) {
        null
    }

    /**
     * The server hands its public key back as base64 DER in the registration
     * response; signature verification wants PEM. Null if the server sent
     * something we cannot read, so a malformed key fails loudly at pairing
     * rather than silently rejecting every manifest later.
     */
    fun pemFromBase64(serverPkB64: String): String? = try {
        if (serverPkB64.isBlank()) null else toPem(Base64.getDecoder().decode(serverPkB64))
    } catch (_: IllegalArgumentException) {
        null
    }

    /**
     * Wrap DER public-key bytes as PEM. Signature verification reads PEM, so
     * this wrapping is the whole conversion, and getting it wrong would show up
     * much later as every manifest failing to verify.
     */
    fun toPem(der: ByteArray): String {
        val b64 = Base64.getEncoder().encodeToString(der)
        return buildString {
            append("-----BEGIN PUBLIC KEY-----\n")
            b64.chunked(64).forEach { append(it).append('\n') }
            append("-----END PUBLIC KEY-----\n")
        }
    }

    private companion object {
        /** `v` is informational; ignore anything we do not know. */
        val WIRE = Json {
            ignoreUnknownKeys = true
            namingStrategy = JsonNamingStrategy.SnakeCase
        }
    }
}
