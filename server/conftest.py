# server/conftest.py
"""Make the repository root importable as a package.

Tests import the ``server`` package as ``server.app.app`` / ``server.app.config``.
When pytest runs from inside ``server/`` (see pytest.ini), the repository root
(the parent of ``server/``) is not on ``sys.path``, so this root-level conftest
inserts it. This is the documented pytest pattern for non-installed packages.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
