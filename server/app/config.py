# server/app/config.py
import os


class Config:
    def __init__(self, **kw):
        self.__dict__.update(kw)


DEFAULTS = {
    "LOGIN_MAX_ATTEMPTS": 5,
    "LOGIN_LOCKOUT_SECONDS": 300,
    "ENROL_TOKEN_TTL_HOURS": 1,
    "ENROL_SESSION_TTL_MINUTES": 15,
    "ENROL_CODE_TTL_SECONDS": 60,
    "ENROL_CODE_MAX_ATTEMPTS": 5,
    "ACCESS_TOKEN_TTL_SECONDS": 3600,
    "PUSH_STORAGE_DIR": "/data/push",
    "DB_PATH": "/data/push/server.db",
    "PUSH_FILE_TTL_HOURS": 24,
    "PUSH_RETRY_COUNT": 5,
    "PUSH_RETRY_INTERVAL_MINUTES": 30,
    "DEVICE_TTL_DAYS": 90,
    "FCM_SERVER_KEY": "",
    "PUSH_PUBLIC_URL": "",
    "LISTEN_ADDR": ":8080",
    # Deployment role: "" = auto-detect from FCM availability, or force one of
    # master | slave | standalone. MASTER_URL is the federation master to enrol
    # with (baked-in default, overridable).
    "ROLE": "",
    "MASTER_URL": "",
    # Per-account pending-storage defaults (overridable per account).
    "ACCOUNT_MAX_BYTES": 104857600,   # 100 MB
    "ACCOUNT_MAX_FILES": 1000,
    "ACCOUNT_MAX_AGE_HOURS": 168,     # 7 days
    "STORAGE_BILL_AFTER_HOURS": 1,    # bill storage once pending longer than this
    "BAN_ENFORCEMENT": True,
    "BILLING_ENFORCEMENT": False,
    # Identity provider for account logins: "local" (default) or a hosted IdP
    # such as "firebase". Admins always use local username/password.
    "IDENTITY_PROVIDER": "local",
    "FIREBASE_PROJECT_ID": "",
    # Firebase **web** config, for the account portal's social + magic-link UI.
    "FIREBASE_API_KEY": "",
    "FIREBASE_AUTH_DOMAIN": "",
    "FIREBASE_APP_ID": "",
    # Service-account JSON for Firebase Auth Admin writes (account profile edits
    # write through to Firebase). Falls back to FCM_SERVER_KEY's path when empty.
    "FIREBASE_SERVICE_ACCOUNT": "",
    # Which Firebase sign-in methods the login/signup pages surface, comma
    # separated. Any of: google, github, phone, password, email_link. Only the
    # listed methods are shown, so a disabled provider never renders a button.
    "FIREBASE_PROVIDERS": "google,github,phone,password,email_link",
    # Server-enforced content encryption: on (clients must encrypt) | off.
    "ENCRYPTION_MODE": "off",
    # Allow a Firebase-linked admin to sign in on the generic front page by
    # their Firebase identity (bind-by-uid). Local /admin-login always works.
    "ADMIN_FIREBASE_LOGIN": False,
    # Whether this master exposes federation at all (env gate). The admin
    # Settings page can then toggle accepting new slaves while this is on.
    "FEDERATION_ENABLED": True,
    # DB-IP Lite MMDB paths for local ASN/country lookups (D9).
    "GEOIP_ASN_DB": "",
    "GEOIP_COUNTRY_DB": "",
}


def load_config(*, overrides: dict | None = None) -> Config:
    env = {k: os.environ.get(k, v) for k, v in DEFAULTS.items()}
    env["SERVER_PASSWORD"] = os.environ.get("SERVER_PASSWORD", "")
    for k in env:
        if k in ("LOGIN_MAX_ATTEMPTS", "LOGIN_LOCKOUT_SECONDS",
                 "ENROL_TOKEN_TTL_HOURS", "ENROL_SESSION_TTL_MINUTES",
                 "ENROL_CODE_TTL_SECONDS", "ENROL_CODE_MAX_ATTEMPTS",
                 "ACCESS_TOKEN_TTL_SECONDS", "PUSH_FILE_TTL_HOURS",
                 "PUSH_RETRY_COUNT", "PUSH_RETRY_INTERVAL_MINUTES",
                 "DEVICE_TTL_DAYS", "ACCOUNT_MAX_BYTES", "ACCOUNT_MAX_FILES",
                 "ACCOUNT_MAX_AGE_HOURS", "STORAGE_BILL_AFTER_HOURS"):
            env[k] = int(env[k])
    for flag in ("BAN_ENFORCEMENT", "BILLING_ENFORCEMENT", "FEDERATION_ENABLED",
                 "ADMIN_FIREBASE_LOGIN"):
        env[flag] = str(env.get(flag, DEFAULTS.get(flag, False))).lower() in (
            "1", "true", "yes", "on")
    if overrides:
        env.update(overrides)
    return Config(**env)
