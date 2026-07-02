#!/bin/bash
set -e
PACKAGE_NAME="com.a42r.mdrender"

echo "Building debug APK..."
./gradlew assembleDebug

APK_PATH="app/build/outputs/apk/debug/app-debug.apk"

if [ -f "$APK_PATH" ]; then
    echo "APK built successfully. Deploying to device..."

    if adb shell pm list packages | grep -q "^package:${PACKAGE_NAME}$"; then
        echo "App is already installed."
        if ! adb install -r "$APK_PATH" 2>&1 | tee /tmp/adb_install.log | grep -q "Success"; then
            if grep -q "INSTALL_FAILED_UPDATE_INCOMPATIBLE" /tmp/adb_install.log; then
                echo ""
                echo "Signature mismatch detected. Uninstalling existing version (this will clear app data)..."
                adb uninstall "$PACKAGE_NAME"
                echo "Installing debug version..."
                adb install "$APK_PATH"
                echo "⚠️  App data was cleared."
            else
                echo "Installation failed. Check logs above."
                exit 1
            fi
        else
            echo "App updated successfully."
        fi
    else
        echo "Installing fresh debug version..."
        adb install "$APK_PATH"
    fi

    echo "Deployment complete."
else
    echo "APK not found! Build may have failed."
    exit 1
fi
