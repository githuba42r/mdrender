package com.a42r.mdrender.audio

import android.bluetooth.BluetoothA2dp
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceInfo
import android.media.AudioManager

object AudioManagerUtil {
    fun isHeadphonesConnected(context: Context): Boolean {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (audioManager.isWiredHeadsetOn || audioManager.isBluetoothA2dpOn) return true
        // Double-check via device list (more reliable on API 23+)
        val devices = audioManager.getDevices(AudioManager.GET_DEVICES_ALL)
        return devices.any { d ->
            d.type == AudioDeviceInfo.TYPE_WIRED_HEADSET ||
            d.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES ||
            d.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
            d.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
        }
    }
}

/** Listens for headphone plug/unplug events and calls [onHeadphonesChanged] with
 *  the new connected state. */
class HeadphoneMonitor(
    private val onHeadphonesChanged: (connected: Boolean) -> Unit
) : BroadcastReceiver() {

    private val filter = IntentFilter().apply {
        addAction(Intent.ACTION_HEADSET_PLUG)
        addAction(BluetoothDevice.ACTION_ACL_CONNECTED)
        addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED)
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            addAction(BluetoothA2dp.ACTION_CONNECTION_STATE_CHANGED)
        }
    }

    fun register(context: Context) {
        context.registerReceiver(this, filter)
    }

    fun unregister(context: Context) {
        context.unregisterReceiver(this)
    }

    override fun onReceive(context: Context, intent: Intent?) {
        when (intent?.action) {
            Intent.ACTION_HEADSET_PLUG -> {
                val plugged = intent.getIntExtra("state", 0) == 1
                onHeadphonesChanged(AudioManagerUtil.isHeadphonesConnected(context))
            }
            BluetoothDevice.ACTION_ACL_CONNECTED,
            BluetoothDevice.ACTION_ACL_DISCONNECTED -> {
                onHeadphonesChanged(AudioManagerUtil.isHeadphonesConnected(context))
            }
            BluetoothA2dp.ACTION_CONNECTION_STATE_CHANGED -> {
                onHeadphonesChanged(AudioManagerUtil.isHeadphonesConnected(context))
            }
        }
    }
}
