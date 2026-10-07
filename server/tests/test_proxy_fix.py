# server/tests/test_proxy_fix.py
"""TRUST_PROXY decides whether proxy headers reach request.remote_addr.

Behind the reverse proxy the app must see the client's address (bans, GeoIP,
the failed-login lockout) instead of the proxy's 127.0.0.1 — but only when the
operator has opted in, because a directly-exposed app must never trust a
client-supplied X-Forwarded-For.
"""
import os

from flask import request

from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True

    @app.route("/__test_request_origin")
    def _request_origin():
        return f"{request.remote_addr}|{request.scheme}"

    return app


def test_trust_proxy_uses_the_last_forwarded_hop(config):
    config.TRUST_PROXY = True
    client = _app(config).test_client()

    resp = client.get("/__test_request_origin",
                      headers={"X-Forwarded-For": "198.51.100.7, 162.158.1.1",
                               "X-Forwarded-Proto": "https"})
    # The last entry is the one the proxy appended and vouches for.
    assert resp.data == b"162.158.1.1|https"


def test_without_trust_proxy_headers_are_ignored(config):
    client = _app(config).test_client()

    resp = client.get("/__test_request_origin",
                      headers={"X-Forwarded-For": "198.51.100.7",
                               "X-Forwarded-Proto": "https"})
    assert resp.data == b"127.0.0.1|http"
