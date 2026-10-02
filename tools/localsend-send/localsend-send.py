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
    localsend-send.py --name "Sunny Falcon" notes.md  # DNS, LAN discovery, cloud fallback
    localsend-send.py --cloud --name "Sunny Falcon" notes.md  # skip LAN, push now
    localsend-send.py --cloud-pin --pin 1964 --name "Sunny Falcon"  # pin + transfer PIN
    localsend-send.py --localsend --name "Sunny Falcon" x.md  # override the pin once
    localsend-send.py --set-default --name "Sunny Falcon"     # bare runs send here
    localsend-send.py --list                          # LAN clients + registered devices
    localsend-send.py --enrol --server https://push.example.com

Exit codes: 0 ok, 2 usage, 3 rejected/timeout / device not found and no creds,
4 PIN required/wrong, 5 receiver busy, 6 server refused / trust failure,
1 other error.
"""
import argparse
import base64
import hashlib
import hmac
import http.client
import json
import mimetypes
import os
import queue
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Keep in step with the packaging manifests (packaging/).
__version__ = "1.0.19"

API = "/api/localsend/v2"
CLOUD_API = "/api"  # cloud-push server base (NOT the LocalSend LAN protocol)

# Every request carries this User-Agent. Cloudflare-fronted servers answer the
# default "Python-urllib/3.x" signature with 403 error code 1010 (banned browser
# signature), which breaks the cloud-push path (enrol, token, push).
USER_AGENT = f"mdrender-send/{__version__}"

# How long `--enrol` waits for the operator to complete registration — by
# approving in the browser or typing the short code.
ENROL_WAIT_SECONDS = 120


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
    req.add_header("User-Agent", USER_AGENT)
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
    conn.putheader("User-Agent", USER_AGENT)
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
    /api/enrol/start. The operator completes the enrolment in the browser at
    the verification_uri; the CLI then collects the credentials (see
    _wait_for_approval), so nothing needs to be copied between the two.
    """
    base = f"{server_url.rstrip('/')}{CLOUD_API}"
    start = json.loads(_post(f"{base}/enrol/start",
                             {"info": _client_info()}, None, 30).read())
    return start["enrolment_id"], start["verification_uri"]


def _default_creds_path():
    return os.path.expanduser("~/.config/mdrender/push-credentials.json")


def _default_cloud_pins_path():
    return os.path.expanduser("~/.config/mdrender/cloud-pins.json")


