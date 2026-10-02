# server/tests/conftest.py
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def db_path():
    with tempfile.TemporaryDirectory() as d:
        yield os.path.join(d, "test.db")


@pytest.fixture(autouse=True)
def _offline_releases(monkeypatch):
    """Keep tests off the network: the Clients pages look up GitHub releases."""
    from server.app import releases

    monkeypatch.setattr(releases, "_cache", {"at": 0.0, "data": None})

    def _offline(url):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(releases, "_fetch", _offline)


@pytest.fixture()
def config(db_path):
    from server.app.config import load_config

    return load_config(overrides={"DB_PATH": db_path, "SERVER_PASSWORD": "testpass"})


def consent_code(app, *, server_id, base_url, public_key,
                 hostname="slave.example", state="st"):
    """Mint a single-use operator consent code for a slave on this master.

    The handshake the master's /federation/connect approval performs, minus the
    browser (design §5): open the signed request, then approve it.
    """
    from server.app import federation

    with app.config["_db"].connect() as conn:
        connect_id = federation.open_connect(
            conn, server_id=server_id, hostname=hostname, base_url=base_url,
            public_key_b64=public_key, state=state)
        code = federation.approve_connect(conn, connect_id)
    assert code, "consent code could not be minted"
    return code
