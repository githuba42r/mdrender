#!/bin/bash
# Usage: push-to-phone --target NAME file1 [file2 ...]
set -euo pipefail
CREDS="${MDRender_PUSH_CREDS:-$HOME/.config/mdrender/push-credentials.json}"
TOKEN="$(python3 - "$CREDS" <<'PY'
import json, sys, urllib.request, urllib.parse
c = json.load(open(sys.argv[1]))
body = urllib.parse.urlencode({
    "grant_type": "client_credentials",
    "client_id": c["client_id"],
    "client_secret": c["client_secret"]}).encode()
r = urllib.request.urlopen(urllib.request.Request(
    c["server_url"] + "/oauth/token", data=body,
    # Cloudflare fronts both servers and rejects urllib's default Python
    # user-agent (error 1010); send the same one the federation client uses.
    headers={"User-Agent": "MDRender/1.0"}))
print(json.load(r)["access_token"])
PY
)"
if [[ "${1:-}" != "--target" || -z "${2:-}" ]]; then
  echo "error: --target NAME is required" >&2
  exit 2
fi
TARGET="$2"; shift 2
[[ $# -gt 0 ]] || { echo "error: no files" >&2; exit 2; }
PUSH_URL="${PUSH_URL:-$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["server_url"])' "$CREDS")}"
FLAGS=(-F "target_device=$TARGET")
for f in "$@"; do FLAGS+=(-F "file=@$f"); done
curl -fsS -H "Authorization: Bearer $TOKEN" "${FLAGS[@]}" "$PUSH_URL/api/push"
echo "pushed $# file(s) to $TARGET"
