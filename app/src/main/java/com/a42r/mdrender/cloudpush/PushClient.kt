package com.a42r.mdrender.cloudpush

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.Base64
import javax.inject.Inject
import javax.inject.Singleton

/**
 * HTTP access to the push server.
 *
 * Every credential travels in a JSON request body, never in the query string:
 * query strings end up in proxy and server access logs. Device calls are POSTs
 * so nothing sensitive would land there even by accident.
 */
@Singleton
class PushClient @Inject constructor() {

    /** A fetched manifest: the exact signed bytes plus the signature covering them. */
    data class FetchedManifest(val body: String, val signature: String)

    /**
     * What the server hands back once a device is registered: the credential it
     * will authenticate with, and the server's public key so this device can
     * verify manifest signatures.
     */
    data class Registered(val deviceAuth: String, val serverPublicKeyB64: String)

    /**
     * Register this device and its freshly negotiated doorbell key.
     *
     * [crypto] signs a proof binding the device secret, name, FCM token, public
     * key, and the push key. The push key is inside the signature, so an attacker
     * cannot substitute a doorbell key of their own and have the server accept it.
     *
     * [serverUrl] is passed in rather than read from [config] so a caller can
     * register against a candidate server before committing anything to its
     * stored configuration; [config] still supplies the local secrets.
     */
    suspend fun registerDevice(
        serverUrl: String,
        config: PushServerConfig,
        crypto: PushCrypto,
        publicKeySpkiDer: ByteArray,
        fcmToken: String,
        pairingToken: String,
        contentPublicKeyB64: String? = null,
        contentProofB64: String? = null,
    ): Result<Registered> = call {
        val publicKeyB64 = Base64.getEncoder().encodeToString(publicKeySpkiDer)
        val pushKeyB64 = config.pushKeyB64
        val digest = MessageDigest.getInstance("SHA-256").digest(
            registrationProofInput(
                config.deviceSecret, config.deviceName, fcmToken, publicKeyB64, pushKeyB64
            )
        )
        val body = buildJsonObject {
            put("device_secret", config.deviceSecret)
            put("device_name", config.deviceName)
            put("fcm_token", fcmToken)
            put("public_key", publicKeyB64)
            put("push_key", pushKeyB64)
            put("pairing_token", pairingToken)
            put("sig", crypto.signRegistration(digest))
            // Server-enforced encryption (design §7b): register the content key
            // and the pairing-key proof so clients can seal to it (design §7c).
            if (contentPublicKeyB64 != null) {
                put("content_pubkey", contentPublicKeyB64)
            }
            if (contentProofB64 != null) {
                put("content_proof", contentProofB64)
            }
        }
        val response = postJson(serverUrl, "/api/register-device", body)
        if (response.code != 200) response.fail("register-device")
        val json = response.json()
        val deviceAuth = json["device_auth"]?.jsonPrimitive?.content
            ?: throw IOException("server did not return device_auth")
        // Refuse a registration that hands back no key: without it every
        // manifest would fail to verify, and failing here says so plainly.
        val serverPublicKeyB64 = json["server_pk"]?.jsonPrimitive?.content
            ?: throw IOException("server did not return its public key")
        Registered(deviceAuth, serverPublicKeyB64)
    }

    /** The server's encryption policy (design §7b). Defaults to "off". */
    suspend fun fetchPolicy(serverUrl: String): Result<String> = call {
        val connection = URL(serverUrl.trimEnd('/') + "/api/server/policy")
            .openConnection() as HttpURLConnection
        connection.requestMethod = "GET"
        connection.connectTimeout = CONNECT_TIMEOUT_MS
        connection.readTimeout = READ_TIMEOUT_MS
        try {
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.use { String(it.readBytes(), Charsets.UTF_8) } ?: ""
            if (code != 200) throw PushHttpException(code, "policy", body.take(200))
            Json.parseToJsonElement(body).jsonObject["encryption"]?.jsonPrimitive?.content
                ?: "off"
        } finally {
            connection.disconnect()
        }
    }

    /** Fetch the client-sealed content key (opaque; design §7a). */
    suspend fun fetchSealedCek(config: PushServerConfig): Result<String> = call {
        val response = postJson(config.serverUrl, "/api/device/content-key", credentials(config) {})
        if (response.code != 200) response.fail("content key")
        response.json()["sealed_cek"]?.jsonPrimitive?.content
            ?: throw IOException("no sealed content key")
    }

    /** Tell the server this device's FCM token changed. */
    suspend fun rotateToken(config: PushServerConfig, newFcmToken: String): Result<Unit> = call {
        val body = credentials(config) { put("fcm_token", newFcmToken) }
        postJson(config.serverUrl, "/api/register-device", body).requireOk("rotate token")
    }

    /** Tell the server this device's display name changed. */
    suspend fun renameDevice(config: PushServerConfig, newName: String): Result<Unit> = call {
        val body = credentials(config) { put("device_name", newName) }
        postJson(config.serverUrl, "/api/register-device", body).requireOk("rename device")
    }

    /**
     * Exchange a doorbell's challenge key for the signed manifest.
     *
     * The body is returned verbatim. The signature covers those exact bytes, so
     * re-serialising the parsed object would break verification — which is why
     * the server sends the signed bytes as the whole response body.
     */
    suspend fun fetchManifest(doorbell: PushCrypto.Doorbell): Result<FetchedManifest> = call {
        val body = buildJsonObject { put("challenge_key", doorbell.challengeKey) }
        val response = postJson(doorbell.serverUrl, "/api/push/${doorbell.pushId}/manifest", body)
        if (response.code != 200) response.fail("fetch manifest")
        val signature = response.header(MANIFEST_SIGNATURE_HEADER)
            ?: throw IOException("manifest response carried no $MANIFEST_SIGNATURE_HEADER header")
        FetchedManifest(response.body, signature)
    }

