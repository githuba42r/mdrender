package com.a42r.mdrender.ui.auth

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.a42r.mdrender.security.AuthMethod

@Composable
fun LockScreen(
    onAuthenticated: () -> Unit,
    viewModel: AuthViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    // If authenticated, notify parent
    LaunchedEffect(uiState.isAuthenticated) {
        if (uiState.isAuthenticated) onAuthenticated()
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
        contentAlignment = Alignment.Center
    ) {
        when {
            uiState.isLockedOut -> {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Too many failed attempts", style = MaterialTheme.typography.headlineSmall)
                    Text(
                        "Wait ${uiState.lockoutRemainingMs / 1000}s before trying again",
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }
            uiState.authMethod == AuthMethod.BIOMETRIC -> {
                BiometricAuth(
                    onSuccess = { viewModel.onBiometricSuccess() },
                    onError = { viewModel.onBiometricError(it) }
                )
            }
            uiState.authMethod == AuthMethod.PATTERN -> {
                PatternLockView(
                    onPatternComplete = { viewModel.verifyPattern(it) },
                    errorMessage = uiState.errorMessage
                )
            }
            uiState.authMethod == AuthMethod.PIN -> {
                PinEntryScreen(
                    onSubmit = { viewModel.verifyPin(it) },
                    errorMessage = uiState.errorMessage
                )
            }
        }
    }
}
