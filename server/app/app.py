# server/app/app.py
"""Flask application factory wiring every route for the Cloud Push server."""
import base64
import datetime
import hmac
import io
import json
import os
import queue
import re
import secrets
import shutil
import time
import urllib.error
import urllib.parse
import uuid

import qrcode
from flask import (Flask, Response, g, jsonify, make_response, redirect,
                   render_template, request, send_file)
from qrcode.image.svg import SvgPathImage

from cryptography.hazmat.primitives import serialization

from server.app import (accounts, bans, billing, crypto, data_export,
                        deletions, encryption, events,
                        federation, federation_client, fcm as fcm_mod, geoip,
                        identity_admin, mail, oidc, pairing, paypal,
                        push_store,
                        releases, settings, storage, trigger)
from server.app.config import load_config
from server.app.auth import (LoginGate, create_session, delete_session,
                             hash_secret, issue_access_token, principal_of,
                             purge_expired_sessions, make_session,
                             revoke_access_tokens, session_is_valid,
                             validate_access_token, verify_secret, verify_session)
from server.app.db import Database
from server.app.deployment import (detect_role, get_or_create_identity,
                                   probe_fcm, public_hostname)
from server.app.identity import (count_admins, create_admin, get_admin,
                                 get_admin_by_email,
                                 get_admin_by_firebase_email,
                                 get_admin_by_firebase_phone,
                                 get_admin_by_firebase_uid, get_identity_provider,
                                 link_firebase, list_admins, unlink_firebase,
                                 update_admin)
from server.app.store import (check_device, create_client, delete_device,
                              get_client, get_device_by_name,
                              get_device_by_secret,
                              get_or_create_server_keypair, list_account_devices,
                              list_clients, list_devices, revoke_client,
                              session_secret_from_pem, set_client_blocked,
                              set_device_blocked, set_device_content_pubkey,
                              touch_last_seen, update_device_name,
                              update_device_push_key, update_device_token)

SESSION_COOKIE = "mdrender_session"

# The manifest response body IS the signed byte string; the signature travels
# beside it in this header. Keeping them separate is what lets the phone verify
# the exact bytes it received instead of re-serialising a parsed object.
MANIFEST_SIGNATURE_HEADER = "X-Push-Manifest-Signature"

# The terms an operator accepts on the master's consent page (design §5).
# Inline so the text that ships with the code is the text the approval covers.
_CONSENT_TERMS = """\
Approving connects this server to the master as a federated push relay.

What this grants:
  - The slave receives federated account, device and push routing rows for
    devices the master's accounts have paired with it, so pushes queued on
    either side can be delivered across the link.
  - The master stores the slave's server id, hostname, public key, last-seen
    time and status. It never receives file contents or encryption keys: only
    routing tuples and sealed doorbell payloads travel over the link.

What it does not grant:
  - No credentials for this master are handed to the slave. The slave gets a
    per-server API secret that the master can revoke at any time from its
    Federation page, which stops the slave from sending anything further.

Ongoing terms:
  - This master may suspend or revoke the connection at any time, and the
    slave operator may disconnect it from the slave's own Federation page.
  - Each side keeps its own data: pushing, retention and deletion stay the
    responsibility of the server that holds them.
"""


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


