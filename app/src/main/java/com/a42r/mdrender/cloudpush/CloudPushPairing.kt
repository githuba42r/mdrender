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
        val pk: String,
        val token: String,
    )

    data class PairingInfo(
        val serverUrl: String,
        val serverPublicKeyDer: ByteArray,
        val token: String,
    )

    /** Parse the JSON the server's `/pair` page encodes. Null if unusable. */
    fun parse(qrText: String): PairingInfo? = try {
        val qr = WIRE.decodeFromString<PairingQr>(qrText)
        PairingInfo(
            serverUrl = qr.serverUrl.trimEnd('/'),
            serverPublicKeyDer = Base64.getDecoder().decode(qr.pk),
            token = qr.token,
        ).takeIf { it.serverUrl.isNotBlank() && it.token.isNotBlank() }
    } catch (_: Exception) {
        null
    }

    /**
     * The server sends its public key as base64 DER; signature verification
     * wants PEM. Wrapping those same bytes is the whole conversion, and getting
     * it wrong would show up much later as every manifest failing to verify.
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
        /** `v` and `expires` are informational; ignore anything we do not know. */
        val WIRE = Json {
            ignoreUnknownKeys = true
            namingStrategy = JsonNamingStrategy.SnakeCase
        }
    }
}