def load_cloud_pins(path=None):
    """Per-device pin records: cloud routing, transfer PIN, default device.

    A record looks like ``{"pinned_at": <ts>, "pin": "1964", "default": true}``:

    * ``pinned_at`` present  -> the device is cloud-pinned (--cloud-pin);
      presence of the record itself means nothing.
    * ``pin``                 -> the receiver's LocalSend transfer PIN,
      applied automatically on LAN runs unless --pin overrides it.
    * ``default``             -> the device is the default target for runs
      with no --name/--host (--set-default).

    Pure routing state (not security). Unreadable or missing files pin
    nothing.
    """
    path = path or _default_cloud_pins_path()
    try:
        with open(path) as fh:
            pins = json.load(fh)
        return pins if isinstance(pins, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cloud_pins(pins, path=None):
    """Write the device-pin map (mode 0600, like push credentials)."""
    path = path or _default_cloud_pins_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(pins, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fchmod(fh.fileno(), 0o600)
    return pins


def _effective_transfer_pin(args):
    """The LocalSend transfer PIN for this run.

    An explicit --pin wins; otherwise the target device's stored record
    (from ``--cloud-pin --name X --pin N``) supplies it. Returns None when
    the device has none (or no --name was given).
    """
    if args.pin:
        return args.pin
    if args.name:
        rec = load_cloud_pins().get(args.name)
        if isinstance(rec, dict):
            return rec.get("pin")
    return None


def _default_device_name():
    """The device marked with --set-default, if any."""
    for name, rec in load_cloud_pins().items():
        if isinstance(rec, dict) and rec.get("default"):
            return name
    return None


def _write_creds(creds, path):
    """Write a credentials record to *path* with 0600 permissions."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(creds, fh)
        fh.flush()
        os.fchmod(fh.fileno(), 0o600)
    return creds


def _enrol_exchange(server_url, enrolment_id, code):
    """Exchange a manually entered code for credentials (no file write)."""
    base = f"{server_url.rstrip('/')}{CLOUD_API}"
    resp = _post(f"{base}/enrol",
                 {"enrolment_id": enrolment_id, "code": code}, None, 30)
    return json.loads(resp.read())


def _enrol_flow(server_url, enrolment_id, code, creds_path=None):
    """Complete the manual code exchange and persist the credentials.

    Submits *code* against *enrolment_id* (minted earlier by _start_enrolment)
    to /api/enrol, then writes the resulting credentials
    ({client_id, client_secret, server_url}) to *creds_path* with 0600
    permissions. Returns the creds dict that was written.
    """
    creds = _enrol_exchange(server_url, enrolment_id, code)
    record = {**creds, "server_url": server_url}
    return _write_creds(record, creds_path or _default_creds_path())


def _await_enrolment(server_url, enrolment_id, timeout=ENROL_WAIT_SECONDS,
                     interval=2.0):
    """Finish an enrolment by whichever path happens first.

    Both completion paths run concurrently so neither locks out the other:
    a worker polls /api/enrol/complete for the browser approval while this
    thread reads the short code from stdin. That is what lets a code be typed
    while an approval request is in flight (and vice versa), instead of the
    HTTP loop owning the terminal for the request's duration.

      * browser: the operator clicks Complete registration, which the poll
        reports as approved; or
      * headless/remote: the operator types the short code shown at
        /enrol/<id>, which /api/enrol exchanges for credentials.

    Returns the credentials record to persist (without writing it), or None on
    timeout/expiry.
    """
    base = f"{server_url.rstrip('/')}{CLOUD_API}"
    outcome = queue.Queue()  # ("ok", creds) | ("gone", None)
    stop = threading.Event()
    deadline = time.monotonic() + timeout

    def poll_approval():
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                resp = _post(f"{base}/enrol/complete",
                             {"enrolment_id": enrolment_id}, None, 30)
                data = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code in (401, 404):  # expired or already consumed
                    outcome.put(("gone", None))
                    return
                raise
            except (urllib.error.URLError, OSError):
                stop.wait(interval)
                continue
            if data.get("status") == "approved":
                outcome.put(("ok", {"client_id": data["client_id"],
                                    "client_secret": data["client_secret"],
                                    "server_url": server_url}))
                return
            stop.wait(interval)

    def read_code():
        if sys.stdin is None:
            return
        while not stop.is_set():
            try:
                line = sys.stdin.readline()
            except (OSError, ValueError):
                return
            if line == "":  # EOF
                return
            code = line.strip()
            if not code:
                continue
            try:
                creds = _enrol_exchange(server_url, enrolment_id, code)
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                print("  code not accepted (expired or wrong); click 'New "
                      "code' on the page, or 'Complete registration'…",
                      file=sys.stderr)
                continue
            outcome.put(("ok", {"client_id": creds["client_id"],
                                "client_secret": creds["client_secret"],
                                "server_url": server_url}))
            return

    threading.Thread(target=poll_approval, daemon=True).start()
    threading.Thread(target=read_code, daemon=True).start()

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                kind, creds = outcome.get(timeout=remaining)
            except queue.Empty:
                return None
            if kind == "ok":
                return creds
            # The browser poll found the enrolment gone — most likely the code
            # exchange just consumed it. Give that a moment to report first.
            try:
                kind, creds = outcome.get(
                    timeout=min(1.5, max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                return None
            return creds if kind == "ok" else None
    finally:
        stop.set()


def _open_browser(uri):
    """Open *uri* fully detached from this terminal.

    The browser must not inherit the CLI's stdin/stdout/stderr: a Chromium
    started by xdg-open writes startup logs to the tty, which clobbers the
    prompt and can swallow typed input.
    """
    try:
        subprocess.Popen(
            ["xdg-open", uri],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass  # headless or no handler; the URL is printed for manual opening


class ClientAuthError(Exception):
    """The server rejected this client's credentials (removed or revoked)."""


_RE_REGISTER_HINT = ("re-register this client with: "
                     "mdrender-send --enrol --server <server-url>")


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
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ClientAuthError(
                "this client is not registered on the server (or was revoked); "
                + _RE_REGISTER_HINT) from e
        raise


def get_access_token(creds_path, server_url=None):
    """Load push credentials and return a fresh cloud access token.

    This is the interface the push path (Task C3) consumes.
    """
    with open(creds_path) as fh:
        creds = json.load(fh)
    server_url = server_url or creds["server_url"]
    return _get_token(creds, server_url)


def list_push_devices(creds_path, server_url=None):
    """Fetch the registered push targets the server can send to.

    Returns a list of {name, registered_at, last_seen}. Raises on HTTP/IO
    errors so the caller can report them.
    """
    with open(creds_path) as fh:
        creds = json.load(fh)
    server_url = (server_url or creds["server_url"]).rstrip("/")
    req = urllib.request.Request(f"{server_url}/api/devices", method="GET")
    req.add_header("Authorization", f"Bearer {_get_token(creds, server_url)}")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["devices"]


# --- Server-blind cloud push (design §7a) -------------------------------
#
# Against an ENCRYPTION_MODE=on server every file crosses the wire as one
# opaque blob: AES-256-GCM under a content key (CEK) derived from a local
# account master secret, with the real filename and destination folder
# inside the encrypted envelope. The server stores the blob verbatim under
# an opaque id and can never read any of it.


class EncryptedPushError(Exception):
    """Encrypted-push setup failure; carries the process exit code."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def _crypto_modules():
    """Lazy import: the LAN/plaintext paths stay standard-library only."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise EncryptedPushError(
            "this server requires encryption, but the 'cryptography' package "
            "is not installed — install it and retry (Arch: pacman -S "
            "python-cryptography; Debian/Ubuntu: apt install "
            "python3-cryptography; or: pip install cryptography)", 6) from e
    return hashes, serialization, padding, AESGCM


def _content_key_path():
    return os.path.expanduser("~/.config/mdrender/content-key")


def _pins_path():
    return os.path.expanduser("~/.config/mdrender/pins.json")


def load_account_master_secret(path=None):
    """Load or create the account master secret (AMS): 32 bytes, mode 0600.

    The AMS is the content-key root shared by an account's push clients
    (design §7a) and is never uploaded; copies of this file on other
    machines must be byte-identical for them to derive the same CEK.
    """
    path = path or _content_key_path()
    if os.path.exists(path):
        try:
            with open(path) as fh:
                ams = bytes.fromhex(fh.read().strip())
        except (OSError, ValueError) as e:
            raise EncryptedPushError(
                f"content-key file {path} is unreadable or corrupt: {e}; "
                "fix or delete it (deleting rotates the content key — "
                "already-pushed files stay sealed to the old one)", 6)
        if len(ams) != 32:
            raise EncryptedPushError(
                f"content-key file {path} must hold 32 bytes", 6)
        return ams
    ams = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(ams.hex() + "\n")
    return ams


def hkdf_sha256(ikm, salt=b"", info=b"", length=32):
    """RFC 5869 HKDF-SHA256 (extract-then-expand), stdlib only."""
    if not salt:
        salt = b"\x00" * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    t = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def fetch_policy(server_url):
    """GET /api/server/policy -> "on" / "off".

    An unreachable probe reads as "off"; the server's own 422 gate still
    refuses plaintext if the guess was wrong.
    """
    try:
        req = urllib.request.Request(
            f"{server_url.rstrip('/')}/api/server/policy")
        req.add_header("User-Agent", USER_AGENT)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("encryption", "off")
    except Exception:  # noqa: BLE001 - probe is advisory; the gate decides
        return "off"


def _pin_content_key(server_url, device, content_pubkey_b64):
    """TOFU pin of the device's content key (§7c). Returns the fingerprint.

    First sight stores and prints the fingerprint; any later change is a
    hard error, because a swapped content key would let the server read
    everything pushed from then on.
    """
    fp = "sha256:" + hashlib.sha256(
        base64.b64decode(content_pubkey_b64)).hexdigest()
    path = _pins_path()
    pins = {}
    if os.path.exists(path):
        try:
            with open(path) as fh:
                pins = json.load(fh)
        except (OSError, ValueError) as e:
            raise EncryptedPushError(f"cannot read pin store {path}: {e}", 6)
    key = f"{server_url}|{device}"
    stored = pins.get(key)
    if stored is None:
        pins[key] = {"fingerprint": fp, "pinned_at": int(time.time())}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(pins, fh, indent=2, sort_keys=True)
        print(f"pinned content key for {device} on {server_url}: {fp}",
              file=sys.stderr)
        return fp
    if stored.get("fingerprint") != fp:
        raise EncryptedPushError(
            f"content key for {device} on {server_url} CHANGED\n"
            f"  pinned: {stored.get('fingerprint')}\n"
            f"  now:    {fp}\n"
            "refusing to seal — possible key substitution (design §7c). If the "
            "device was legitimately re-paired, delete that entry from "
            f"{path} and retry.", 6)
    return fp


def _encrypted_session(server_url, token, target_device):
    """Fetch, verify and pin the device's content key; seal the CEK to it.

    Returns (cek, sealed_cek_b64) ready for the push request.
    """
    hashes, serialization, padding, _ = _crypto_modules()
    url = (f"{server_url}/api/push/content-key"
           f"?device={urllib.parse.quote(target_device)}")
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            info = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 404:
            raise EncryptedPushError(
                "the server has no content key for this device — pair it with "
                "a current app version (which registers its content key at "
                "pairing) and retry", 3)
        raise EncryptedPushError(
            f"content-key lookup failed: HTTP {e.code} {body[:200]}", 1)

    # §7c: the pairing key must verify the content key's proof. This catches
    # a broken key chain; the TOFU pin below catches a *substituted* one.
    try:
        pairing_pub = serialization.load_der_public_key(
            base64.b64decode(info["device_public_key"]))
        pairing_pub.verify(
            base64.b64decode(info["content_proof"]),
            b"content:" + info["content_pubkey"].encode(),
            padding.PKCS1v15(), hashes.SHA256())
    except EncryptedPushError:
        raise
    except Exception as e:
        raise EncryptedPushError(
            "content-key proof failed to verify against the device's pairing "
            "key — refusing to seal (design §7c)", 6) from e

    _pin_content_key(server_url, target_device, info["content_pubkey"])

    content_pub = serialization.load_der_public_key(
        base64.b64decode(info["content_pubkey"]))
    cek = hkdf_sha256(load_account_master_secret(),
                       info=b"mdrender-content", length=32)
    # OAEP message digest SHA-256, MGF1 digest SHA-1: Android Keystore's OAEP
    # profile (its "RSA/ECB/OAEPWithSHA-256AndMGF1Padding" maps to exactly
    # this). MGF1-SHA256 is rejected at unwrap time as INCOMPATIBLE_MGF_DIGEST.
    sealed = content_pub.encrypt(cek, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA1()),
        algorithm=hashes.SHA256(), label=None))
    return cek, base64.b64encode(sealed).decode()


