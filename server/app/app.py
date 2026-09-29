# server/app/app.py
"""Flask application factory wiring every route for the Cloud Push server."""
import base64
import datetime
import hmac
import json
import os
import secrets
import shutil
import time
import urllib.parse
import uuid

import qrcode
from flask import (Flask, Response, g, jsonify, make_response, redirect,
                   render_template, request, send_file)
from qrcode.image.svg import SvgPathImage

from cryptography.hazmat.primitives import serialization

from server.app import (accounts, crypto, federation, fcm as fcm_mod, pairing,
                        push_store, storage, trigger)
from server.app.config import load_config
from server.app.auth import (LoginGate, create_session, delete_session,
                             hash_secret, issue_access_token, principal_of,
                             purge_expired_sessions, make_session,
                             revoke_access_tokens, session_is_valid,
                             validate_access_token, verify_secret, verify_session)
from server.app.db import Database
from server.app.deployment import (detect_role, get_or_create_identity,
                                   probe_fcm)
from server.app.identity import (count_admins, create_admin,
                                 get_identity_provider, list_admins)
from server.app.store import (check_device, create_client, delete_device,
                              get_client, get_device_by_name,
                              get_or_create_server_keypair, list_clients,
                              list_devices, revoke_client,
                              session_secret_from_pem, touch_last_seen,
                              update_device_name, update_device_push_key,
                              update_device_token)

SESSION_COOKIE = "mdrender_session"

# The manifest response body IS the signed byte string; the signature travels
# beside it in this header. Keeping them separate is what lets the phone verify
# the exact bytes it received instead of re-serialising a parsed object.
MANIFEST_SIGNATURE_HEADER = "X-Push-Manifest-Signature"


def make_fcm_client(config):
    """Build a real FCM client from the configured service account.

    Raises FcmError when no service account is configured; create_app swallows
    that and stores None so pushes skip the send loop (tests inject a fake by
    monkeypatching this factory before create_app runs).
    """
    if not config.FCM_SERVER_KEY:
        raise fcm_mod.FcmError("no FCM service account configured")
    return fcm_mod.FcmClient(fcm_mod.load_service_account(config.FCM_SERVER_KEY))


