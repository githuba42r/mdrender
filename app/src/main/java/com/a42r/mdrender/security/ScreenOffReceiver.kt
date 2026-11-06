package com.a42r.mdrender.security

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.PowerManager

/** Fires [onScreenOff] when the device screen turns off. */
class ScreenOffReceiver : BroadcastReceiver() {

    companion object {
        val FILTER = IntentFilter(Intent.ACTION_SCREEN_OFF)
    }

    var onScreenOff: (() -> Unit)? = null

    override fun onReceive(context: Context?, intent: Intent?) {
        if (intent?.action != Intent.ACTION_SCREEN_OFF) return
        // A frozen process (e.g. Samsung Freecess while backgrounded) gets its
        // queued SCREEN_OFF only when it unfreezes — which is when the user
        // relaunches the app. Acting on it then would finish the activity that
        // was just opened. The screen being on now means the broadcast is stale.
        val power = context?.getSystemService(PowerManager::class.java)
        if (power?.isInteractive == true) return
        onScreenOff?.invoke()
    }
}
