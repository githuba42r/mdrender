package com.a42r.mdrender.audio

import android.content.Context
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AudioPlayerPrefs @Inject constructor() {
    companion object {
        private const val PREFS_NAME = "audio_player"
        private const val KEY_HEADPHONES_ONLY = "headphones_only"
    }

    private var prefs: android.content.SharedPreferences? = null

    fun init(context: Context) {
        prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    var headphonesOnly: Boolean
        get() = prefs?.getBoolean(KEY_HEADPHONES_ONLY, false) ?: false
        set(value) { prefs?.edit()?.putBoolean(KEY_HEADPHONES_ONLY, value)?.apply() }
}
