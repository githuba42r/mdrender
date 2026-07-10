package com.a42r.mdrender.data.repository

import android.content.Context
import com.a42r.mdrender.MDRenderApplication
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class StoragePrefs @Inject constructor() {
    companion object {
        private const val PREFS_NAME = "storage"
        private const val KEY_ENCRYPT_LARGE_FILES = "encrypt_large_files"
    }

    private val prefs by lazy {
        MDRenderApplication.instance.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    var encryptLargeFiles: Boolean
        get() = prefs.getBoolean(KEY_ENCRYPT_LARGE_FILES, false)
        set(value) { prefs.edit().putBoolean(KEY_ENCRYPT_LARGE_FILES, value).apply() }
}
