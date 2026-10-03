# server/tests/test_legal_pages.py
"""Terms & privacy pages: public access, and footer links on every page."""
import os

from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def test_terms_and_privacy_render_without_a_session(config, db_path):
    app = _app(config)
    c = app.test_client()
    for path, marker in (("/terms", b"Terms &amp; conditions"),
                         ("/privacy", b"Privacy policy")):
        resp = c.get(path)
        assert resp.status_code == 200, path
        assert marker in resp.data, path
        # No session required: the response is a page, not a login bounce.
        assert "Location" not in resp.headers or path in resp.headers["Location"]
    # The legal pages cross-link to each other (and share the site footer).
    terms = c.get("/terms").data
    privacy = c.get("/privacy").data
    assert b'href="/privacy"' in terms
    assert b'href="/terms"' in privacy


def test_footer_links_appear_even_on_the_login_pages(config, db_path):
    app = _app(config)
    c = app.test_client()
    for path in ("/login", "/account/login", "/admin-login", "/terms"):
        page = c.get(path)
        assert page.status_code == 200, path
        assert b'href="/terms"' in page.data, path
        assert b'href="/privacy"' in page.data, path
