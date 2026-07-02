package com.a42r.mdrender.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.ui.auth.LockScreen
import com.a42r.mdrender.ui.theme.MDRenderTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class LockScreenActivity : ComponentActivity() {

    @Inject lateinit var appLockManager: AppLockManager

    companion object {
        fun launch(context: Context) {
            val intent = Intent(context, LockScreenActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }
            context.startActivity(intent)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MDRenderTheme {
                LockScreen(
                    onAuthenticated = {
                        appLockManager.unlock()
                        setResult(Activity.RESULT_OK)
                        finish()
                    }
                )
            }
        }
    }

    override fun onBackPressed() {
        // Block back button — must authenticate to proceed
    }
}