def encrypt_push_blob(file_bytes, name, folder, cek):
    """Seal one file into the wire blob: nonce(12) || AES-GCM(envelope).

    envelope = u32be(len(header)) || header_json || file_bytes, where
    header carries the original filename and destination folder — the only
    place they exist in transit or at rest on the server (§7a/D13). Each
    blob is self-describing: the app reads the nonce from its prefix.
    """
    _, _, _, AESGCM = _crypto_modules()
    header = json.dumps({"name": name, "path": folder or ""},
                        separators=(",", ":")).encode()
    plaintext = struct.pack(">I", len(header)) + header + file_bytes
    nonce = secrets.token_bytes(12)
    return nonce, nonce + AESGCM(cek).encrypt(nonce, plaintext, None)


def _discover(timeout=3.0, want=None):
    """Best-effort LocalSend v2 UDP discovery: maps alias -> info dict.

    Announces to the LocalSend multicast group (224.0.0.167:53317) and collects
    each receiver's reply. The app only answers announces (it drops messages
    where announce is not true), so the multicast group -- NOT
    255.255.255.255 -- and the announce flag are both load-bearing. `want` limits
    the search to those aliases and stops early once all are found; None collects
    everyone. Same-subnet only; never raises; returns whatever was found.
    """
    found = {}
    want = set(want) if want is not None else None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return found
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)
        msg = json.dumps({**_client_info(), "announce": True}).encode()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and (want is None or want):
            try:
                sock.sendto(msg, ("224.0.0.167", 53317))
            except OSError:
                pass  # best-effort: keep trying until the deadline
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                info = json.loads(data)
            except ValueError:
                continue
            alias = info.get("alias")
            if not alias:
                continue
            if want is not None and alias not in want:
                continue
            found[alias] = {
                "ip": addr[0],
                "port": info.get("port", 53317),
                "protocol": info.get("protocol", "https"),
                "version": info.get("version", ""),
                "app": info.get("app", ""),
                "deviceModel": info.get("deviceModel", ""),
                "deviceType": info.get("deviceType", ""),
                "extensions": info.get("extensions") or [],
            }
            if want is not None:
                want.discard(alias)
    finally:
        sock.close()
    return found


