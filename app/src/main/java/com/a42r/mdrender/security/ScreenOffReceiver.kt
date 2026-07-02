package com.a42r.mdrender.security

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter

/** Fires [onScreenOff] when the device screen turns off. */
class ScreenOffReceiver : BroadcastReceiver() {

    companion object {
        val FILTER = IntentFilter(Intent.ACTION_SCREEN_OFF)
    }

    var onScreenOff: (() -> Unit)? = null

    override fun onReceive(context: Context?, intent: Intent?) {
        if (intent?.action == Intent.ACTION_SCREEN_OFF) {
            onScreenOff?.invoke()
        }
    }
}
