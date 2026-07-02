package com.a42r.mdrender.security

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import javax.inject.Inject

class ScreenOffReceiver : BroadcastReceiver() {

    companion object {
        fun create(): ScreenOffReceiver = ScreenOffReceiver()
        val FILTER = IntentFilter(Intent.ACTION_SCREEN_OFF)
    }

    // Injected manually by Application since BroadcastReceiver can't use @AndroidEntryPoint
    var appLockManager: AppLockManager? = null

    override fun onReceive(context: Context?, intent: Intent?) {
        if (intent?.action == Intent.ACTION_SCREEN_OFF) {
            appLockManager?.lock()
        }
    }
}
