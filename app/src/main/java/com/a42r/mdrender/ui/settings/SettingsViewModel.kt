package com.a42r.mdrender.ui.settings

import androidx.lifecycle.ViewModel
import com.a42r.mdrender.data.repository.AuthRepository
import com.a42r.mdrender.security.AuthMethod
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import javax.inject.Inject

data class SettingsUiState(
    val authMethod: AuthMethod = AuthMethod.BIOMETRIC,
    val idleTimeoutSeconds: Int = 120,
    val hasPatternSet: Boolean = false,
    val hasPinSet: Boolean = false,
    val appVersion: String = "0.1.0",
    val showPatternSetup: Boolean = false,
    val showPinSetup: Boolean = false
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val authRepository: AuthRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(SettingsUiState(
        authMethod = authRepository.getAuthMethod(),
        idleTimeoutSeconds = authRepository.getIdleTimeoutSeconds(),
        hasPatternSet = authRepository.hasPatternSet(),
        hasPinSet = authRepository.hasPinSet()
    ))
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    fun setAuthMethod(method: AuthMethod) {
        authRepository.setAuthMethod(method)
        _uiState.update { it.copy(authMethod = method) }
    }

    fun setIdleTimeout(seconds: Int) {
        authRepository.setIdleTimeoutSeconds(seconds)
        _uiState.update { it.copy(idleTimeoutSeconds = seconds) }
    }

    fun showPatternSetup() {
        _uiState.update { it.copy(showPatternSetup = true) }
    }

    fun patternSetupComplete(pattern: List<Int>) {
        authRepository.setPattern(pattern)
        _uiState.update { it.copy(showPatternSetup = false, hasPatternSet = true) }
    }

    fun showPinSetup() {
        _uiState.update { it.copy(showPinSetup = true) }
    }

    fun pinSetupComplete(pin: String) {
        authRepository.setPin(pin)
        _uiState.update { it.copy(showPinSetup = false, hasPinSet = true) }
    }

    fun dismissPatternSetup() {
        _uiState.update { it.copy(showPatternSetup = false) }
    }

    fun dismissPinSetup() {
        _uiState.update { it.copy(showPinSetup = false) }
    }
}
