#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${FIREBASE_PROJECT_ID:-mdrender-push}"
SA="fcm-pusher@$PROJECT_ID.iam.gserviceaccount.com"
command -v firebase >/dev/null || { echo "install Firebase CLI"; exit 1; }
command -v gcloud   >/dev/null || { echo "install gcloud"; exit 1; }
command -v jq       >/dev/null || { echo "install jq"; exit 1; }

firebase login --no-localhost

# An existing GCP project must be linked into Firebase before the CLI can see it.
if ! firebase projects:list --json | jq -e --arg p "$PROJECT_ID" '.result[]?|select(.projectId==$p)' >/dev/null 2>&1; then
    firebase projects:create "$PROJECT_ID" --display-name "MDRender Cloud Push" \
      || firebase projects:addfirebase "$PROJECT_ID"
fi

# The package name goes in -a/--package-name, not as a positional argument;
# passing it positionally fails with "Package name for Android app cannot be empty".
APP_ID="$(firebase apps:create ANDROID "MDRender" -a com.a42r.mdrender \
            --project "$PROJECT_ID" --json 2>/dev/null | jq -r '.appId // empty')"
if [ -z "$APP_ID" ]; then
    # Already registered (re-run): fetch the existing app id instead.
    APP_ID="$(firebase apps:list ANDROID --project "$PROJECT_ID" --json \
              | jq -r --arg pkg com.a42r.mdrender \
                  '.result[]?|select(.packageName==$pkg)|.appId' | head -1)"
fi
[ -n "$APP_ID" ] || { echo "could not resolve the Android app id"; exit 1; }
firebase apps:sdkconfig android "$APP_ID" --project "$PROJECT_ID" \
    > app/google-services.json


# The FCM v1 send API is fcm.googleapis.com. There is no
# firebasemessaging.googleapis.com service in the catalog; enabling that
# name fails with SERVICE_CONFIG_NOT_FOUND_OR_PERMISSION_DENIED.
gcloud services enable fcm.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts create fcm-pusher --project "$PROJECT_ID" || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$SA" --role roles/firebasecloudmessaging.admin
gcloud iam service-accounts keys create server/fcm-service-account.json \
    --iam-account "$SA" --project "$PROJECT_ID"

echo "DONE. Commit app/google-services.json; mount server/fcm-service-account.json"
echo "into the server image as FCM_SERVER_KEY."
