# server/app/geoip.py
"""Local IP→ASN and IP→Country lookups (DB-IP Lite MMDB, design D9).

Reads a bundled MMDB with the pure-Python `maxminddb` library when configured.
Never raises: a missing library, missing DB, or unknown IP yields None. Country
is preferred from Cloudflare's `CF-IPCountry` header when the request is proxied
(the master sits behind Cloudflare).
"""


def _open(path):
    if not path:
        return None
    try:
        import maxminddb
    except ImportError:
        return None
    try:
        return maxminddb.open_database(path)
    except Exception:  # noqa: BLE001
        return None


def asn_for_ip(config, ip):
    reader = _open(getattr(config, "GEOIP_ASN_DB", ""))
    if reader is None or not ip:
        return None
    try:
        record = reader.get(ip)
    except Exception:  # noqa: BLE001
        return None
    if not record:
        return None
    return record.get("autonomous_system_number")


def country_for_ip(config, ip):
    reader = _open(getattr(config, "GEOIP_COUNTRY_DB", ""))
    if reader is None or not ip:
        return None
    try:
        record = reader.get(ip)
    except Exception:  # noqa: BLE001
        return None
    if not record:
        return None
    return (record.get("country") or {}).get("iso_code") or record.get("country_code")


def country_from_headers(headers):
    """Cloudflare's edge country header, if present (free, no lookup)."""
    code = headers.get("CF-IPCountry")
    return code.upper() if code and code != "XX" else None
