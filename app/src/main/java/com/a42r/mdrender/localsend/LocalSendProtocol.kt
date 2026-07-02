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
 *  [port] is the actually-bound HTTP port — may differ from 53317 when
 *  another LocalSend instance runs on the same device. */
data class DeviceInfo(
    val alias: String,
    val fingerprint: String,
    val port: Int = LocalSendProtocol.PORT
) {
    fun toJson(announce: Boolean? = null): JSONObject = JSONObject().apply {
        put("alias", alias)
        put("version", LocalSendProtocol.PROTOCOL_VERSION)
        put("deviceModel", android.os.Build.MODEL ?: "Android")
        put("deviceType", "mobile")
        put("fingerprint", fingerprint)
        put("port", port)
        put("protocol", "http")
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
