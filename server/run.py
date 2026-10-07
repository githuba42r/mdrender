# server/run.py
import threading

from server.app.app import create_app
from server.app.config import load_config
from server.app.db import Database
from server.app.retry import run_forever


def main():
    config = load_config()
    app = create_app(config)
    db = Database(config.DB_PATH)
    if config.FCM_SERVER_KEY:
        from server.app import fcm
        fcm_client = fcm.FcmClient(fcm.load_service_account(config.FCM_SERVER_KEY))
    else:
        fcm_client = None
    threading.Thread(target=run_forever, args=(config, db, fcm_client), daemon=True).start()
    from server.app import federation_worker
    threading.Thread(target=federation_worker.run_forever, args=(config, db),
                     daemon=True).start()
    from server.app import billing_worker
    threading.Thread(target=billing_worker.run_forever, args=(config, db),
                     daemon=True).start()
    host, port = config.LISTEN_ADDR.rsplit(":", 1)
    host = host or "0.0.0.0"
    from waitress import serve
    # Headroom for long-lived Server-Sent Event streams (pairing page) alongside
    # ordinary requests; waitress keeps each stream on its own thread.
    # Waitress clears X-Forwarded-*/Forwarded as "untrusted" unless it is told
    # to trust the hop itself, so ProxyFix would never see the proxy chain.
    # With TRUST_PROXY on (loopback nginx that overwrites the headers), let
    # them through; direct deployments keep waitress's safe default.
    serve(app, host=host, port=int(port), threads=24,
          clear_untrusted_proxy_headers=not config.TRUST_PROXY)


if __name__ == "__main__":
    main()
