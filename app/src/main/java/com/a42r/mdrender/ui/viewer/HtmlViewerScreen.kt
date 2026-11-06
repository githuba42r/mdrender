package com.a42r.mdrender.ui.viewer

import android.content.Intent
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * Renders an HTML file in a WebView.
 *
 * The WebView loads the decrypted content with a base URL pointing at the
 * file's own directory (plain storage), so relative links to sibling
 * images, css, and pages resolve. In-page back history is stepped through
 * before leaving the screen; external http(s) links open in the browser.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HtmlViewerScreen(
    onBack: () -> Unit,
    onNavigateToFile: (Long) -> Unit = {},
    viewModel: ViewerViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current

    val webView = remember {
        WebView(context).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = true
            settings.setSupportZoom(true)
            settings.builtInZoomControls = true
            settings.displayZoomControls = false
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest
                ): Boolean {
                    val url = request.url.toString()
                    return if (url.startsWith("http://") || url.startsWith("https://")) {
                        runCatching {
                            view.context.startActivity(Intent(Intent.ACTION_VIEW, request.url))
                        }
                        true
                    } else {
                        false
                    }
                }
            }
        }
    }

    // Load once the decrypted content is ready; key on content so a
    // LocalSend-triggered reload works too.
    LaunchedEffect(uiState.htmlContent) {
        uiState.htmlContent?.let { html ->
            webView.loadDataWithBaseURL(uiState.htmlBaseUrl, html, "text/html", "utf-8", null)
        }
    }

    DisposableEffect(Unit) {
        onDispose { webView.destroy() }
    }

    // Step back through the WebView's own history first.
    BackHandler {
        if (webView.canGoBack()) webView.goBack() else onBack()
    }

    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(uiState.updatedFileId) {
        if (uiState.fileUpdated && uiState.updatedFileId != null) {
            val result = snackbarHostState.showSnackbar(
                message = "File updated",
                actionLabel = "Reopen",
                duration = SnackbarDuration.Indefinite
            )
            if (result == SnackbarResult.ActionPerformed) {
                onNavigateToFile(uiState.updatedFileId!!)
            }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(uiState.fileName) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                }
            )
        }
    ) { padding ->
        when {
            uiState.isLoading -> Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center
            ) { CircularProgressIndicator() }

            uiState.error != null -> Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    "Error: ${uiState.error}",
                    color = MaterialTheme.colorScheme.error
                )
            }

            else -> AndroidHtmlView(webView, Modifier.padding(padding))
        }
    }
}

@Composable
private fun AndroidHtmlView(webView: WebView, modifier: Modifier = Modifier) {
    androidx.compose.ui.viewinterop.AndroidView(
        factory = { webView },
        modifier = modifier.fillMaxSize()
    )
}