def encryption_required(config) -> bool:
    """Server setting: on = clients must encrypt (design §7b)."""
    return encryption.requires_encryption(config)


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
        server_identity = get_or_create_identity(conn,
                                                 hostname=public_hostname(config))
        # The default billing group always exists (accounts fall back to it).
        billing.ensure_default_group(conn)
        if encryption_required(config):
            # An encryption-on server must hold no plaintext push metadata
            # (§7b/D13): scrub legacy rows on every boot. Content bytes are
            # the client's responsibility; metadata is the server's.
            conn.execute("UPDATE pushes SET target_folder = ''"
                         " WHERE target_folder != ''")
            conn.execute("UPDATE push_files SET file_name = file_id"
                         " WHERE file_name != file_id")
            conn.execute("UPDATE push_files SET file_path = ''"
                         " WHERE file_path != ''")
            # Legacy files were stored under their plaintext name; rename them
            # so the directory listing carries no metadata either.
            for row in conn.execute(
                    "SELECT file_id, stored_path FROM push_files").fetchall():
                sp = row["stored_path"]
                if sp and os.path.basename(sp) != row["file_id"]:
                    new_sp = os.path.join(os.path.dirname(sp), row["file_id"])
                    try:
                        os.replace(sp, new_sp)
                        conn.execute(
                            "UPDATE push_files SET stored_path = ?"
                            " WHERE file_id = ?", (new_sp, row["file_id"]))
                    except OSError:
                        pass  # content already gone; nothing left to hide
            conn.commit()

    # The server keypair is stable per database; the session secret derives from it.
    config.session_secret = session_secret_from_pem(server_pem)

    app.config["_db"] = db
    app.config["_server_pem"] = server_pem
    app.config["_server_pk_b64"] = server_pk_b64
    app.config["_enrol_keys"] = {}          # enrolment_id -> {"key", "expires"}
    app.config["_pending_connects"] = {}    # state -> {"ts", "master_url"}
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

    @app.template_filter("money")
    def _fmt_money(cents):
        """Render a cent amount as dollars (no cents when the amount is whole)."""
        try:
            value = int(cents)
        except (TypeError, ValueError):
            return "$0"
        sign = "-" if value < 0 else ""
        value = abs(value)
        if value % 100 == 0:
            return f"{sign}${value // 100}"
        return f"{sign}${value / 100:.2f}"

    @app.template_filter("dollars")
    def _fmt_dollars(cents):
        """Render a cent amount as a plain dollar number (for form values)."""
        try:
            value = int(cents) / 100
        except (TypeError, ValueError):
            return "0"
        return f"{value:g}"

    @app.template_filter("mb")
    def _fmt_mb(num_bytes):
        """Render a byte count as a plain megabytes number (for form values)."""
        try:
            return f"{int(num_bytes) / 1048576:g}"
        except (TypeError, ValueError):
            return "0"

    @app.template_filter("filesize")
    def _fmt_filesize(size):
        """Render a byte count as a short human-readable size."""
        try:
            value = float(size or 0)
        except (TypeError, ValueError):
            return "-"
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    @app.before_request
    def _open_db():
        g.db = app.config["_db"].connect()
        g.cfg = config
        # Ban enforcement at the edge of every endpoint (design §14).
        if bool(getattr(config, "BAN_ENFORCEMENT", True)):
            try:
                kinds = bans.active_kinds(g.db)
                if kinds:
                    asn = (geoip.asn_for_ip(config, request.remote_addr)
                           if "asn" in kinds else None)
                    country = None
                    if "country" in kinds:
                        country = (geoip.country_from_headers(request.headers)
                                   or geoip.country_for_ip(config, request.remote_addr))
                    if bans.is_banned(g.db, ip=request.remote_addr, asn=asn,
                                      country=country):
                        return jsonify({"error": "forbidden"}), 403
            except Exception:  # noqa: BLE001 - never let a ban check break requests
                pass

    @app.teardown_request
    def _close_db(exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def _principal():
        p = principal_of(g.db, config.session_secret,
                         request.cookies.get(SESSION_COOKIE), config)
        # A confirmed deletion locks the account: its sessions become inert
        # everywhere at once (pages, forms and the account API all gate here).
        if (p is not None and p["type"] == "account"
                and deletions.active_for(g.db, p["id"]) is not None):
            return None
        return p

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
        return redirect(f"/admin-login?next={nxt}", 303)

    def require_account_session():
        """Gate an account (tenant) page on a live account session."""
        principal = _principal()
        if principal is not None and principal["type"] == "account":
            return None
        return redirect("/account/login", 303)

    def require_account_form(default):
        """Gate an account form POST, bouncing a stale session to account login."""
        principal = _principal()
        if principal is not None and principal["type"] == "account":
            return None
        nxt = urllib.parse.quote(default, safe="/")
        return redirect(f"/account/login?next={nxt}", 303)

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
        return redirect(f"/admin-login?next={nxt}", 303)

    def client_denied():
        """None when the bearer client is valid and active, else a 401 response.

        A revoked/deleted client gets a distinct `client_revoked` error so a tool
        can tell its user to re-register instead of retrying forever. The row is
        re-checked on every call, so even a token that somehow outlived the
        revoke is refused.
        """
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "unauthorized", "detail":
                            "No client token. Register this client with --enrol."}), 401
        client_id = validate_access_token(config, header[7:])
        if client_id is None:
            return jsonify({"error": "unauthorized", "detail":
                            "Client token missing or expired. Re-register with --enrol."}), 401
        row = get_client(g.db, client_id)
        if row is None or row["revoked_at"] is not None:
            return jsonify({"error": "client_revoked", "detail":
                            "This client was removed from the server. "
                            "Re-register it with --enrol."}), 401
        if row["blocked_at"] is not None:
            return jsonify({"error": "client_blocked", "detail":
                            "This client has been blocked by an administrator. "
                            "Ask them to unblock it."}), 401
        return None

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

    def _approve_enrolment(eid: str, account_id=None):
        """Mint the OAuth client for an enrolment and stash its credentials.

        Runs in a request context (uses g.db). Idempotent: a second approval
        returns the same credentials rather than creating another client. When
        *account_id* is given (an account approved it, not an admin) the client
        is bound to that account so it shows on the account's Clients page.
        """
        entry = app.config["_enrol_keys"].get(eid)
        if entry is None:
            return None
        if entry["creds"] is None:
            secret = uuid.uuid4().hex
            binding = account_id or entry.get("account_id")
            client_id = create_client(g.db, entry["name"], hash_secret(secret),
                                      account_id=binding)
            entry["account_id"] = binding
            entry["creds"] = {"client_id": client_id, "client_secret": secret}
            entry["approved"] = True
        return entry["creds"]

    def _enrol_actor():
        """(kind, account_id) for a signed-in admin or account, else None."""
        principal = _principal()
        if principal is None:
            return None
        if principal["type"] == "admin":
            return "admin", None
        if principal["type"] == "account":
            return "account", principal["id"]
        return None

    def _publish_device(device: dict) -> None:
        """Register a device's no-PII routing tuple at its host (design §6)."""
        account_id = device.get("account_id")
        if not account_id:
            return
        # A slave owns no FCM project: the master resolves the token and relays
        # the doorbell (§7B), so the tuple belongs in the master's registry -
        # keyed by this slave's server_id. A master (or a slave that has not
        # enrolled yet, and so has nowhere to sync to) keeps its own locally.
        if app.config["_server_role"] == "slave" and federation_client.get_state(g.db) is not None:
            try:
                federation_client.sync_device(
                    config, g.db, app.config["_server_identity"], account_id,
                    device["device_secret"], device.get("fcm_token") or "")
            except Exception:  # noqa: BLE001 - ring_via_master republishes it
                pass
            return
        accounts.upsert_device(g.db, account_id=account_id,
                               device_id=device["device_secret"],
                               server_id="master",
                               fcm_token=device.get("fcm_token") or "")

    def _notify_unpaired(device) -> None:
        """Best-effort FCM nudge telling a device it has been unpaired.

        Sent just before the row is removed so the app clears its stored pairing
        without waiting for the next settings open. A push failure must never
        stop the removal, so this never raises.
        """
        fcm = app.config["_fcm"]
        token = device["fcm_token"] if "fcm_token" in device.keys() else None
        if fcm is None:
            app.logger.warning("unpaired nudge skipped: FCM not configured")
            return
        if not token:
            app.logger.warning("unpaired nudge skipped: no FCM token for %s",
                               device.get("device_name"))
            return
        try:
            fcm.send({"type": "unpaired"}, token, high_priority=True)
            app.logger.info("sent unpaired nudge to %s", device.get("device_name"))
        except Exception as exc:  # noqa: BLE001 - removal must not depend on the push
            app.logger.warning("unpaired nudge failed for %s: %s",
                               device.get("device_name"), exc)

    def _ring_doorbell(device, push_row) -> bool:
        """Tell a device a push is waiting.

        A master sends FCM directly; a slave (no FCM of its own) forwards the
        sealed trigger to the master, which sends it (design §7). The trigger is
        constant-size and carries no file names or keys.
        """
        if not device["push_key"]:
            return False
        if "blocked_at" in device.keys() and device["blocked_at"] is not None:
            return False  # blocked devices are not rung (no FCM, no relay)
        if _encryption_required() and encryption.get_sealed_cek(
                g.db, device["device_secret"]) is None:
            return False  # client/app must negotiate encryption first (§7b)
        sealed = trigger.seal_trigger(
            base64.b64decode(device["push_key"]), config.PUSH_PUBLIC_URL,
            push_row["push_id"], push_row["challenge_key"],
        )
        account_id = device["account_id"] if "account_id" in device.keys() else None
        fcm = app.config["_fcm"]
        if fcm is not None and device["fcm_token"]:
            # High priority so the FCM message temp-allowlists the app to start
            # its download service even from the background — normal-priority
            # doorbells are dropped with ForegroundServiceStartNotAllowed
            # on Android 12+ and burn a retry.
            fcm.send({"p": sealed["c"], "i": sealed["i"]}, device["fcm_token"],
                     high_priority=True)
            if account_id:
                accounts.increment_messages(g.db, account_id)  # metering (D5)
            return True
        if account_id and federation_client.get_state(g.db) is not None:
            try:
                federation_client.ring_via_master(
                    config, g.db, app.config["_server_identity"], device, sealed)
                return True
            except Exception:  # noqa: BLE001 - retry worker will retry
                return False
        return False

    # ---- Browser session-gated pages ----

    @app.context_processor
    def _inject_assets():
        """Cache-bust static assets by file mtime, so a deploy is picked up."""
        def stamp(name):
            try:
                return int(os.path.getmtime(os.path.join(app.static_folder, name)))
            except OSError:
                return 0
        return {"asset_css": stamp("app.css"), "asset_js": stamp("app.js")}

    @app.context_processor
    def _inject_firebase():
        """Expose the Firebase web config to templates when it is configured."""
        api_key = getattr(config, "FIREBASE_API_KEY", "")
        project = getattr(config, "FIREBASE_PROJECT_ID", "")
        auth_domain = getattr(config, "FIREBASE_AUTH_DOMAIN", "") or (
            f"{project}.firebaseapp.com" if project else "")
        providers = [p.strip().lower() for p in
                     str(getattr(config, "FIREBASE_PROVIDERS", "")).split(",")
                     if p.strip()]
        firebase = None
        if api_key and auth_domain and (getattr(config, "IDENTITY_PROVIDER", "local")
                                        == "firebase"):
            firebase = {"apiKey": api_key, "authDomain": auth_domain,
                        "projectId": project,
                        "appId": getattr(config, "FIREBASE_APP_ID", ""),
                        "providers": providers}
        return {"firebase": firebase}

    @app.context_processor
    def _inject_settings():
        """Expose the signup toggle and deletion grace period to templates."""
        try:
            signup = settings.signup_enabled(g.db)
        except Exception:
            signup = True
        return {"signup_open": signup,
                "grace_days": int(config.DELETION_GRACE_DAYS)}

    @app.context_processor
    def _inject_auth_state():
        """Let every template hide the menus unless a session is live.

        Without this the nav renders on the login page too, which both leaks
        the page list to an anonymous visitor and offers links that 401.
        """
        principal = _principal()
        account_logged_in = principal is not None and principal["type"] == "account"
        account_name = None
        if account_logged_in:
            account = accounts.get_account(g.db, principal["id"])
            if account is not None:
                account_name = account["name"] or account["email"]
        return {"logged_in": _is_admin(),
                "account_logged_in": account_logged_in,
                "account_name": account_name}

    @app.route("/")
    def index():
        """Landing: setup on a fresh server, else the user login, else home.

        `/` is the public face of the site, so an anonymous visitor gets the
        *account* sign-in (`/login`); the operator console is `/admin-login`
        and is reached directly or by bouncing from an admin-only page.
        """
        if not session_is_valid(g.db, config.session_secret,
                                request.cookies.get(SESSION_COOKIE), config):
            if count_admins(g.db) == 0:
                return redirect("/setup")
            return redirect("/login")
        # Signed in: accounts get the portal, admins get the console.
        return redirect("/account" if not _is_admin()
                        else _post_login_target())

    @app.route("/terms")
    def terms_page():
        """Terms and conditions - legal pages are public, no session."""
        return render_template("terms.html")

    @app.route("/privacy")
    def privacy_page():
        """Privacy policy - legal pages are public, no session."""
        return render_template("privacy.html")

    @app.route("/account/delete/confirm", methods=["GET"])
    def account_delete_confirm():
        """The emailed confirmation link: show what confirming will do."""
        token = request.args.get("token", "")
        row = deletions.token_row(g.db, token)
        if row is None:
            return render_template("account_delete_confirm.html",
                                   invalid=True), 400
        if row["confirmed_at"] is not None:
            return render_template("account_delete_confirm.html", already=True)
        if deletions.token_usable(g.db, config, token) is None:
            return render_template("account_delete_confirm.html",
                                   invalid=True), 400
        account = accounts.get_account(g.db, row["account_id"])
        purge_preview = int(time.time()) + int(config.DELETION_GRACE_DAYS) * 86400
        return render_template("account_delete_confirm.html", token=token,
                               account=account, purge_preview=purge_preview)

    @app.route("/account/delete/confirm", methods=["POST"])
    def account_delete_confirm_submit():
        """Confirm deletion: lock the account and start the grace period."""
        token = request.form.get("token", "")
        row = deletions.confirm(g.db, config, token)
        if row is None:
            return render_template("account_delete_confirm.html",
                                   invalid=True), 400
        g.db.execute("DELETE FROM sessions WHERE principal_type = 'account'"
                     " AND principal_id = ?", (row["account_id"],))
        g.db.commit()
        return redirect("/login?deleted=1", 303)

    @app.route("/setup", methods=["GET"])
    def setup_page():
        """First-run admin creation; only while no admin exists."""
        if count_admins(g.db) > 0:
            return redirect("/admin-login")
        return render_template("setup.html")

    @app.route("/setup", methods=["POST"])
    def setup():
        """Create the first admin. The only unauthenticated write path, and it
        closes permanently once an admin exists."""
        if count_admins(g.db) > 0:
            return jsonify({"error": "already_configured"}), 409
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        def _reject(message):
            return render_template("setup.html", error=message), 400

        if len(username) < 3 or len(password) < 8:
            return _reject("Choose a username of 3+ characters and a "
                           "password of 8+.")
        if not ("@" in email and "." in email.rsplit("@", 1)[-1]):
            return _reject("Enter a valid email address for this admin.")
        if password != confirm:
            return _reject("The passwords do not match.")
        create_admin(g.db, username, password, email=email)
        purge_expired_sessions(g.db)
        token = create_session(g.db, config.session_secret, config)
        # Fresh slave: step 2 -> step 3, the Connect screen, not the push list.
        resp = make_response(redirect(_post_login_target()))
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                        secure=config.PUSH_PUBLIC_URL.startswith("https"))
        return resp

    def _check_local_credentials(username, password):
        """Shared local username/password check with lockout. -> (ok, retry)."""
        gate = app.config["_login_gate"]
        key = f"{username}@{request.remote_addr}"
        retry = gate.is_locked(key)
        if retry:
            return False, retry
        if app.config["_identity"].authenticate(g.db, username, password) is None:
            return False, gate.record_failure(key)
        gate.record_success(key)
        return True, 0

    def _local_login_cookie(resp):
        token = create_session(g.db, config.session_secret, config)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                        secure=config.PUSH_PUBLIC_URL.startswith("https"))
        return resp

    def _effective_master_url():
        """The master this slave will Connect to: operator override, else env."""
        override = settings.get(g.db, "federation_master_url") or ""
        if override:
            return override.rstrip("/")
        return (getattr(config, "MASTER_URL", "") or "").rstrip("/")

    def _clean_master_url(raw):
        """Accept only a plain http(s) origin the browser can be sent to."""
        raw = (raw or "").strip()
        if not raw:
            return None
        if "://" not in raw:
            raw = "https://" + raw
        parts = urllib.parse.urlparse(raw.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return None
        if parts.username or parts.password or parts.fragment or parts.query:
            return None
        try:
            parts.port  # noqa: B018 - rejects netloc like "host:not-a-port"
        except ValueError:
            return None
        if not parts.hostname:
            return None
        path = (parts.path or "").rstrip("/")
        return urllib.parse.urlunparse((parts.scheme, parts.netloc, path, "", "", ""))

    def _needs_master_registration():
        """A slave with a master to connect to that has not enrolled yet."""
        return (app.config["_server_role"] == "slave"
                and bool(_effective_master_url())
                and federation_client.get_state(g.db) is None)

    def _post_login_target(default="/pushes"):
        """Where an admin lands after signing in.

        An explicit `next` always wins - the CLI tool-enrolment flow depends on
        bouncing straight back to the enrol URL. Otherwise a slave that has
        never registered with its master goes to the Federation page, which
        offers to register it.
        """
        nxt = request.form.get("next", "")
        if nxt.startswith("/") and not nxt.startswith("//"):
            return nxt
        return "/federation" if _needs_master_registration() else default

    def _master_host(master_url):
        return urllib.parse.urlparse(master_url or "").netloc or (master_url or "")

    def _identity_owned_by_admin(email=None, phone=None):
        """A verified email/phone that belongs to an admin's login identity."""
        if email:
            if get_admin_by_email(g.db, email) is not None:
                return True
            if get_admin_by_firebase_email(g.db, email) is not None:
                return True
        if phone and get_admin_by_firebase_phone(g.db, phone) is not None:
            return True
        return False

    def _firebase_link_conflict(uid, email, phone, *, admin_id):
        """Reject a link that would make an identity resolve to two principals."""
        existing = get_admin_by_firebase_uid(g.db, uid)
        if existing is not None and existing["admin_id"] != admin_id:
            return "this Firebase account is already linked to another admin"
        if email:
            other = get_admin_by_firebase_email(g.db, email)
            if other is not None and other["admin_id"] != admin_id:
                return "this email is already linked to another admin"
            if accounts.get_account_by_email(g.db, email) is not None:
                return "this email belongs to a user account"
        if phone:
            other = get_admin_by_firebase_phone(g.db, phone)
            if other is not None and other["admin_id"] != admin_id:
                return "this phone number is already linked to another admin"
        return None

    @app.route("/login", methods=["GET", "POST"])
    @app.route("/account/login", methods=["GET", "POST"])
    def account_login():
        """User (account) sign-in.

        `/login` is the human URL (the tool-enrolment flow and the apps link
        here); `/account/login` is the original path kept as an alias so old
        links, logout, and bookmarks keep working.
        """
        if request.method == "GET":
            # A server with no admin yet has nothing to sign in to: finish setup.
            if request.path == "/login" and count_admins(g.db) == 0:
                return redirect("/setup")
            deletion = None
            if request.args.get("deleted") == "1":
                deletion = {"state": "confirmed"}
            else:
                p = principal_of(g.db, config.session_secret,
                                 request.cookies.get(SESSION_COOKIE), config)
                if p is not None and p["type"] == "account":
                    row = deletions.active_for(g.db, p["id"])
                    if row is not None:
                        deletion = {"state": "scheduled",
                                    "purge_after": row["purge_after"]}
            return render_template("account_login.html",
                                   next=request.args.get("next", ""),
                                   deletion=deletion)
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password", "")
        account_id = accounts.verify_account_password(g.db, email, password)
        if account_id is None:
            return render_template("account_login.html",
                                   error="Invalid email or password.",
                                   next=request.form.get("next", "")), 401
        row = deletions.active_for(g.db, account_id)
        if row is not None:
            return render_template(
                "account_login.html",
                deletion={"state": "scheduled",
                          "purge_after": row["purge_after"]},
                next=request.form.get("next", "")), 403
        accounts.touch_login(g.db, account_id)
        token = create_session(g.db, config.session_secret, config,
                               principal_type="account", principal_id=account_id)
        resp = make_response(redirect(_return_to("/account")))
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                        secure=config.PUSH_PUBLIC_URL.startswith("https"))
        return resp

    @app.route("/admin-login", methods=["GET"])
    def admin_login_page():
        """Administrator sign-in: local password, or Firebase when configured."""
        if count_admins(g.db) == 0:
            return redirect("/setup")
        return render_template("login.html", next=request.args.get("next", ""))

    @app.route("/admin-login", methods=["POST"])
    def admin_login():
        # Fresh server: the first login *is* the account creation step, so a
        # posted form (autofill, saved bookmark) goes to /setup, not to a
        # credentials error for an account that cannot exist yet.
        if count_admins(g.db) == 0:
            return redirect("/setup")
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        nxt = request.form.get("next", "")
        ok, retry = _check_local_credentials(username, password)
        if not ok:
            # An email that belongs to a *user* account is on the wrong form -
            # this is the operator sign-in. Say so instead of "invalid
            # credentials", which sends people back to the same wrong page.
            if ("@" in username
                    and accounts.get_account_by_email(g.db, username) is not None):
                return render_template("login.html", account_login_hint=True,
                                       username=username, next=nxt), 401
            error = ("Too many attempts. Try again shortly." if retry
                     else "Invalid username or password.")
            return render_template("login.html", error=error, username=username,
                                   next=nxt), 401
        # Opportunistic cleanup so a long-lived server does not accumulate rows
        # for sessions nobody is holding any more.
        purge_expired_sessions(g.db)
        # Honour the page the visitor was originally headed for (e.g. the enrol
        # URL a CLI just opened); off-site targets are rejected, and a slave
        # that has never registered with its master is sent there instead.
        return _local_login_cookie(make_response(redirect(_post_login_target())))

    def _assign_signup_group(account_id, affiliate_code=None):
        """Place a new account in a group.

        An enabled affiliate code wins; otherwise the operator's signup group
        applies. With neither, the account has no group and falls back to the
        system group.
        """
        group = billing.group_by_affiliate(g.db, affiliate_code)
        group_id = group["group_id"] if group else settings.signup_group_id(g.db)
        if group_id:
            billing.assign_account_group(g.db, billing.SCOPE_ACCOUNT,
                                         account_id, group_id)

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        """Public account (tenant) signup by email (design §9)."""
        nxt = request.args.get("next", "")
        if not settings.signup_enabled(g.db):
            return render_template("signup.html", closed=True, next=nxt), 403
        if request.method == "GET":
            return render_template(
                "signup.html", next=nxt,
                affiliate=(request.args.get("affiliate")
                           or request.args.get("affiliate_code") or ""))
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
        account_id = accounts.create_account(g.db, email, password)
        affiliate = (request.form.get("affiliate_code")
                     or request.args.get("affiliate")
                     or request.args.get("affiliate_code") or "").strip()
        _assign_signup_group(account_id, affiliate)
        return render_template("signup.html", done=True)

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
        return render_template(
            "account.html", account=account,
            stats=accounts.stats(g.db, principal["id"]),
            usage=storage.usage(g.db, principal["id"]),
            quota=storage.effective_quota(g.db, principal["id"], config),
            balance=billing.balance(g.db, billing.SCOPE_ACCOUNT,
                                    principal["id"]))

    @app.route("/account/pair", methods=["GET"])
    def account_pair():
        """Mint an account-bound pairing token and show its QR (device approval)."""
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        token = pairing.create_pairing_token(
            g.db, config.ENROL_SESSION_TTL_MINUTES, account_id=_principal()["id"])
        qr_text = pairing.build_pairing_qr(config.PUSH_PUBLIC_URL, token)
        img = qrcode.make(qr_text, image_factory=SvgPathImage)
        return render_template("account_pair.html",
                               qr_svg=img.to_string().decode(),
                               server_url=config.PUSH_PUBLIC_URL,
                               pairing_token=token)

    @app.route("/account/pair/events", methods=["GET"])
    def account_pair_events():
        """SSE stream: tell the pairing page the moment its device registers."""
        principal = _principal()
        if principal is None or principal["type"] != "account":
            return jsonify({"error": "unauthorized"}), 401
        token = request.args.get("token", "")
        row = g.db.execute(
            "SELECT account_id FROM pairing_tokens WHERE token = ?", (token,)).fetchone()
        if row is None or row["account_id"] != principal["id"]:
            return jsonify({"error": "unknown pairing token"}), 404

        def stream():
            channel = events.subscribe(token)
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        item = channel.get(timeout=15)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield (f"event: {item['event']}\n"
                           f"data: {json.dumps(item['data'])}\n\n")
            finally:
                events.unsubscribe(token, channel)

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    @app.route("/account/devices", methods=["GET"])
    def account_devices():
        """The account's paired devices, with approve/remove."""
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        account_id = _principal()["id"]
        devices = [dict(r) for r in list_account_devices(g.db, account_id)]
        # Arriving from the pairing page: surface the device just added (newest
        # by registration) in a success banner.
        just_paired = devices[-1] if (request.args.get("paired") and devices) else None
        return render_template("account_devices.html", devices=devices,
                               just_paired=just_paired)

    @app.route("/account/devices/<device_secret>/approve", methods=["POST"])
    def account_device_approve(device_secret):
        auth_error = require_account_form("/account/devices")
        if auth_error:
            return auth_error
        device = get_device_by_secret(g.db, device_secret)
        if device is None or device["account_id"] != _principal()["id"]:
            return redirect("/account/devices", 303)
        pairing.approve_device(g.db, device_secret)
        _publish_device(dict(device))
        return redirect("/account/devices", 303)

    @app.route("/account/devices/<device_secret>/revoke", methods=["POST"])
    def account_device_revoke(device_secret):
        auth_error = require_account_form("/account/devices")
        if auth_error:
            return auth_error
        device = get_device_by_secret(g.db, device_secret)
        if device is None or device["account_id"] != _principal()["id"]:
            return redirect("/account/devices", 303)
        _notify_unpaired(dict(device))
        delete_device(g.db, device_secret)
        return redirect("/account/devices", 303)

    @app.route("/account/clients", methods=["GET"])
    def account_clients():
        """The account's CLI clients, with revoke."""
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        account_id = _principal()["id"]
        return render_template(
            "account_clients.html",
            clients=[dict(r) for r in list_clients(g.db, account_id)],
            server_url=config.PUSH_PUBLIC_URL,
            release=releases.latest(config))

    @app.route("/account/clients/<client_id>/revoke", methods=["POST"])
    def account_client_revoke(client_id):
        auth_error = require_account_form("/account/clients")
        if auth_error:
            return auth_error
        client = get_client(g.db, client_id)
        if client is None or client["account_id"] != _principal()["id"]:
            return redirect("/account/clients", 303)
        revoke_client(g.db, client_id)
        revoke_access_tokens(client_id)
        return redirect("/account/clients", 303)

    @app.route("/account/profile", methods=["GET"])
    def account_profile():
        """Account info: name, email, phone and password."""
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        account = accounts.get_account(g.db, _principal()["id"])
        return render_template("account_profile.html", account=account,
                               ok=request.args.get("ok", ""))

    @app.route("/account/profile", methods=["POST"])
    def account_profile_update():
        auth_error = require_account_form("/account/profile")
        if auth_error:
            return auth_error
        account_id = _principal()["id"]
        account = accounts.get_account(g.db, account_id)
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        password = request.form.get("password", "")
        error = None
        if not email or "@" not in email:
            error = "Enter a valid email."
        elif email != (account["email"] or "") and (
                accounts.get_account_by_email(g.db, email) is not None
                or get_admin_by_email(g.db, email) is not None
                or get_admin_by_firebase_email(g.db, email) is not None):
            error = "That email is already in use."
        if error is None and phone and not re.match(r"^\+\d{6,15}$", phone):
            # Firebase requires E.164; reject early rather than fail the write-through.
            error = "Enter the phone in international format, e.g. +61400000000."
        if error is None and phone and phone != (account["phone"] or "") and (
                accounts.get_account_by_phone(g.db, phone) is not None
                or get_admin_by_firebase_phone(g.db, phone) is not None):
            error = "That phone number is already in use."
        if error is None and password and len(password) < 8:
            error = "Choose a password of 8+ characters."
        if error is None and password and password != request.form.get(
                "password_confirm", ""):
            error = "The passwords do not match."
        if error:
            return render_template("account_profile.html", account=account,
                                   error=error), 400
        # Write through to Firebase so the sign-in provider and the local row
        # stay in step. Identity fields (email/phone/password) must succeed; a
        # name-only change is best-effort, since it isn't an identity.
        uid = account["firebase_uid"]
        identity_changing = (email != (account["email"] or "")
                             or phone != (account["phone"] or "")
                             or bool(password))
        name_changing = name != (account["name"] or "")
        if uid and (identity_changing or name_changing) and identity_admin.available(config):
            fields = {}
            if email != (account["email"] or ""):
                fields["email"] = email
                fields["emailVerified"] = False
            if phone != (account["phone"] or ""):
                if phone:
                    fields["phoneNumber"] = phone
                else:
                    fields["deleteAttribute"] = ["PHONE_NUMBER"]
            if password:
                fields["password"] = password
            if name_changing:
                fields["displayName"] = name
            _result, err = identity_admin.update_user(config, uid, **fields)
            if err and identity_changing:
                return render_template(
                    "account_profile.html", account=account,
                    error=f"Could not update the sign-in provider: {err}"), 502
        accounts.update_account(
            g.db, account_id, name=name, email=email, phone=phone,
            password=password if password else None)
        return redirect("/account/profile", 303)

    @app.route("/account/export")
    def account_export():
        """Download everything this server holds about the signed-in account."""
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        data, name = data_export.account_zip(g.db, config, _principal()["id"])
        return send_file(io.BytesIO(data), mimetype="application/zip",
                         as_attachment=True, download_name=name)

    @app.route("/account/delete-request", methods=["POST"])
    def account_delete_request():
        """Ask to delete the account: email a one-time confirmation link.

        Nothing is scheduled until the link is opened; the account stays
        fully usable until then.
        """
        auth_error = require_account_form("/account/profile")
        if auth_error:
            return auth_error
        account = accounts.get_account(g.db, _principal()["id"])
        if deletions.active_for(g.db, account["account_id"]) is not None:
            return redirect("/account/profile?ok=already-deleted", 303)
        token = deletions.request_self(g.db, config, account)
        base = (config.PUSH_PUBLIC_URL or request.url_root).rstrip("/")
        url = f"{base}/account/delete/confirm?token={token}"
        grace = int(config.DELETION_GRACE_DAYS)
        ttl = int(config.DELETION_EMAIL_TTL_HOURS)
        body = (f"Someone with access to {account['email']} asked to delete"
                f" that account.\n\nConfirm by opening this link within"
                f" {ttl} hours:\n\n{url}\n\nOnce confirmed, the account and"
                f" its data are held for {grace} days and then purged"
                f" permanently; an operator can restore it before then."
                f" If this was not you, ignore this message - nothing has"
                f" been scheduled.\n")
        if app.config.get("TESTING"):
            app.config.setdefault("_test_emails", []).append(
                {"to": account["email"], "subject": "Confirm your account"
                 " deletion", "body": body})
            sent = True
        else:
            sent = mail.send(config, account["email"],
                             "Confirm your account deletion", body)
        if not sent:
            return render_template(
                "account_profile.html", account=account,
                error="Email is not configured on this server - ask the"
                      " operator to delete the account for you."), 503
        return redirect("/account/profile?ok=delete-sent", 303)

    @app.route("/account/link", methods=["POST"])
    def account_link():
        """Bind the signed-in account to the Firebase identity in an ID token.

        The browser runs a Firebase sign-in (magic link, password, or a social
        provider) and posts the ID token here; the account is bound by `sub`
        (uid), so a later email change cannot re-target it. An identity already
        bound elsewhere - another account, or an administrator - is refused,
        and a token that merely *matches* this account's email is still bound
        only by its uid.
        """
        principal = _principal()
        if principal is None or principal["type"] != "account":
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        claims = oidc.verify_firebase_id_token(config, data.get("id_token", ""))
        if claims is None:
            return jsonify({"error": "invalid token"}), 401
        uid = claims.get("sub") or claims.get("user_id")
        if not uid:
            return jsonify({"error": "token has no subject"}), 400
        account_id = principal["id"]
        account = accounts.get_account(g.db, account_id)
        if account is None:
            return jsonify({"error": "unknown account"}), 404
        email = (claims.get("email") or "").strip().lower() or None
        if email and not claims.get("email_verified", False):
            email = None
        phone = (claims.get("phone_number") or "").strip() or None
        other = accounts.get_account_by_firebase_uid(g.db, uid)
        if other is not None and other["account_id"] != account_id:
            return jsonify({"error": "that sign-in is linked to another account"}), 409
        if get_admin_by_firebase_uid(g.db, uid) is not None:
            return jsonify({"error": "that sign-in belongs to an administrator"}), 409
        if (email or phone) and _identity_owned_by_admin(email, phone):
            return jsonify({"error": "that sign-in belongs to an administrator"}), 409
        if email:
            other = accounts.get_account_by_email(g.db, email)
            if other is not None and other["account_id"] != account_id:
                return jsonify({"error": "that email belongs to another account"}), 409
        accounts.set_firebase_uid(g.db, account_id, uid)
        return jsonify({"ok": True, "uid": uid})

    @app.route("/account/pushes", methods=["GET"])
    def account_pushes():
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        return render_template(
            "account_pushes.html",
            pushes=push_store.list_pushes(g.db, _principal()["id"]))

    @app.route("/account/pending", methods=["GET"])
    def account_pending():
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        return render_template(
            "account_pending.html",
            pushes=push_store.list_pending_pushes(g.db, _principal()["id"]))

    @app.route("/account/pending/<push_id>/delete", methods=["POST"])
    def account_pending_delete(push_id):
        auth_error = require_account_form("/account/pending")
        if auth_error:
            return auth_error
        push = push_store.get_push_by_id(g.db, push_id)
        if push is None or push["account_id"] != _principal()["id"]:
            return redirect("/account/pending", 303)
        push_store.delete_push(g.db, push_id)
        shutil.rmtree(os.path.join(config.PUSH_STORAGE_DIR, push_id),
                      ignore_errors=True)
        return redirect(_return_to("/account/pending"), 303)

    @app.route("/account/pushes/purge", methods=["POST"])
    def account_pushes_purge():
        auth_error = require_account_form("/account/pushes")
        if auth_error:
            return auth_error
        _purge_push_dirs(push_store.purge_all(g.db, _principal()["id"]))
        return redirect("/account/pushes", 303)

    @app.route("/account/pending/purge", methods=["POST"])
    def account_pending_purge():
        auth_error = require_account_form("/account/pending")
        if auth_error:
            return auth_error
        _purge_push_dirs(push_store.purge_pending(g.db, _principal()["id"]))
        return redirect("/account/pending", 303)

    # ---- Account billing (PayPal top-ups) ----

    def _public_base() -> str:
        """Externally visible origin for PayPal return/cancel redirects."""
        return (config.PUSH_PUBLIC_URL or request.url_root).rstrip("/")

    def _fail_landing(path, message):
        return redirect(f"{path}?error=" +
                        urllib.parse.quote(str(message)[:160]), 303)

    @app.route("/account/billing", methods=["GET"])
    def account_billing():
        """Self-serve billing: prepaid top-ups, balance and usage history."""
        auth_error = require_account_session()
        if auth_error:
            return auth_error
        account_id = _principal()["id"]
        ledger = [dict(r) for r in g.db.execute(
            "SELECT * FROM billing_ledger WHERE account_type = ? AND account_id = ?"
            " ORDER BY id DESC LIMIT 25",
            (billing.SCOPE_ACCOUNT, account_id)).fetchall()]
        return render_template(
            "account_billing.html",
            account=accounts.get_account(g.db, account_id),
            balance=billing.balance(g.db, billing.SCOPE_ACCOUNT, account_id),
            ledger=ledger,
            plan=billing.effective_plan(g.db, billing.SCOPE_ACCOUNT, account_id),
            paypal_enabled=paypal.enabled(config),
            exempt=billing.account_credit_exempt(g.db, account_id),
            topup_min=getattr(config, "PAYPAL_TOPUP_MIN_CENTS", 500),
            topup_max=getattr(config, "PAYPAL_TOPUP_MAX_CENTS", 50000),
            currency=getattr(config, "PAYPAL_CURRENCY", "AUD"),
            ok=request.args.get("ok", ""),
            error=request.args.get("error", ""))

    @app.route("/account/billing/checkout", methods=["POST"])
    def account_billing_checkout():
        """Start a PayPal checkout for a one-time prepaid credit top-up.

        The browser is redirected to PayPal's hosted approval page; the
        session returns to /billing/paypal/return which reconciles the row.
        """
        auth_error = require_account_form("/account/billing")
        if auth_error:
            return auth_error
        account_id = _principal()["id"]

        def _fail(message):
            return _fail_landing("/account/billing", message)

        if not paypal.enabled(config):
            return _fail("PayPal is not configured on this server")
        base = _public_base()
        client = paypal.client(config)
        amount = _opt_dollars(request.form.get("amount"))
        if not amount:
            return _fail("Enter a top-up amount")
        if amount < getattr(config, "PAYPAL_TOPUP_MIN_CENTS", 500):
            return _fail("Minimum top-up is "
                         f"${getattr(config, 'PAYPAL_TOPUP_MIN_CENTS', 500) / 100:g}")
        if amount > getattr(config, "PAYPAL_TOPUP_MAX_CENTS", 50000):
            return _fail("Maximum top-up is "
                         f"${getattr(config, 'PAYPAL_TOPUP_MAX_CENTS', 50000) / 100:g}")
        currency = getattr(config, "PAYPAL_CURRENCY", "AUD")
        order = billing.create_order(g.db, billing.SCOPE_ACCOUNT, account_id,
                                     amount, currency)
        try:
            payload = client.create_order(
                amount_cents=amount, currency=currency,
                custom_id=f"order:{order['order_id']}",
                return_url=f"{base}/billing/paypal/return?checkout={order['order_id']}",
                cancel_url=f"{base}/billing/paypal/cancel?checkout={order['order_id']}",
                description="MDRender prepaid credit")
        except paypal.PaypalError as exc:
            return _fail(exc)
        approve = paypal.PaypalClient.approve_link_for_order(payload)
        if not approve:
            return _fail("PayPal returned no approval link")
        billing.attach_order_provider(g.db, order["order_id"], payload.get("id"))
        return redirect(approve, 302)

    @app.route("/api/billing/subscription", methods=["GET"])
    def api_billing_subscription():
        """JSON snapshot of the account's plan, credit and entitlement."""
        auth_error = require_account_api()
        if auth_error:
            return auth_error
        account_id = _principal()["id"]
        plan = billing.effective_plan(g.db, billing.SCOPE_ACCOUNT, account_id)
        return jsonify({
            "balance_cents": billing.balance(g.db, billing.SCOPE_ACCOUNT, account_id),
            "entitled": billing.entitled(g.db, billing.SCOPE_ACCOUNT, account_id),
            "credit_gate_exempt": billing.account_credit_exempt(g.db, account_id),
            "plan": ({"id": plan["plan_id"], "name": plan["name"],
                      "price_cents": plan["price_cents"],
                      "currency": plan["currency"],
                      "require_credit": bool(plan["require_credit"])}
                     if plan else None),
        })

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
        # Gated only when the plan says so (require_credit or a storage
        # rate) and the account is not exempt - enforcement is the master
        # switch on top.
        gated = bool(getattr(config, "BILLING_ENFORCEMENT", False)) and \
            billing.storage_credit_gate(g.db, account_id)
        credit = billing.balance(g.db, billing.SCOPE_ACCOUNT, account_id)
        if gated and credit <= 0:
            return jsonify({"error": "payment required",
                            "detail": "This account has no credit: top up on"
                                      " the Billing page before uploading."}), 402
        if _encryption_required() and not (
                request.form.get("alg") and request.form.get("nonce")):
            return jsonify({"error": "encryption required"}), 422
        uploads = request.files.getlist("file")
        if not uploads:
            return jsonify({"error": "no files"}), 400
        if gated:
            # Every file in the batch must fit inside the current credit.
            for f in uploads:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(0)
                cost = billing.file_cost_cents(g.db, account_id, size)
                if cost > credit:
                    return jsonify({
                        "error": "payment required",
                        "detail": f"'{os.path.basename(f.filename or 'file')}'"
                                  f" costs {cost} cents of credit but this"
                                  f" account only has {credit} cents: top up"
                                  " before uploading."}), 402
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

    @app.route("/api/account/keys", methods=["PUT"])
    def account_set_key():
        """Register the account's content public key (design §7a)."""
        auth_error = require_account_api()
        if auth_error:
            return auth_error
        data = request.get_json(silent=True) or {}
        public_key = data.get("public_key")
        if not public_key:
            return jsonify({"error": "public_key required"}), 400
        encryption.set_account_public_key(g.db, _principal()["id"], public_key)
        return jsonify({"ok": True})

    @app.route("/api/account/devices/<device_id>/content-pubkey",
               methods=["PUT", "GET"])
    def account_device_content_pubkey(device_id):
        """The app's content public key (the client seals the CEK to it)."""
        auth_error = require_account_api()
        if auth_error:
            return auth_error
        if request.method == "PUT":
            data = request.get_json(silent=True) or {}
            public_key = data.get("public_key")
            if not public_key:
                return jsonify({"error": "public_key required"}), 400
            encryption.set_device_public_key(g.db, device_id, public_key)
            return jsonify({"ok": True})
        # Return the app's content key together with the pairing-key proof and the
        # device's pairing public key, so the client can verify the chain before
        # sealing the CEK (design §7c). The server holds no decryption material.
        device = get_device_by_secret(g.db, device_id)
        if device is None or not device["content_pubkey"]:
            return jsonify({"error": "not found"}), 404
        return jsonify({"device_id": device_id,
                        "content_pubkey": device["content_pubkey"],
                        "content_proof": device["content_proof"],
                        "device_public_key": device["public_key"]})

    @app.route("/api/account/devices/<device_id>/sealed-cek", methods=["PUT"])
    def account_device_sealed_cek(device_id):
        """Store the client-sealed CEK (opaque; the server cannot open it)."""
        auth_error = require_account_api()
        if auth_error:
            return auth_error
        data = request.get_json(silent=True) or {}
        sealed = data.get("sealed_cek")
        if not sealed:
            return jsonify({"error": "sealed_cek required"}), 400
        encryption.set_sealed_cek(g.db, device_id, sealed,
                                  alg=data.get("alg", "rsa-oaep-sha256"))
        return jsonify({"ok": True})

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
        # An admin (CLI tools) or an account (own CLI clients) may approve.
        if _enrol_actor() is None:
            nxt = urllib.parse.quote(request.path, safe="/")
            return redirect(f"/login?next={nxt}", 303)
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
        human at this server approved the tool. An account's approval binds the
        client to that account.
        """
        actor = _enrol_actor()
        if actor is None:
            nxt = urllib.parse.quote(f"/enrol/{eid}", safe="/")
            return redirect(f"/login?next={nxt}", 303)
        if _approve_enrolment(eid, actor[1]) is None:
            return render_template("enrol.html", eid=eid, code=None,
                                   code_expires=None, approved=False), 404
        return render_template("enrol_done.html")

    @app.route("/enrol/<eid>/new-code", methods=["POST"])
    def enrol_new_code(eid):
        """Issue a fresh short code for an enrolment whose code expired."""
        if _enrol_actor() is None:
            nxt = urllib.parse.quote(f"/enrol/{eid}", safe="/")
            return redirect(f"/login?next={nxt}", 303)
        entry = app.config["_enrol_keys"].get(eid)
        if entry is not None and not entry["approved"]:
            _refresh_code(entry)
        return redirect(f"/enrol/{eid}", 303)

    @app.route("/admins", methods=["GET"])
    def admins():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template(
            "admins.html", admins=list_admins(g.db),
            admin_firebase_login=bool(getattr(config, "ADMIN_FIREBASE_LOGIN", False)))

    @app.route("/admins", methods=["POST"])
    def admins_create():
        auth_error = require_form_session("/admins")
        if auth_error:
            return auth_error
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        name = (request.form.get("name") or "").strip() or None
        email = (request.form.get("email") or "").strip() or None
        if len(username) >= 3 and len(password) >= 8:
            create_admin(g.db, username, password, email=email, name=name)
        return redirect("/admins", 303)

    @app.route("/admins/<admin_id>/update", methods=["POST"])
    def admins_update(admin_id):
        """Change an admin's name/email and optionally their password."""
        auth_error = require_form_session("/admins")
        if auth_error:
            return auth_error
        name = (request.form.get("name") or "").strip() or None
        email = (request.form.get("email") or "").strip() or None
        password = request.form.get("password", "")
        update_admin(g.db, admin_id, name=name, email=email,
                     password=password if len(password) >= 8 else None)
        return redirect("/admins", 303)

    @app.route("/admins/link", methods=["POST"])
    def admins_link():
        """Bind a Firebase uid (from a freshly-signed-in user) to an admin.

        The browser signs in with Firebase and posts the ID token here; we bind
        by `sub` (uid), so a later email/phone change cannot re-target the admin.
        """
        auth_error = require_session()
        if auth_error:
            return auth_error
        data = request.get_json(silent=True) or {}
        claims = oidc.verify_firebase_id_token(config, data.get("id_token", ""))
        if claims is None:
            return jsonify({"error": "invalid token"}), 401
        uid = claims.get("sub") or claims.get("user_id")
        if not uid:
            return jsonify({"error": "token has no subject"}), 400
        email = (claims.get("email") or "").strip().lower() or None
        if email and not claims.get("email_verified", False):
            email = None
        phone = (claims.get("phone_number") or "").strip() or None
        principal = _principal()
        admin_id = data.get("admin_id") or (principal["id"] if principal else None)
        if get_admin(g.db, admin_id) is None:
            return jsonify({"error": "unknown admin"}), 404
        conflict = _firebase_link_conflict(uid, email, phone, admin_id=admin_id)
        if conflict:
            return jsonify({"error": conflict}), 409
        link_firebase(g.db, admin_id, uid=uid, email=email, phone=phone)
        return jsonify({"ok": True, "uid": uid, "email": email, "phone": phone})

    @app.route("/admins/<admin_id>/unlink", methods=["POST"])
    def admins_unlink(admin_id):
        auth_error = require_form_session("/admins")
        if auth_error:
            return auth_error
        unlink_firebase(g.db, admin_id)
        return redirect("/admins", 303)

    @app.route("/accounts", methods=["GET"])
    def accounts_page():
        """Users (accounts) and the devices related to them (design §9)."""
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        rows = accounts.list_accounts(g.db)
        balances = {r["account_id"]: r["bal"] for r in g.db.execute(
            "SELECT account_id, SUM(amount_cents) AS bal FROM billing_ledger"
            " WHERE account_type = ? GROUP BY account_id",
            (billing.SCOPE_ACCOUNT,)).fetchall()}
        items = [{**dict(r),
                  "device_count": accounts.device_count(g.db, r["account_id"]),
                  "balance": balances.get(r["account_id"], 0)}
                 for r in rows]
        pending = deletions.list_pending(g.db)
        return render_template(
            "accounts.html", accounts=items,
            pending=pending,
            pending_ids={p["account_id"]: p for p in pending},
            can_sync=identity_admin.available(config),
            error=request.args.get("error", ""))

    @app.route("/accounts", methods=["POST"])
    def accounts_create():
        auth_error = require_form_session("/accounts")
        if auth_error:
            return auth_error
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password", "")
        name = (request.form.get("name") or "").strip() or None
        if "@" in email and accounts.get_account_by_email(g.db, email) is None:
            accounts.create_account(g.db, email, password or None, name=name)
        return redirect("/accounts", 303)

    @app.route("/accounts/<account_id>/update", methods=["POST"])
    def accounts_update(account_id):
        auth_error = require_form_session("/accounts")
        if auth_error:
            return auth_error
        accounts.update_account(
            g.db, account_id,
            name=(request.form.get("name") or "").strip() or None,
            email=(request.form.get("email") or "").strip() or None,
            password=request.form.get("password") or None)
        return redirect("/accounts", 303)

    @app.route("/accounts/<account_id>/sync-firebase", methods=["POST"])
    def accounts_sync_firebase(account_id):
        """Create (or adopt) the Firebase Auth user behind a local account.

        An email that already exists in Firebase is bound to that user;
        otherwise the user is created (with no password - they sign in by
        link, social, or a reset). Either way the account records the uid so
        the next sign-in resolves to it. A uid bound to anyone else - another
        account or an administrator - is refused.
        """
        auth_error = require_form_session("/accounts")
        if auth_error:
            return auth_error

        def _fail(message):
            return redirect("/accounts?error=" + urllib.parse.quote(message), 303)

        if not identity_admin.available(config):
            return _fail("Firebase Admin credentials unavailable.")
        account = accounts.get_account(g.db, account_id)
        if account is None:
            return _fail("Unknown account.")
        if account["firebase_uid"]:
            return redirect("/accounts", 303)
        email = (account["email"] or "").strip().lower()
        if not email:
            return _fail("This account has no email address to link.")
        if _identity_owned_by_admin(email, None):
            return _fail("That email belongs to an administrator.")
        user, error = identity_admin.get_user_by_email(config, email)
        if error:
            return _fail("Could not reach the identity provider: " + error)
        if user is not None:
            uid = user.get("localId")
            if not uid:
                return _fail("The identity provider returned no user id.")
            other = accounts.get_account_by_firebase_uid(g.db, uid)
            if other is not None and other["account_id"] != account_id:
                return _fail("That Firebase user is already linked to another account.")
            if get_admin_by_firebase_uid(g.db, uid) is not None:
                return _fail("That Firebase user belongs to an administrator.")
            accounts.set_firebase_uid(g.db, account_id, uid)
            return redirect("/accounts", 303)
        uid, error = identity_admin.create_user(
            config, email=email, display_name=account["name"])
        if error:
            return _fail("Could not create the Firebase user: " + str(error))
        accounts.set_firebase_uid(g.db, account_id, uid)
        return redirect("/accounts", 303)

    @app.route("/accounts/<account_id>/status", methods=["POST"])
    def accounts_status(account_id):
        """Activate / deactivate (block) / ban a user."""
        auth_error = require_form_session("/accounts")
        if auth_error:
            return auth_error
        status = request.form.get("status", "")
        if status in (accounts.ACTIVE, accounts.BLOCKED, accounts.BANNED):
            accounts.set_account_status(g.db, account_id, status)
        return redirect(_return_to("/accounts"), 303)

    @app.route("/accounts/<account_id>/delete", methods=["POST"])
    def accounts_delete(account_id):
        """Schedule deletion: the account locks now and is purged after the
        grace period. Listed on the Users page under pending deletions, where
        the operator can still restore it."""
        auth_error = require_form_session("/accounts")
        if auth_error:
            return auth_error
        if accounts.get_account(g.db, account_id) is None:
            return redirect("/accounts", 303)
        deletions.schedule_admin(g.db, config, account_id, _principal()["id"])
        return redirect(_return_to("/accounts"), 303)

    @app.route("/accounts/<account_id>/restore", methods=["POST"])
    def accounts_restore(account_id):
        """Undelete a pending account: drop its deletion and unlock it."""
        auth_error = require_form_session("/accounts")
        if auth_error:
            return auth_error
        deletions.restore(g.db, account_id)
        return redirect(_return_to("/accounts"), 303)

    @app.route("/accounts/<account_id>/export")
    def accounts_export(account_id):
        """Download an account's data as a ZIP (operator view)."""
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        if accounts.get_account(g.db, account_id) is None:
            return redirect("/accounts", 303)
        data, name = data_export.account_zip(g.db, config, account_id)
        return send_file(io.BytesIO(data), mimetype="application/zip",
                         as_attachment=True, download_name=name)

    @app.route("/accounts/<account_id>", methods=["GET"])
    def account_detail(account_id):
        """One user's devices, clients and pushes, with block/remove controls.

        The Users list links here; every action posts back to this page via a
        hidden `next` field (design §9).
        """
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        account = accounts.get_account(g.db, account_id)
        if account is None:
            return redirect(
                "/accounts?error=" + urllib.parse.quote("Unknown account."), 303)
        return render_template(
            "account_detail.html",
            account=dict(account),
            devices=[dict(r) for r in list_account_devices(g.db, account_id)],
            clients=[dict(r) for r in list_clients(g.db, account_id)],
            pushes=push_store.list_pushes(g.db, account_id),
            pending=push_store.list_pending_pushes(g.db, account_id),
            stats=accounts.stats(g.db, account_id),
            balance=billing.balance(g.db, billing.SCOPE_ACCOUNT, account_id),
            error=request.args.get("error", ""))

    @app.route("/accounts/<account_id>/devices/<device_secret>/block",
               methods=["POST"])
    def account_detail_device_block(account_id, device_secret):
        """Block or unblock a device without unpairing it."""
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        device = get_device_by_secret(g.db, device_secret)
        if device is None or device["account_id"] != account_id:
            return redirect(f"/accounts/{account_id}", 303)
        set_device_blocked(g.db, device_secret,
                           request.form.get("blocked", "") == "1")
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

    @app.route("/accounts/<account_id>/credit-gate-exempt", methods=["POST"])
    def accounts_credit_gate_exempt(account_id):
        """Per-account override: exempt from / return to the credit gate.

        Wins over the plan's ``require_credit`` checkbox and metering: an
        exempt account may upload and push whatever its balance says.
        """
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        if accounts.get_account(g.db, account_id) is None:
            return redirect(f"/accounts/{account_id}", 303)
        billing.set_account_credit_exempt(
            g.db, account_id, request.form.get("exempt") == "1")
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

    @app.route("/accounts/<account_id>/devices/<device_secret>/revoke",
               methods=["POST"])
    def account_detail_device_revoke(account_id, device_secret):
        """Unpair a device (design §9): it must re-pair before receiving pushes."""
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        device = get_device_by_secret(g.db, device_secret)
        if device is None or device["account_id"] != account_id:
            return redirect(f"/accounts/{account_id}", 303)
        _notify_unpaired(dict(device))
        delete_device(g.db, device_secret)
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

    @app.route("/accounts/<account_id>/clients/<client_id>/block", methods=["POST"])
    def account_detail_client_block(account_id, client_id):
        """Block or unblock a CLI client; unlike revoke, this is reversible."""
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        client = get_client(g.db, client_id)
        if client is None or client["account_id"] != account_id:
            return redirect(f"/accounts/{account_id}", 303)
        set_client_blocked(g.db, client_id, request.form.get("blocked", "") == "1")
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

    @app.route("/accounts/<account_id>/clients/<client_id>/revoke", methods=["POST"])
    def account_detail_client_revoke(account_id, client_id):
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        client = get_client(g.db, client_id)
        if client is None or client["account_id"] != account_id:
            return redirect(f"/accounts/{account_id}", 303)
        revoke_client(g.db, client_id)
        revoke_access_tokens(client_id)
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

    @app.route("/accounts/<account_id>/pushes/<push_id>/delete", methods=["POST"])
    def account_detail_push_delete(account_id, push_id):
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        push = push_store.get_push_by_id(g.db, push_id)
        if push is None or push["account_id"] != account_id:
            return redirect(f"/accounts/{account_id}", 303)
        push_store.delete_push(g.db, push_id)
        _purge_push_dirs([push_id])
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

    @app.route("/accounts/<account_id>/pushes/purge", methods=["POST"])
    def account_detail_pushes_purge(account_id):
        """Delete every push (and its stored files) belonging to this account."""
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        _purge_push_dirs(push_store.purge_all(g.db, account_id))
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

    @app.route("/accounts/<account_id>/pending/purge", methods=["POST"])
    def account_detail_pending_purge(account_id):
        """Delete this account's still-pending pushes (free their storage)."""
        auth_error = require_form_session(f"/accounts/{account_id}")
        if auth_error:
            return auth_error
        _purge_push_dirs(push_store.purge_pending(g.db, account_id))
        return redirect(_return_to(f"/accounts/{account_id}"), 303)

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
            encryption=("on" if _encryption_required() else "off"),
        )

    @app.route("/settings", methods=["GET"])
    def settings_page():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template(
            "settings.html",
            federation_env=bool(getattr(config, "FEDERATION_ENABLED", True)),
            accept_new_slaves=settings.accept_new_slaves(g.db),
            signup_enabled=settings.signup_enabled(g.db),
            signup_group_id=settings.signup_group_id(g.db),
            groups=[dict(gr) for gr in billing.list_groups(g.db)],
            server_plans=[dict(p) for p in billing.list_plans(g.db)
                          if p["scope"] == billing.SCOPE_SLAVE],
            default_server_plan_id=settings.default_server_plan_id(g.db),
        )

    @app.route("/settings", methods=["POST"])
    def settings_update():
        auth_error = require_form_session("/settings")
        if auth_error:
            return auth_error
        settings.set_value(g.db, "accept_new_slaves",
                           "on" if request.form.get("accept_new_slaves") else "off")
        settings.set_value(g.db, "signup_enabled",
                           "on" if request.form.get("signup_enabled") else "off")
        settings.set_value(g.db, "signup_group_id",
                           (request.form.get("signup_group_id") or "").strip())
        # Only a real server plan may be the default; anything else is ignored
        # (the stored value stands) so a forged post cannot set nonsense.
        plan_id = (request.form.get("default_server_plan_id") or "").strip()
        if not plan_id:
            settings.set_value(g.db, "default_server_plan_id", "")
        else:
            plan = billing.get_plan(g.db, plan_id)
            if plan is not None and plan["scope"] == billing.SCOPE_SLAVE:
                settings.set_value(g.db, "default_server_plan_id", plan_id)
        return redirect("/settings", 303)

    @app.route("/federation", methods=["GET"])
    def federation_page():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        master_url = _effective_master_url() if app.config[
            "_server_role"] == "slave" else getattr(config, "MASTER_URL", "")
        reg = federation_client.get_state(g.db)
        # A slave always gets the Connect panel (even before it has picked a
        # master); everything else (including a master that lists its slaves)
        # keeps the enrolment table.
        slave_view = app.config["_server_role"] == "slave"
        plans_by_id = {p["plan_id"]: dict(p)
                       for p in billing.list_plans(g.db)
                       if p["scope"] == billing.SCOPE_SLAVE}
        default_plan = plans_by_id.get(settings.default_server_plan_id(g.db))
        balance_by_server = {r["account_id"]: r["bal"] for r in g.db.execute(
            "SELECT account_id, SUM(amount_cents) AS bal FROM billing_ledger"
            " WHERE account_type = ? GROUP BY account_id",
            (billing.SCOPE_SLAVE,)).fetchall()}
        # A slave shows the plan the master has for it: fetched live over the
        # signed link (design §7) so the page reflects the master's current view.
        plan_state, plan = "none", None
        if slave_view and reg is not None:
            try:
                reply = federation_client.fetch_plan(
                    config, g.db, app.config["_server_identity"], timeout=4)
                plan = reply.get("plan")
                plan_state = "ok"
            except Exception:  # noqa: BLE001 - master unreachable/offline
                plan_state = "unavailable"
        return render_template(
            "federation.html",
            slave_view=slave_view,
            servers=[dict(r) for r in federation.list_servers(g.db)],
            server_plans=sorted(plans_by_id.values(),
                                key=lambda p: p["name"].lower()),
            plans_by_id=plans_by_id,
            default_plan=default_plan,
            default_plan_id=default_plan["plan_id"] if default_plan else None,
            default_plan_name=default_plan["name"] if default_plan else None,
            error=request.args.get("error", ""),
            balance_by_server=balance_by_server,
            paypal_url=request.args.get("paypal_url", ""),
            paypal_ok=bool(request.args.get("paypal_ok") or
                            request.args.get("ok") == "topup"),
            paypal_cancelled=bool(request.args.get("paypal_cancelled")),
            paypal_enabled=paypal.enabled(config),
            
            master_url=master_url,
            master_host=_master_host(master_url),
            registered=reg is not None,
            last_heartbeat=reg["last_heartbeat"] if reg else None,
            register_ok=bool(request.args.get("registered")),
            register_error=request.args.get("error", ""),
            plan=plan,
            plan_state=plan_state,
            disconnected=bool(request.args.get("disconnected")),
            notified=request.args.get("notified", "1") == "1")

    @app.route("/federation/connect", methods=["POST"])
    def federation_connect_start():
        """Step 3->4: send the operator to the master to approve this slave.

        The master page is opened as a signed, stateless GET; the slave keeps
        the state so that only the matching callback can finish the enrolment.
        """
        auth_error = require_form_session("/federation")
        if auth_error:
            return auth_error
        if app.config["_server_role"] != "slave":
            return redirect("/federation", 303)

        def _fail(message):
            return redirect("/federation?error=" +
                            urllib.parse.quote(message[:160]), 303)

        master_url = _clean_master_url(request.form.get("master_url"))
        if not master_url:
            return _fail("Enter a master URL, e.g. https://md.example.com")
        if _master_host(master_url) == _master_host(config.PUSH_PUBLIC_URL):
            return _fail("That is this server - a slave connects to a master, "
                         "not to itself")
        settings.set_value(g.db, "federation_master_url", master_url)

        ident = app.config["_server_identity"]
        state = uuid.uuid4().hex
        ts = int(time.time())
        try:
            signature = federation.sign_bytes(
                ident["private_key_pem"],
                federation.connect_canonical(
                    ident["server_id"], ident["hostname"],
                    config.PUSH_PUBLIC_URL, ident["public_key"], state, ts))
        except Exception as exc:  # noqa: BLE001 - unusable identity key
            return _fail(str(exc))
        app.config["_pending_connects"][state] = {"ts": ts,
                                                  "master_url": master_url}
        query = urllib.parse.urlencode({
            "server_id": ident["server_id"],
            "hostname": ident["hostname"],
            "base_url": config.PUSH_PUBLIC_URL,
            "public_key": ident["public_key"],
            "state": state,
            "ts": ts,
            "sig": signature,
        })
        return redirect(f"{master_url}/federation/connect?{query}", 302)

    @app.route("/federation/disconnect", methods=["POST"])
    def disconnect_from_master():
        """Terminate the master connection from the slave (consent terms §5).

        The master is notified over the signed link when it is reachable; the
        local registration is cleared either way, so the button never strands
        the operator. The callout reports whether the master was notified.
        """
        auth_error = require_form_session("/federation")
        if auth_error:
            return auth_error
        if app.config["_server_role"] != "slave":
            return redirect("/federation", 303)
        if federation_client.get_state(g.db) is None:
            return redirect("/federation", 303)
        notified = federation_client.disconnect(
            config, g.db, app.config["_server_identity"])
        return redirect(f"/federation?disconnected=1&notified="
                        f"{'1' if notified else '0'}", 303)

    @app.route("/federation/callback", methods=["GET"])
    def federation_callback():
        """Step 5: the master approved us; exchange the code, then go live."""
        if not _is_admin():
            nxt = request.path
            if request.query_string:
                nxt = request.full_path
            return redirect("/admin-login?next=" +
                            urllib.parse.quote(nxt, safe="/"), 303)
        if app.config["_server_role"] != "slave":
            return redirect("/federation", 303)

        def _fail(message):
            return redirect("/federation?error=" +
                            urllib.parse.quote(message[:160]), 303)

        state = request.args.get("state", "")
        code = request.args.get("code", "")
        pending = app.config["_pending_connects"].pop(state, None)
        if pending is None or int(time.time()) - pending["ts"] > \
                federation.CONNECT_TTL_SECONDS:
            return _fail("The connection request expired or was already used - "
                         "press Connect again")
        if not code:
            return _fail("The master declined the connection or issued no code")
        try:
            federation_client.enrol(config, g.db,
                                    app.config["_server_identity"],
                                    master_url=pending["master_url"],
                                    code=code)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode() or "{}")
                message = detail.get("detail") or detail.get("error") or ""
            except Exception:  # noqa: BLE001 - non-JSON error body
                message = ""
            return _fail(f"master rejected the enrolment ({exc.code})"
                         + (f": {message}" if message else ""))
        except Exception as exc:  # noqa: BLE001 - master unreachable, DNS, ...
            return _fail(str(exc))
        return redirect("/pushes", 303)

    @app.route("/federation/<server_id>/plan", methods=["POST"])
    def federation_server_plan(server_id):
        """Set (or clear) the billing plan for one federated server.

        The stored plan wins over the master's default; an empty selection
        hands the server back to the default.
        """
        auth_error = require_form_session("/federation")
        if auth_error:
            return auth_error

        def _fail(message):
            return redirect("/federation?error=" + urllib.parse.quote(message), 303)

        if federation.get_server(g.db, server_id) is None:
            return _fail("That server is no longer enrolled.")
        plan_id = (request.form.get("plan_id") or "").strip()
        if plan_id:
            plan = billing.get_plan(g.db, plan_id)
            if plan is None or plan["scope"] != billing.SCOPE_SLAVE:
                return _fail("Choose a plan for federated servers.")
        federation.set_plan(g.db, server_id, plan_id or None)
        return redirect("/federation", 303)

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

    @app.route("/federation/<server_id>/checkout", methods=["POST"])
    def federation_checkout(server_id):
        """Start a PayPal top-up for a federated server's prepaid credit.

        Server operators have no session on the master, so the admin gets a
        shareable approval link back on the Federation page (or opens it
        themselves to pay on the server's behalf).
        """
        auth_error = require_form_session("/federation")
        if auth_error:
            return auth_error

        def _fail(message):
            return redirect("/federation?error=" +
                            urllib.parse.quote(str(message)[:160]), 303)

        if not paypal.enabled(config):
            return _fail("PayPal is not configured on this server")
        if federation.get_server(g.db, server_id) is None:
            return _fail("Unknown server")
        plan = billing.slave_effective_plan(g.db, server_id)
        if plan is None or not plan["price_cents"]:
            return _fail("This server's plan is free - no payment needed")
        amount = _opt_dollars(request.form.get("amount"))
        if not amount:
            return _fail("Enter a top-up amount")
        if amount < getattr(config, "PAYPAL_TOPUP_MIN_CENTS", 500):
            return _fail("Minimum top-up is "
                         f"${getattr(config, 'PAYPAL_TOPUP_MIN_CENTS', 500) / 100:g}")
        if amount > getattr(config, "PAYPAL_TOPUP_MAX_CENTS", 50000):
            return _fail("Maximum top-up is "
                         f"${getattr(config, 'PAYPAL_TOPUP_MAX_CENTS', 50000) / 100:g}")
        currency = getattr(config, "PAYPAL_CURRENCY", "AUD")
        order = billing.create_order(g.db, billing.SCOPE_SLAVE, server_id,
                                     amount, currency)
        base = _public_base()
        try:
            payload = paypal.client(config).create_order(
                amount_cents=amount, currency=currency,
                custom_id=f"order:{order['order_id']}",
                return_url=f"{base}/billing/paypal/return?checkout={order['order_id']}",
                cancel_url=f"{base}/billing/paypal/cancel?checkout={order['order_id']}",
                description="MDRender server prepaid credit")
        except paypal.PaypalError as exc:
            return _fail(exc)
        approve = paypal.PaypalClient.approve_link_for_order(payload)
        if not approve:
            return _fail("PayPal returned no approval link")
        billing.attach_order_provider(g.db, order["order_id"], payload.get("id"))
        return redirect("/federation?paypal_url=" +
                        urllib.parse.quote(approve, safe=""), 303)

    def _consent_blocked():
        """Why this master cannot show a consent page, else None."""
        if app.config["_server_role"] != "master":
            return "This server is not a master - it cannot accept slaves.", 404
        if not bool(getattr(config, "FEDERATION_ENABLED", True)):
            return "Federation is disabled on this server.", 403
        if not settings.accept_new_slaves(g.db):
            return "This master is not accepting new slaves.", 403
        return None

    @app.route("/federation/connect", methods=["GET"])
    def federation_consent():
        """Consent page a slave's operator is sent to before it can enrol.

        Reached by browser redirect from the slave, carrying the slave's own
        signed request - so it is public, shows only what the approval would
        grant, and never returns a secret other than the single-use code that
        goes straight back to that slave.
        """
        blocked = _consent_blocked()
        if blocked:
            message, status = blocked
            return render_template("federation_connect.html",
                                   error=message), status

        def _reject(message, status=400):
            return render_template("federation_connect.html",
                                   error=message), status

        server_id = request.args.get("server_id", "")
        hostname = request.args.get("hostname", "")
        base_url = _clean_master_url(request.args.get("base_url", ""))
        public_key = request.args.get("public_key", "")
        state = request.args.get("state", "")
        ts = request.args.get("ts", "")
        sig = request.args.get("sig", "")
        if not all([server_id, hostname, base_url, public_key, state, ts, sig]):
            return _reject("This connection request is incomplete - open the "
                           "Connect link on the slave again.")
        try:
            ts_int = int(ts)
        except ValueError:
            return _reject("This connection request has an invalid timestamp.")
        if abs(int(time.time()) - ts_int) > federation.SIGNATURE_WINDOW_SECONDS:
            return _reject("This connection request has expired - open the "
                           "Connect link on the slave again.", 410)
        if not federation.verify_connect_signature(
                public_key, sig, server_id=server_id, hostname=hostname,
                base_url=base_url, state=state, ts=ts_int):
            return _reject("This connection request could not be verified.",
                           403)
        if _master_host(base_url) == _master_host(config.PUSH_PUBLIC_URL):
            return _reject("That server is this master - a slave connects to "
                           "a master, not to itself.")
        try:
            connect_id = federation.open_connect(
                g.db, server_id=server_id, hostname=hostname,
                base_url=base_url, public_key_b64=public_key, state=state)
        except Exception as exc:  # noqa: BLE001 - duplicate/invalid row
            return _reject(f"Could not record the request ({exc})")
        return render_template(
            "federation_connect.html",
            connect_id=connect_id,
            hostname=hostname,
            server_id=server_id,
            base_url=base_url,
            plans=[dict(p) for p in billing.list_plans(g.db)
                   if p["active"] and p["scope"] == billing.SCOPE_SLAVE],
            terms=_CONSENT_TERMS)

    @app.route("/federation/connect/<connect_id>/approve", methods=["POST"])
    def federation_connect_approve(connect_id):
        blocked = _consent_blocked()
        if blocked:
            message, status = blocked
            return render_template("federation_connect.html",
                                   error=message), status
        code = federation.approve_connect(g.db, connect_id)
        if code is None:
            return render_template(
                "federation_connect.html",
                error="This connection request has expired or was already "
                      "handled - ask the server to press Connect again."), 410
        row = federation.get_connect(g.db, connect_id)
        query = urllib.parse.urlencode({"code": code, "state": row["state"]})
        return redirect(f"{row['base_url'].rstrip('/')}/federation/callback?"
                        f"{query}", 302)

    @app.route("/federation/connect/<connect_id>/decline", methods=["POST"])
    def federation_connect_decline(connect_id):
        blocked = _consent_blocked()
        if blocked:
            message, status = blocked
            return render_template("federation_connect.html",
                                   error=message), status
        federation.decline_connect(g.db, connect_id)
        return render_template("federation_connect.html",
                               declined=True), 200

    @app.route("/bans", methods=["GET"])
    def bans_page():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("bans.html",
                               bans=[dict(b) for b in bans.list_bans(g.db)],
                               kinds=bans.KINDS)

    @app.route("/bans", methods=["POST"])
    def bans_add():
        auth_error = require_form_session("/bans")
        if auth_error:
            return auth_error
        kind = request.form.get("kind", "")
        value = (request.form.get("value") or "").strip()
        if kind in bans.KINDS and value:
            bans.add_ban(g.db, kind, value,
                         scope=request.form.get("scope", "global"),
                         reason=request.form.get("reason") or None)
        return redirect("/bans", 303)

    @app.route("/bans/<int:ban_id>/delete", methods=["POST"])
    def bans_delete(ban_id):
        auth_error = require_form_session("/bans")
        if auth_error:
            return auth_error
        bans.remove_ban(g.db, ban_id)
        return redirect("/bans", 303)

    def _opt_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _opt_dollars(value):
        """Dollars from the form -> whole cents (None if not a number)."""
        try:
            return int(round(float(value) * 100))
        except (TypeError, ValueError):
            return None

    def _opt_mb(value):
        """Megabytes from the form -> bytes (None if not a number)."""
        try:
            return int(round(float(value) * 1048576))
        except (TypeError, ValueError):
            return None

    @app.route("/billing", methods=["GET"])
    def billing_page():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        billing.ensure_default_group(g.db)
        tab = request.args.get("tab", "plans")
        if tab not in ("plans", "groups", "payments"):
            tab = "plans"
        plans = [dict(p) for p in billing.list_plans(g.db)]
        groups = [dict(gr) for gr in billing.list_groups(g.db)]
        plans_by_id = {p["plan_id"]: p for p in plans}
        orders = [dict(o) for o in billing.list_orders(g.db)]
        return render_template(
            "billing.html", tab=tab, plans=plans, groups=groups,
            plans_by_id=plans_by_id,
            plan_names={p["plan_id"]: p["name"] for p in plans},
            group_names={gr["group_id"]: gr["name"] for gr in groups},
            orders=orders,
            paypal_enabled=paypal.enabled(config),
            paypal_mode=getattr(config, "PAYPAL_MODE", "sandbox"),
            paypal_webhook=bool(getattr(config, "PAYPAL_WEBHOOK_ID", "")),
            notice=request.args.get("paypal", ""),
            error=request.args.get("error", ""))

    @app.route("/billing/plans", methods=["POST"])
    def billing_plan_create():
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        name = (request.form.get("name") or "").strip()
        scope = request.form.get("scope", billing.SCOPE_ACCOUNT)
        if name and scope in (billing.SCOPE_SLAVE, billing.SCOPE_ACCOUNT):
            billing.create_plan(
                g.db, name, scope,
                price_cents=_opt_dollars(request.form.get("price")) or 0,
                interval=request.form.get("interval", "month"),
                included_messages=_opt_int(request.form.get("included_messages")) or 0,
                message_cents_per_1000=_opt_dollars(request.form.get("message_cost")) or 0,
                storage_cents_per_mb=_opt_dollars(request.form.get("storage_cost")) or 0,
                storage_grace_days=_opt_int(request.form.get("storage_grace_days")) or 0,
                max_messages_per_month=_opt_int(request.form.get("max_messages_per_month")) or 0,
                max_pending_bytes=_opt_mb(request.form.get("pending_mb")) or 0,
                pending_expiry_hours=_opt_int(request.form.get("pending_expiry_hours")) or 0,
                require_credit=1 if request.form.get("require_credit") else 0)
        return redirect("/billing?tab=plans", 303)

    @app.route("/billing/plans/<plan_id>", methods=["POST"])
    def billing_plan_update(plan_id):
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        billing.update_plan(
            g.db, plan_id,
            name=(request.form.get("name") or "").strip() or None,
            scope=request.form.get("scope") or None,
            price_cents=_opt_dollars(request.form.get("price")),
            interval=request.form.get("interval") or None,
            included_bytes=_opt_int(request.form.get("included_bytes")),
            included_messages=_opt_int(request.form.get("included_messages")),
            storage_cents_per_mb=_opt_dollars(request.form.get("storage_cost")),
            message_cents_per_1000=_opt_dollars(request.form.get("message_cost")),
            max_messages_per_month=_opt_int(request.form.get("max_messages_per_month")),
            storage_grace_days=_opt_int(request.form.get("storage_grace_days")),
            max_pending_bytes=_opt_mb(request.form.get("pending_mb")),
            pending_expiry_hours=_opt_int(request.form.get("pending_expiry_hours")),
            require_credit=1 if request.form.get("require_credit") else 0,
        )
        return redirect("/billing?tab=plans", 303)

    @app.route("/billing/plans/<plan_id>/active", methods=["POST"])
    def billing_plan_active(plan_id):
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        billing.set_plan_active(g.db, plan_id, request.form.get("active") == "1")
        return redirect("/billing?tab=plans", 303)

    @app.route("/billing/groups", methods=["POST"])
    def billing_group_create():
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        name = (request.form.get("name") or "").strip()
        if name:
            billing.create_group(
                g.db, name,
                plan_id=request.form.get("plan_id") or None,
                trial_days=_opt_int(request.form.get("trial_days")) or 0,
                next_group_id=request.form.get("next_group_id") or None,
                affiliate_code=(request.form.get("affiliate_code") or "").strip() or None,
                affiliate_enabled=1 if request.form.get("affiliate_enabled") else 0)
        return redirect("/billing?tab=groups", 303)

    @app.route("/billing/groups/<group_id>/update", methods=["POST"])
    def billing_group_update(group_id):
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        billing.update_group(
            g.db, group_id,
            name=(request.form.get("name") or "").strip() or None,
            plan_id=request.form.get("plan_id") or None,
            trial_days=_opt_int(request.form.get("trial_days")) or 0,
            next_group_id=request.form.get("next_group_id") or None,
            affiliate_code=(request.form.get("affiliate_code") or "").strip() or None,
            affiliate_enabled=1 if request.form.get("affiliate_enabled") else 0)
        return redirect("/billing?tab=groups", 303)

    @app.route("/billing/groups/<group_id>/plan", methods=["POST"])
    def billing_group_plan(group_id):
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        billing.set_group_plan(g.db, group_id, request.form.get("plan_id") or None)
        return redirect("/billing?tab=groups", 303)

    @app.route("/billing/groups/<group_id>/rename", methods=["POST"])
    def billing_group_rename(group_id):
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        name = (request.form.get("name") or "").strip()
        if name:
            billing.rename_group(g.db, group_id, name)
        return redirect("/billing?tab=groups", 303)

    @app.route("/billing/groups/<group_id>/delete", methods=["POST"])
    def billing_group_delete(group_id):
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        billing.delete_group(g.db, group_id)
        return redirect("/billing?tab=groups", 303)

    @app.route("/billing/credit", methods=["POST"])
    def billing_credit():
        auth_error = require_form_session("/billing")
        if auth_error:
            return auth_error
        account_type = request.form.get("account_type", billing.SCOPE_ACCOUNT)
        account_id = (request.form.get("account_id") or "").strip()
        try:
            amount = int(request.form.get("amount_cents") or 0)
        except ValueError:
            amount = 0
        if account_id and amount:
            billing.add_credit(g.db, account_type, account_id, amount,
                               reason=request.form.get("reason") or "manual")
        return redirect("/billing", 303)

    def _paypal_landing(sub_or_order):
        """Where a PayPal return/cancel goes back to, by checkout scope."""
        if sub_or_order["account_type"] == billing.SCOPE_ACCOUNT:
            return "/account/billing"
        return "/federation"

    def _paypal_cancel_url(landing, account_type):
        if account_type == billing.SCOPE_ACCOUNT:
            return f"{landing}?cancelled=1"
        return f"{landing}?paypal_cancelled=1"

    @app.route("/billing/paypal/return", methods=["GET"])
    def paypal_return():
        """PayPal sent the buyer back: reconcile the checkout with the API.

        Deliberately session-less - the approval link may be completed in
        another browser (e.g. a payment link handed to a server operator),
        and entitlement is decided by what *PayPal* reports here, not by the
        caller's identity.
        """
        checkout = (request.args.get("checkout") or "").strip()
        order = billing.get_order(g.db, checkout) if checkout else None
        if order is not None:
            landing = _paypal_landing(order)
            if order["status"] == "pending":
                if not paypal.enabled(config):
                    return _fail_landing(landing, "PayPal is not configured")
                client = paypal.client(config)
                provider_id = order["provider_order_id"] or request.args.get(
                    "token") or ""
                try:
                    remote = client.get_order(provider_id)
                    if remote.get("status") == "APPROVED":
                        remote = client.capture_order(remote.get("id") or provider_id)
                    if remote.get("status") in ("COMPLETED", "CAPTURED"):
                        billing.complete_order(g.db, order["order_id"],
                                               remote.get("id") or provider_id)
                        return redirect(f"{landing}?ok=topup", 303)
                    return _fail_landing(
                        landing, f"PayPal order is {remote.get('status', 'unknown')}")
                except paypal.PaypalError as exc:
                    return _fail_landing(landing, exc)
            return redirect(f"{landing}?ok=topup", 303)
        return redirect("/", 303)

    @app.route("/billing/paypal/cancel", methods=["GET"])
    def paypal_cancel():
        """The buyer backed out of PayPal: drop the abandoned checkout."""
        checkout = (request.args.get("checkout") or "").strip()
        order = billing.get_order(g.db, checkout) if checkout else None
        if order is not None:
            landing = _paypal_landing(order)
            if order["status"] == "pending":
                g.db.execute("DELETE FROM billing_orders WHERE order_id = ?",
                             (order["order_id"],))
                g.db.commit()
            return redirect(
                _paypal_cancel_url(landing, order["account_type"]), 303)
        return redirect("/", 303)

    def _apply_billing_event(conn, event) -> str:
        """One webhook event -> local bookkeeping. Returns a short note.

        Idempotent by construction: every branch re-reads the row, and the
        webhook's event id is claimed before this runs. Only one-time top-up
        captures matter now; subscription lifecycle events are ignored.
        """
        etype = event.get("event_type", "")
        res = event.get("resource") or {}
        provider_id = res.get("id", "")
        if etype == "PAYMENT.CAPTURE.COMPLETED":
            custom = res.get("custom_id") or ""
            if not custom:
                units = res.get("purchase_units") or []
                custom = (units[0].get("custom_id") or "") if units else ""
            if custom.startswith("order:"):
                order = billing.get_order(conn, custom[6:])
                if order is None:
                    return "unknown order"
                if billing.complete_order(conn, order["order_id"], provider_id):
                    return "topup credited"
                return "topup already credited"
        return "ignored"

    @app.route("/api/billing/webhook", methods=["POST"])
    def billing_webhook():
        """PayPal lifecycle events: signature-verified and event-id idempotent.

        A duplicate delivery answers 200 without re-running; a processing
        failure answers 500 so PayPal retries.
        """
        raw = request.get_data(cache=False) or b""
        if bool(getattr(config, "PAYPAL_VERIFY_WEBHOOKS", True)):
            try:
                if not paypal.client(config).verify_webhook(request.headers, raw):
                    return jsonify({"error": "bad signature"}), 400
            except paypal.PaypalError as exc:
                return jsonify({"error": str(exc)}), 400
        try:
            event = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return jsonify({"error": "bad payload"}), 400
        event_id = event.get("id") or ""
        event_type = event.get("event_type", "")
        if event_id:
            cur = g.db.execute(
                "INSERT OR IGNORE INTO billing_webhook_events"
                " (event_id, event_type, status, received_at) VALUES (?, ?, 'received', ?)",
                (event_id, event_type, int(time.time())))
            g.db.commit()
            if cur.rowcount == 0:
                return jsonify({"ok": True, "duplicate": True})
        try:
            detail = _apply_billing_event(g.db, event)
        except Exception as exc:  # noqa: BLE001 - report, PayPal will retry
            if event_id:
                g.db.execute(
                    "UPDATE billing_webhook_events SET status = 'error', detail = ?"
                    " WHERE event_id = ?", (str(exc)[:300], event_id))
                g.db.commit()
            return jsonify({"error": "processing failed"}), 500
        if event_id:
            g.db.execute(
                "UPDATE billing_webhook_events SET status = 'processed', detail = ?"
                " WHERE event_id = ?", (detail[:300], event_id))
            g.db.commit()
        return jsonify({"ok": True, "detail": detail})

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
                               server_url=config.PUSH_PUBLIC_URL,
                               release=releases.latest(config))

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
                               pushes=push_store.list_pushes(g.db),
                               stats=push_store.push_stats(g.db),
                               by_account=push_store.pushes_by_account(g.db))

    @app.route("/pending", methods=["GET"])
    def pending():
        auth_error = require_page_session()
        if auth_error:
            return auth_error
        return render_template("pending.html",
                               pushes=push_store.list_pending_pushes(g.db))

    def _purge_push_dirs(push_ids):
        for push_id in push_ids:
            shutil.rmtree(os.path.join(config.PUSH_STORAGE_DIR, push_id),
                          ignore_errors=True)

    @app.route("/pushes/purge", methods=["POST"])
    def pushes_purge():
        """Delete every push record and its stored files."""
        auth_error = require_form_session("/pushes")
        if auth_error:
            return auth_error
        _purge_push_dirs(push_store.purge_all(g.db))
        return redirect("/pushes", 303)

    @app.route("/pending/purge", methods=["POST"])
    def pending_purge():
        """Delete every push that still has pending files."""
        auth_error = require_form_session("/pending")
        if auth_error:
            return auth_error
        _purge_push_dirs(push_store.purge_pending(g.db))
        return redirect("/pending", 303)

    @app.route("/devices/<device_secret>", methods=["DELETE"])
    def devices_delete(device_secret):
        auth_error = require_form_session("/devices")
        if auth_error:
            return auth_error
        device = get_device_by_secret(g.db, device_secret)
        if device is not None:
            _notify_unpaired(dict(device))
        delete_device(g.db, device_secret)
        return redirect("/devices", 303)

    @app.route("/devices/<device_secret>/delete", methods=["POST"])
    def devices_delete_post(device_secret):
        auth_error = require_form_session("/devices")
        if auth_error:
            return auth_error
        device = get_device_by_secret(g.db, device_secret)
        if device is not None:
            _notify_unpaired(dict(device))
        delete_device(g.db, device_secret)
        return redirect("/devices", 303)

    # ---- Unauthenticated / Bearer / enrolment API ----

    def _encryption_required() -> bool:
        """Server setting: on = clients must encrypt (design §7b)."""
        return encryption_required(config)

    @app.context_processor
    def _inject_policy():
        """Let templates conceal push metadata an encrypted server never keeps.

        With encryption on, the server stores opaque ids only (§7b/D13), so
        every folder/name/path cell renders as "(encrypted)" rather than
        showing values that should not exist.
        """
        return {"encryption_on": _encryption_required()}

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"ok": True})

    @app.route("/api/server/policy", methods=["GET"])
    def server_policy():
        """Advertise server policy so clients/apps negotiate (design §7b)."""
        return jsonify({"encryption": "on" if _encryption_required() else "off"})

    @app.route("/auth/providers", methods=["GET"])
    def auth_providers():
        """Enabled login providers for the account portal (design §4a)."""
        providers = ["local"]
        if (getattr(config, "IDENTITY_PROVIDER", "local") or "local").lower() == "firebase":
            providers.append("firebase")
        return jsonify({"providers": providers})

    @app.route("/auth/oidc", methods=["POST"])
    def auth_oidc():
        """Exchange a hosted-IdP (Firebase) ID token for an account session.

        The token is verified against Google's signing certs; only a verified
        email may sign in, and it must match an existing active account
        (D3/D11). Local accounts work without a provider.
        """
        data = request.get_json(silent=True) or {}
        claims = oidc.verify_firebase_id_token(config, data.get("id_token", ""))
        if claims is None:
            return jsonify({"error": "invalid token"}), 401
        uid = claims.get("sub") or claims.get("user_id")
        phone = (claims.get("phone_number") or "").strip() or None
        # Seamless admin sign-in: a Firebase uid bound to an admin (via the
        # Admins page) elevates straight to an admin session — provided the
        # operator has enabled it. Local /admin-login always remains.
        if bool(getattr(config, "ADMIN_FIREBASE_LOGIN", False)) and uid:
            admin = get_admin_by_firebase_uid(g.db, uid)
            if admin is not None and admin["disabled_at"] is None:
                token = create_session(g.db, config.session_secret, config)
                resp = jsonify({"ok": True, "redirect": _post_login_target()})
                resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                                secure=config.PUSH_PUBLIC_URL.startswith("https"))
                return resp
        email = (claims.get("email") or "").strip().lower()
        if not email or not claims.get("email_verified", False):
            return jsonify({"error": "verified email required"}), 403
        # Match by Firebase uid first, so an email change made via the profile
        # (written through to Firebase) still resolves to the same account.
        account = accounts.get_account_by_firebase_uid(g.db, uid) if uid else None
        if account is None:
            account = accounts.get_account_by_email(g.db, email)
        if account is None:
            # An admin's login identity is never also a customer account, so a
            # verified email/phone that belongs to an admin cannot sign up here.
            if _identity_owned_by_admin(email, phone):
                return jsonify({"error": "email belongs to an administrator"}), 403
            # A first verified login creates the account (signup via the same
            # flow), subject to the signup toggle and email-domain rules (§9).
            if not settings.signup_enabled(g.db):
                return jsonify({"error": "signup disabled"}), 403
            if not accounts.domain_allowed(g.db, email):
                return jsonify({"error": "email domain not allowed"}), 403
            account_id =             account_id = accounts.create_account(g.db, email)
            if uid:
                accounts.set_firebase_uid(g.db, account_id, uid)
            _assign_signup_group(account_id, (data.get("affiliate_code") or "").strip())
            account = accounts.get_account(g.db, account_id)
        elif uid and account["firebase_uid"] != uid:
            accounts.set_firebase_uid(g.db, account["account_id"], uid)
        if account["status"] != accounts.ACTIVE:
            return jsonify({"error": f"account {account['status']}"}), 403
        accounts.touch_login(g.db, account["account_id"])
        token = create_session(g.db, config.session_secret, config,
                               principal_type="account",
                               principal_id=account["account_id"])
        resp = jsonify({"ok": True, "account_id": account["account_id"]})
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                        secure=config.PUSH_PUBLIC_URL.startswith("https"))
        return resp

    # ---- Federation API (design §5/§5a) ----

    def _federation_token():
        header = request.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else None

    def _resolved_plan(conn, row):
        """The server's billing plan: its assignment, else the default (§7).

        Returns a JSON-safe dict (plan_id renamed to id) or None when the
        server has no plan and no default is configured.
        """
        plans = {p["plan_id"]: p for p in billing.list_plans(conn)
                 if p["scope"] == billing.SCOPE_SLAVE}
        plan_id = row["plan_id"] or settings.default_server_plan_id(conn)
        plan = plans.get(plan_id)
        if plan is None:
            return None
        return {"id": plan["plan_id"], "name": plan["name"],
                "price_cents": plan["price_cents"],
                "included_messages": plan["included_messages"],
                "message_cents_per_1000": plan["message_cents_per_1000"],
                "max_messages_per_month": plan["max_messages_per_month"],
                "active": bool(plan["active"]),
                "is_default": not row["plan_id"]}

    @app.route("/api/federation/enrol", methods=["POST"])
    def federation_enrol():
        """A slave enrols: prove consent, verify it via callback, then activate.

        Consent (design §5) - this master never accepts a slave by itself.
        An unknown/changed server_id must carry a single-use code minted when
        an operator approved it on /federation/connect; a server_id already
        consented to with the same URL and key (not revoked) may re-enrol
        code-less, so heartbeats can recover from a wipe. Gated by the
        master's federation settings (design §8) as well: the feature must be
        enabled by env, and the admin must be accepting new slaves.
        """
        if not bool(getattr(config, "FEDERATION_ENABLED", True)):
            return jsonify({"error": "federation disabled"}), 403
        if not settings.accept_new_slaves(g.db):
            return jsonify({"error": "not accepting new slaves"}), 403
        data = request.get_json(silent=True) or {}
        server_id = data.get("server_id")
        hostname = data.get("hostname")
        base_url = data.get("base_url")
        public_key_b64 = data.get("public_key")
        if not all([server_id, hostname, base_url, public_key_b64]):
            return jsonify({"error": "server_id, hostname, base_url and public_key"
                                     " are required"}), 400
        code = data.get("code") or ""
        consented = federation.consent_not_required(
            g.db, server_id=server_id, base_url=base_url,
            public_key_b64=public_key_b64)
        connect_row = None
        if not consented:
            connect_row = federation.find_connect_by_code(g.db, code)
            if (connect_row is None or connect_row["server_id"] != server_id
                    or connect_row["base_url"] != base_url
                    or connect_row["public_key"] != public_key_b64):
                return jsonify({"error": "consent required",
                                "detail": "no operator approval covers this "
                                          "slave - open the master's connect "
                                          "link on the slave and approve it "
                                          "first"}), 403
        challenge = federation.new_challenge()
        try:
            signature = federation.verify_slave_callback(base_url, challenge)
        except Exception as exc:  # noqa: BLE001 - unreachable slave => not verified
            return jsonify({"error": "callback failed", "detail": str(exc)[:120]}), 400
        if not federation.verify_callback_signature(public_key_b64, challenge, signature):
            return jsonify({"error": "callback signature invalid"}), 400
        if connect_row is not None and not federation.mark_connect_consumed(
                g.db, code):
            return jsonify({"error": "consent code already used"}), 403
        # The plan a server registers with: one already assigned survives a
        # re-enrol (INSERT OR REPLACE would otherwise drop it); a new or
        # plan-less server takes the master's default (design §7).
        existing = federation.get_server(g.db, server_id)
        plan_id = (existing["plan_id"] if existing is not None
                   and existing["plan_id"] else None)
        if not plan_id:
            plan_id = settings.default_server_plan_id(g.db)
        secret = federation.register_active(
            g.db, server_id=server_id, hostname=hostname, base_url=base_url,
            public_key_b64=public_key_b64, plan_id=plan_id)
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
        """Signed identity check (design §5a/§7): the slave learns who it is on
        this master and the billing plan it is running under."""
        row = federation.check_bearer(g.db, _federation_token())
        if row is None:
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_data() or b""
        if not federation.verify_request(g.db, row, request.method, request.path,
                                         body, request.headers):
            return jsonify({"error": "bad signature"}), 401
        plan = _resolved_plan(g.db, row)
        entitled = billing.slave_entitled(g.db, row["server_id"])
        return jsonify(
            {"server_id": row["server_id"], "hostname": row["hostname"],
             "status": row["status"], "plan": plan,
             "billing": {
                 "required": bool(plan and plan["price_cents"]),
                 "entitled": entitled,
                 "status": "in credit" if entitled else "out of credit"}})

    @app.route("/api/federation/disconnect", methods=["POST"])
    def federation_disconnect():
        """A slave terminates its own connection (consent terms §5): the row and
        its queued messages are removed, so a later re-enrol needs a fresh
        operator consent code."""
        row = federation.check_bearer(g.db, _federation_token())
        if row is None:
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_data() or b""
        if not federation.verify_request(g.db, row, request.method, request.path,
                                         body, request.headers):
            return jsonify({"error": "bad signature"}), 401
        federation.delete_server(g.db, row["server_id"])
        return jsonify({"ok": True})

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
        # Paid plan without credit -> no relay (design F7). Sync/heartbeat
        # keep working so nothing is lost while out of credit; only the
        # metered action (ringing the doorbell) is gated.
        if bool(getattr(config, "BILLING_ENFORCEMENT", False)) and not \
                billing.slave_entitled(g.db, row["server_id"]):
            return jsonify({"error": "payment required",
                            "detail": "this server's plan requires prepaid"
                                      " credit"}), 402
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
        if _encryption_required() and encryption.get_sealed_cek(
                g.db, device_id) is None:
            return jsonify({"error": "encryption required"}), 409
        fcm = app.config["_fcm"]
        if fcm is None or not device["fcm_token"]:
            return jsonify({"error": "fcm unavailable"}), 503
        try:
            fcm.send({"p": sealed["c"], "i": sealed["i"]}, device["fcm_token"],
                     high_priority=True)
        except Exception:  # noqa: BLE001 - report, the slave's retry worker will retry
            return jsonify({"error": "fcm send failed"}), 502
        accounts.increment_messages(g.db, account_id)  # metering (D5)
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
        denied = client_denied()
        if denied is not None:
            return denied
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
        client_id = create_client(g.db, entry["name"], hash_secret(secret),
                                  account_id=entry.get("account_id"))
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

    @app.route("/api/push/content-key", methods=["GET"])
    def api_push_content_key():
        """The target device's content public key, for sealing the CEK (§7a).

        A push client fetches this before an encrypted push, verifies the
        pairing-key proof, pins the key (TOFU) and seals the content key to
        it. Only public material travels here — the server relays keys it
        can never open.
        """
        denied = client_denied()
        if denied is not None:
            return denied
        device = get_device_by_name(g.db, request.args.get("device", ""))
        if device is None:
            return jsonify({"error": "device not found"}), 404
        if not device["content_pubkey"]:
            return jsonify({"error": "no content key", "detail":
                            "This device has no content public key: pair it"
                            " with a current app version."}), 404
        # Same shape as the account-scoped route: the key, its pairing-key
        # proof, and the pairing public key to verify that proof against
        # (design §7c).
        return jsonify({"content_pubkey": device["content_pubkey"],
                        "content_proof": device["content_proof"],
                        "device_public_key": device["public_key"]})

    @app.route("/api/push", methods=["POST"])
    def api_push():
        denied = client_denied()
        if denied is not None:
            return denied
        target_device = request.form.get("target_device")
        if not target_device:
            return jsonify({"error": "device not found"}), 400
        device = get_device_by_name(g.db, target_device)
        if device is None:
            return jsonify({"error": "device not found"}), 400
        if "blocked_at" in device.keys() and device["blocked_at"] is not None:
            return jsonify({"error": "device blocked", "detail":
                            "This device has been blocked by an administrator."}), 403
        # §7b: an encryption-on server accepts no plaintext. Uploads must carry
        # encryption metadata (like /api/account/upload), and the destination
        # folder is metadata the server must not see or store (D13) — it
        # belongs in the encrypted payload until the app/client carry it there.
        encrypted = _encryption_required()
        if encrypted and not (request.form.get("alg") and request.form.get("nonce")):
            return jsonify({"error": "encryption required"}), 422
        target_folder = (request.form.get("target_folder") or "").strip()
        if encrypted and target_folder:
            return jsonify({"error": "target folder not permitted", "detail":
                            "When encryption is on the destination folder must"
                            " travel inside the encrypted payload, not as"
                            " plaintext metadata."}), 400
        # §7a: "any client" may seal the content key in the same request, and
        # an encryption-on server refuses a push the app could never open —
        # without a sealed CEK the doorbell would never ring and the files
        # would sit pending forever (§7b). Fail fast instead (428).
        if encrypted:
            sealed = request.form.get("sealed_cek")
            if sealed:
                encryption.set_sealed_cek(g.db, device["device_secret"], sealed)
            elif encryption.get_sealed_cek(
                    g.db, device["device_secret"]) is None:
                return jsonify({"error": "seal required", "detail":
                                "This device has no sealed content key: fetch"
                                " GET /api/push/content-key?device=<name>,"
                                " verify and pin the key, seal the content key"
                                " to it, and resend with sealed_cek"
                                " (design §7a)."}), 428
        uploads = request.files.getlist("file")
        if not uploads:
            return jsonify({"error": "no files uploaded"}), 400
        account_id = device["account_id"] if "account_id" in device.keys() else None
        # Same gate as uploads: plan requires credit (or meters messages)
        # and the account is not exempt, behind the enforcement switch.
        if account_id and bool(getattr(config, "BILLING_ENFORCEMENT", False)) and \
                billing.message_credit_gate(g.db, account_id):
            credit = billing.balance(g.db, billing.SCOPE_ACCOUNT, account_id)
            if credit <= 0:
                return jsonify({"error": "payment required",
                                "detail": "This account has no credit: top up"
                                          " on the Billing page."}), 402
            # Every attached file must fit inside the current credit.
            for f in uploads:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(0)
                cost = billing.file_cost_cents(g.db, account_id, size)
                if cost > credit:
                    return jsonify({
                        "error": "payment required",
                        "detail": f"'{os.path.basename(f.filename or 'file')}'"
                                  f" costs {cost} cents of credit but this"
                                  f" account only has {credit} cents: top up"
                                  " before sending."}), 402
        push_id = uuid.uuid4().hex
        challenge_key = uuid.uuid4().hex
        # Where the device should file these, and what to do if a name is
        # already taken. Both are per-push options, mirroring the LocalSend
        # "mds" options, and are carried in the signed manifest so the device
        # cannot be redirected between fetching it and importing.
        conflict = push_store.normalise_conflict(request.form.get("conflict"))
        # The plan caps how much pending storage an account may hold; reject a
        # push that would exceed it (checked here, on the way in).
        policy = billing.pending_policy(g.db, config, account_id) if account_id else None
        used = push_store.pending_bytes(g.db, account_id) if policy and policy["max_bytes"] else 0
        push_store.create_push(g.db, push_id, target_device, challenge_key,
                               target_folder, conflict, account_id=account_id)
        sent = 0
        total = 0
        for f in uploads:
            file_id = uuid.uuid4().hex
            retrieval_key = uuid.uuid4().hex
            # With encryption on, the multipart filename is plaintext metadata
            # the server must not keep: the opaque file_id stands in for it,
            # in the database and on disk alike (§7a step 2, D13).
            name = file_id if encrypted else (os.path.basename(f.filename) or "file")
            stored_dir = os.path.join(config.PUSH_STORAGE_DIR, push_id, file_id)
            os.makedirs(stored_dir, exist_ok=True)
            stored_path = os.path.join(stored_dir, name)
            f.save(stored_path)
            size = os.path.getsize(stored_path)
            total += size
            if policy and policy["max_bytes"] and used + total > policy["max_bytes"]:
                push_store.delete_push(g.db, push_id)
                shutil.rmtree(os.path.join(config.PUSH_STORAGE_DIR, push_id),
                              ignore_errors=True)
                return jsonify({
                    "error": "pending storage limit exceeded",
                    "detail": f"This account's plan allows at most "
                              f"{policy['max_bytes']} bytes of pending files."}), 507
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
                device_model=data.get("device_model"),
            )
            if device_auth is None:
                return jsonify({"error": "registration failed"}), 400
            # The app's content decryption public key (design §7a), when the app
            # uses server-enforced encryption. The server stores only the public
            # key; the client seals the CEK to it later.
            if data.get("content_pubkey"):
                set_device_content_pubkey(g.db, data["device_secret"],
                                          data["content_pubkey"],
                                          data.get("content_proof"))
            # An account-bound device is approved at registration, so publish its
            # no-PII routing tuple now rather than waiting for a manual approval.
            device = get_device_by_secret(g.db, data.get("device_secret"))
            if device is not None and device["account_id"] and device["approved_at"]:
                _publish_device(dict(device))
            # Tell the pairing page (SSE) that its QR was scanned and paired.
            events.publish(data.get("pairing_token", ""), "paired", {
                "device_name": data.get("device_name") or "",
                "device_model": data.get("device_model") or ""})
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
        if data.get("fcm_token") or data.get("device_name") or data.get("push_key"):
            # A blocked device may not rotate its token, name or push key.
            if not check_device(g.db, device_secret, device_auth):
                return jsonify({"error": "unauthorized"}), 401
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
        target = get_device_by_name(g.db, push_row["target_device"])
        if target is not None and "blocked_at" in target.keys() \
                and target["blocked_at"] is not None:
            return jsonify({"error": "forbidden", "detail":
                            "This device has been blocked by an administrator."}), 403
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

    @app.route("/api/device/content-key", methods=["POST"])
    def device_content_key():
        """Return the client-sealed content key for this device (design §7a).

        The app fetches it once and unwraps it with its Keystore private key; the
        server never sees the CEK.
        """
        data = request.get_json(silent=True) or {}
        secret = data.get("device_secret")
        auth = data.get("device_auth")
        if not secret or not auth or not check_device(g.db, secret, auth):
            return jsonify({"error": "re-register"}), 404
        row = encryption.get_sealed_cek(g.db, secret)
        if row is None or not row["sealed_cek"]:
            return jsonify({"error": "no content key"}), 404
        return jsonify({"sealed_cek": row["sealed_cek"], "alg": row["alg"]})

    @app.route("/api/push/<push_id>/status", methods=["GET"])
    def push_status(push_id):
        denied = client_denied()
        if denied is not None:
            return denied
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
