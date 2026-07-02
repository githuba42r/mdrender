package com.a42r.mdrender.ui.navigation

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument

@Composable
fun MDRenderNavHost() {
    val navController = rememberNavController()

    NavHost(
        navController = navController,
        startDestination = Routes.FolderBrowser.createRoute(null)
    ) {
        composable(
            route = Routes.FolderBrowser.route,
            arguments = listOf(navArgument("folderId") { type = NavType.StringType })
        ) {
            // Placeholder — replaced in Task 11
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("MDRender — Secure File Viewer")
            }
        }
        composable(
            route = Routes.MarkdownViewer.route,
            arguments = listOf(navArgument("fileId") { type = NavType.LongType })
        ) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Markdown Viewer") } }
        composable(
            route = Routes.TextViewer.route,
            arguments = listOf(navArgument("fileId") { type = NavType.LongType })
        ) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Text Viewer") } }
        composable(
            route = Routes.ImageViewer.route,
            arguments = listOf(navArgument("fileId") { type = NavType.LongType })
        ) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Image Viewer") } }
        composable(Routes.Settings.route) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Settings") } }
        composable(
            route = Routes.Import.route,
            arguments = listOf(navArgument("folderId") { type = NavType.StringType })
        ) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Import") } }
    }
}
