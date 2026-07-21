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
 *
 * @param allowWeakBiometric when true, uses [BiometricManager.Authenticators.BIOMETRIC_WEAK]
 *   (e.g. face unlock) as the primary biometric with PIN/pattern fallback.
 *   When false, uses [BiometricManager.Authenticators.BIOMETRIC_STRONG]
 *   (e.g. fingerprint) with PIN/pattern fallback.
 *   Only one biometric type is included at a time to avoid the system picker
 *   dialog that would otherwise force the user to choose.
 */
object DeviceAuth {

    /** The authenticator flags: the chosen biometric plus device credential
     *  (PIN/pattern) as fallback. DEVICE_CREDENTIAL is always included because
     *  BIOMETRIC_WEAK alone can fail silently on some devices (e.g. Samsung
     *  Galaxy S25 — the biometric prompt never appears and the user is stuck
     *  on the lock gate). */
    private fun authenticators(allowWeak: Boolean): Int {
        val biometric = if (allowWeak) BiometricManager.Authenticators.BIOMETRIC_WEAK
        else BiometricManager.Authenticators.BIOMETRIC_STRONG
        return biometric or BiometricManager.Authenticators.DEVICE_CREDENTIAL
    }

    /** True only when the device has NO authentication configured at all, so
     *  the app cannot gate access and must let the user in. Transient errors
     *  (hardware busy/unavailable) return false — we stay locked and retry,
     *  never silently unlock. */
    fun noCredentialConfigured(context: Context, allowWeak: Boolean = false): Boolean {
        return when (BiometricManager.from(context).canAuthenticate(authenticators(allowWeak))) {
            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED,
            BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE,
            BiometricManager.BIOMETRIC_ERROR_SECURITY_UPDATE_REQUIRED -> true
            else -> false
        }
    }

    fun authenticate(
        activity: FragmentActivity,
        allowWeak: Boolean = false,
        onSuccess: () -> Unit,
        onFailure: () -> Unit
    ) {
        val executor = ContextCompat.getMainExecutor(activity)
        val callback = object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = onSuccess()
            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) = onFailure()
        }
        val prompt = BiometricPrompt(activity, executor, callback)

        val authTypes = authenticators(allowWeak)
        val builder = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Unlock MDRender")
            .setSubtitle("Authenticate to access your files")

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            builder.setAllowedAuthenticators(authTypes)
        } else {
            @Suppress("DEPRECATION")
            builder.setDeviceCredentialAllowed(true)
        }
        prompt.authenticate(builder.build())
    }
}
