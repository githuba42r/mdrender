# server/app/app.py
"""Flask application factory wiring every route for the Cloud Push server."""
import base64
import datetime
import os
import time
import uuid

import qrcode
from flask import (Flask, Response, g, jsonify, make_response, redirect,
                   render_template, request, send_file)
from qrcode.image.svg import SvgImage

from cryptography.hazmat.primitives import serialization

from server.app import crypto, fcm as fcm_mod, pairing, push_store, trigger
from server.app.config import load_config
from server.app.auth import (LoginGate, hash_secret, issue_access_token,
                             make_session, validate_access_token,
                             verify_secret, verify_session)
from server.app.db import Database
from server.app.store import (check_device, create_client, delete_device,
                              get_client, get_device_by_name,
                              get_or_create_server_keypair, list_devices,
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

    # The server keypair is stable per database; the session secret derives from it.
    config.session_secret = session_secret_from_pem(server_pem)

    app.config["_db"] = db
    app.config["_server_pem"] = server_pem
    app.config["_server_pk_b64"] = server_pk_b64
    app.config["_enrol_keys"] = {}          # enrolment_id -> {"key", "expires"}
    app.config["_login_gate"] = LoginGate(config)
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

    def require_session():
        token = request.cookies.get(SESSION_COOKIE)
        if not token or not verify_session(config.session_secret, token, config):
            return jsonify({"error": "unauthorized"}), 401
        return None

    def bearer_client_id():
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return validate_access_token(config, header[7:])

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

    @app.route("/login", methods=["GET"])
    def login_page():
        return render_template("login.html")

    @app.route("/login", methods=["POST"])
    def login():
        password = request.form.get("password", "")
        allowed, retry = app.config["_login_gate"].check(request.remote_addr, password)
        if not allowed:
            return jsonify({"error": "locked", "retry_after": retry}), 401
        resp = make_response(redirect("/pair"))
        resp.set_cookie(SESSION_COOKIE, make_session(config.session_secret, config))
        return resp

    @app.route("/pair", methods=["GET"])
    def pair():
        auth_error = require_session()
        if auth_error:
            return auth_error
        token = pairing.create_pairing_token(g.db, config.ENROL_SESSION_TTL_MINUTES)
        expires_at = (datetime.datetime.now(datetime.timezone.utc)
                      + datetime.timedelta(minutes=config.ENROL_SESSION_TTL_MINUTES))
        expires_iso = expires_at.isoformat()
        app.config["_test_pairing_token"] = token
        app.config["_pairing_tokens"] = {"token": token, "expires": expires_iso}
        qr_text = pairing.build_pairing_qr(config.PUSH_PUBLIC_URL,
                                           app.config["_server_pk_b64"],
                                           token, expires_iso)
        # Inline SVG QR (qrcode SVG factory; no Pillow required).
        img = qrcode.make(qr_text, image_factory=SvgImage)
        qr_svg = img.to_string().decode()
        return render_template(
            "pair.html",
            qr_svg=qr_svg,
            server_url=config.PUSH_PUBLIC_URL,
            expires=expires_at.strftime("%Y-%m-%d %H:%M UTC"),
        )

    @app.route("/enrol/<eid>", methods=["GET"])
    def enrol_page(eid):
        auth_error = require_session()
        if auth_error:
            return auth_error
        entry = app.config["_enrol_keys"].get(eid)
        key = entry["key"] if entry else None
        return render_template("enrol.html", key=key)

    @app.route("/devices", methods=["GET"])
    def devices():
        auth_error = require_session()
        if auth_error:
            return auth_error
        return render_template("devices.html",
                               devices=[dict(r) for r in list_devices(g.db)])

    @app.route("/pushes", methods=["GET"])
    def pushes():
        auth_error = require_session()
        if auth_error:
            return auth_error
        return render_template("pushes.html",
                               pushes=[dict(r) for r in push_store.list_pushes(g.db)])

    @app.route("/pending", methods=["GET"])
    def pending():
        auth_error = require_session()
        if auth_error:
            return auth_error
        return render_template("pending.html",
                               pending=[dict(r) for r in push_store.list_pending(g.db)])

    @app.route("/devices/<device_secret>", methods=["DELETE"])
    def devices_delete(device_secret):
        auth_error = require_session()
        if auth_error:
            return auth_error
        delete_device(g.db, device_secret)
        return redirect("/devices", 303)

    @app.route("/devices/<device_secret>/delete", methods=["POST"])
    def devices_delete_post(device_secret):
        auth_error = require_session()
        if auth_error:
            return auth_error
        delete_device(g.db, device_secret)
        return redirect("/devices", 303)

    # ---- Unauthenticated / Bearer / enrolment API ----

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"ok": True})

    @app.route("/api/enrol/start", methods=["POST"])
    def enrol_start():
        eid = uuid.uuid4().hex
        key = uuid.uuid4().hex
        app.config["_enrol_keys"][eid] = {
            "key": key, "expires": time.time() + config.ENROL_TOKEN_TTL_HOURS * 3600,
        }
        return jsonify({"enrolment_id": eid,
                        "verification_uri": f"{config.PUSH_PUBLIC_URL}/enrol/{eid}"})

    @app.route("/api/enrol", methods=["POST"])
    def enrol():
        data = request.get_json(silent=True) or {}
        entry = app.config["_enrol_keys"].get(data.get("enrolment_id", ""))
        if entry is None or entry["key"] != data.get("key"):
            return jsonify({"error": "invalid enrolment key"}), 401
        if time.time() > entry["expires"]:
            app.config["_enrol_keys"].pop(data["enrolment_id"], None)
            return jsonify({"error": "enrolment expired"}), 401
        app.config["_enrol_keys"].pop(data["enrolment_id"], None)
        secret = uuid.uuid4().hex
        secret_hash = hash_secret(secret)
        client_id = create_client(g.db, data.get("name", "cli"), secret_hash)
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
        push_store.create_push(g.db, push_id, target_device, challenge_key)
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
            return jsonify({"ok": True, "device_auth": device_auth})
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
        auth_error = require_session()
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

    return app


if __name__ == "__main__":
    cfg = load_config()
    host, _, port = cfg.LISTEN_ADDR.partition(":")
    create_app(cfg).run(host=host or "0.0.0.0", port=int(port or "8080"))
