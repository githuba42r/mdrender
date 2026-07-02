package com.a42r.mdrender.localsend

import android.util.Log
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject

/**
 * Embedded HTTP server implementing the receive side of the LocalSend v2
 * protocol on port 53317. One request thread per connection; prepare-upload
 * blocks until the user accepts or rejects the transfer.
 */
class LocalSendServer(
    private val prefs: LocalSendPrefs,
    private val sessionManager: LocalSendSessionManager,
    port: Int = LocalSendProtocol.PORT,
    private val certificate: LocalSendCertificate? = null
) : NanoHTTPD(port) {

    init {
        certificate?.let {
            makeSecure(makeSSLSocketFactory(it.keyStore, it.keyManagerFactory), null)
        }
    }

    val fingerprint: String get() = certificate?.fingerprint ?: prefs.fingerprint
    val protocolName: String get() = if (certificate != null) "https" else "http"

    private fun deviceInfo() =
        DeviceInfo(prefs.alias, fingerprint, listeningPort, protocolName)

    override fun serve(session: IHTTPSession): Response {
        return try {
            when (session.uri) {
                LocalSendProtocol.PATH_INFO -> json(Response.Status.OK, deviceInfo().toJson())
                LocalSendProtocol.PATH_REGISTER -> handleRegister(session)
                LocalSendProtocol.PATH_PREPARE_UPLOAD -> handlePrepareUpload(session)
                LocalSendProtocol.PATH_UPLOAD -> handleUpload(session)
                LocalSendProtocol.PATH_CANCEL -> handleCancel(session)
                else -> error(Response.Status.NOT_FOUND, "Unknown endpoint")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error serving ${session.uri}", e)
            error(Response.Status.INTERNAL_ERROR, "Internal error")
        }
    }

    private fun handleRegister(session: IHTTPSession): Response {
        readBody(session) // consume; sender's info is not needed beyond logging
        return json(Response.Status.OK, deviceInfo().toJson())
    }

    private fun handlePrepareUpload(session: IHTTPSession): Response {
        if (session.method != Method.POST) return error(Response.Status.METHOD_NOT_ALLOWED, "POST required")

        // PIN gate
        val requiredPin = prefs.pin
        if (requiredPin.isNotEmpty()) {
            val sentPin = session.parameters["pin"]?.firstOrNull()
            if (sentPin != requiredPin) return error(Response.Status.UNAUTHORIZED, "PIN required")
        }

        val body = JSONObject(String(readBody(session), Charsets.UTF_8))
        val senderAlias = body.optJSONObject("info")?.optString("alias") ?: "Unknown device"
        val filesJson = body.getJSONObject("files")
        val files = filesJson.keys().asSequence()
            .map { IncomingFile.fromJson(filesJson.getJSONObject(it)) }
            .toList()
        if (files.isEmpty()) return error(Response.Status.BAD_REQUEST, "No files")

        if (sessionManager.hasBlockingSession()) {
            return error(Response.Status.CONFLICT, "Blocked by another session")
        }
        Log.d(TAG, "prepare-upload from $senderAlias: ${files.size} file(s)")

        val grant = sessionManager.requestSession(senderAlias, files)
            ?: return error(Response.Status.FORBIDDEN, "Rejected")

        val response = JSONObject().apply {
            put("sessionId", grant.sessionId)
            put("files", JSONObject().apply {
                grant.tokens.forEach { (fileId, token) -> put(fileId, token) }
            })
        }
        return json(Response.Status.OK, response)
    }

    private fun handleUpload(session: IHTTPSession): Response {
        if (session.method != Method.POST) return error(Response.Status.METHOD_NOT_ALLOWED, "POST required")
        val sessionId = session.parameters["sessionId"]?.firstOrNull()
            ?: return error(Response.Status.BAD_REQUEST, "Missing sessionId")
        val fileId = session.parameters["fileId"]?.firstOrNull()
            ?: return error(Response.Status.BAD_REQUEST, "Missing fileId")
        val token = session.parameters["token"]?.firstOrNull()
            ?: return error(Response.Status.BAD_REQUEST, "Missing token")

        val bytes = readBody(session)
        return if (sessionManager.receiveFile(sessionId, fileId, token, bytes)) {
            json(Response.Status.OK, JSONObject())
        } else {
            error(Response.Status.FORBIDDEN, "Invalid session/token")
        }
    }

    private fun handleCancel(session: IHTTPSession): Response {
        session.parameters["sessionId"]?.firstOrNull()?.let { sessionManager.cancel(it) }
        return json(Response.Status.OK, JSONObject())
    }

    private fun readBody(session: IHTTPSession): ByteArray {
        val length = session.headers["content-length"]?.toIntOrNull() ?: 0
        if (length <= 0) return ByteArray(0)
        val buffer = ByteArray(length)
        var read = 0
        while (read < length) {
            val n = session.inputStream.read(buffer, read, length - read)
            if (n < 0) break
            read += n
        }
        return if (read == length) buffer else buffer.copyOf(read)
    }

    private fun json(status: Response.Status, body: JSONObject): Response =
        newFixedLengthResponse(status, "application/json", body.toString())

    private fun error(status: Response.Status, message: String): Response =
        newFixedLengthResponse(status, "application/json", JSONObject().put("message", message).toString())

    companion object {
        private const val TAG = "LocalSendServer"
    }
}
