package com.a42r.mdrender

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.view.WindowManager
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
    /** True while a non-transient activity is resumed. Used by LocalSend to
     *  decide between an in-app dialog and a notification. */
    @Volatile
    var isForeground: Boolean = false
        private set

    companion object {
        lateinit var instance: MDRenderApplication
            private set
    }

    private fun isTransient(activity: Activity): Boolean = activity is ShareReceiverActivity

    // Count of visible non-transient activities; 0 means the app is backgrounded.
    private var startedActivities = 0
    private var foregroundActivity: WeakReference<Activity>? = null
    private lateinit var screenOffReceiver: ScreenOffReceiver

    override fun onCreate() {
        super.onCreate()
        instance = this

        // Crash-safe plaintext cleanup: any decrypted share copies left by an
        // unexpected shutdown are removed before anything else runs.
        thread { shareOutManager.clearShareCache() }

        // NB: the LocalSend foreground service is started from MainActivity,
        // not here — startForegroundService() is illegal when the process is
        // created in the background (e.g. after a restart).

        // On screen-off: lock and push the app behind the launcher, so
        // unlocking the phone doesn't land on our lock gate. Re-opening the
        // app then requests authentication.
        screenOffReceiver = ScreenOffReceiver().also {
            it.onScreenOff = {
                appLock.onBackground()
                foregroundActivity?.get()?.let { activity ->
                    activity.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    activity.finishAndRemoveTask()
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
                    if (activity.isChangingConfigurations) {
                        // Config change (rotation, etc.) — don't lock and
                        // keep the app "foreground" for other consumers
                        // (e.g. LocalSend dialog decision).
                        return
                    }
                    // App going to background. Lock now so Compose has time
                    // to recompose LockGate before the OS captures the
                    // window surface buffer in onStop.
                    if (isForeground && startedActivities <= 1) {
                        // onBackground returns true when it actually locked
                        // (false when suspendNextLock was set, e.g. system
                        // permission dialog is showing).
                        val didLock = appLock.onBackground()
                        activity.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                        if (didLock) {
                            // Destroy the task to prevent stale surface buffer
                            // from being shown during the reopen animation.
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