    /**
     * Stream a file straight to [dest] without buffering the whole thing in
     * memory. Nothing is moved into place unless the server accepted the key, and
     * the bytes land under a temp name first so a killed process cannot leave a
     * truncated file that looks complete.
     */
    suspend fun downloadFile(
        config: PushServerConfig,
        fileId: String,
        key: String,
        dest: File,
    ): Result<Unit> = call {
        val body = buildJsonObject { put("key", key) }
        val connection = open(config.serverUrl, "/api/push/$fileId/download", body)
        try {
            val code = connection.responseCode
            if (code != 200) {
                val message = connection.errorStream?.use {
                    String(it.readBytes(), Charsets.UTF_8)
                }.orEmpty()
                throw PushHttpException(code, "download", message.take(200))
            }
            dest.parentFile?.mkdirs()
            val tmp = File(dest.parentFile, dest.name + ".part")
            try {
                connection.inputStream.use { input ->
                    tmp.outputStream().use { output -> input.copyTo(output) }
                }
                if (!tmp.renameTo(dest)) {
                    tmp.copyTo(dest, overwrite = true)
                    tmp.delete()
                }
            } catch (e: Exception) {
                tmp.delete()
                throw e
            }
        } finally {
            connection.disconnect()
        }
    }

    /** Acknowledge receipt so a later manifest stops offering the file. */
    suspend fun ackReceived(config: PushServerConfig, fileId: String, key: String): Result<Unit> = call {
        val body = buildJsonObject { put("key", key) }
        postJson(config.serverUrl, "/api/push/$fileId/received", body).requireOk("ack")
    }

    /** True when the server still knows this device; false means re-pair. */
    suspend fun checkRegistration(config: PushServerConfig): Boolean = call {
        postJson(config.serverUrl, "/api/device/status", credentials(config) {}).code == 200
    }.getOrDefault(false)

    /**
     * The server's verdict on this device: `true` still paired, `false` the
     * server no longer knows it (404), `null` unreachable/undecided. Callers that
     * clear local pairing must only do so on `false` — clearing on `null` would
     * wipe a perfectly good pairing just because the phone was offline.
     */
    suspend fun verifyRegistration(config: PushServerConfig): Boolean? = call {
        postJson(config.serverUrl, "/api/device/status", credentials(config) {}).code
    }.fold(
        onSuccess = { code -> if (code == 200) true else if (code == 404) false else null },
        onFailure = { null },
    )

    /** The exact bytes the server signs at registration; kept here so both sides agree. */
    fun registrationProofInput(
        deviceSecret: String,
        deviceName: String,
        fcmToken: String,
        publicKeyB64: String,
        pushKeyB64: String,
    ): ByteArray = "$deviceSecret$deviceName$fcmToken$publicKeyB64$pushKeyB64".toByteArray()

    // --- plumbing -----------------------------------------------------------

    private suspend fun <T> call(block: () -> T): Result<T> = withContext(Dispatchers.IO) {
        try {
            Result.success(block())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    private fun credentials(
        config: PushServerConfig,
        extra: JsonObjectBuilder.() -> Unit = {},
    ): JsonObject = buildJsonObject {
        put("device_secret", config.deviceSecret)
        put("device_auth", config.deviceAuth)
        extra()
    }

    private fun postJson(serverUrl: String, path: String, body: JsonObject): Response {
        val connection = open(serverUrl, path, body)
        try {
            val code = connection.responseCode
            // A non-2xx puts the body on the error stream, not the input stream.
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
            return Response(
                code,
                String(bytes, Charsets.UTF_8),
                connection.headerFields.orEmpty()
                    .filterKeys { it != null }
                    .mapKeys { (k, _) -> k!! },
            )
        } finally {
            connection.disconnect()
        }
    }

    private fun open(serverUrl: String, path: String, body: JsonObject): HttpURLConnection {
        if (serverUrl.isBlank()) throw IOException("no server URL: not paired")
        val connection = URL(serverUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json")
            val payload = Json.encodeToString(JsonObject.serializer(), body).toByteArray()
            connection.setFixedLengthStreamingMode(payload.size)
            connection.outputStream.use { it.write(payload) }
            connection
        } catch (e: Exception) {
            connection.disconnect()
            throw e
        }
    }

    private fun Response.requireOk(what: String) {
        if (code != 200) fail(what)
    }

    private fun Response.fail(what: String): Nothing =
        throw PushHttpException(code, what, body.take(200))

    private fun Response.json(): JsonObject =
        runCatching { Json.parseToJsonElement(body).jsonObject }.getOrElse {
            throw IOException("server sent a non-JSON body: ${body.take(200)}")
        }

    private fun Response.header(name: String): String? =
        headers.entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value?.firstOrNull()

    private data class Response(
        val code: Int,
        val body: String,
        val headers: Map<String, List<String>>,
    )

    companion object {
        const val MANIFEST_SIGNATURE_HEADER = "X-Push-Manifest-Signature"
        private const val CONNECT_TIMEOUT_MS = 15_000
        private const val READ_TIMEOUT_MS = 60_000
    }
}

/**
 * A server response with a non-success status.
 *
 * The code is carried rather than only formatted into the message so callers
 * can tell "this device is no longer registered" (401/404) apart from a
 * transient failure, which is the difference between prompting the user to
 * re-pair and quietly retrying.
 */
class PushHttpException(
    val code: Int,
    val what: String,
    val detail: String,
) : java.io.IOException("$what failed: HTTP $code $detail") {

    /** The server has forgotten this device, or rejects its credentials. */
    val isUnregistered: Boolean get() = code == 401 || code == 404
}
