package com.a42r.mdrender.ui.settings

import android.content.Context
import androidx.lifecycle.ViewModel
import com.a42r.mdrender.audio.AudioPlayerPrefs
import com.a42r.mdrender.localsend.LocalSendPrefs
import com.a42r.mdrender.ui.viewer.ViewerPrefs
import com.a42r.mdrender.localsend.LocalSendService
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import javax.inject.Inject

data class SettingsUiState(
    val appVersion: String = "0.1.0",
    val localSendEnabled: Boolean = false,
    val localSendAlias: String = "",
    val localSendPin: String = "",
    val localSendAutoAccept: Boolean = false,
    val headphonesOnly: Boolean = false,
    val indexTocEnabled: Boolean = true
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val localSendPrefs: LocalSendPrefs,
    private val audioPlayerPrefs: AudioPlayerPrefs,
    private val viewerPrefs: ViewerPrefs,
    @ApplicationContext private val context: Context
) : ViewModel() {

    private val _uiState = MutableStateFlow(SettingsUiState(
        localSendEnabled = localSendPrefs.enabled,
        localSendAlias = localSendPrefs.alias,
        localSendPin = localSendPrefs.pin,
        localSendAutoAccept = localSendPrefs.autoAccept,
        headphonesOnly = audioPlayerPrefs.headphonesOnly,
        indexTocEnabled = viewerPrefs.indexTocEnabled
    ))
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    fun setLocalSendEnabled(enabled: Boolean) {
        localSendPrefs.enabled = enabled
        if (enabled) LocalSendService.start(context) else LocalSendService.stop(context)
        _uiState.update { it.copy(localSendEnabled = enabled) }
    }

    /** New alias takes effect immediately (service announces on discovery requests). */
    fun regenerateLocalSendAlias() {
        val alias = localSendPrefs.regenerateAlias()
        _uiState.update { it.copy(localSendAlias = alias) }
        if (localSendPrefs.enabled) {
            LocalSendService.stop(context)
            LocalSendService.start(context)
        }
    }

    fun setLocalSendPin(pin: String) {
        val trimmed = pin.trim()
        localSendPrefs.pin = trimmed
        // Auto-accept is only meaningful with a PIN; clear it when the PIN goes away.
        if (trimmed.isEmpty() && localSendPrefs.autoAccept) {
            localSendPrefs.autoAccept = false
            _uiState.update { it.copy(localSendPin = trimmed, localSendAutoAccept = false) }
        } else {
            _uiState.update { it.copy(localSendPin = trimmed) }
        }
    }

    fun setHeadphonesOnly(enabled: Boolean) {
        audioPlayerPrefs.headphonesOnly = enabled
        _uiState.update { it.copy(headphonesOnly = enabled) }
    }

    fun setLocalSendAutoAccept(enabled: Boolean) {
        // Guard: never enable auto-accept without a PIN.
        val effective = enabled && localSendPrefs.pin.isNotEmpty()
        localSendPrefs.autoAccept = effective
        _uiState.update { it.copy(localSendAutoAccept = effective) }
    }

    fun setIndexTocEnabled(enabled: Boolean) {
        viewerPrefs.indexTocEnabled = enabled
        _uiState.update { it.copy(indexTocEnabled = enabled) }
    }
}
