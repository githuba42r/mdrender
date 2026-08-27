def test_login_gate_locks_out(config):
    from server.app.auth import LoginGate

    gate = LoginGate(config)
    # config.LOGIN_MAX_ATTEMPTS == 5
    for _ in range(5):
        allowed, _ = gate.check("1.2.3.4", "wrong")
        assert allowed is False
    allowed, retry_after = gate.check("1.2.3.4", "wrong")
    assert allowed is False
    assert retry_after > 0
    # A different IP is not locked out, and the right password resets:
    allowed, _ = gate.check("5.6.7.8", config.SERVER_PASSWORD)
    assert allowed is True


def test_session_token_roundtrip(config):
    from server.app.auth import make_session, verify_session

    config.session_secret = "test-secret"
    token = make_session(config.session_secret, config)
    assert verify_session(config.session_secret, token, config) is True
    assert verify_session(config.session_secret, "forged", config) is False
