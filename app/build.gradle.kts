import java.util.Properties
import java.text.SimpleDateFormat
import java.util.Date

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
    alias(libs.plugins.play.publisher)
    kotlin("kapt")
}

val versionPropsFile = rootProject.file("version.properties")
val versionProps = Properties()
if (versionPropsFile.exists()) versionPropsFile.inputStream().use { versionProps.load(it) }

val versionMajor = versionProps.getProperty("VERSION_MAJOR")?.toIntOrNull() ?: 0
val versionMinor = versionProps.getProperty("VERSION_MINOR")?.toIntOrNull() ?: 0
val versionPatch = versionProps.getProperty("VERSION_PATCH")?.toIntOrNull() ?: 0
val appVersionCode = versionProps.getProperty("VERSION_CODE")?.toIntOrNull() ?: 1
val stableVersionName = "$versionMajor.$versionMinor.$versionPatch"
val nextPatchVersion = "$versionMajor.$versionMinor.${versionPatch + 1}"

// --- RC detection: non-master branches produce -rc.N tags ---
data class GitInfo(val branch: String, val rcCount: Int)

val gitInfo: GitInfo = try {
    val proc = ProcessBuilder("git", "rev-parse", "--abbrev-ref", "HEAD")
        .directory(rootProject.projectDir)
        .redirectErrorStream(false)
        .start()
    val branch = proc.inputStream.bufferedReader().readText().trim()
    proc.waitFor()

    val isMain = branch == "master" || branch == "main"
    val rcCount = if (isMain) 0 else {
        val c = ProcessBuilder("git", "rev-list", "--count", "HEAD", "^master")
            .directory(rootProject.projectDir)
            .redirectErrorStream(false)
            .start()
        c.inputStream.bufferedReader().readText().trim().toInt().coerceAtLeast(1)
    }
    GitInfo(branch, rcCount)
} catch (_: Exception) {
    GitInfo("unknown", 0)
}

// versionName for the Android manifest (no rc suffix — Play Store rejects it)
// displayVersionName shown in the app's About section
val displayVersionName = if (gitInfo.rcCount > 0) {
    "$nextPatchVersion-rc.${gitInfo.rcCount}"
} else {
    stableVersionName
}

val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties()
if (keystorePropertiesFile.exists()) keystorePropertiesFile.inputStream().use { keystoreProperties.load(it) }

android {
    namespace = "com.a42r.mdrender"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.a42r.mdrender"
        minSdk = 26
        targetSdk = 36
        versionCode = appVersionCode
        versionName = stableVersionName
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables { useSupportLibrary = true }
        resValue("string", "build_date", SimpleDateFormat("E, MMM d, yyyy").format(Date()))
        resValue("string", "build_time", SimpleDateFormat("HH:mm").format(Date()))
        resValue("string", "version_display", displayVersionName)
    }

    signingConfigs {
        create("release") {
            if (keystorePropertiesFile.exists()) {
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (keystorePropertiesFile.exists()) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    packaging {
        resources {
            // BouncyCastle jars each ship this OSGi manifest
            excludes += "META-INF/versions/9/OSGI-INF/MANIFEST.MF"
        }
    }

}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)
    implementation(libs.androidx.security.crypto)
    implementation(libs.androidx.biometric)
    implementation(libs.androidx.fragment.ktx)
    implementation(libs.hilt.android)
    kapt(libs.hilt.compiler)
    implementation(libs.hilt.navigation.compose)
    implementation(libs.coil.compose)
    implementation(libs.nanohttpd)
    implementation(libs.bouncycastle.bcpkix)
    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.androidx.media3.session)
    implementation(libs.androidx.media3.common.ktx)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    testImplementation(libs.junit)
    testImplementation(libs.mockito.kotlin)
    testImplementation(libs.kotlinx.coroutines.test)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.ui.test.junit4)
    debugImplementation(libs.androidx.ui.tooling)
    debugImplementation(libs.androidx.ui.test.manifest)
}

play {
    val serviceAccountFile = rootProject.file("play-service-account.json")
    if (serviceAccountFile.exists()) serviceAccountCredentials.set(serviceAccountFile)
    track.set("internal")
    releaseStatus.set(com.github.triplet.gradle.androidpublisher.ReleaseStatus.DRAFT)
    defaultToAppBundles.set(true)
}
