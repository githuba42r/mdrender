def test_client_credentials_flow(config, db_path):
    from server.app.auth import hash_secret, issue_access_token, validate_access_token
    from server.app.db import Database
    from server.app.store import create_client, get_or_create_server_keypair

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        pem, _ = get_or_create_server_keypair(conn)
        assert pem
        client_id = create_client(conn, "my-tool", hash_secret("super-secret"))
        token = issue_access_token(config, client_id)
        assert validate_access_token(config, token) == client_id
        assert validate_access_token(config, "bogus") is None
