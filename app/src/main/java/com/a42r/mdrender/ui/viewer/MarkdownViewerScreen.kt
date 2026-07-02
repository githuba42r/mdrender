package com.a42r.mdrender.ui.viewer

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MarkdownViewerScreen(
    onBack: () -> Unit,
    viewModel: ViewerViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    Scaffold(
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
            uiState.isLoading -> Box(Modifier.fillMaxSize().padding(padding)) { CircularProgressIndicator() }
            uiState.error != null -> Box(Modifier.fillMaxSize().padding(padding)) {
                Text("Error: ${uiState.error}", color = MaterialTheme.colorScheme.error)
            }
            else -> {
                val scrollState = rememberScrollState()
                SelectionContainer {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(padding)
                            .verticalScroll(scrollState)
                            .padding(16.dp)
                    ) {
                        MarkdownText(uiState.markdownContent)
                    }
                }
            }
        }
    }
}

@Composable
fun MarkdownText(markdown: String) {
    // Simple MD renderer — headings, bold, italic, code, lists, links
    val annotatedString = buildAnnotatedString {
        val lines = markdown.split("\n")
        for (line in lines) {
            when {
                line.startsWith("# ") -> withStyle(SpanStyle(fontSize = 24.sp, fontWeight = FontWeight.Bold)) {
                    append(line.removePrefix("# "))
                }
                line.startsWith("## ") -> withStyle(SpanStyle(fontSize = 20.sp, fontWeight = FontWeight.Bold)) {
                    append(line.removePrefix("## "))
                }
                line.startsWith("### ") -> withStyle(SpanStyle(fontSize = 18.sp, fontWeight = FontWeight.Bold)) {
                    append(line.removePrefix("### "))
                }
                line.startsWith("- ") || line.startsWith("* ") -> {
                    append("  • ")
                    append(renderInlineMarkdown(line.removePrefix("- ").removePrefix("* ")))
                }
                line.startsWith("`") && line.endsWith("`") -> {
                    withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = androidx.compose.ui.graphics.Color(0xFFEEEEEE))) {
                        append(line.removeSurrounding("`", "`"))
                    }
                }
                line.startsWith("> ") -> withStyle(SpanStyle(fontStyle = FontStyle.Italic, color = MaterialTheme.colorScheme.onSurfaceVariant)) {
                    append(line.removePrefix("> "))
                }
                else -> append(renderInlineMarkdown(line))
            }
            append("\n")
        }
    }
    Text(text = annotatedString)
}

private fun renderInlineMarkdown(text: String): String {
    // For the viewer, simply strip common inline markers and return plain text.
    // A full inline parser can be added later. This keeps the initial viewer simple
    // and functional for the common case.
    return text
        .replace(Regex("\\*\\*(.+?)\\*\\*"), "$1") // bold
        .replace(Regex("\\*(.+?)\\*"), "$1")       // italic
        .replace(Regex("`(.+?)`"), "$1")            // inline code
        .replace(Regex("\\[(.+?)\\]\\(.+?\\)"), "$1") // links
}
