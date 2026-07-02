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

Exit codes: 0 ok, 2 usage, 3 rejected/timeout, 4 PIN required/wrong,
5 receiver busy, 1 other error.
"""
import argparse
import json
import mimetypes
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "/api/localsend/v2"


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


def _post_bytes(url, raw, ctx, timeout):
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    return urllib.request.urlopen(req, context=ctx, timeout=timeout)


def main(argv=None):
    p = argparse.ArgumentParser(description="Send files to a LocalSend receiver by IP.")
    p.add_argument("files", nargs="+", help="one or more file paths")
    p.add_argument("--host", required=True, help="receiver IP or hostname")
    p.add_argument("--port", type=int, default=53317, help="receiver port (default 53317)")
    p.add_argument("--pin", default=None, help="transfer PIN, if the receiver requires one")
    p.add_argument("--http", action="store_true", help="use http instead of https")
    p.add_argument("--insecure", action="store_true",
                   help="(default for https) accept self-signed certs")
    p.add_argument("--accept-timeout", type=int, default=200,
                   help="seconds to wait for the receiver to accept (default 200)")
    args = p.parse_args(argv)

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

    print(f"→ {args.host}:{args.port}  {len(paths)} file(s)  "
          f"(waiting up to {args.accept_timeout}s for accept…)", flush=True)
    try:
        resp = _post(prepare_url, {"info": _client_info(), "files": files_meta},
                     ctx, args.accept_timeout)
        grant = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("rejected: PIN required or incorrect (use --pin)", file=sys.stderr)
            return 4
        if e.code == 403:
            print("rejected: the receiver declined or timed out", file=sys.stderr)
            return 3
        if e.code == 409:
            print("busy: the receiver is handling another transfer", file=sys.stderr)
            return 5
        print(f"error: prepare-upload failed: HTTP {e.code} {e.read().decode()[:200]}",
              file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"error: cannot reach receiver: {e}", file=sys.stderr)
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
            with open(path, "rb") as fh:
                _post_bytes(upload_url, fh.read(), ctx, args.accept_timeout)
            ok += 1
            print(f"  ✓ {name}")
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"  ✗ {name}: {e}", file=sys.stderr)

    print(f"done: {ok}/{len(tokens)} uploaded")
    return 0 if ok == len(tokens) else 1


if __name__ == "__main__":
    sys.exit(main())
