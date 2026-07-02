package com.a42r.mdrender.security

import android.content.Context
import android.os.Build
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

/**
 * Presents the device's own authentication (biometric, or device PIN/pattern/
 * password as a fallback) to unlock the app. No app-specific credential.
 */
object DeviceAuth {

    private const val STRONG_OR_CREDENTIAL =
        BiometricManager.Authenticators.BIOMETRIC_STRONG or
            BiometricManager.Authenticators.DEVICE_CREDENTIAL

    /** True only when the device has NO authentication configured at all, so
     *  the app cannot gate access and must let the user in. Transient errors
     *  (hardware busy/unavailable) return false — we stay locked and retry,
     *  never silently unlock. */
    fun noCredentialConfigured(context: Context): Boolean {
        return when (BiometricManager.from(context).canAuthenticate(STRONG_OR_CREDENTIAL)) {
            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED,
            BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE,
            BiometricManager.BIOMETRIC_ERROR_SECURITY_UPDATE_REQUIRED -> true
            else -> false
        }
    }

    fun authenticate(
        activity: FragmentActivity,
        onSuccess: () -> Unit,
        onFailure: () -> Unit
    ) {
        val executor = ContextCompat.getMainExecutor(activity)
        val callback = object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = onSuccess()
            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) = onFailure()
        }
        val prompt = BiometricPrompt(activity, executor, callback)

        val builder = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Unlock MDRender")
            .setSubtitle("Authenticate to access your files")

        // Combining a biometric with DEVICE_CREDENTIAL in setAllowedAuthenticators
        // is only supported on API 30+. Fall back to the legacy flag below that.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            builder.setAllowedAuthenticators(STRONG_OR_CREDENTIAL)
        } else {
            @Suppress("DEPRECATION")
            builder.setDeviceCredentialAllowed(true)
        }
        prompt.authenticate(builder.build())
    }
}
