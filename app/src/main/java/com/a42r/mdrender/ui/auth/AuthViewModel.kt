package com.a42r.mdrender.ui.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.a42r.mdrender.data.repository.AuthRepository
import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.security.AuthMethod
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class AuthUiState(
    val authMethod: AuthMethod = AuthMethod.BIOMETRIC,
    val isLockedOut: Boolean = false,
    val lockoutRemainingMs: Long = 0L,
    val errorMessage: String? = null,
    val isAuthenticated: Boolean = false
)

@HiltViewModel
class AuthViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val appLockManager: AppLockManager
) : ViewModel() {

    private val _uiState = MutableStateFlow(AuthUiState())
    val uiState: StateFlow<AuthUiState> = _uiState.asStateFlow()

    init {
        _uiState.value = _uiState.value.copy(
            authMethod = authRepository.getAuthMethod()
        )
    }

    fun onBiometricSuccess() {
        _uiState.value = _uiState.value.copy(isAuthenticated = true)
    }

    fun onBiometricError(message: String) {
        _uiState.value = _uiState.value.copy(errorMessage = message)
    }

    fun verifyPattern(pattern: List<Int>): Boolean {
        if (appLockManager.isLockedOut()) {
            updateLockoutState()
            return false
        }
        return if (authRepository.verifyPattern(pattern)) {
            _uiState.value = _uiState.value.copy(isAuthenticated = true, errorMessage = null)
            true
        } else {
            val lockedOut = appLockManager.recordFailedAttempt()
            _uiState.value = _uiState.value.copy(
                errorMessage = "Incorrect pattern",
                isLockedOut = lockedOut
            )
            if (lockedOut) startLockoutTimer()
            false
        }
    }

    fun verifyPin(pin: String): Boolean {
        if (appLockManager.isLockedOut()) {
            updateLockoutState()
            return false
        }
        return if (authRepository.verifyPin(pin)) {
            _uiState.value = _uiState.value.copy(isAuthenticated = true, errorMessage = null)
            true
        } else {
            val lockedOut = appLockManager.recordFailedAttempt()
            _uiState.value = _uiState.value.copy(
                errorMessage = "Incorrect PIN",
                isLockedOut = lockedOut
            )
            if (lockedOut) startLockoutTimer()
            false
        }
    }

    private fun updateLockoutState() {
        _uiState.value = _uiState.value.copy(
            isLockedOut = true,
            lockoutRemainingMs = appLockManager.lockoutRemainingMillis()
        )
    }

    private fun startLockoutTimer() {
        viewModelScope.launch {
            while (appLockManager.isLockedOut()) {
                _uiState.value = _uiState.value.copy(
                    lockoutRemainingMs = appLockManager.lockoutRemainingMillis()
                )
                delay(1000)
            }
            _uiState.value = _uiState.value.copy(isLockedOut = false, errorMessage = null)
        }
    }

    fun hasPatternSet(): Boolean = authRepository.hasPatternSet()
    fun hasPinSet(): Boolean = authRepository.hasPinSet()
}
