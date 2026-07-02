package com.a42r.mdrender

import android.app.Activity
import android.app.Application
import android.os.Bundle
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.security.AuthPreferencesStore
import com.a42r.mdrender.security.ScreenOffReceiver
import com.a42r.mdrender.ui.LockScreenActivity
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

@HiltAndroidApp
class MDRenderApplication : Application() {

    @Inject lateinit var appLockManager: AppLockManager
    @Inject lateinit var authPrefs: AuthPreferencesStore
    @Inject lateinit var fileRepository: FileRepository

    companion object {
        lateinit var instance: MDRenderApplication
            private set
    }

    private lateinit var screenOffReceiver: ScreenOffReceiver
    private var activityCount = 0

    override fun onCreate() {
        super.onCreate()
        instance = this

        appLockManager.setIdleTimeoutSeconds(authPrefs.idleTimeoutSeconds)

        screenOffReceiver = ScreenOffReceiver().also {
            it.appLockManager = appLockManager
        }
        registerReceiver(screenOffReceiver, ScreenOffReceiver.FILTER)

        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityStarted(activity: Activity) {
                // Don't count LockScreenActivity; it lives on top of MainActivity
            }
            override fun onActivityResumed(activity: Activity) {
                if (activity !is LockScreenActivity) {
                    appLockManager.onAppInForeground()
                    if (appLockManager.isLocked.value) {
                        LockScreenActivity.launch(activity)
                    }
                }
            }
            override fun onActivityPaused(activity: Activity) {
                if (activity !is LockScreenActivity) {
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
