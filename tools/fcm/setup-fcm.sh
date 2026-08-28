#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${FIREBASE_PROJECT_ID:-mdrender-push}"
SA="fcm-pusher@$PROJECT_ID.iam.gserviceaccount.com"
command -v firebase >/dev/null || { echo "install Firebase CLI"; exit 1; }
command -v gcloud   >/dev/null || { echo "install gcloud"; exit 1; }
command -v jq       >/dev/null || { echo "install jq"; exit 1; }

firebase login --no-localhost

firebase projects:create "$PROJECT_ID" --display-name "MDRender Cloud Push" || true

APP_ID="$(firebase apps:create android com.a42r.mdrender --project "$PROJECT_ID" --json \
            | jq -r '.appId')"
firebase apps:sdkconfig android "$APP_ID" --project "$PROJECT_ID" \
    > app/google-services.json

gcloud services enable firebasemessaging.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts create fcm-pusher --project "$PROJECT_ID" || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$SA" --role roles/firebasecloudmessaging.admin
gcloud iam service-accounts keys create server/fcm-service-account.json \
    --iam-account "$SA" --project "$PROJECT_ID"

echo "DONE. Commit app/google-services.json; mount server/fcm-service-account.json"
echo "into the server image as FCM_SERVER_KEY."
