#!/bin/bash
# Usage: mdrender-push --target NAME file1 [file2 ...]
#
# Cloud push only — no LAN discovery, no LocalSend handshake: enrol the
# machine once with `mdrender-send --enrol --server <url>`, then push to any
# paired device by name. Shares the OAuth2 credential file with mdrender-send
# (design §"Tool enrolment").
#
# Servers running with encryption required (ENCRYPTION_MODE=on) reject raw
# payloads with 422; we check the advertised policy first and point at the
# sealing client instead (design §7b).
set -euo pipefail
PROG="$(basename "$0")"
CREDS="${MDRender_PUSH_CREDS:-$HOME/.config/mdrender/push-credentials.json}"

if [[ "${1:-}" != "--target" || -z "${2:-}" ]]; then
  echo "usage: $PROG --target NAME file1 [file2 ...]" >&2
  exit 2
fi
TARGET="$2"; shift 2
[[ $# -gt 0 ]] || { echo "error: no files" >&2; exit 2; }

if [[ ! -f "$CREDS" ]]; then
  echo "error: no push credentials at $CREDS" >&2
  echo "enrol this machine first: mdrender-send --enrol --server <url>" >&2
  exit 2
fi

PUSH_URL="${PUSH_URL:-$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["server_url"])' "$CREDS")}"

# Best-effort policy probe: refuse only on a definitive "on". An unreachable
# endpoint or an older server without /api/server/policy ("unknown") lets the
# push proceed and surface real errors downstream.
ENC="$(curl -fsS --max-time 10 "$PUSH_URL/api/server/policy" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("encryption","off"))' 2>/dev/null \
  || echo unknown)"
if [[ "$ENC" == "on" ]]; then
  echo "error: $PUSH_URL requires sealed pushes (encryption on)" >&2
  echo "use mdrender-send --cloud --name $TARGET <files> instead" >&2
  exit 3
fi

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
FLAGS=(-F "target_device=$TARGET")
for f in "$@"; do FLAGS+=(-F "file=@$f"); done
curl -fsS -H "Authorization: Bearer $TOKEN" "${FLAGS[@]}" "$PUSH_URL/api/push"
echo "pushed $# file(s) to $TARGET"