def discover_lan(names, timeout=3.0):
    """Discover specific aliases on the LAN; maps alias -> ip (see _discover)."""
    return {alias: info["ip"]
            for alias, info in _discover(timeout=timeout, want=names).items()}


def is_mdrender(info):
    """True when a discovered device is an MDRender receiver.

    LocalSend's hello has no vendor field, so MDRender adds its own. Accept any
    of the signals it advertises: the `app` name, the `mds` extension, or a
    deviceModel that says MDRender (covers older/newer variants).
    """
    if "mds" in (info.get("extensions") or []):
        return True
    if "mdrender" in str(info.get("app", "")).lower():
        return True
    return "mdrender" in str(info.get("deviceModel", "")).lower()


def resolve_dns(name):
    """Resolve a device name through the local resolver (hosts/DNS/mDNS).

    Tries the name as given, a slug form for names containing spaces, and the
    mDNS `.local` variant, so both a configured DNS entry and a Bonjour/avahi
    name resolve. Returns an IPv4 address string, or None.
    """
    candidates = [name]
    slug = name.strip().lower().replace(" ", "-")
    if slug != name:
        candidates.append(slug)
    for host in (slug, name):
        if host and "." not in host:
            candidates.append(f"{host}.local")
    for host in dict.fromkeys(candidates):  # de-dupe, keep order
        try:
            return socket.gethostbyname(host)
        except OSError:
            continue
    return None


def resolve_name(name, timeout=3.0):
    """Resolve a device name to a LAN IP: local DNS first, then discovery.

    DNS covers names the system resolver knows (hosts file, local DNS, mDNS
    `.local`); UDP discovery covers the app's own alias broadcast. Returns the
    IP, or None if neither found it so the caller can fall back to cloud push.
    """
    ip = resolve_dns(name)
    if ip:
        return ip
    return discover_lan([name], timeout=timeout).get(name)


