def test_login_gate_locks_out(config):
    from server.app.auth import LoginGate

    gate = LoginGate(config)
    # config.LOGIN_MAX_ATTEMPTS == 5
    for _ in range(4):
        assert gate.record_failure("admin@1.2.3.4") == 0
    assert gate.record_failure("admin@1.2.3.4") > 0  # 5th trips the lock
    assert gate.is_locked("admin@1.2.3.4") > 0
    # A different identity is not locked out, and success resets the counter.
    assert gate.is_locked("other@1.2.3.4") == 0
    gate.record_success("admin@1.2.3.4")
    assert gate.is_locked("admin@1.2.3.4") == 0


def test_session_token_roundtrip(config):
    from server.app.auth import make_session, verify_session

    config.session_secret = "test-secret"
    token = make_session(config.session_secret, config)
    assert verify_session(config.session_secret, token, config) is True
    assert verify_session(config.session_secret, "forged", config) is False


def test_signed_token_without_a_session_row_is_rejected(config, db_path):
    """A correctly-signed cookie is not enough; the server must hold a row.

    Guards the logout path: the HMAC stays verifiable after logout, so without
    the database check a logged-out cookie would keep working.
    """
    from server.app.auth import (create_session, make_session, session_is_valid,
                                 verify_session)

    config.session_secret = "test-secret"
    app_conn = None
    from server.app.db import Database
    conn = Database(db_path).connect()
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS sessions ("
        " token_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL,"
        " expires_at INTEGER NOT NULL);"
    )

    # Signed but never recorded -> rejected.
    orphan = make_session(config.session_secret, config)
    assert verify_session(config.session_secret, orphan, config) is True
    assert session_is_valid(conn, config.session_secret, orphan, config) is False

    # Recorded -> accepted.
    real = create_session(conn, config.session_secret, config)
    assert session_is_valid(conn, config.session_secret, real, config) is True

    # A guessed/forged token is rejected without a row lookup mattering.
    assert session_is_valid(conn, config.session_secret, "forged", config) is False
    assert session_is_valid(conn, config.session_secret, None, config) is False
    conn.close()


def test_expired_session_row_is_rejected_and_pruned(config, db_path):
    from server.app.auth import (create_session, purge_expired_sessions,
                                 session_is_valid, session_token_hash)
    from server.app.db import Database

    config.session_secret = "test-secret"
    conn = Database(db_path).connect()
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS sessions ("
        " token_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL,"
        " expires_at INTEGER NOT NULL);"
    )
    token = create_session(conn, config.session_secret, config)
    digest = session_token_hash(config.session_secret, token)

    # Backdate the deadline past the expiry window.
    conn.execute("UPDATE sessions SET expires_at = 1 WHERE token_hash = ?", (digest,))
    conn.commit()

    assert session_is_valid(conn, config.session_secret, token, config) is False
    # Reading an expired session removes it rather than leaving it to accumulate.
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()


def test_session_token_hash_is_keyed_so_a_leaked_db_cannot_be_replayed(config, db_path):
    from server.app.auth import session_token_hash

    token = "a:1:deadbeef"
    # The stored column is an HMAC under the server secret, not the token or a
    # plain digest, so copying the table does not hand over live cookies.
    assert session_token_hash("secret-a", token) != session_token_hash("secret-b", token)
    assert token not in session_token_hash("secret-a", token)
