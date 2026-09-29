package com.a42r.mdrender.ui.settings

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.cloudpush.CloudPushKeyStore
import com.a42r.mdrender.cloudpush.CloudPushManager
import com.a42r.mdrender.cloudpush.CloudPushPairing
import com.a42r.mdrender.cloudpush.PushClient
import com.a42r.mdrender.cloudpush.PushCrypto
import com.a42r.mdrender.cloudpush.PushServerConfig
import com.google.firebase.messaging.FirebaseMessaging
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import javax.inject.Inject
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

data class CloudPushUiState(
    val isPaired: Boolean = false,
    val deviceName: String = "",
    val serverUrl: String = "",
    val deviceSecret: String = "",
    val needsReRegistration: Boolean = false,
    val isBusy: Boolean = false,
    val message: String? = null,
    val registrationKnown: Boolean? = null,
    val downloads: List<CloudPushManager.DownloadTask> = emptyList(),
)

@HiltViewModel
class CloudPushViewModel @Inject constructor(
    private val config: PushServerConfig,
    private val keyStore: CloudPushKeyStore,
    private val crypto: PushCrypto,
    private val client: PushClient,
    private val pairing: CloudPushPairing,
    private val manager: CloudPushManager,
) : ViewModel() {

    private val _status = MutableStateFlow(CloudPushUiState())

    val uiState: StateFlow<CloudPushUiState> = combine(
        _status,
        manager.state,
        manager.needsReRegistration,
    ) { status, downloads, needsReRegistration ->
        status.copy(
            isPaired = config.isPaired,
            deviceName = config.deviceName,
            serverUrl = config.serverUrl,
            deviceSecret = config.deviceSecret,
            needsReRegistration = needsReRegistration,
            downloads = downloads,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), CloudPushUiState())

    fun clearMessage() = _status.update { it.copy(message = null) }

    fun cancelDownload(fileId: String) = manager.cancel(fileId)

    /** Pair from a scanned QR code. Safe to call with a code from the wrong app. */
    fun pairWithQr(qrText: String) {
        viewModelScope.launch {
            val info = pairing.parse(qrText)
            if (info == null) {
                _status.update { it.copy(message = "That is not a Cloud Push pairing code.") }
                return@launch
            }
            runCatching {
                val publicKey = keyStore.getOrCreateKeyPair().public.encoded
                val registered = client.registerDevice(
                    serverUrl = info.serverUrl,
                    config = config,
                    crypto = crypto,
                    publicKeySpkiDer = publicKey,
                    fcmToken = awaitFcmToken(),
                    pairingToken = info.token,
                ).getOrThrow()
                val serverPem = pairing.pemFromBase64(registered.serverPublicKeyB64)
                    ?: throw IllegalStateException("server sent an unreadable public key")
                // Commit only once registration has actually succeeded, so a
                // failed or half-finished attempt cannot leave a stored server
                // URL behind that the UI would then present as paired.
                config.serverUrl = info.serverUrl
                config.serverPublicKeyPem = serverPem
                config.deviceAuth = registered.deviceAuth
            }.onSuccess {
                _status.update { it.copy(message = "Paired with ${config.serverUrl}", isBusy = false) }
            }.onFailure { e ->
                Log.w(TAG, "CloudPush: pairing failed (${e.javaClass.simpleName})")
                _status.update { it.copy(message = "Pairing failed: ${e.message ?: "unknown error"}", isBusy = false) }
            }
        }
    }

    fun checkRegistration() {
        viewModelScope.launch {
            val known = runCatching { client.checkRegistration(config) }.getOrDefault(false)
            _status.update { it.copy(registrationKnown = known) }
        }
    }

    /** Forget this server entirely, including the key it trusts. */
    fun rotateKeys() {
        viewModelScope.launch {
            config.clear()
            keyStore.deleteKeyPair()
            manager.setReRegistrationNeeded(false)
            _status.update {
                it.copy(message = "Pairing cleared. Scan a new pairing code.", registrationKnown = null)
            }
        }
    }

    /**
     * FCM's token task, bridged to a coroutine. `Tasks.await` is avoided so a
     * slow or missing Firebase install cannot block this thread.
     */
    private suspend fun awaitFcmToken(): String =
        suspendCancellableCoroutine { cont ->
            FirebaseMessaging.getInstance().token
                .addOnCompleteListener { task ->
                    if (!cont.isActive) return@addOnCompleteListener
                    val error = task.exception
                    if (error != null) cont.resumeWithException(error)
                    else cont.resume(task.result)
                }
        }

    private companion object {
        const val TAG = "CloudPushViewModel"
    }
}