def _load_private(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _get_file(conn, file_id):
    return conn.execute(
        "SELECT * FROM push_files WHERE file_id = ?", (file_id,)
    ).fetchone()


def create_app(config):
    app = Flask(__name__)

    db = Database(config.DB_PATH)
    with db.connect() as conn:
        db.init_schema(conn)
        server_pem, server_pk_b64 = get_or_create_server_keypair(conn)
        # Bootstrap an admin from SERVER_PASSWORD so an existing deployment
        # keeps a working login until the operator sets up a named admin.
        if count_admins(conn) == 0 and getattr(config, "SERVER_PASSWORD", ""):
            create_admin(conn, "admin", config.SERVER_PASSWORD)
        server_identity = get_or_create_identity(conn)

    # The server keypair is stable per database; the session secret derives from it.
    config.session_secret = session_secret_from_pem(server_pem)

    app.config["_db"] = db
    app.config["_server_pem"] = server_pem
    app.config["_server_pk_b64"] = server_pk_b64
    app.config["_enrol_keys"] = {}          # enrolment_id -> {"key", "expires"}
    app.config["_login_gate"] = LoginGate(config)
    app.config["_identity"] = get_identity_provider(config)
    app.config["_server_identity"] = server_identity
    app.config["_fcm_available"] = probe_fcm(config)
    app.config["_server_role"] = detect_role(config, app.config["_fcm_available"])
    app.config["_pairing_tokens"] = {}      # current pairing token (browser artefact)
    app.config["_test_pairing_token"] = None
    try:
        app.config["_fcm"] = make_fcm_client(config)
    except fcm_mod.FcmError:
        app.config["_fcm"] = None

    os.makedirs(config.PUSH_STORAGE_DIR, exist_ok=True)

    @app.template_filter("dt")
    def _fmt_epoch(ts):
        """Render an epoch timestamp as a local human-readable string."""
        try:
            return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OverflowError, OSError):
            return "-"

    @app.before_request
    def _open_db():
        g.db = app.config["_db"].connect()
        g.cfg = config

    @app.teardown_request
    def _close_db(exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def _principal():
        return principal_of(g.db, config.session_secret,
                            request.cookies.get(SESSION_COOKIE), config)

    def _is_admin():
        principal = _principal()
        return principal is not None and principal["type"] == "admin"

    def require_session():
        """Gate an admin endpoint on a live **admin** session.

        The signature check alone would keep accepting a logged-out token, so
        the cookie is also required to have an unexpired row in `sessions`; and
        the principal must be an admin (account sessions are rejected).
        """
        if _is_admin():
            return None
        return jsonify({"error": "unauthorized"}), 401

    def require_page_session():
        """Gate a GET browser page, bouncing anonymous visitors through login.

        Returning the API's raw JSON 401 to a browser is a dead end — the tool
        enrolment flow opens such a URL directly — so remember where the visitor
        was headed and send them to the login form instead. Endpoints keep using
        require_session()'s JSON 401.
        """
        if _is_admin():
            return None
        nxt = urllib.parse.quote(request.path, safe="/")
        return redirect(f"/login?next={nxt}", 303)

    def require_account_session():
        """Gate an account (tenant) page on a live account session."""
        principal = _principal()
        if principal is not None and principal["type"] == "account":
            return None
        return redirect("/account/login", 303)

    def _return_to(default):
        """Where a POST sends the browser next, read from a hidden `next` field.

        Restricted to same-site paths so a crafted form cannot bounce an admin
        off-site through the redirect.
        """
        nxt = request.form.get("next", "")
        if nxt.startswith("/") and not nxt.startswith("//"):
            return nxt
        return default

    def require_form_session(default):
        """Gate a browser form POST the same way as a page.

        A logged-out admin's session may have expired mid-use, so rather than
        show raw JSON, send them through login and back to the page the form
        lived on. The POST itself is not replayed. API/bearer endpoints keep
        require_session()'s JSON 401.
        """
        if _is_admin():
            return None
        nxt = urllib.parse.quote(_return_to(default), safe="/")
        return redirect(f"/login?next={nxt}", 303)

    def bearer_client_id():
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return validate_access_token(config, header[7:])

    def _new_code() -> str:
        """Six characters from an unambiguous set, for typing by hand.

        No 0/O, 1/I/L: the code is read off a screen and typed on a headless or
        remote machine, so confusable glyphs are removed.
        """
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(6))

    def _refresh_code(entry):
        entry["code"] = _new_code()
        entry["code_expires"] = time.time() + config.ENROL_CODE_TTL_SECONDS
        entry["code_attempts"] = 0

    def _check_code(entry, submitted: str) -> bool:
        """Validate a manually entered code, with a short TTL and attempt cap.

        The code is short enough to type, so it is deliberately short-lived and
        burns out after ENROL_CODE_MAX_ATTEMPTS wrong guesses.
        """
        if not submitted:
            return False
        if entry["code_attempts"] >= config.ENROL_CODE_MAX_ATTEMPTS:
            return False
        if time.time() > entry["code_expires"]:
            return False
        if hmac.compare_digest(entry["code"], submitted.strip().upper()):
            return True
        entry["code_attempts"] += 1
        return False

    def _mint_enrolment(name: str = "cli") -> str:
        """Create a one-time enrolment and return its id.

        Shared by the CLI-facing /api/enrol/start and the browser /clients/new
        so both mint from the same single-use registry. An enrolment starts
        unapproved. It can be completed two ways: the operator clicks Complete
        registration in the browser (which mints the OAuth client and the CLI
        collects the credentials by polling /api/enrol/complete), or — for a
        headless/remote machine — the short code is entered into the CLI, which
        exchanges it at /api/enrol.
        """
        eid = uuid.uuid4().hex
        entry = {
            "name": name or "cli",
            "expires": time.time() + config.ENROL_TOKEN_TTL_HOURS * 3600,
            "approved": False,
            "creds": None,
        }
        _refresh_code(entry)
        app.config["_enrol_keys"][eid] = entry
        return eid

    def _approve_enrolment(eid: str):
        """Mint the OAuth client for an enrolment and stash its credentials.

        Runs in a request context (uses g.db). Idempotent: a second approval
        returns the same credentials rather than creating another client.
        """
        entry = app.config["_enrol_keys"].get(eid)
        if entry is None:
            return None
        if entry["creds"] is None:
            secret = uuid.uuid4().hex
            client_id = create_client(g.db, entry["name"], hash_secret(secret))
            entry["creds"] = {"client_id": client_id, "client_secret": secret}
            entry["approved"] = True
        return entry["creds"]

    def _ring_doorbell(device, push_row) -> bool:
        """Send the one FCM message that tells a device a push is waiting.

        The message is a constant-size encrypted trigger naming where to look, so
        the file count can never exhaust FCM's 4 KB limit. It carries no file
        names, paths, or retrieval keys. Returns False if there is no client (or
        the device has no usable doorbell key).
        """
        fcm = app.config["_fcm"]
        if fcm is None or not device["fcm_token"]:
            return False
        if not device["push_key"]:
            return False
        sealed = trigger.seal_trigger(
            base64.b64decode(device["push_key"]), config.PUSH_PUBLIC_URL,
            push_row["push_id"], push_row["challenge_key"],
        )
        fcm.send({"p": sealed["c"], "i": sealed["i"]}, device["fcm_token"])
        return True

    # ---- Browser session-gated pages ----

    @app.context_processor
    def _inject_auth_state():
        """Let every template hide the admin menu unless a session is live.

        Without this the nav renders on the login page too, which both leaks
        the page list to an anonymous visitor and offers links that 401.
        """
        return {"logged_in": _is_admin()}

    @app.route("/")
    def index():
        """Landing page: setup on a fresh server, else login, else the push list."""
        if not session_is_valid(g.db, config.session_secret,
                                request.cookies.get(SESSION_COOKIE), config):
            if count_admins(g.db) == 0:
                return redirect("/setup")
            return render_template("login.html")
        return redirect("/pushes")

    @app.route("/setup", methods=["GET"])
    def setup_page():
        """First-run admin creation; only while no admin exists."""
        if count_admins(g.db) > 0:
            return redirect("/login")
        return render_template("setup.html")

    @app.route("/setup", methods=["POST"])
    def setup():
        """Create the first admin. The only unauthenticated write path, and it
        closes permanently once an admin exists."""
        if count_admins(g.db) > 0:
            return jsonify({"error": "already_configured"}), 409
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 8:
            return render_template(
                "setup.html",
                error="Choose a username of 3+ characters and a password of 8+."), 400
        create_admin(g.db, username, password)
        purge_expired_sessions(g.db)
        token = create_session(g.db, config.session_secret, config)
        resp = make_response(redirect("/pushes"))
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                        secure=config.PUSH_PUBLIC_URL.startswith("https"))
        return resp

    @app.route("/login", methods=["GET"])
    def login_page():
        if count_admins(g.db) == 0:
            return redirect("/setup")
        return render_template("login.html", next=request.args.get("next", ""))

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        """Public account (tenant) signup by email (design §9)."""
        if request.method == "GET":
            return render_template("signup.html")
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password", "")
        valid_email = "@" in email and "." in email.rsplit("@", 1)[-1]
        if not valid_email or len(password) < 8:
            return render_template(
                "signup.html",
                error="Enter a valid email and a password of 8+ characters."), 400
        if accounts.get_account_by_email(g.db, email) is not None:
            return render_template("signup.html",
                                   error="That email is already registered."), 400
        accounts.create_account(g.db, email, password)
        return render_template("signup.html", done=True)

    @app.route("/account/login", methods=["GET", "POST"])
    def account_login():
        if request.method == "GET":
            return render_template("account_login.html")
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password", "")
        account_id = accounts.verify_account_password(g.db, email, password)
        if account_id is None:
            return render_template("account_login.html",
                                   error="Invalid email or password."), 401
        token = create_session(g.db, config.session_secret, config,
                               principal_type="account", principal_id=account_id)
        resp = make_response(redirect("/account"))
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                        secure=config.PUSH_PUBLIC_URL.startswith("https"))
        return resp

    @app.route("/account/logout", methods=["POST"])
    def account_logout():
        delete_session(g.db, config.session_secret, request.cookies.get(SESSION_COOKIE))
        resp = make_response(redirect("/account/login"))
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.route("/account", methods=["GET"])
    def account_home():
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        principal = _principal()
        account = accounts.get_account(g.db, principal["id"])
        devices = accounts.list_devices(g.db, principal["id"], account["host"])
        return render_template("account.html", account=account, devices=devices,
                               usage=storage.usage(g.db, principal["id"]),
                               quota=storage.effective_quota(g.db, principal["id"], config))

    def require_account_api():
        principal = _principal()
        if principal is not None and principal["type"] == "account":
            return None
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/api/account/upload", methods=["POST"])
    def account_upload():
        """Upload files into the account's pending storage, enforcing quota.

        With client-side encryption the bytes are already ciphertext and the
        filename is inside the payload; the server stores an opaque blob (§7a).
        """
        auth_error = require_account_api()
        if auth_error:
            return auth_error
        account_id = _principal()["id"]
        uploads = request.files.getlist("file")
        if not uploads:
            return jsonify({"error": "no files"}), 400
        stored = []
        account_dir = os.path.join(config.PUSH_STORAGE_DIR, "accounts", account_id)
        for f in uploads:
            data = f.read()
            if not storage.can_store(g.db, account_id, len(data), config):
                return jsonify({"error": "quota exceeded"}), 413
            file_id = uuid.uuid4().hex
            os.makedirs(account_dir, exist_ok=True)
            stored_path = os.path.join(account_dir, file_id)
            with open(stored_path, "wb") as fh:
                fh.write(data)
            storage.add_file(g.db, file_id=file_id, account_id=account_id,
                             size=len(data), stored_path=stored_path,
                             alg=request.form.get("alg"),
                             nonce=request.form.get("nonce"))
            stored.append(file_id)
        return jsonify({"ok": True, "file_ids": stored})

    @app.route("/login", methods=["POST"])
    def login():
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        gate = app.config["_login_gate"]
        key = f"{username}@{request.remote_addr}"
        retry = gate.is_locked(key)
        if retry:
            return jsonify({"error": "locked", "retry_after": retry}), 401
        if app.config["_identity"].authenticate(g.db, username, password) is None:
            retry = gate.record_failure(key)
            return jsonify({"error": "invalid_credentials", "retry_after": retry}), 401
        gate.record_success(key)
        # Opportunistic cleanup so a long-lived server does not accumulate rows
        # for sessions nobody is holding any more.
        purge_expired_sessions(g.db)
        token = create_session(g.db, config.session_secret, config)
        # Honour the page the visitor was originally headed for (e.g. the enrol
        # URL a CLI just opened); _return_to rejects off-site targets.
        resp = make_response(redirect(_return_to("/pushes")))
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                        secure=config.PUSH_PUBLIC_URL.startswith("https"))
        return resp

    @app.route("/logout", methods=["POST"])
    def logout():
        """Drop the session server-side, so the old cookie is worthless."""
        delete_session(g.db, config.session_secret, request.cookies.get(SESSION_COOKIE))
        resp = make_response(redirect("/"))
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.route("/pair", methods=["GET"])
    def pair():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        token = pairing.create_pairing_token(g.db, config.ENROL_SESSION_TTL_MINUTES)
        expires_at = (datetime.datetime.now(datetime.timezone.utc)
                      + datetime.timedelta(minutes=config.ENROL_SESSION_TTL_MINUTES))
        expires_iso = expires_at.isoformat()
        app.config["_test_pairing_token"] = token
        app.config["_pairing_tokens"] = {"token": token, "expires": expires_iso}
        qr_text = pairing.build_pairing_qr(config.PUSH_PUBLIC_URL, token)
        # Inline SVG QR (qrcode SvgPathImage; no Pillow required). Uses
        # SvgPathImage, not SvgImage/SvgFragmentImage, because those emit
        # <svg:rect> children with a namespace prefix that browsers discard
        # when the SVG is embedded inline in HTML, so no QR code renders.
        img = qrcode.make(qr_text, image_factory=SvgPathImage)
        qr_svg = img.to_string().decode()
        return render_template(
            "pair.html",
            qr_svg=qr_svg,
            server_url=config.PUSH_PUBLIC_URL,
            expires=expires_at.strftime("%Y-%m-%d %H:%M UTC"),
        )

    @app.route("/enrol/<eid>", methods=["GET"])
    def enrol_page(eid):
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        entry = app.config["_enrol_keys"].get(eid)
        if (entry is not None and not entry["approved"]
                and time.time() > entry["code_expires"]):
            # The code's clock starts when it was minted, but the operator has
            # to sign in before they can see it — often longer than the TTL.
            # Refresh on view so the code they read is always still valid.
            _refresh_code(entry)
        return render_template("enrol.html",
                               eid=eid,
                               code=entry["code"] if entry else None,
                               code_expires=entry["code_expires"] if entry else None,
                               approved=bool(entry and entry["approved"]))

    @app.route("/enrol/<eid>/approve", methods=["POST"])
    def enrol_approve(eid):
        """Complete an enrolment: create the client, then tell the CLI to collect.

        The signed-in browser session is the authorisation — it is what proves a
        human at this server approved the tool.
        """
        auth_error = require_form_session(f"/enrol/{eid}")
        if auth_error:
            return auth_error
        if _approve_enrolment(eid) is None:
            return render_template("enrol.html", eid=eid, code=None,
                                   code_expires=None, approved=False), 404
        return render_template("enrol_done.html")

    @app.route("/enrol/<eid>/new-code", methods=["POST"])
    def enrol_new_code(eid):
        """Issue a fresh short code for an enrolment whose code expired."""
        auth_error = require_form_session(f"/enrol/{eid}")
        if auth_error:
            return auth_error
        entry = app.config["_enrol_keys"].get(eid)
        if entry is not None and not entry["approved"]:
            _refresh_code(entry)
        return redirect(f"/enrol/{eid}", 303)

    @app.route("/admins", methods=["GET"])
    def admins():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("admins.html", admins=list_admins(g.db))

    @app.route("/admins", methods=["POST"])
    def admins_create():
        auth_error = require_form_session("/admins")
        if auth_error:
            return auth_error
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        if len(username) >= 3 and len(password) >= 8:
            create_admin(g.db, username, password)
        return redirect("/admins", 303)

    @app.route("/status", methods=["GET"])
    def status():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        ident = app.config["_server_identity"]
        return render_template(
            "status.html",
            role=app.config["_server_role"],
            server_id=ident["server_id"],
            hostname=ident["hostname"],
            fcm_configured=bool(getattr(config, "FCM_SERVER_KEY", "")),
            fcm_available=app.config["_fcm_available"],
            master_url=getattr(config, "MASTER_URL", ""),
        )

    @app.route("/federation", methods=["GET"])
    def federation_page():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("federation.html",
                               servers=[dict(r) for r in federation.list_servers(g.db)],
                               master_url=getattr(config, "MASTER_URL", ""))

    @app.route("/federation/<server_id>/<action>", methods=["POST"])
    def federation_action(server_id, action):
        auth_error = require_form_session("/federation")
        if auth_error:
            return auth_error
        if action == "revoke":
            federation.revoke_server(g.db, server_id)
        elif action == "deactivate":
            federation.set_status(g.db, server_id, "deactivated")
        elif action == "activate":
            federation.set_status(g.db, server_id, "active")
        elif action == "delete":
            federation.delete_server(g.db, server_id)
        return redirect("/federation", 303)

    @app.route("/devices", methods=["GET"])
    def devices():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("devices.html",
                               devices=[dict(r) for r in list_devices(g.db)])

    @app.route("/clients", methods=["GET"])
    def clients():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("clients.html",
                               clients=[dict(r) for r in list_clients(g.db)],
                               server_url=config.PUSH_PUBLIC_URL)

    @app.route("/clients/<client_id>/revoke", methods=["POST"])
    def clients_revoke(client_id):
        auth_error = require_form_session("/clients")
        if auth_error:
            return auth_error
        revoke_client(g.db, client_id)
        # Drop any live bearer tokens so the client stops working now, and the
        # row vanishes from /clients (list_clients only shows active ones).
        revoke_access_tokens(client_id)
        return redirect("/clients", 303)

    @app.route("/pushes", methods=["GET"])
    def pushes():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("pushes.html",
                               pushes=[dict(r) for r in push_store.list_pushes(g.db)])

    @app.route("/pending", methods=["GET"])
    def pending():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("pending.html",
                               pushes=push_store.list_pending_pushes(g.db))

    @app.route("/devices/<device_secret>", methods=["DELETE"])
    def devices_delete(device_secret):
        auth_error = require_form_session("/devices")
        if auth_error:
            return auth_error
        delete_device(g.db, device_secret)
        return redirect("/devices", 303)

    @app.route("/devices/<device_secret>/delete", methods=["POST"])
    def devices_delete_post(device_secret):
        auth_error = require_form_session("/devices")
        if auth_error:
            return auth_error
        delete_device(g.db, device_secret)
        return redirect("/devices", 303)

    # ---- Unauthenticated / Bearer / enrolment API ----

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"ok": True})

    # ---- Federation API (design §5/§5a) ----

    def _federation_token():
        header = request.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else None

    @app.route("/api/federation/enrol", methods=["POST"])
    def federation_enrol():
        """A slave enrols: verify it via the signed callback, then activate.

        Automatic (D2) — no operator approval; bans/revocation still apply.
        """
        data = request.get_json(silent=True) or {}
        server_id = data.get("server_id")
        hostname = data.get("hostname")
        base_url = data.get("base_url")
        public_key_b64 = data.get("public_key")
        if not all([server_id, hostname, base_url, public_key_b64]):
            return jsonify({"error": "server_id, hostname, base_url and public_key"
                                     " are required"}), 400
        challenge = federation.new_challenge()
        try:
            signature = federation.verify_slave_callback(base_url, challenge)
        except Exception as exc:  # noqa: BLE001 - unreachable slave => not verified
            return jsonify({"error": "callback failed", "detail": str(exc)[:120]}), 400
        if not federation.verify_callback_signature(public_key_b64, challenge, signature):
            return jsonify({"error": "callback signature invalid"}), 400
        secret = federation.register_active(
            g.db, server_id=server_id, hostname=hostname, base_url=base_url,
            public_key_b64=public_key_b64)
        return jsonify({"server_id": server_id, "server_secret": secret,
                        "status": "active"})

    def _federation_alive():
        """Shared heartbeat/ping: verify the signed request, then flush the outbox."""
        row = federation.check_bearer(g.db, _federation_token())
        if row is None:
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_data() or b""
        if not federation.verify_request(g.db, row, request.method, request.path,
                                         body, request.headers):
            return jsonify({"error": "bad signature"}), 401
        federation.touch_seen(g.db, row["server_id"], up=True)
        queued = federation.pending_outbox(g.db, row["server_id"])
        federation.mark_outbox_sent(g.db, [q["id"] for q in queued])
        return jsonify({"ok": True, "status": "active",
                        "queued": [json.loads(q["payload"]) for q in queued]})

    @app.route("/api/federation/heartbeat", methods=["POST"])
    def federation_heartbeat():
        return _federation_alive()

    @app.route("/api/federation/ping", methods=["POST"])
    def federation_ping():
        return _federation_alive()

    @app.route("/api/federation/whoami", methods=["GET"])
    def federation_whoami():
        row = federation.check_bearer(g.db, _federation_token())
        if row is None:
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"server_id": row["server_id"], "hostname": row["hostname"],
                        "status": row["status"]})

    @app.route("/api/federation/accounts/<account_id>/devices/<device_id>",
               methods=["PUT", "DELETE"])
    def federation_device(account_id, device_id):
        """No-PII device sync from a slave: routing tuple only (design §6)."""
        row = federation.check_bearer(g.db, _federation_token())
        if row is None:
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_data() or b""
        if not federation.verify_request(g.db, row, request.method, request.path,
                                         body, request.headers):
            return jsonify({"error": "bad signature"}), 401
        if request.method == "PUT":
            data = request.get_json(silent=True) or {}
            accounts.upsert_device(
                g.db, account_id=account_id, device_id=device_id,
                server_id=row["server_id"], fcm_token=data.get("fcm_token", ""),
                name=None)
        else:
            accounts.delete_device(g.db, account_id=account_id, device_id=device_id,
                                   server_id=row["server_id"])
        return jsonify({"ok": True})

    @app.route("/api/federation/doorbell", methods=["POST"])
    def federation_doorbell():
        """Relay a sealed doorbell to a slave-hosted account's device (design §7B).

        The master cannot read the trigger (no push_key); it only resolves the
        device's FCM token and sends. Ownership is enforced: a slave may only
        ring devices registered under its own server_id + account.
        """
        row = federation.check_bearer(g.db, _federation_token())
        if row is None:
            return jsonify({"error": "unauthorized"}), 401
        if row["status"] != "active":
            return jsonify({"error": "slave not active"}), 403
        body = request.get_data() or b""
        if not federation.verify_request(g.db, row, request.method, request.path,
                                         body, request.headers):
            return jsonify({"error": "bad signature"}), 401
        data = json.loads(body or b"{}")
        account_id = data.get("account_id")
        device_id = data.get("device_id")
        sealed = data.get("sealed") or {}
        if not account_id or not device_id or not sealed.get("c") or not sealed.get("i"):
            return jsonify({"error": "account_id, device_id and sealed {c,i} required"}), 400
        device = accounts.get_device(g.db, account_id=account_id, device_id=device_id,
                                     server_id=row["server_id"])
        if device is None:
            return jsonify({"error": "device not found for this slave"}), 403
        fcm = app.config["_fcm"]
        if fcm is None or not device["fcm_token"]:
            return jsonify({"error": "fcm unavailable"}), 503
        try:
            fcm.send({"p": sealed["c"], "i": sealed["i"]}, device["fcm_token"])
        except Exception:  # noqa: BLE001 - report, the slave's retry worker will retry
            return jsonify({"error": "fcm send failed"}), 502
        return jsonify({"ok": True})

    def _federation_sign_challenge(status=None):
        """Slave side: sign a master challenge to prove key possession + liveness."""
        data = request.get_json(silent=True) or {}
        challenge = data.get("challenge", "")
        if not challenge:
            return jsonify({"error": "challenge required"}), 400
        ident = app.config["_server_identity"]
        reply = {"server_id": ident["server_id"],
                 "signature": federation.sign_bytes(ident["private_key_pem"],
                                                    challenge.encode())}
        if status:
            reply["status"] = status
        return jsonify(reply)

    @app.route("/api/federation/verify", methods=["POST"])
    def federation_verify():
        return _federation_sign_challenge()

    @app.route("/api/federation/probe", methods=["POST"])
    def federation_probe():
        return _federation_sign_challenge(app.config["_server_role"])

    @app.route("/api/devices", methods=["GET"])
    def api_devices():
        """List registered push targets so a client can see where it can send.

        Bearer-gated like /api/push; the device secret is never returned.
        """
        if bearer_client_id() is None:
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"devices": [
            {"name": r["device_name"],
             "registered_at": r["registered_at"],
             "last_seen": r["last_seen"]}
            for r in list_devices(g.db)
        ]})

    @app.route("/api/enrol/start", methods=["POST"])
    def enrol_start():
        data = request.get_json(silent=True) or {}
        name = (data.get("info") or {}).get("alias") or data.get("name") or "cli"
        eid = _mint_enrolment(name)
        return jsonify({"enrolment_id": eid,
                        "verification_uri": f"{config.PUSH_PUBLIC_URL}/enrol/{eid}"})

    @app.route("/api/enrol/complete", methods=["POST"])
    def enrol_complete():
        """Collect the credentials once the browser has approved the enrolment.

        The CLI polls this with the enrolment_id (a value only it and the
        browser URL hold). Credentials are handed over exactly once.
        """
        data = request.get_json(silent=True) or {}
        eid = data.get("enrolment_id", "")
        entry = app.config["_enrol_keys"].get(eid)
        if entry is None:
            return jsonify({"status": "unknown"}), 404
        if time.time() > entry["expires"]:
            app.config["_enrol_keys"].pop(eid, None)
            return jsonify({"status": "expired"}), 401
        if not entry["approved"]:
            return jsonify({"status": "pending"})
        creds = entry["creds"]
        app.config["_enrol_keys"].pop(eid, None)
        return jsonify({"status": "approved", **creds})

    @app.route("/api/enrol", methods=["POST"])
    def enrol():
        data = request.get_json(silent=True) or {}
        eid = data.get("enrolment_id", "")
        entry = app.config["_enrol_keys"].get(eid)
        # `code` is the short manually-typed code; `key` is accepted for
        # backward compatibility with earlier clients.
        submitted = data.get("code") or data.get("key") or ""
        if entry is None or not _check_code(entry, submitted):
            return jsonify({"error": "invalid enrolment code"}), 401
        if time.time() > entry["expires"]:
            app.config["_enrol_keys"].pop(eid, None)
            return jsonify({"error": "enrolment expired"}), 401
        app.config["_enrol_keys"].pop(eid, None)
        secret = uuid.uuid4().hex
        client_id = create_client(g.db, entry["name"], hash_secret(secret))
        return jsonify({"client_id": client_id, "client_secret": secret})

    @app.route("/oauth/token", methods=["POST"])
    def oauth_token():
        grant_type = request.form.get("grant_type")
        client_id = request.form.get("client_id")
        client_secret = request.form.get("client_secret")
        if grant_type != "client_credentials" or not client_id or not client_secret:
            return jsonify({"error": "invalid_grant"}), 401
        row = get_client(g.db, client_id)
        if (row is None or row["revoked_at"] is not None
                or not verify_secret(client_secret, row["client_secret_hash"])):
            return jsonify({"error": "invalid_client"}), 401
        token = issue_access_token(config, client_id)
        return jsonify({"access_token": token, "token_type": "Bearer",
                        "expires_in": config.ACCESS_TOKEN_TTL_SECONDS})

    @app.route("/api/push", methods=["POST"])
    def api_push():
        if bearer_client_id() is None:
            return jsonify({"error": "unauthorized"}), 401
        target_device = request.form.get("target_device")
        if not target_device:
            return jsonify({"error": "device not found"}), 400
        device = get_device_by_name(g.db, target_device)
        if device is None:
            return jsonify({"error": "device not found"}), 400
        uploads = request.files.getlist("file")
        if not uploads:
            return jsonify({"error": "no files uploaded"}), 400
        push_id = uuid.uuid4().hex
        challenge_key = uuid.uuid4().hex
        # Where the device should file these, and what to do if a name is
        # already taken. Both are per-push options, mirroring the LocalSend
        # "mds" options, and are carried in the signed manifest so the device
        # cannot be redirected between fetching it and importing.
        target_folder = (request.form.get("target_folder") or "").strip()
        conflict = push_store.normalise_conflict(request.form.get("conflict"))
        push_store.create_push(g.db, push_id, target_device, challenge_key,
                               target_folder, conflict)
        sent = 0
        for f in uploads:
            file_id = uuid.uuid4().hex
            retrieval_key = uuid.uuid4().hex
            name = os.path.basename(f.filename) or "file"
            stored_dir = os.path.join(config.PUSH_STORAGE_DIR, push_id, file_id)
            os.makedirs(stored_dir, exist_ok=True)
            stored_path = os.path.join(stored_dir, name)
            f.save(stored_path)
            size = os.path.getsize(stored_path)
            push_store.add_file(g.db, file_id=file_id, push_id=push_id, file_name=name,
                                file_path="", size=size, retrieval_key=retrieval_key,
                                stored_path=stored_path, created_at=int(time.time()))
            sent += 1
        push_row = push_store.get_push_by_id(g.db, push_id)
        _ring_doorbell(device, push_row)
        return jsonify({"push_id": push_id, "files_sent": sent})

    @app.route("/api/register-device", methods=["POST"])
    def register_device_route():
        data = request.get_json(silent=True) or {}
        if data.get("pairing_token"):
            device_auth, displaced = pairing.register_device(
                g.db,
                device_secret=data.get("device_secret"),
                device_name=data.get("device_name"),
                fcm_token=data.get("fcm_token"),
                public_key_b64=data.get("public_key"),
                push_key_b64=data.get("push_key", ""),
                pairing_token=data.get("pairing_token"),
                sig_b64=data.get("sig"),
            )
            if device_auth is None:
                return jsonify({"error": "registration failed"}), 400
            # The device needs this key to verify the manifest signature, and it
            # only ever learns it from here, so hand it over in the same response
            # that completes pairing. The request arrived over the TLS connection
            # to the very server named in the scanned QR.
            return jsonify({
                "ok": True,
                "device_auth": device_auth,
                "server_pk": app.config["_server_pk_b64"],
            })
        device_secret = data.get("device_secret")
        device_auth = data.get("device_auth")
        if not device_secret or not device_auth:
            return jsonify({"error": "device_secret and device_auth required"}), 400
        ok = True
        if data.get("fcm_token"):
            ok = update_device_token(g.db, device_secret, device_auth,
                                     data["fcm_token"]) and ok
        if data.get("device_name"):
            ok = update_device_name(g.db, device_secret, device_auth,
                                    data["device_name"]) and ok
        if data.get("push_key"):
            ok = update_device_push_key(g.db, device_secret, device_auth,
                                        data["push_key"]) and ok
        if not ok:
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"ok": True, "device_auth": device_auth})

    @app.route("/api/push/<push_id>/manifest", methods=["POST"])
    def push_manifest(push_id):
        """Exchange a doorbell's challenge_key for the signed file manifest.

        The challenge key is a capability from inside the authenticated trigger,
        so this needs no device credential. The manifest is signed with the
        server key the phone pinned at pairing, which is what makes the response
        trustworthy even if the TLS terminator is not.

        The body is exactly the bytes that were signed, and the signature rides
        in a header. Nesting the manifest inside a JSON envelope instead would
        force the phone to slice the signed bytes back out of the response by
        hand, and any such slicing is a place a signature check can silently go
        wrong. With the body being the signed bytes, the phone verifies what it
        received without re-serialising or parsing anything first.
        """
        push_row = push_store.get_push_by_id(g.db, push_id)
        if push_row is None:
            return jsonify({"error": "not found"}), 404
        data = request.get_json(silent=True) or {}
        if not data.get("challenge_key") or not push_row["challenge_key"]:
            return jsonify({"error": "forbidden"}), 403
        if data["challenge_key"] != push_row["challenge_key"]:
            return jsonify({"error": "forbidden"}), 403
        manifest = trigger.build_manifest(
            push_id,
            datetime.datetime.fromtimestamp(
                push_row["date"], datetime.timezone.utc
            ).isoformat(),
            push_store.get_unacked_files(g.db, push_id),
            push_row["target_folder"],
            push_row["conflict"],
        )
        server_priv = _load_private(app.config["_server_pem"])
        body = trigger.manifest_bytes(manifest)
        sig = crypto.sign(server_priv, body)
        return Response(
            body,
            status=200,
            mimetype="application/json",
            headers={MANIFEST_SIGNATURE_HEADER: base64.b64encode(sig).decode()},
        )

    @app.route("/api/push/<file_id>/download", methods=["POST"])
    def download(file_id):
        row = _get_file(g.db, file_id)
        if row is None:
            return jsonify({"error": "not found"}), 404
        data = request.get_json(silent=True) or {}
        if data.get("key") != row["retrieval_key"]:
            return jsonify({"error": "forbidden"}), 403
        if not row["stored_path"] or not os.path.exists(row["stored_path"]):
            return jsonify({"error": "not found"}), 404
        return send_file(row["stored_path"], download_name=row["file_name"])

    @app.route("/api/push/<file_id>/received", methods=["POST"])
    def received(file_id):
        row = _get_file(g.db, file_id)
        if row is None:
            return jsonify({"error": "not found"}), 404
        data = request.get_json(silent=True) or {}
        if data.get("key") != row["retrieval_key"]:
            return jsonify({"error": "forbidden"}), 403
        push_store.mark_acked(g.db, file_id, int(time.time()))
        return jsonify({"ok": True})

    @app.route("/api/device/status", methods=["POST"])
    def device_status():
        data = request.get_json(silent=True) or {}
        secret = data.get("device_secret")
        auth = data.get("device_auth")
        if not secret or not auth or not check_device(g.db, secret, auth):
            return jsonify({"error": "re-register"}), 404
        touch_last_seen(g.db, secret)
        return jsonify({"ok": True})

    @app.route("/api/push/<push_id>/status", methods=["GET"])
    def push_status(push_id):
        if bearer_client_id() is None:
            return jsonify({"error": "unauthorized"}), 401
        rows = push_store.get_push_files(g.db, push_id)
        return jsonify({"push_id": push_id, "files": [
            {"file_id": r["file_id"], "name": r["file_name"],
             "status": r["status"], "retries": r["retries"]} for r in rows]})

    @app.route("/api/push/<push_id>/retry", methods=["POST"])
    def push_retry(push_id):
        auth_error = require_form_session("/pushes")
        if auth_error:
            return auth_error
        push_row = push_store.get_push_by_id(g.db, push_id)
        if push_row is None:
            return jsonify({"error": "not found"}), 404
        device = get_device_by_name(g.db, push_row["target_device"])
        if device is None:
            return jsonify({"error": "device not found"}), 400
        now = int(time.time())
        push_store.reset_push_retries(g.db, push_id, now)
        pending = [r for r in push_store.get_pending_files(g.db, now)
                   if r["push_id"] == push_id]
        if pending:
            _ring_doorbell(device, push_row)
        return jsonify({"ok": True, "files_sent": len(pending)})

    @app.route("/pushes/<push_id>/delete", methods=["POST"])
    def pushes_delete(push_id):
        """Drop a push from the admin list, including any bytes left on disk.

        Only a push that actually existed has its directory removed, so a
        crafted id can never point the recursive delete at some other path.
        """
        auth_error = require_form_session("/pushes")
        if auth_error:
            return auth_error
        if push_store.delete_push(g.db, push_id):
            shutil.rmtree(os.path.join(config.PUSH_STORAGE_DIR, push_id),
                          ignore_errors=True)
        return redirect(_return_to("/pushes"), 303)

    return app


if __name__ == "__main__":
    cfg = load_config()
    host, _, port = cfg.LISTEN_ADDR.partition(":")
    create_app(cfg).run(host=host or "0.0.0.0", port=int(port or "8080"))
