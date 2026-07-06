package com.a42r.mdrender.ui.viewer

import android.content.Context
import com.a42r.mdrender.MDRenderApplication
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ViewerPrefs @Inject constructor() {
    companion object {
        private const val PREFS_NAME = "viewer_prefs"
        private const val KEY_INDEX_TOC_ENABLED = "index_toc_enabled"
    }

    private val prefs by lazy {
        MDRenderApplication.instance.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    var indexTocEnabled: Boolean
        get() = prefs.getBoolean(KEY_INDEX_TOC_ENABLED, true)
        set(value) { prefs.edit().putBoolean(KEY_INDEX_TOC_ENABLED, value).apply() }
}
