package com.a42r.mdrender.ui.browser

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/** Persists browser UI state (last viewed folder, view mode) across app restarts. */
@Singleton
class BrowserPreferencesStore @Inject constructor(
    @ApplicationContext context: Context
) {
    private val prefs = context.getSharedPreferences("mdrender_browser_prefs", Context.MODE_PRIVATE)

    /** null = root */
    var lastFolderId: Long?
        get() = prefs.getLong(KEY_LAST_FOLDER, -1L).takeIf { it >= 0 }
        set(value) {
            prefs.edit().putLong(KEY_LAST_FOLDER, value ?: -1L).apply()
        }

    var isGridView: Boolean
        get() = prefs.getBoolean(KEY_GRID_VIEW, true)
        set(value) {
            prefs.edit().putBoolean(KEY_GRID_VIEW, value).apply()
        }

    var showThumbnails: Boolean
        get() = prefs.getBoolean(KEY_SHOW_THUMBNAILS, false)
        set(value) {
            prefs.edit().putBoolean(KEY_SHOW_THUMBNAILS, value).apply()
        }

    companion object {
        private const val KEY_LAST_FOLDER = "last_folder_id"
        private const val KEY_GRID_VIEW = "is_grid_view"
        private const val KEY_SHOW_THUMBNAILS = "show_thumbnails"
    }
}
