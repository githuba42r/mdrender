package com.a42r.mdrender.cloudpush

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Foreground downloader for a verified cloud push.
 *
 * It is started with a [EXTRA_PUSH_ID] that came from a *signed* manifest, not
 * from the FCM message: a doorbell that decrypts only proves someone holding
 * our push key sent something, so the manifest signature is what actually
 * authorises a download.
 *
 * The downloading itself lives in [CloudPushDownloader]; this class is only the
 * Android shell that keeps the work alive and tells the user about it.
 */
@AndroidEntryPoint
class CloudPushDownloadService : Service() {

    @Inject lateinit var manager: CloudPushManager
    @Inject lateinit var downloader: CloudPushDownloader

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val pushId = intent?.getStringExtra(EXTRA_PUSH_ID)
        if (pushId == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(
            NOTIF_ID,
            buildNotification("Preparing downloads…"),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
        )
        scope.launch {
            try {
                val imported = downloader.drain(pushId, cacheDir) { name ->
                    updateNotification("Downloading $name…")
                }
                notifyResult(imported)
            } catch (e: Exception) {
                Log.w(TAG, "CloudPush: download failed (${e.javaClass.simpleName})")
            } finally {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun notifyResult(imported: Int) {
        val text = if (imported == 1) "1 file received in Cloud Push" else "$imported files received in Cloud Push"
        val notification = buildNotification(text)
        getSystemService(NotificationManager::class.java).notify(RESULT_NOTIF_ID, notification)
    }

    private fun updateNotification(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(text))
    }

    private fun buildNotification(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Cloud Push")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID, "Cloud Push downloads", NotificationManager.IMPORTANCE_LOW
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "CloudPushDownload"
        private const val CHANNEL_ID = "cloud_push_downloads"
        private const val NOTIF_ID = 42
        private const val RESULT_NOTIF_ID = 43
        const val EXTRA_PUSH_ID = "push_id"

        fun start(context: Context, pushId: String) {
            context.startForegroundService(
                Intent(context, CloudPushDownloadService::class.java)
                    .putExtra(EXTRA_PUSH_ID, pushId)
            )
        }
    }
}
