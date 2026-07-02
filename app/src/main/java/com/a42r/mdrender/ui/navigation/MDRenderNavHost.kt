package com.a42r.mdrender.ui.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.a42r.mdrender.ui.browser.BrowserViewModel
import com.a42r.mdrender.ui.browser.FolderBrowserScreen
import com.a42r.mdrender.ui.viewer.ImageViewerScreen
import com.a42r.mdrender.ui.viewer.MarkdownViewerScreen
import com.a42r.mdrender.ui.viewer.TextViewerScreen
import com.a42r.mdrender.ui.import.ImportScreen
import com.a42r.mdrender.ui.settings.SettingsScreen

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
        ) { backStackEntry ->
            val folderIdStr = backStackEntry.arguments?.getString("folderId") ?: "root"
            val folderId = folderIdStr.toLongOrNull()
            val viewModel: BrowserViewModel = hiltViewModel()
            LaunchedEffect(folderId) { viewModel.initialize(folderId) }
            FolderBrowserScreen(navController = navController, viewModel = viewModel)
        }
        composable(
            route = Routes.MarkdownViewer.route,
            arguments = listOf(navArgument("fileId") { type = NavType.LongType })
        ) { MarkdownViewerScreen(onBack = { navController.popBackStack() }) }
        composable(
            route = Routes.TextViewer.route,
            arguments = listOf(navArgument("fileId") { type = NavType.LongType })
        ) { TextViewerScreen(onBack = { navController.popBackStack() }) }
        composable(
            route = Routes.ImageViewer.route,
            arguments = listOf(navArgument("fileId") { type = NavType.LongType })
        ) { ImageViewerScreen(onBack = { navController.popBackStack() }) }
        composable(Routes.Settings.route) {
            SettingsScreen(onBack = { navController.popBackStack() })
        }
        composable(
            route = Routes.Import.route,
            arguments = listOf(navArgument("folderId") { type = NavType.StringType })
        ) { backStackEntry ->
            val folderIdStr = backStackEntry.arguments?.getString("folderId") ?: "root"
            val folderId = folderIdStr.toLongOrNull()
            ImportScreen(onBack = { navController.popBackStack() }, folderId = folderId)
        }
    }
}