def push_to_server(creds_path, target_device, paths, *, folder=None, conflict=None):
    """Push files to a registered device via the cloud-push server.

    Fetches a fresh OAuth2 token from *creds_path* (C2's get_access_token),
    then POSTs a multipart/form-data body to {server_url}/api/push carrying a
    target_device text part and one file part per path. Prints per-file results
    and a summary; returns 0 on success (files_sent >= 1), 1 on any failure.

    *folder* and *conflict* mirror the LocalSend "mds" options so the cloud path
    behaves like a direct send: the same destination folder and the same
    collision behaviour.

    When the server's policy is encryption=on (§7b), each file is sealed
    client-side into an opaque blob (§7a): AES-256-GCM under the content key,
    with the original filename and folder inside the envelope, and the content
    key sealed to the device's content public key. The server then stores only
    ciphertext under an opaque id and can never read any of it.
    """
    try:
        token = get_access_token(creds_path)
    except ClientAuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 6
    with open(creds_path) as fh:
        server_url = json.load(fh)["server_url"].rstrip("/")

    # §7b: an encryption-on server accepts only opaque blobs. Probe the
    # policy before building the body so plaintext never hits it.
    encrypted = fetch_policy(server_url) == "on"
    cek = sealed_cek = None
    if encrypted:
        try:
            cek, sealed_cek = _encrypted_session(
                server_url, token, target_device)
        except EncryptedPushError as e:
            print(f"error: {e}", file=sys.stderr)
            return e.code

    # Read and (when required) seal every file up front: the encryption
    # metadata is per-push, so the first blob's nonce rides in the form.
    blobs = []
    for path in paths:
        with open(path, "rb") as fh:
            data = fh.read()
        if encrypted:
            nonce, blob = encrypt_push_blob(
                data, os.path.basename(path), folder, cek)
            blobs.append((path, blob, nonce))
        else:
            blobs.append((path, data, None))

    boundary = f"----mdrender{uuid.uuid4().hex}"
    parts = []

    def text_part(field, value):
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{field}"'.encode())
        parts.append(b"Content-Type: text/plain")
        parts.append(b"")
        parts.append(value.encode())

    # Text part: the target device name, plus the destination folder and
    # conflict strategy when set (the server reads target_folder/conflict).
    text_part("target_device", target_device)
    if encrypted:
        # No target_folder: the folder travels inside each envelope (D13).
        # alg/nonce satisfy the server's encryption-metadata gate (§7b);
        # every blob is self-describing (nonce prefix), so the form nonce
        # is the first file's — the server stores neither field.
        text_part("alg", "aes-256-gcm")
        text_part("nonce", base64.b64encode(blobs[0][2]).decode())
        text_part("sealed_cek", sealed_cek)
    elif folder:
        text_part("target_folder", folder)
    if conflict:
        text_part("conflict", conflict)
    # One file part per path: the opaque blob when encrypted (the original
    # filename never reaches the server), the plain file otherwise.
    for path, data, nonce in blobs:
        part_name = "blob" if nonce is not None else os.path.basename(path)
        parts.append(f"--{boundary}".encode())
        parts.append(
            f'Content-Disposition: form-data; name="file"; filename="{part_name}"'.encode())
        parts.append(b"Content-Type: application/octet-stream")
        parts.append(b"")
        parts.append(data)
    parts.append(f"--{boundary}--".encode())
    body = b"\r\n".join(parts)

    req = urllib.request.Request(f"{server_url}/api/push", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            reply = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code in (401, 403):
            # The server refused the bearer token: the client was removed.
            detail = ""
            try:
                detail = json.loads(body).get("detail", "")
            except ValueError:
                pass
            print("error: push refused — " + (detail or
                  "this client is no longer registered; " + _RE_REGISTER_HINT),
                  file=sys.stderr)
            return 6
        if e.code in (422, 428):
            print("hint: the server requires encryption (design §7b) — this "
                  "client supports it; retry", file=sys.stderr)
        print(f"error: cloud push failed: HTTP {e.code} {body[:200]}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"error: cloud push failed: {e}", file=sys.stderr)
        return 1

    sent = reply.get("files_sent", 0)
    for path in paths:
        print(f"  ✓ {os.path.basename(path)}")
    push_id = reply.get("push_id", "?")
    print(f"cloud push to {target_device}: {sent} file(s) sent "
          f"(push {push_id}{', encrypted' if encrypted else ''})")
    return 0 if sent >= 1 else 1


def cmd_enrol(args):
    """Enrol an OAuth client: browser approval or a pasted code, then save."""
    path = args.creds or _default_creds_path()
    eid, uri = _start_enrolment(args.server)
    print("Complete the registration either way:")
    print("  * browser: open this link, sign in, click 'Complete registration':")
    print(f"      {uri}")
    print("  * headless / remote: open that link elsewhere and read the code.")
    if os.environ.get("DISPLAY"):
        _open_browser(uri)
    print(f"Waiting up to {ENROL_WAIT_SECONDS}s for either method…", flush=True)
    print("Enter the one-time code here (or approve in the browser):")
    print("code> ", end="", flush=True)
    creds = _await_enrolment(args.server, eid)
    print()  # leave the prompt line cleanly whatever completed it
    if creds is None:
        print("error: registration was not completed (timed out or expired)",
              file=sys.stderr)
        return 3
    _write_creds(creds, path)
    print(f"registered; wrote {path} (0600)")
    return 0


def _fmt_ts(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return "-"


def _device_names(args):
    """Every known device name: LAN aliases plus the server's registered ones.

    Kept as a plain function so `--list --names` and bash completion share one
    implementation. Failures are swallowed: completion just gets fewer names.
    """
    names = sorted(_discover(timeout=args.discover_timeout))

    creds = args.creds or _default_creds_path()
    if args.server or os.path.exists(creds):
        try:
            names += [d["name"] for d in list_push_devices(creds, args.server)]
        except Exception:  # noqa: BLE001 - completion must never error out
            pass
    names += list(load_cloud_pins())
    return list(dict.fromkeys(names))  # de-dupe, keep order


def cmd_list(args):
    """List LocalSend clients on the LAN and the server's registered devices.

    The LAN list flags MDRender receivers (which advertise the "mds"
    extension), so it is clear which ones accept --folder/--conflict.
    """
    if getattr(args, "names", False):
        for name in _device_names(args):
            print(name)
        return 0

    print(f"Discovering LocalSend clients on the LAN "
          f"(~{args.discover_timeout:g}s)…", flush=True)
    clients = _discover(timeout=args.discover_timeout)
    if clients:
        print(f"\nLocalSend clients ({len(clients)}):")
        for alias in sorted(clients):
            info = clients[alias]
            addr = f"{info['ip']}:{info['port']}"
            model = info.get("deviceModel") or "?"
            support = ("MDRender (mds: folder, conflict)"
                       if is_mdrender(info) else "LocalSend")
            print(f"  {alias}  [{addr}, {info['protocol']}]  {model}  {support}")
    else:
        print("\nNo LocalSend clients found on the LAN.")

    creds = args.creds or _default_creds_path()
    if args.server or os.path.exists(creds):
        server_url = args.server
        try:
            if not server_url:
                with open(creds) as fh:
                    server_url = json.load(fh)["server_url"]
            devices = list_push_devices(creds, args.server)
        except Exception as e:  # noqa: BLE001 - report, keep the LAN list
            print(f"\ncould not list registered push devices: {e}",
                  file=sys.stderr)
        else:
            pins = load_cloud_pins()
            print(f"\nRegistered push devices on {server_url} ({len(devices)}):")
            if devices:
                for d in devices:
                    rec = pins.get(d["name"])
                    rec = rec if isinstance(rec, dict) else {}
                    marks = []
                    if "pinned_at" in rec:
                        marks.append("cloud-pinned")
                    if rec.get("default"):
                        marks.append("default")
                    mark = f"  [{', '.join(marks)}]" if marks else ""
                    print(f"  {d['name']}  (registered {_fmt_ts(d['registered_at'])},"
                          f" last seen {_fmt_ts(d['last_seen'])}){mark}")
            else:
                print("  (none)")
    else:
        print("\n(no push credentials; skipping registered push devices)")
    return 0


def _cmd_pin_action(args):
    """Config actions: --cloud-pin/--cloud-unpin/--set-default/--clear-default."""
    n_actions = sum((args.cloud_pin, args.cloud_unpin,
                     args.set_default, args.clear_default))
    if n_actions > 1:
        print("error: config actions (--cloud-pin/--cloud-unpin/"
              "--set-default/--clear-default) are mutually exclusive",
              file=sys.stderr)
        return 2
    if args.cloud or args.localsend:
        print("error: config actions (--cloud-pin/--cloud-unpin/"
              "--set-default/--clear-default) are not push modes; "
              "do not combine them with --cloud/--localsend", file=sys.stderr)
        return 2
    if not args.name:
        print("error: config actions require --name <device>", file=sys.stderr)
        return 2
    if args.files:
        print("error: config actions take no files", file=sys.stderr)
        return 2

    pins = load_cloud_pins()
    rec = pins.get(args.name)
    rec = dict(rec) if isinstance(rec, dict) else {}

    if args.set_default:
        # Exactly one default: demote any other holder (and drop records
        # that end up empty).
        drop = []
        for other, other_rec in pins.items():
            if other == args.name or not isinstance(other_rec, dict):
                continue
            other_rec.pop("default", None)
            if not other_rec:
                drop.append(other)
        for other in drop:
            del pins[other]
        rec["default"] = True
        pins[args.name] = rec
        save_cloud_pins(pins)
        print(f"set {args.name} as the default device "
              f"(runs with no --name/--host send here)")
        return 0
    if args.clear_default:
        if not rec.pop("default", None):
            print(f"{args.name} was not the default device")
            return 0
        if rec:
            pins[args.name] = rec
        else:
            pins.pop(args.name, None)
        save_cloud_pins(pins)
        print(f"cleared {args.name} as the default device")
        return 0
    if args.cloud_unpin:
        if rec.pop("pinned_at", None) is None:
            print(f"{args.name} was not cloud-pinned")
            return 0
        if rec:
            pins[args.name] = rec
        else:
            pins.pop(args.name, None)
        save_cloud_pins(pins)
        print(f"unpinned {args.name}: --name will try the LAN again")
        return 0

    # --cloud-pin: record (or refresh) the cloud pin, keeping any stored
    # transfer PIN and default-device flag. --pin updates the transfer PIN.
    rec["pinned_at"] = int(time.time())
    if args.pin:
        rec["pin"] = args.pin
    pins[args.name] = rec
    save_cloud_pins(pins)
    print(f"pinned {args.name} to cloud push "
          f"(--name skips the LAN lookup; --localsend overrides once)")
    if args.pin:
        print(f"stored transfer PIN {args.pin} "
              f"(used automatically for LAN transfers; --pin overrides)")
    return 0


def _build_parser():
    p = argparse.ArgumentParser(
        description="Send files to a LocalSend receiver by IP, or enrol this "
                    "CLI with the cloud-push server.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("files", nargs="*", help="one or more file paths")
    p.add_argument("--host", default=None, help="receiver IP or hostname")
    p.add_argument("--name", default=None,
                   help="device name: local DNS, then LAN discovery, else cloud push")
    p.add_argument("--cloud", action="store_true",
                   help="with --name: push via the cloud-push server, "
                        "skip the LAN lookup entirely")
    p.add_argument("--localsend", action="store_true",
                   help="with --name: force the LocalSend LAN lookup even if "
                        "the device is cloud-pinned (falls back to cloud push "
                        "if the device is not found on the LAN)")
    p.add_argument("--cloud-pin", action="store_true",
                   help="pin --name's device to always use cloud push (no "
                        "LAN lookup); stored in ~/.config/mdrender/cloud-pins.json. "
                        "Records --pin as the device's transfer PIN too")
    p.add_argument("--cloud-unpin", action="store_true",
                   help="remove the device's cloud pin (with --name); keeps "
                        "its stored transfer PIN and default-device flag")
    p.add_argument("--set-default", action="store_true",
                   help="make --name's device the default target for runs "
                        "with no --name/--host (works for LocalSend and "
                        "cloud-pinned devices alike)")
    p.add_argument("--clear-default", action="store_true",
                   help="clear the default device (with --name)")
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
    p.add_argument("--list", action="store_true",
                   help="list LocalSend clients found on the LAN and the cloud "
                        "push server's registered devices")
    p.add_argument("--discover-timeout", type=float, default=3.0,
                   help="seconds to listen for LAN discovery replies with --list "
                        "(default 3)")
    p.add_argument("--names", action="store_true",
                   help="with --list, print device names only, one per line "
                        "(used by shell completion)")
    p.add_argument("--enrol", action="store_true",
                   help="enrol an OAuth client with the cloud-push server (requires --server)")
    p.add_argument("--server", default=None,
                   help="cloud-push server base URL, e.g. https://push.example.com "
                        "(required with --enrol; optional with --list)")
    p.add_argument("--creds", default=None,
                   help="path for the push credentials JSON "
                        "(default ~/.config/mdrender/push-credentials.json)")
    p.add_argument("--completion", nargs="?", const="bash", choices=["bash"],
                   metavar="SHELL",
                   help="print a shell completion script to stdout and exit "
                        "(default: bash)")
    return p


def _bash_completion_script():
    """A bash completion script generated from the parser's own arguments.

    Generating it from the parser keeps completion in step with the CLI: every
    option and its choices is reflected automatically. Load it with:

        source <(mdrender-send --completion bash)

    or install it system-wide:

        mdrender-send --completion bash > /etc/bash_completion.d/mdrender-send
    """
    parser = _build_parser()
    options = []
    choices_by_option = {}
    for action in parser._actions:
        for opt in action.option_strings:
            options.append(opt)
            if action.choices:
                choices_by_option[opt] = [str(c) for c in action.choices]
    options = sorted(set(options))
    choice_cases = "\n".join(
        f'        {opt}) COMPREPLY=( $(compgen -W "{" ".join(vals)}" -- "$cur") ); return 0 ;;'
        for opt, vals in sorted(choices_by_option.items())
    )
    # Same choices for the --opt=value form (bash may not split on '=').
    choice_cases_eq = "\n".join(
        f'        {opt}=) COMPREPLY=( $(compgen -W "{" ".join(vals)}" -P "{opt}=" '
        f'-- "$val") ); return 0 ;;'
        for opt, vals in sorted(choices_by_option.items())
    )

    return f"""\
# bash completion for mdrender-send (localsend-send.py).
# Load with:  source <(mdrender-send --completion bash)
# Install:    mdrender-send --completion bash > /etc/bash_completion.d/mdrender-send
# --name completes device names gathered from:  mdrender-send --list --names
# Running from a source checkout?  export MDRENDER_SEND=/path/to/localsend-send.py

_mdrender_send_names() {{
    local cache="${{TMPDIR:-/tmp}}/mdrender-send-devices"
    if [ -s "$cache" ] && [ -z "$(find "$cache" -mmin +1 2>/dev/null)" ]; then
        cat "$cache"
    else
        "${{MDRENDER_SEND:-mdrender-send}}" --list --names --discover-timeout 1 2>/dev/null > "$cache"
        cat "$cache"
    fi
}}

_mdrender_send() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # --opt=value form: bash splits the word at '=', so the option is two
    # words back from the value and the previous word is a lone '='.
    local opt="" val="$cur"
    if [[ "$prev" == "=" && $COMP_CWORD -ge 2 ]]; then
        opt="${{COMP_WORDS[COMP_CWORD-2]}}="
    elif [[ "$cur" == --*=* ]]; then
        opt="${{cur%%=*}}="
        val="${{cur#*=}}"
    fi
    case "$opt" in
        --name=)
            local n_eq
            while IFS= read -r n_eq; do
                [ -n "$n_eq" ] || continue
                [[ "$n_eq" == "$val"* ]] && COMPREPLY+=( "$(printf '%q' "$opt$n_eq")" )
            done < <(_mdrender_send_names)
            return 0 ;;
{choice_cases_eq}
    esac

    case "$prev" in
{choice_cases}
        --name)
            local n
            while IFS= read -r n; do
                [ -n "$n" ] || continue
                [[ "$n" == "$cur"* ]] && COMPREPLY+=( "$(printf '%q' "$n")" )
            done < <(_mdrender_send_names)
            return 0 ;;
    esac

    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "{' '.join(options)}" -- "$cur") )
        return 0
    fi

    # Files to send: defer to readline's own filename completion, so ~, spaces,
    # directories, and single-match insertion all behave exactly as usual.
    compopt -o default 2>/dev/null || true
    COMPREPLY=()
}}
complete -o default -F _mdrender_send mdrender-send localsend-send.py
"""


def main(argv=None):
    p = _build_parser()
    args = p.parse_args(argv)

    if args.completion:
        print(_bash_completion_script())
        return 0

    if args.enrol:
        if not args.server:
            print("error: --enrol requires --server <URL>", file=sys.stderr)
            return 2
        return cmd_enrol(args)

    if args.list or args.names:
        return cmd_list(args)

    if args.cloud_pin or args.cloud_unpin or args.set_default \
            or args.clear_default:
        return _cmd_pin_action(args)

    # No target given: fall back to the pinned default device so a bare
    # `mdrender-send file.md` just works. Explicit --name/--host win.
    if not args.name and not args.host:
        default_name = _default_device_name()
        if default_name:
            args.name = default_name

    if (args.cloud or args.localsend) and not args.name:
        print("error: --cloud/--localsend require --name <device>", file=sys.stderr)
        return 2
    if args.cloud and args.localsend:
        print("error: --cloud and --localsend are mutually exclusive",
              file=sys.stderr)
        return 2

    if not args.files:
        print("error: at least one file is required", file=sys.stderr)
        return 2
    if not args.name and not args.host:
        print("error: --host <IP or hostname> or --name <device name> is "
              "required (or set one with --set-default)", file=sys.stderr)
        return 2

    paths = []
    for f in args.files:
        if not os.path.isfile(f):
            print(f"error: not a file: {f}", file=sys.stderr)
            return 2
        paths.append(f)

    # --name routing: --cloud forces the cloud server, --localsend forces the
    # LAN probe, a cloud pin (recorded by --cloud-pin) makes the server the
    # default for that name, and otherwise resolve on the LAN (local DNS
    # first, then LocalSend UDP discovery) and fall back to the cloud-push
    # server when push credentials exist. --host stays direct with no
    # fallback (spec), and --name wins if both are given.
    if args.name:
        rec = load_cloud_pins().get(args.name)
        rec = rec if isinstance(rec, dict) else {}
        pinned = (not args.cloud and not args.localsend
                  and "pinned_at" in rec)
        cloud = args.cloud or pinned
        if not cloud:
            ip = resolve_name(args.name)
            if ip:
                args.host = ip
            else:
                cloud = True  # not on the LAN → cloud fallback
        if cloud:
            creds = args.creds or os.path.expanduser(
                "~/.config/mdrender/push-credentials.json")
            if not os.path.exists(creds):
                if args.cloud or pinned:
                    print("cloud push requested but no push credentials",
                          file=sys.stderr)
                else:
                    print("device not found on LAN and no push credentials",
                          file=sys.stderr)
                return 3
            return push_to_server(creds, args.name, paths,
                                  folder=args.folder, conflict=args.conflict)

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
    transfer_pin = _effective_transfer_pin(args)
    if transfer_pin:
        prepare_url += f"?pin={urllib.parse.quote(transfer_pin)}"

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
