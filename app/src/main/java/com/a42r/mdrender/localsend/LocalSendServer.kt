package com.a42r.mdrender.localsend

import android.util.Log
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import java.io.File

/**
 * Embedded HTTP server implementing the receive side of the LocalSend v2
 * protocol on port 53317. One request thread per connection; prepare-upload
 * blocks until the user accepts or rejects the transfer.
 *
 * @param cacheDir directory used for temp files during upload streaming.
 *   Pass [android.content.Context.cacheDir] from the hosting service.
 */
class LocalSendServer(
    private val prefs: LocalSendPrefs,
    private val sessionManager: LocalSendSessionManager,
    port: Int = LocalSendProtocol.PORT,
    private val certificate: LocalSendCertificate? = null,
    private val cacheDir: File = File(System.getProperty("java.io.tmpdir", "/tmp"))
) : NanoHTTPD(port) {
    private val uploadDir = File(cacheDir, "uploads").also { it.mkdirs() }

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
        val pinValidated = requiredPin.isNotEmpty()
        if (pinValidated) {
            val sentPin = session.parameters["pin"]?.firstOrNull()
            if (sentPin != requiredPin) return error(Response.Status.UNAUTHORIZED, "PIN required")
        }
        // Auto-accept only when a PIN is set (and thus just validated).
        val autoAccept = pinValidated && prefs.autoAccept

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

        // Parse optional MDRender protocol extension (backwards compatible).
        val mdOptions = MDRenderOptions.fromRequestBody(body)
        Log.d(TAG, "MDRender options: folder='${mdOptions.folder}' conflict=${mdOptions.conflict.value}")

        val grant = sessionManager.requestSession(senderAlias, files, autoAccept, mdOptions)
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

        val tempFile = File.createTempFile("upload_${sessionId}_${fileId}_", ".tmp", uploadDir)
        return try {
            // Stream the body straight to a temp file in fixed-size chunks so a
            // large upload (e.g. a 100 MB+ MP3) never occupies the heap as a
            // single ByteArray. The temp file is then imported by the repository,
            // which streams it into app storage.
            val bytesRead = streamBodyToFile(session, sessionId, fileId, tempFile)
            if (bytesRead <= 0L)
                return error(Response.Status.BAD_REQUEST, "Empty body")

            if (sessionManager.receiveFile(sessionId, fileId, token, tempFile)) {
                json(Response.Status.OK, JSONObject())
            } else {
                sessionManager.cancel(sessionId)
                error(Response.Status.FORBIDDEN, "Invalid session/token")
            }
        } catch (e: Exception) {
            sessionManager.cancel(sessionId)
            throw e
        } finally {
            tempFile.delete()
        }
    }

    private fun streamBodyToFile(session: IHTTPSession, sessionId: String, fileId: String, target: File): Long {
        val length = session.headers["content-length"]?.toLongOrNull() ?: 0L
        if (length <= 0L) return 0L

        // Read EXACTLY content-length bytes, then stop. The sender uses
        // Content-Length + keep-alive and keeps the connection open waiting for
        // our response — reading until EOF would deadlock (we'd block forever,
        // never process the file, never respond) until the sender's own timeout
        // closed the socket. Reading exactly the declared length lets us respond
        // as soon as the body is fully received.
        val buffer = ByteArray(STREAM_BUFFER_SIZE)
        var total = 0L
        var lastReported = 0L
        target.outputStream().use { out ->
            while (total < length) {
                val want = minOf(buffer.size.toLong(), length - total).toInt()
                val n = session.inputStream.read(buffer, 0, want)
                if (n < 0) break // early EOF / connection dropped
                if (n == 0) continue
                out.write(buffer, 0, n)
                total += n
                // Report progress periodically so the transfer notification stays
                // live and the inactivity watchdog keeps resetting during a long
                // upload. contentLength is null so the total falls back to the
                // session's recorded file size (avoids Int overflow on >2GiB).
                if (total - lastReported >= PROGRESS_INTERVAL_BYTES) {
                    lastReported = total
                    sessionManager.reportUploadProgress(sessionId, fileId, total, null)
                }
            }
        }
        // Final report so the notification shows the full total and the watchdog
        // is reset right before the (slower) import into app storage.
        if (total > lastReported) {
            sessionManager.reportUploadProgress(sessionId, fileId, total, null)
        }
        return total
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
        private const val STREAM_BUFFER_SIZE = 64 * 1024
        private const val PROGRESS_INTERVAL_BYTES = 1024L * 1024L
    }
}
