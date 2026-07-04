# localsend-send

A minimal, dependency-free LocalSend v2 sender for scripting bulk uploads to
the MDRender Android app (or any LocalSend receiver). Targets a device
**directly by IP** — no discovery — and supports a **PIN** and **multiple
files**, which the common third-party CLIs (e.g. localsend-go) do not.

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
| `--host` | Receiver IP or hostname (required) |
| `--port` | Receiver port (default 53317) |
| `--pin` | Transfer PIN, if the receiver requires one |
| `--http` | Use http instead of https |
| `--insecure` | Accept self-signed certs (already the default for https) |
| `--accept-timeout` | Seconds to wait for the receiver to accept (default 200) |
| `--folder` | Destination folder path on receiver (MDRender extension), e.g. `Docs/Reports` |
| `--conflict` | What to do on name conflict (MDRender extension): `replace`, `skip`, or `rename` (default) |

TLS certificate verification is disabled by design: every LocalSend device
uses a self-signed certificate, identified by fingerprint rather than a CA
chain.

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
