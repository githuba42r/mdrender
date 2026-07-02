package com.a42r.mdrender.localsend

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.MulticastSocket

/**
 * LocalSend v2 UDP multicast discovery: announces this device on start and
 * answers other devices' announcements so they list us as a send target.
 */
class LocalSendDiscovery(
    private val prefs: LocalSendPrefs,
    private val httpPort: Int = LocalSendProtocol.PORT,
    private val fingerprint: String = prefs.fingerprint,
    private val protocol: String = "http"
) {
    private var scope: CoroutineScope? = null
    private var socket: MulticastSocket? = null

    fun start() {
        stop()
        val newScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        scope = newScope
        newScope.launch { listen() }
        newScope.launch { announce() }
    }

    fun stop() {
        scope?.cancel()
        scope = null
        runCatching { socket?.close() }
        socket = null
    }

    private fun myInfoJson(announce: Boolean): ByteArray =
        DeviceInfo(prefs.alias, fingerprint, httpPort, protocol).toJson(announce).toString()
            .toByteArray(Charsets.UTF_8)

    /** Broadcast our presence so already-running clients pick us up. */
    private fun announce() {
        try {
            val group = InetAddress.getByName(LocalSendProtocol.MULTICAST_GROUP)
            DatagramSocket().use { sender ->
                val data = myInfoJson(announce = true)
                repeat(3) {
                    sender.send(DatagramPacket(data, data.size, group, LocalSendProtocol.PORT))
                    Thread.sleep(500)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Announce failed: ${e.message}")
        }
    }

    private fun listen() {
        try {
            val group = InetAddress.getByName(LocalSendProtocol.MULTICAST_GROUP)
            val sock = MulticastSocket(LocalSendProtocol.PORT)
            socket = sock
            @Suppress("DEPRECATION")
            sock.joinGroup(group)
            val buffer = ByteArray(64 * 1024)
            while (scope?.isActive == true) {
                val packet = DatagramPacket(buffer, buffer.size)
                sock.receive(packet)
                handlePacket(packet)
            }
        } catch (e: Exception) {
            if (scope?.isActive == true) Log.w(TAG, "Discovery listener stopped: ${e.message}")
        }
    }

    private fun handlePacket(packet: DatagramPacket) {
        try {
            val json = JSONObject(String(packet.data, packet.offset, packet.length, Charsets.UTF_8))
            if (json.optString("fingerprint") == fingerprint) return // our own packet
            if (!json.optBoolean("announce", false)) return

            // Respond directly to the announcer so it lists us immediately.
            val data = myInfoJson(announce = false)
            DatagramSocket().use { sender ->
                sender.send(DatagramPacket(data, data.size, packet.address, packet.port))
                // Also answer on the multicast group for clients that only listen there.
                val group = InetAddress.getByName(LocalSendProtocol.MULTICAST_GROUP)
                sender.send(DatagramPacket(data, data.size, group, LocalSendProtocol.PORT))
            }
        } catch (_: Exception) {
            // Not a LocalSend packet — ignore.
        }
    }

    companion object {
        private const val TAG = "LocalSendDiscovery"
    }
}
