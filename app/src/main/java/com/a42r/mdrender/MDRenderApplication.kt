package com.a42r.mdrender

import android.app.Activity
import android.app.Application
import android.os.Bundle
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.localsend.LocalSendPrefs
import com.a42r.mdrender.localsend.LocalSendService
import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.security.AuthPreferencesStore
import com.a42r.mdrender.security.ScreenOffReceiver
import com.a42r.mdrender.ui.LockScreenActivity
import com.a42r.mdrender.ui.ShareReceiverActivity
import dagger.hilt.android.HiltAndroidApp
import java.lang.ref.WeakReference
import javax.inject.Inject

@HiltAndroidApp
class MDRenderApplication : Application() {

    @Inject lateinit var appLockManager: AppLockManager
    @Inject lateinit var authPrefs: AuthPreferencesStore
    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var localSendPrefs: LocalSendPrefs

    /** True while a non-transient activity is resumed. */
    @Volatile
    var isForeground: Boolean = false
        private set

    companion object {
        lateinit var instance: MDRenderApplication
            private set
    }

    private lateinit var screenOffReceiver: ScreenOffReceiver

    // Last-resumed activities, used to push the app out of the way when the
    // phone locks so unlocking the phone doesn't land on our auth prompt.
    private var foregroundActivity: WeakReference<Activity>? = null
    private var lockScreenActivity: WeakReference<Activity>? = null

    private fun isTransient(activity: Activity): Boolean {
        return activity is LockScreenActivity || activity is ShareReceiverActivity
    }

    /** Screen turned off: lock the app and send it behind the launcher so
     *  unlocking the phone doesn't immediately present our auth screen. */
    private fun onScreenOff() {
        appLockManager.lock()
        lockScreenActivity?.get()?.finish()
        foregroundActivity?.get()?.moveTaskToBack(true)
    }

    override fun onCreate() {
        super.onCreate()
        instance = this

        appLockManager.setIdleTimeoutSeconds(authPrefs.idleTimeoutSeconds)

        screenOffReceiver = ScreenOffReceiver().also {
            it.onScreenOff = { onScreenOff() }
        }
        registerReceiver(screenOffReceiver, ScreenOffReceiver.FILTER)

        if (localSendPrefs.enabled) {
            LocalSendService.start(this)
        }

        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityStarted(activity: Activity) {
                // Don't count LockScreenActivity; it lives on top of MainActivity
            }
            override fun onActivityResumed(activity: Activity) {
                if (activity is LockScreenActivity) {
                    lockScreenActivity = WeakReference(activity)
                }
                if (!isTransient(activity)) {
                    foregroundActivity = WeakReference(activity)
                    isForeground = true
                    appLockManager.onAppInForeground()
                    if (appLockManager.isLocked.value) {
                        LockScreenActivity.launch(activity)
                    }
                }
            }
            override fun onActivityPaused(activity: Activity) {
                if (!isTransient(activity)) {
                    isForeground = false
                    appLockManager.onAppInBackground()
                }
            }
            override fun onActivityStopped(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }

    override fun onTerminate() {
        unregisterReceiver(screenOffReceiver)
        super.onTerminate()
    }
}
