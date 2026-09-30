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
    serve(app, host=host, port=int(port), threads=24)


if __name__ == "__main__":
    main()
