package com.a42r.mdrender

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.view.WindowManager
import com.a42r.mdrender.audio.AudioPlayerState
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.security.ScreenOffReceiver
import com.a42r.mdrender.share.ShareOutManager
import com.a42r.mdrender.ui.ShareReceiverActivity
import dagger.hilt.android.HiltAndroidApp
import java.lang.ref.WeakReference
import javax.inject.Inject
import kotlin.concurrent.thread

@HiltAndroidApp
class MDRenderApplication : Application() {

    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var appLock: AppLock
    @Inject lateinit var shareOutManager: ShareOutManager
    @Inject lateinit var audioPlayerState: AudioPlayerState

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
                }
            }
            override fun onActivityPaused(activity: Activity) {
                if (!isTransient(activity)) {
                    if (activity.isChangingConfigurations) return
                    if (isForeground && startedActivities <= 1) {
                        val didLock = appLock.onBackground()
                        activity.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                        if (didLock && !isAudioActive()) {
                            activity.finishAndRemoveTask()
                        }
                    }
                    isForeground = false
                }
            }
            override fun onActivityStopped(activity: Activity) {
                if (!isTransient(activity)) {
                    startedActivities = (startedActivities - 1).coerceAtLeast(0)
                }
            }
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }
}
