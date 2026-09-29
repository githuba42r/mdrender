package com.a42r.mdrender.cloudpush

import javax.inject.Inject
import javax.inject.Singleton

/**
 * Turns a raw FCM data message into queued downloads.
 *
 * This is deliberately separate from [PushFcmService]: the service is a thin
 * Android adapter that Firebase owns, while the ordering rule that matters —
 * never enqueue anything until the manifest signature verifies — is ordinary
 * logic and can be tested on the JVM.
 *
 * Every failure is silent by design. A doorbell that will not open is either
 * addressed to another device or tampered with, and in neither case is there
 * anything useful to tell the sender.
 */
@Singleton
class CloudPushMessageHandler @Inject constructor(
    private val crypto: PushCrypto,
    private val client: PushClient,
    private val config: PushServerConfig,
    private val manager: CloudPushManager,
) {

    /** Why a message was dropped, for logging. */
    enum class Drop { NOT_PAIRED, NOT_A_DOORBELL, MANIFEST_UNAVAILABLE, BAD_MANIFEST_SIGNATURE }

    sealed interface Outcome {
        data object Enqueued : Outcome
        data class Dropped(val reason: Drop) : Outcome
    }

    /**
     * Decrypt the doorbell, fetch the signed manifest, verify it, and queue the
     * files. Returns [Outcome.Dropped] rather than throwing: an unusable message
     * is an expected event on a shared FCM project, not an error.
     */
    suspend fun handle(ct: String, iv: String): Outcome {
        if (!config.isPaired) return Outcome.Dropped(Drop.NOT_PAIRED)

        val doorbell = crypto.decryptTrigger(ct, iv, config.pushKey)
            ?: return Outcome.Dropped(Drop.NOT_A_DOORBELL)

        val fetched = client.fetchManifest(doorbell).getOrNull()
            ?: return Outcome.Dropped(Drop.MANIFEST_UNAVAILABLE)

        val files = crypto.verifyManifest(
            fetched.body, fetched.signature, config.serverPublicKeyPem
        ) ?: return Outcome.Dropped(Drop.BAD_MANIFEST_SIGNATURE)

        // Only now is any file name or retrieval key trusted.
        manager.enqueue(doorbell, files)
        return Outcome.Enqueued
    }
}
