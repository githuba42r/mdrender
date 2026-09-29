# localsend-send

A minimal, dependency-free LocalSend v2 sender for scripting bulk uploads to
the MDRender Android app (or any LocalSend receiver). Targets a device
**directly by IP**, or **by name** via LAN discovery with a cloud-push
fallback, and supports a **PIN** and **multiple files**, which the common
third-party CLIs (e.g. localsend-go) do not.

### MDRender protocol extensions

```bash
# Send files to a subfolder, auto-renaming on name conflicts
./localsend-send.py --host 10.0.1.226 --pin 1964 --folder "Tax/2025" receipts.pdf

# Replace any same-named files (no (1) suffix)
./localsend-send.py --host 10.0.1.226 --pin 1964 --conflict replace notes.md

# Skip files that already exist on the receiver
./localsend-send.py --host 10.0.1.226 --pin 1964 --conflict skip *.jpg
```

## Requirements

Python 3.8+. No pip packages — standard library only.

Requests carry a `localsend-send/1.0` User-Agent. Cloudflare-fronted servers
answer Python's default `Python-urllib/3.x` signature with `403 error code 1010`,
which breaks the cloud-push path (enrol, token, push).

## Usage

```bash
# Send two files to a receiver that requires PIN 1964
./localsend-send.py --host 10.0.1.226 --pin 1964 notes.md photo.jpg

# Bulk send every markdown file in a directory
./localsend-send.py --host 10.0.1.226 --pin 1964 *.md

# Non-default port (the app falls back off 53317 if it's taken)
./localsend-send.py --host 10.0.1.226 --port 53318 report.pdf

# Plaintext http receiver instead of https
./localsend-send.py --host 10.0.1.226 --http notes.md
```

### Options

| Flag | Meaning |
|------|---------|
| `--host` | Receiver IP or hostname (required unless `--name`/`--enrol`) |
| `--name` | Resolve the device by name: local DNS first, then LAN discovery, then cloud push |
| `--port` | Receiver port (default 53317) |
| `--pin` | Transfer PIN, if the receiver requires one |
| `--http` | Use http instead of https |
| `--insecure` | Accept self-signed certs (already the default for https) |
| `--accept-timeout` | Seconds to wait for the receiver to accept (default 200) |
| `--folder` | Destination folder path on receiver (MDRender extension), e.g. `Docs/Reports` |
| `--conflict` | What to do on name conflict (MDRender extension): `replace`, `skip`, or `rename` (default) |
| `--list` | List LocalSend clients found on the LAN and the server's registered push devices |
| `--discover-timeout` | Seconds to listen for LAN discovery replies with `--list` (default 3) |
| `--enrol` | Enrol this machine with a cloud-push server (requires `--server`) |
| `--server` | Cloud-push server base URL for `--enrol`, e.g. `https://push.example.com` (optional with `--list`) |
| `--creds` | Push-credentials JSON path (default `~/.config/mdrender/push-credentials.json`) |

TLS certificate verification is disabled by design: every LocalSend device
uses a self-signed certificate, identified by fingerprint rather than a CA
chain.

## Listing targets (`--list`)

```bash
# LocalSend clients on the LAN, plus the server's registered devices
./localsend-send.py --list --creds ~/.config/mdrender/push-credentials.json
```

```
Discovering LocalSend clients on the LAN (~3s)…
LocalSend clients (2):
  Clever Juniper  [10.0.0.5:53317, https]  Pixel 8  MDRender (mds: folder, conflict)
  Laptop          [10.0.0.6:53317, https]  ThinkPad  LocalSend

Registered push devices on https://push.example.com (1):
  Clever Juniper  (registered 2026-09-29 19:47, last seen 2026-09-29 19:53)
```

MDRender receivers advertise an `extensions: ["mds"]` field in discovery, so the
tool can flag them and confirm they accept `--folder`/`--conflict`. Vanilla
LocalSend clients show as `LocalSend`. The registered-device section needs push
credentials (or `--server`); without them it is skipped.

## Cloud push

Enrol once, then push by device name even when the phone is not on the LAN:

```bash
# One-time enrolment
./localsend-send.py --enrol --server https://push.example.com
# A browser opens; sign in and either click "Complete registration", or — on a
# headless/remote machine — open the link elsewhere and type the short
# 6-character code the page shows into this terminal.

# Push by name: local DNS, then LAN discovery, then cloud fallback
./localsend-send.py --name "Clever Juniper" report.pdf
```

`--name` resolves in order: the system resolver (hosts file, local DNS, mDNS
`<name>.local`, plus a `spaces→-` slug form), then LocalSend UDP discovery on
the LAN, and finally the cloud-push server if the name is a registered device
and push credentials exist.

The one-time code is case-insensitive and short-lived (60 s by default). If it
expires, click **New code** on the page and type the fresh one.

## How it maps to the receiver

1. `POST /api/localsend/v2/prepare-upload?pin=<pin>` with the file manifest.
   The MDRender app prompts you (notification or in-app dialog) and this call
   blocks until you Accept — hence `--accept-timeout`.
2. On accept, the receiver returns a session id and a per-file token.
3. `POST /api/localsend/v2/upload?sessionId&fileId&token` streams each file.

Files land in the app's auto-created **LocalSend** folder, encrypted, with
`name (1).ext` auto-rename on conflicts — unless the MDRender `--folder` and
`--conflict` extensions are used, in which case:
- `--folder "Docs/Reports"` creates or reuses the `Docs > Reports` subfolder tree.
- `--conflict replace` overwrites existing files.
- `--conflict skip` skips files that already exist (counted as received).

These extensions are sent as an optional `"mds"` key in the
`prepare-upload` JSON body. Vanilla LocalSend receivers ignore the unknown
key and use their own defaults.

## Exit codes

`0` success · `1` other error · `2` usage · `3` rejected/timeout ·
`4` PIN required/incorrect · `5` receiver busy
