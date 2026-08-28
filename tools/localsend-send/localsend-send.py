#!/usr/bin/env python3
"""Minimal LocalSend v2 sender.

Sends one or more files to a LocalSend receiver identified directly by IP
(no discovery), with optional PIN, over HTTPS (self-signed certs accepted,
as every LocalSend device uses one). Built for scripting bulk uploads into
the MDRender Android app, but speaks standard LocalSend v2 so it works with
any receiver.

Examples:
    localsend-send.py --host 10.0.1.226 --pin 1964 notes.md photo.jpg
    localsend-send.py --host 10.0.1.226 *.md
    localsend-send.py --host 10.0.1.226 --port 53318 --insecure report.pdf
    localsend-send.py --enrol --server https://push.example.com

Exit codes: 0 ok, 2 usage, 3 rejected/timeout, 4 PIN required/wrong,
5 receiver busy, 1 other error.
"""
import argparse
import http.client
import json
import mimetypes
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "/api/localsend/v2"
CLOUD_API = "/api"  # cloud-push server base (NOT the LocalSend LAN protocol)


def _client_info():
    return {
        "alias": f"cli-{os.uname().nodename}",
        "version": "2.1",
        "deviceModel": "CLI",
        "deviceType": "headless",
        "fingerprint": str(uuid.uuid4()),
        "port": 53317,
        "protocol": "https",
        "download": False,
    }


def _post(url, body, ctx, timeout):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, context=ctx, timeout=timeout)


def _upload_stream(url, path, ctx, timeout):
    """Upload *path* to *url* using Content-Length, streaming 64 KB at a time.
    Uses Content-Length (not chunked TE) because NanoHTTPD's chunked decoder
    can produce 500 errors on large bodies."""
    file_size = os.path.getsize(path)
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path_qs = parsed.path + ("?" + parsed.query if parsed.query else "")

    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)

    conn.connect()
    conn.putrequest("POST", path_qs, skip_accept_encoding=False)
    conn.putheader("Content-Type", "application/octet-stream")
    conn.putheader("Content-Length", str(file_size))
    conn.endheaders()

    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)  # 64 KB
            if not chunk:
                break
            conn.send(chunk)

    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise urllib.error.HTTPError(url, resp.status, resp.reason, resp.headers, resp)
    return body


def _start_enrolment(server_url):
    """Mint a fresh enrolment on the server.

    Returns (enrolment_id, verification_uri) from a single POST to
    /api/enrol/start. The server stores the matching key against this
    enrolment_id, so the operator reads it back from the browser at the
    verification_uri and it must be submitted against this same id.
    """
    base = f"{server_url.rstrip('/')}{CLOUD_API}"
    start = json.loads(_post(f"{base}/enrol/start",
                             {"info": _client_info()}, None, 30).read())
    return start["enrolment_id"], start["verification_uri"]


