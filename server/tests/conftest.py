# server/tests/conftest.py
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def db_path():
    with tempfile.TemporaryDirectory() as d:
        yield os.path.join(d, "test.db")


@pytest.fixture()
def config(db_path):
    from server.app.config import load_config

    return load_config(overrides={"DB_PATH": db_path, "SERVER_PASSWORD": "testpass"})
