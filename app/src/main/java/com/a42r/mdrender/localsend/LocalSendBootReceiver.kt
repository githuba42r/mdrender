package com.a42r.mdrender.localsend

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class LocalSendBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            val prefs = context.getSharedPreferences("mdrender_localsend_prefs", Context.MODE_PRIVATE)
            if (prefs.getBoolean("enabled", false)) {
                LocalSendService.start(context)
            }
        }
    }
}
