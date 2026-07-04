package com.a42r.mdrender.localsend

import org.json.JSONObject

/**
 * LocalSend v2 protocol messages (receive side).
 * See https://github.com/localsend/localsend/blob/main/documents/protocol/README.md
 */
object LocalSendProtocol {
    const val PORT = 53317
    const val MULTICAST_GROUP = "224.0.0.167"
    const val PROTOCOL_VERSION = "2.1"

    const val PATH_REGISTER = "/api/localsend/v2/register"
    const val PATH_PREPARE_UPLOAD = "/api/localsend/v2/prepare-upload"
    const val PATH_UPLOAD = "/api/localsend/v2/upload"
    const val PATH_CANCEL = "/api/localsend/v2/cancel"
    const val PATH_INFO = "/api/localsend/v2/info"
}

/** This device's identity as announced to the network.
 *  [port] is the actually-bound port — may differ from 53317 when another
 *  LocalSend instance runs on the same device. [fingerprint] is the SHA-256
 *  of the TLS certificate when serving https, or a random id for http. */
data class DeviceInfo(
    val alias: String,
    val fingerprint: String,
    val port: Int = LocalSendProtocol.PORT,
    val protocol: String = "https"
) {
    fun toJson(announce: Boolean? = null): JSONObject = JSONObject().apply {
        put("alias", alias)
        put("version", LocalSendProtocol.PROTOCOL_VERSION)
        put("deviceModel", android.os.Build.MODEL ?: "Android")
        put("deviceType", "mobile")
        put("fingerprint", fingerprint)
        put("port", port)
        put("protocol", protocol)
        put("download", false)
        if (announce != null) put("announce", announce)
    }
}

/** Metadata for one file offered in a prepare-upload request. */
data class IncomingFile(
    val id: String,
    val fileName: String,
    val size: Long,
    val fileType: String
) {
    companion object {
        fun fromJson(json: JSONObject): IncomingFile = IncomingFile(
            id = json.getString("id"),
            fileName = json.getString("fileName"),
            size = json.optLong("size", 0L),
            fileType = json.optString("fileType", "application/octet-stream")
        )
    }
}

/**
 * MDRender protocol extension for LocalSend v2. Sent as an optional top-level
 * `"mds"` field in the prepare-upload request body. Vanilla LocalSend receivers
 * ignore the unknown key and fall back to their defaults.
 */
data class MDRenderOptions(
    /** Folder path relative to root, e.g. "Docs/Reports". Empty = root. */
    val folder: String = "",
    /** What to do when a file with the same name already exists. */
    val conflict: ConflictStrategy = ConflictStrategy.RENAME
) {
    companion object {
        private const val KEY = "mds"

        fun fromRequestBody(body: JSONObject): MDRenderOptions {
            val mds = body.optJSONObject(KEY) ?: return MDRenderOptions()
            return MDRenderOptions(
                folder = mds.optString("folder", ""),
                conflict = ConflictStrategy.fromValue(mds.optString("conflict", null))
            )
        }
    }
}

enum class ConflictStrategy(val value: String) {
    /** Overwrite the existing file. */
    REPLACE("replace"),
    /** Skip importing the file if the name exists. */
    SKIP("skip"),
    /** Auto-rename to "name (1).ext" style. */
    RENAME("rename");

    companion object {
        fun fromValue(v: String?): ConflictStrategy = values().firstOrNull { it.value == v } ?: RENAME
    }
}
