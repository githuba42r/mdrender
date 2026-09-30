package com.a42r.mdrender.cloudpush

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import javax.inject.Inject

/**
 * Firebase entry point for cloud push.
 *
 * Always registered, paired or not: the service only does anything once a
 * doorbell actually opens, so an unpaired install costs nothing.
 */
@AndroidEntryPoint
class PushFcmService : FirebaseMessagingService() {

    @Inject lateinit var handler: CloudPushMessageHandler
    @Inject lateinit var client: PushClient
    @Inject lateinit var config: PushServerConfig
    @Inject lateinit var keyStore: CloudPushKeyStore
    @Inject lateinit var manager: CloudPushManager

    override fun onMessageReceived(message: RemoteMessage) {
        // The server removes a device by sending this: forget the pairing here so
        // the app stops acting on a server that no longer knows it.
        if (message.data["type"] == "unpaired") {
            Log.d(TAG, "CloudPush: unpaired by server; clearing local pairing")
            config.clear()
            keyStore.deleteKeyPair()
            keyStore.deleteContentKeyPair()
            manager.setReRegistrationNeeded(false)
            manager.notifyPairingChanged()
            return
        }
        val ct = message.data["p"] ?: return
        val iv = message.data["i"] ?: return
        // EnhancedIntentService delivers on its own worker executor and keeps
        // this service alive only while this call is on the stack, so block it
        // deliberately. A detached coroutine would be killed the moment the
        // executor task returned. There is no Service.goAsync() to hold the
        // window open — that API does not exist in this SDK — and Firebase's
        // own MESSAGE_TIMEOUT_S already caps how long we can hold it.
        runBlocking {
            try {
                withTimeout(HANDLE_TIMEOUT_MS) {
                    when (val outcome = handler.handle(ct, iv)) {
                        is CloudPushMessageHandler.Outcome.Enqueued ->
                            Log.d(TAG, "CloudPush: queued downloads")
                        is CloudPushMessageHandler.Outcome.Dropped ->
                            Log.d(TAG, "CloudPush: dropped (${outcome.reason})")
                    }
                }
            } catch (_: kotlinx.coroutines.TimeoutCancellationException) {
                Log.w(TAG, "CloudPush: timed out; the server will retry")
            } catch (e: Exception) {
                Log.w(TAG, "CloudPush: dropped message (${e.javaClass.simpleName})")
            }
        }
    }

    override fun onNewToken(token: String) {
        if (!config.isPaired) return
        runBlocking {
            try {
                withTimeout(ROTATE_TIMEOUT_MS) { client.rotateToken(config, token) }
            } catch (e: Exception) {
                Log.w(TAG, "CloudPush: token rotation failed (${e.javaClass.simpleName})")
            }
        }
    }

    companion object {
        private const val TAG = "PushFcmService"

        /** Comfortably inside Firebase's own 20s service window. */
        private const val HANDLE_TIMEOUT_MS = 15_000L
        private const val ROTATE_TIMEOUT_MS = 10_000L
    }
}
