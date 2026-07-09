package com.a42r.mdrender.localsend

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.a42r.mdrender.MDRenderApplication
import com.a42r.mdrender.R
import com.a42r.mdrender.ui.MainActivity
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Foreground service hosting the LocalSend receiver: HTTP server, multicast
 * discovery, and Accept/Reject notifications for incoming transfers.
 */
@AndroidEntryPoint
class LocalSendService : Service() {

    @Inject lateinit var prefs: LocalSendPrefs
    @Inject lateinit var sessionManager: LocalSendSessionManager

    private var server: LocalSendServer? = null
    private var discovery: LocalSendDiscovery? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannels()
        startInForeground()

        multicastLock = (applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
            .createMulticastLock("mdrender-localsend").apply {
                setReferenceCounted(false)
                acquire()
            }

        // HTTPS with a self-signed cert (LocalSend default; some clients
        // hardcode https). Fall back to plain http if TLS setup fails.
        val certificate = try {
            LocalSendCertificate.getOrCreate(this)
        } catch (e: Exception) {
            Log.w(TAG, "TLS unavailable, serving plain http", e)
            null
        }

        // Prefer the standard port; fall back when another LocalSend instance
        // (e.g. the official app) already holds it. Announcements carry the
        // actual port so clients connect correctly either way.
        var started: LocalSendServer? = null
        for (port in LocalSendProtocol.PORT..LocalSendProtocol.PORT + 10) {
            try {
                started = LocalSendServer(prefs, sessionManager, port, certificate, this.cacheDir).also { it.start() }
                break
            } catch (e: Exception) {
                Log.w(TAG, "Port $port unavailable: ${e.message}")
            }
        }
        if (started == null) {
            Log.e(TAG, "Failed to start LocalSend receiver — no free port")
            stopSelf()
            return
        }
        server = started
        discovery = LocalSendDiscovery(
            prefs, started.listeningPort, started.fingerprint, started.protocolName
        ).also { it.start() }
        Log.i(TAG, "LocalSend receiver active as '${prefs.alias}' " +
            "(${started.protocolName}) on port ${started.listeningPort}")

        // Incoming transfer requests → notification when app is backgrounded.
        scope.launch {
            sessionManager.pendingTransfer.collect { pending ->
                if (pending == null) {
                    NotificationManagerCompat.from(this@LocalSendService).cancel(NOTIF_ID_REQUEST)
                } else if (!MDRenderApplication.instance.isForeground) {
                    postTransferRequestNotification(pending)
                }
            }
        }
        // Completion notice when backgrounded — suppressed under auto-accept,
        // where silent, unattended transfers shouldn't spam notifications.
        scope.launch {
            sessionManager.lastCompleted.collect { message ->
                if (message != null && !MDRenderApplication.instance.isForeground && !prefs.autoAccept) {
                    postCompletedNotification(message)
                }
            }
        }
        // Upload progress notification — shows a progress bar and file name
        // during active transfers.
        scope.launch {
            sessionManager.transferProgress.collect { progress ->
                if (progress != null) {
                    updateTransferProgressNotification(progress)
                } else {
                    NotificationManagerCompat.from(this@LocalSendService).cancel(NOTIF_ID_PROGRESS)
                }
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_ACCEPT -> intent.getStringExtra(EXTRA_SESSION_ID)?.let { sessionManager.accept(it) }
            ACTION_REJECT -> intent.getStringExtra(EXTRA_SESSION_ID)?.let { sessionManager.reject(it) }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        server?.stop()
        discovery?.stop()
        runCatching { multicastLock?.release() }
        NotificationManagerCompat.from(this).cancel(NOTIF_ID_REQUEST)
        super.onDestroy()
    }

    private fun startInForeground() {
        val openApp = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_stat_localsend)
            .setContentTitle("LocalSend receiver active")
            .setContentText("Visible to other devices as \"${prefs.alias}\"")
            .setOngoing(true)
            .setContentIntent(openApp)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID_STATUS, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID_STATUS, notification)
        }
    }

    private fun postTransferRequestNotification(pending: PendingTransfer) {
        val acceptIntent = serviceAction(ACTION_ACCEPT, pending.sessionId, 1)
        val rejectIntent = serviceAction(ACTION_REJECT, pending.sessionId, 2)
        val fileSummary = if (pending.files.size == 1) pending.files.first().fileName
            else "${pending.files.size} files"

        val notification = NotificationCompat.Builder(this, CHANNEL_TRANSFERS)
            .setSmallIcon(R.drawable.ic_stat_localsend)
            .setContentTitle("Incoming files from ${pending.senderAlias}")
            .setContentText(fileSummary)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .addAction(0, "Accept", acceptIntent)
            .addAction(0, "Reject", rejectIntent)
            .build()
        notifySafely(NOTIF_ID_REQUEST, notification)
    }

    private fun postCompletedNotification(message: String) {
        val openApp = PendingIntent.getActivity(
            this, 3, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_TRANSFERS)
            .setSmallIcon(R.drawable.ic_stat_localsend)
            .setContentTitle("LocalSend")
            .setContentText(message)
            .setAutoCancel(true)
            .setContentIntent(openApp)
            .build()
        notifySafely(NOTIF_ID_COMPLETED, notification)
    }

    private fun updateTransferProgressNotification(progress: TransferProgress) {
        val pct = if (progress.totalBytes > 0)
            ((progress.receivedBytes * 100) / progress.totalBytes).toInt() else 0
        val body = if (progress.totalFiles > 1)
            "File ${progress.fileIndex} of ${progress.totalFiles} — ${progress.fileName} ($pct%)"
        else
            "${progress.fileName} — ${formatSize(progress.receivedBytes)} / ${formatSize(progress.totalBytes)}"

        val notification = NotificationCompat.Builder(this, CHANNEL_TRANSFERS)
            .setSmallIcon(R.drawable.ic_stat_localsend)
            .setContentTitle("Receiving files")
            .setContentText(body)
            .setProgress(100, pct, false)
            .setOngoing(true)
            .build()
        notifySafely(NOTIF_ID_PROGRESS, notification)
    }

    private fun serviceAction(action: String, sessionId: String, requestCode: Int): PendingIntent =
        PendingIntent.getService(
            this, requestCode,
            Intent(this, LocalSendService::class.java)
                .setAction(action)
                .putExtra(EXTRA_SESSION_ID, sessionId),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

    private fun notifySafely(id: Int, notification: Notification) {
        try {
            NotificationManagerCompat.from(this).notify(id, notification)
        } catch (e: SecurityException) {
            Log.w(TAG, "Notification permission not granted", e)
        }
    }

    private fun createChannels() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_STATUS, "LocalSend status", NotificationManager.IMPORTANCE_LOW)
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_TRANSFERS, "LocalSend transfers", NotificationManager.IMPORTANCE_HIGH)
        )
    }

    private fun formatSize(bytes: Long): String {
        if (bytes < 1024) return "$bytes B"
        val units = arrayOf("KiB", "MiB", "GiB")
        var value = bytes.toDouble() / 1024
        for (unit in units) {
            if (value < 1024) return "%.1f %s".format(value, unit)
            value /= 1024
        }
        return "%.1f TiB".format(value)
    }

    companion object {
        private const val TAG = "LocalSendService"
        private const val CHANNEL_STATUS = "localsend_status"
        private const val CHANNEL_TRANSFERS = "localsend_transfers"
        private const val NOTIF_ID_STATUS = 100
        private const val NOTIF_ID_REQUEST = 101
        private const val NOTIF_ID_COMPLETED = 102
        private const val NOTIF_ID_PROGRESS = 103
        const val ACTION_ACCEPT = "com.a42r.mdrender.localsend.ACCEPT"
        const val ACTION_REJECT = "com.a42r.mdrender.localsend.REJECT"
        const val EXTRA_SESSION_ID = "session_id"

        fun start(context: Context) {
            context.startForegroundService(Intent(context, LocalSendService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, LocalSendService::class.java))
        }
    }
}
