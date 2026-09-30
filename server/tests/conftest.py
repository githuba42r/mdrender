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
