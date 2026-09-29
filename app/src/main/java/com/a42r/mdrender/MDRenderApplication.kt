package com.a42r.mdrender

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.view.WindowManager
import com.a42r.mdrender.audio.AudioPlayerState
import com.a42r.mdrender.cloudpush.CloudPushDownloadService
import com.a42r.mdrender.cloudpush.CloudPushManager
import com.a42r.mdrender.cloudpush.PushClient
import com.a42r.mdrender.cloudpush.PushServerConfig
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.security.ScreenOffReceiver
import com.a42r.mdrender.share.ShareOutManager
import com.a42r.mdrender.ui.ShareReceiverActivity
import dagger.hilt.android.HiltAndroidApp
import java.lang.ref.WeakReference
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import kotlin.concurrent.thread
import kotlinx.coroutines.runBlocking

@HiltAndroidApp
class MDRenderApplication : Application() {

    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var appLock: AppLock
    @Inject lateinit var shareOutManager: ShareOutManager
    @Inject lateinit var audioPlayerState: AudioPlayerState
    @Inject lateinit var cloudPushManager: CloudPushManager
    @Inject lateinit var pushConfig: PushServerConfig
    @Inject lateinit var pushClient: PushClient

    @Volatile
    var isForeground: Boolean = false
        private set

    companion object {
        lateinit var instance: MDRenderApplication
            private set
    }

    private var startedActivities = 0
    private var foregroundActivity: WeakReference<Activity>? = null
    private lateinit var screenOffReceiver: ScreenOffReceiver

    /** True when the audio player has a file loaded. */
    private fun isAudioActive(): Boolean = audioPlayerState.info.value.fileId != 0L

    private fun isTransient(activity: Activity): Boolean = activity is ShareReceiverActivity

    @Volatile
    private var registrationCheckDue = AtomicBoolean(true)

    /**
     * Ask the paired server once per foreground whether it still knows us.
     *
     * There is no polling on purpose: a push that fails because the server
     * dropped us is indistinguishable from one that never arrived, so the
     * cheapest honest moment to find out is when the user is already looking
     * at the app.
     */
    private fun checkPushRegistration() {
        if (!pushConfig.isPaired) return
        if (!registrationCheckDue.compareAndSet(true, false)) return
        thread {
            val known = runBlocking { pushClient.checkRegistration(pushConfig) }
            cloudPushManager.setReRegistrationNeeded(!known)
            // Allow another check next time the app comes back to the front.
            registrationCheckDue.set(true)
        }
    }

    private fun cleanupOrphanedFiles() {
        val prefs = getSharedPreferences("mdrender_cleanup", MODE_PRIVATE)
        val cleanedVersion = prefs.getInt("db_version", 0)
        if (cleanedVersion >= 7) return
        val dbFile = getDatabasePath("mdrender.db")
        if (!dbFile.exists()) return
        try {
            val db = android.database.sqlite.SQLiteDatabase.openDatabase(
                dbFile.absolutePath, null, android.database.sqlite.SQLiteDatabase.OPEN_READONLY
            )
            val paths = mutableSetOf<String>()
            val cursor = db.rawQuery("SELECT storage_path FROM files WHERE storage_path IS NOT NULL", null)
            while (cursor.moveToNext()) {
                cursor.getString(0)?.let { paths.add(it) }
            }
            cursor.close()
            db.close()
            val encryptedDir = java.io.File(filesDir, "encrypted")
            val plainDir = java.io.File(filesDir, "plain")
            fun cleanDir(dir: java.io.File) {
                if (!dir.exists()) return
                dir.listFiles()?.forEach { file ->
                    if (!paths.contains(file.name)) file.delete()
                }
            }
            cleanDir(encryptedDir)
            cleanDir(plainDir)
            prefs.edit().putInt("db_version", 7).apply()
        } catch (_: Exception) {
            // DB not yet open — will retry on next launch
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        thread { shareOutManager.clearShareCache() }
        thread { cleanupOrphanedFiles() }

        // The manager only fires this once a manifest has been verified, so the
        // download service is never started by an unverified doorbell.
        cloudPushManager.onPushReady { pushId ->
            CloudPushDownloadService.start(this, pushId)
        }

        screenOffReceiver = ScreenOffReceiver().also {
            it.onScreenOff = {
                appLock.onBackground()
                foregroundActivity?.get()?.let { activity ->
                    activity.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    if (!isAudioActive()) activity.finishAndRemoveTask()
                }
            }
        }
        registerReceiver(screenOffReceiver, ScreenOffReceiver.FILTER)

        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityStarted(activity: Activity) {
                if (!isTransient(activity)) startedActivities++
            }
            override fun onActivityResumed(activity: Activity) {
                if (!isTransient(activity)) {
                    isForeground = true
                    foregroundActivity = WeakReference(activity)
                    checkPushRegistration()
                }
            }
            override fun onActivityPaused(activity: Activity) {
                if (!isTransient(activity)) {
                    if (activity.isChangingConfigurations) return
                    // A translucent system activity on top of us (e.g. the
                    // runtime camera-permission prompt) pauses us WITHOUT
                    // stopping us, so a pause alone does NOT mean the user left
                    // the app. Only secure the window here — it must be set
                    // before the recents snapshot. The relock + task removal
                    // live in onActivityStopped, which a translucent overlay
                    // never triggers.
                    if (isForeground) {
                        activity.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    }
                    isForeground = false
                }
            }
            override fun onActivityStopped(activity: Activity) {
                if (isTransient(activity)) return
                startedActivities = (startedActivities - 1).coerceAtLeast(0)
                if (startedActivities > 0) return
                if (activity.isChangingConfigurations) return
                // Every activity stopped => the app is truly backgrounded. A
                // translucent overlay only pauses us, so this fires only on a
                // real background transition, never on an in-app/system dialog.
                val didLock = appLock.onBackground()
                if (didLock && !isAudioActive()) {
                    (foregroundActivity?.get() ?: activity).finishAndRemoveTask()
                }
            }
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }
}
