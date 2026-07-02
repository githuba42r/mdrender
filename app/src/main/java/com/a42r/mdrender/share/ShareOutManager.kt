package com.a42r.mdrender.share

import android.content.Context
import com.a42r.mdrender.data.repository.FileRepository
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/** Stages decrypted copies of files for outbound sharing.
 *
 *  Plaintext only ever exists under cacheDir/share/ — wiped on every app
 *  start (crash-safe) and again before each share. */
@Singleton
class ShareOutManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val fileRepository: FileRepository
) {
    companion object {
        const val SHARE_DIR = "share"

        /** On-disk names for staged files: duplicates get "name (1).ext". */
        fun dedupeNames(names: List<String>): List<String> {
            val used = mutableSetOf<String>()
            return names.map { name ->
                if (used.add(name)) return@map name
                val dot = name.lastIndexOf('.')
                val base = if (dot > 0) name.substring(0, dot) else name
                val ext = if (dot > 0) name.substring(dot) else ""
                var n = 1
                var candidate = "$base ($n)$ext"
                while (!used.add(candidate)) {
                    n++
                    candidate = "$base ($n)$ext"
                }
                candidate
            }
        }

        /** Narrowest MIME type covering [mimes]: exact match, "type/*", or "*/*". */
        fun commonMimeType(mimes: List<String>): String {
            if (mimes.isEmpty()) return "*/*"
            if (mimes.distinct().size == 1) return mimes[0]
            val primaries = mimes.map { it.substringBefore('/') }.distinct()
            return if (primaries.size == 1) "${primaries[0]}/*" else "*/*"
        }
    }
}
