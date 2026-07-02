package com.a42r.mdrender

import android.app.Activity
import android.app.Application
import android.os.Bundle
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.localsend.LocalSendPrefs
import com.a42r.mdrender.localsend.LocalSendService
import com.a42r.mdrender.ui.ShareReceiverActivity
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

@HiltAndroidApp
class MDRenderApplication : Application() {

    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var localSendPrefs: LocalSendPrefs

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

    override fun onCreate() {
        super.onCreate()
        instance = this

        if (localSendPrefs.enabled) {
            LocalSendService.start(this)
        }

        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityStarted(activity: Activity) {}
            override fun onActivityResumed(activity: Activity) {
                if (!isTransient(activity)) isForeground = true
            }
            override fun onActivityPaused(activity: Activity) {
                if (!isTransient(activity)) isForeground = false
            }
            override fun onActivityStopped(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }
}
