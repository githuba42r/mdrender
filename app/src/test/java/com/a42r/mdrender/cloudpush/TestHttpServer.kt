package com.a42r.mdrender.cloudpush

import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A throwaway HTTP/1.1 server for client tests.
 *
 * The JDK ships `com.sun.net.httpserver`, but the Android Gradle plugin's unit
 * test compilation cannot see it (android.jar shadows the JDK's `com.sun.*`),
 * so the plan's suggestion of using it does not hold. Rather than add a test
 * dependency for a dozen lines of protocol, this speaks just enough HTTP for
 * `HttpURLConnection` to talk to.
 */
class TestHttpServer {

    data class Req(
        val method: String,
        val path: String,
        val query: String?,
        val headers: Map<String, String>,
        val body: String,
    ) {
        fun header(name: String): String? =
            headers.entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value
    }

    data class Resp(
        val code: Int = 200,
        val body: ByteArray = ByteArray(0),
        val contentType: String = "application/json",
        val headers: Map<String, String> = emptyMap(),
    ) {
        constructor(code: Int, text: String) : this(code, text.toByteArray(Charsets.UTF_8))
    }

    val port: Int get() = socket.localPort
    val baseUrl: String get() = "http://127.0.0.1:$port"

    /** Every request that arrived, in order. */
    val requests = CopyOnWriteArrayList<Req>()

    private val socket = ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"))
    private val handlers = mutableMapOf<String, (Req) -> Resp>()
    private var running = true

    fun on(path: String, handler: (Req) -> Resp) {
        handlers[path] = handler
    }

    fun start() {
        Thread({ acceptLoop() }, "test-http-server").apply { isDaemon = true }.start()
    }

    fun stop() {
        running = false
        socket.close()
    }

    private fun acceptLoop() {
        while (running) {
            val conn = try {
                socket.accept()
            } catch (_: Exception) {
                return
            }
            // One thread per connection: HttpURLConnection may hold a
            // keep-alive connection open, and a serial loop would deadlock on it.
            Thread({ serve(conn) }, "test-http-conn").apply { isDaemon = true }.start()
        }
    }

    private fun serve(conn: Socket) = conn.use { c ->
        try {
            val input = c.getInputStream()
            val requestLine = readLine(input) ?: return
            val parts = requestLine.split(" ")
            if (parts.size < 2) return
            val method = parts[0]
            val target = parts[1]
            val path = target.substringBefore("?")
            val query = target.substringAfter("?", "").ifEmpty { null }

            val headers = mutableMapOf<String, String>()
            while (true) {
                val line = readLine(input) ?: break
                if (line.isEmpty()) break
                val idx = line.indexOf(':')
                if (idx > 0) headers[line.substring(0, idx).trim()] = line.substring(idx + 1).trim()
            }

            val length = headers.entries
                .firstOrNull { it.key.equals("Content-Length", ignoreCase = true) }
                ?.value?.toIntOrNull() ?: 0
            val body = if (length > 0) {
                String(input.readNBytes(length), Charsets.UTF_8)
            } else {
                ""
            }

            val req = Req(method, path, query, headers, body)
            requests += req

            val resp = handlers[path]?.invoke(req) ?: Resp(404, """{"error":"no handler"}""")
            writeResponse(c, resp)
        } catch (_: Exception) {
            // A test that closed the socket mid-request is not a failure here.
        }
    }

    private fun writeResponse(conn: Socket, resp: Resp) {
        val head = buildString {
            append("HTTP/1.1 ${resp.code} ${reason(resp.code)}\r\n")
            append("Content-Type: ${resp.contentType}\r\n")
            append("Content-Length: ${resp.body.size}\r\n")
            resp.headers.forEach { (k, v) -> append("$k: $v\r\n") }
            // Closing keeps this deliberately simple: no keep-alive bookkeeping.
            append("Connection: close\r\n\r\n")
        }
        conn.getOutputStream().apply {
            write(head.toByteArray(Charsets.US_ASCII))
            write(resp.body)
            flush()
        }
    }

    private fun readLine(input: InputStream): String? {
        val out = ByteArrayOutputStream()
        while (true) {
            val b = input.read()
            if (b == -1) return if (out.size() == 0) null else out.toString(Charsets.US_ASCII.name())
            if (b == '\n'.code) break
            if (b != '\r'.code) out.write(b)
        }
        return out.toString(Charsets.US_ASCII.name())
    }

    private fun reason(code: Int): String = when (code) {
        200 -> "OK"
        400 -> "Bad Request"
        403 -> "Forbidden"
        404 -> "Not Found"
        500 -> "Internal Server Error"
        else -> "Status"
    }
}