def _enrol_flow(server_url, enrolment_id, key_input, creds_path=None):
    """Complete the browser-key enrolment exchange and persist the credentials.

    Submits the operator's key against *enrolment_id* (minted earlier by
    _start_enrolment) to /api/enrol, then writes the resulting credentials
    ({client_id, client_secret, server_url}) to *creds_path* with 0600
    permissions. Returns the creds dict that was written.
    """
    base = f"{server_url.rstrip('/')}{CLOUD_API}"
    resp = _post(f"{base}/enrol",
                 {"enrolment_id": enrolment_id, "key": key_input},
                 None, 30)
    creds = json.loads(resp.read())
    record = {**creds, "server_url": server_url}
    path = creds_path or os.path.expanduser(
        "~/.config/mdrender/push-credentials.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(record, fh)
        fh.flush()
        os.fchmod(fh.fileno(), 0o600)
    return record


def _get_token(creds, server_url):
    """POST the client-credentials grant and return the access token."""
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).encode()
    req = urllib.request.Request(f"{server_url.rstrip('/')}/oauth/token",
                                 data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def get_access_token(creds_path, server_url=None):
    """Load push credentials and return a fresh cloud access token.

    This is the interface the push path (Task C3) consumes.
    """
    with open(creds_path) as fh:
        creds = json.load(fh)
    server_url = server_url or creds["server_url"]
    return _get_token(creds, server_url)


def cmd_enrol(args):
    """Interactively enrol an OAuth client with the cloud-push server."""
    path = args.creds or os.path.expanduser(
        "~/.config/mdrender/push-credentials.json")
    eid, uri = _start_enrolment(args.server)
    print(f"Open in your browser:  {uri}")
    if os.environ.get("DISPLAY"):
        subprocess.Popen(["xdg-open", uri])
    key = input("Enter enrolment key: ").strip()
    _enrol_flow(args.server, eid, key, path)
    print(f"wrote {path} (0600)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Send files to a LocalSend receiver by IP, or enrol this "
                    "CLI with the cloud-push server.")
    p.add_argument("files", nargs="*", help="one or more file paths")
    p.add_argument("--host", default=None, help="receiver IP or hostname")
    p.add_argument("--port", type=int, default=53317, help="receiver port (default 53317)")
    p.add_argument("--pin", default=None, help="transfer PIN, if the receiver requires one")
    p.add_argument("--http", action="store_true", help="use http instead of https")
    p.add_argument("--insecure", action="store_true",
                   help="(default for https) accept self-signed certs")
    p.add_argument("--accept-timeout", type=int, default=200,
                   help="seconds to wait for the receiver to accept (default 200)")
    p.add_argument("--folder", default="",
                   help="destination folder path on receiver, e.g. 'Docs/Reports' (MDRender extension)")
    p.add_argument("--conflict", default="rename", choices=["replace", "skip", "rename"],
                   help="what to do when a file name already exists (MDRender extension, default: rename)")
    p.add_argument("--enrol", action="store_true",
                   help="enrol an OAuth client with the cloud-push server (requires --server)")
    p.add_argument("--server", default=None,
                   help="cloud-push server base URL, e.g. https://push.example.com "
                        "(required with --enrol)")
    p.add_argument("--creds", default=None,
                   help="path for the push credentials JSON "
                        "(default ~/.config/mdrender/push-credentials.json)")
    args = p.parse_args(argv)

    if args.enrol:
        if not args.server:
            print("error: --enrol requires --server <URL>", file=sys.stderr)
            return 2
        return cmd_enrol(args)

    if not args.host:
        print("error: --host <IP or hostname> is required", file=sys.stderr)
        return 2
    if not args.files:
        print("error: at least one file is required", file=sys.stderr)
        return 2

    paths = []
    for f in args.files:
        if not os.path.isfile(f):
            print(f"error: not a file: {f}", file=sys.stderr)
            return 2
        paths.append(f)

    scheme = "http" if args.http else "https"
    base = f"{scheme}://{args.host}:{args.port}{API}"
    ctx = None
    if scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # LocalSend devices are all self-signed

    # Build the prepare-upload manifest: fileId -> metadata.
    files_meta = {}
    id_to_path = {}
    for path in paths:
        fid = str(uuid.uuid4())
        id_to_path[fid] = path
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        files_meta[fid] = {
            "id": fid,
            "fileName": os.path.basename(path),
            "size": os.path.getsize(path),
            "fileType": mime,
        }

    prepare_url = f"{base}/prepare-upload"
    if args.pin:
        prepare_url += f"?pin={urllib.parse.quote(args.pin)}"

    # MDRender protocol extension: destination folder and conflict strategy.
    # Only sent when non-default so vanilla receivers are not bothered.
    mds = {}
    if args.folder:
        mds["folder"] = args.folder
    if args.conflict != "rename":
        mds["conflict"] = args.conflict

    body = {"info": _client_info(), "files": files_meta}
    if mds:
        body["mds"] = mds

    print(f"→ {args.host}:{args.port}  {len(paths)} file(s)  "
          f"(waiting up to {args.accept_timeout}s for accept…)"
          + (f"  folder={args.folder!r}" if args.folder else ""), flush=True)

    # Retry on 409 (receiver busy / stale session) with backoff up to deadline.
    deadline = time.monotonic() + args.accept_timeout
    resp = None
    grant = None
    last_err = None
    while time.monotonic() < deadline:
        try:
            resp = _post(prepare_url, body, ctx, max(10, int(deadline - time.monotonic())))
            grant = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 409:
                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    break
                wait = min(30, max(3, remaining // 10))
                print(f"\r  busy… retrying in {wait}s ({remaining}s left)  ", end="", flush=True)
                time.sleep(wait)
                continue
            break
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                break
            wait = min(10, max(2, remaining // 10))
            print(f"\r  retrying ({remaining}s left)…  ", end="", flush=True)
            time.sleep(wait)
            continue

    if grant is None:
        if isinstance(last_err, urllib.error.HTTPError):
            if last_err.code == 401:
                print("rejected: PIN required or incorrect (use --pin)", file=sys.stderr)
                return 4
            if last_err.code == 403:
                print("rejected: the receiver declined or timed out", file=sys.stderr)
                return 3
            if last_err.code == 409:
                print("busy: the receiver is handling another transfer", file=sys.stderr)
                return 5
            print(f"error: prepare-upload failed: HTTP {last_err.code} "
                  f"{last_err.read().decode()[:200]}", file=sys.stderr)
            return 1
        if isinstance(last_err, (urllib.error.URLError, TimeoutError)):
            print(f"error: cannot reach receiver: {last_err}", file=sys.stderr)
            return 1
        print("error: no response from receiver", file=sys.stderr)
        return 1

    session_id = grant["sessionId"]
    tokens = grant.get("files", {})
    if not tokens:
        print("receiver accepted but selected no files.", file=sys.stderr)
        return 0

    ok = 0
    for fid, token in tokens.items():
        path = id_to_path.get(fid)
        if path is None:
            continue
        name = os.path.basename(path)
        upload_url = (f"{base}/upload?sessionId={urllib.parse.quote(session_id)}"
                      f"&fileId={urllib.parse.quote(fid)}&token={urllib.parse.quote(token)}")
        try:
            _upload_stream(upload_url, path, ctx, args.accept_timeout)
            ok += 1
            print(f"  ✓ {name}")
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"  ✗ {name}: {e}", file=sys.stderr)

    print(f"done: {ok}/{len(tokens)} uploaded")
    return 0 if ok == len(tokens) else 1


if __name__ == "__main__":
    sys.exit(main())
