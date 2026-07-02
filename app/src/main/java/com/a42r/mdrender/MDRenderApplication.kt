package com.a42r.mdrender

import android.app.Activity
import android.app.Application
import android.os.Bundle
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.security.AppLock
import com.a42r.mdrender.ui.ShareReceiverActivity
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

@HiltAndroidApp
class MDRenderApplication : Application() {

    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var appLock: AppLock

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

    override fun onCreate() {
        super.onCreate()
        instance = this

        // NB: the LocalSend foreground service is started from MainActivity,
        // not here — startForegroundService() is illegal when the process is
        // created in the background (e.g. after a restart).

        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityStarted(activity: Activity) {
                if (!isTransient(activity)) startedActivities++
            }
            override fun onActivityResumed(activity: Activity) {
                if (!isTransient(activity)) isForeground = true
            }
            override fun onActivityPaused(activity: Activity) {
                if (!isTransient(activity)) isForeground = false
            }
            override fun onActivityStopped(activity: Activity) {
                if (!isTransient(activity)) {
                    startedActivities--
                    if (startedActivities <= 0) {
                        startedActivities = 0
                        // App fully backgrounded — re-lock so the next open re-auths.
                        appLock.onBackground()
                    }
                }
            }
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }
}
