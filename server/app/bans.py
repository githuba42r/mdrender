# server/app/bans.py
"""Network/identity bans (design §8/§14): ip, cidr, asn, hostname, domain.

Enforced at the edge of every endpoint (signup, federation, account, admin).
CIDR matching uses the stdlib `ipaddress`; ASN/country lookups are separate
(DB-IP Lite, D9).
"""
import ipaddress
import time

KINDS = ("ip", "cidr", "asn", "hostname", "domain")


def add_ban(conn, kind, value, *, scope="global", reason=None, expires_at=None) -> int:
    if kind not in KINDS:
        raise ValueError(f"unknown ban kind: {kind}")
    value = str(value).strip()
    if kind in ("hostname", "domain"):
        value = value.lower()
    cur = conn.execute(
        "INSERT INTO bans (kind, value, scope, reason, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (kind, value, scope, reason, int(time.time()), expires_at))
    conn.commit()
    return cur.lastrowid


def remove_ban(conn, ban_id) -> None:
    conn.execute("DELETE FROM bans WHERE id = ?", (ban_id,))
    conn.commit()


def list_bans(conn, *, active_only=False, now=None):
    now = int(now or time.time())
    rows = conn.execute("SELECT * FROM bans ORDER BY created_at").fetchall()
    if active_only:
        rows = [r for r in rows if not r["expires_at"] or r["expires_at"] >= now]
    return rows


def _matches(ban, ip, asn, hostname, domain) -> bool:
    kind, value = ban["kind"], ban["value"]
    if kind == "ip":
        return ip is not None and ip == value
    if kind == "cidr":
        if ip is None:
            return False
        try:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(value, strict=False)
        except ValueError:
            return False
    if kind == "asn":
        return asn is not None and str(asn).lower().lstrip("as") == value.lower().lstrip("as")
    if kind == "hostname":
        return hostname is not None and hostname.lower() == value
    if kind == "domain":
        candidates = [c.lower() for c in (domain, hostname) if c]
        # A domain ban matches the domain itself or any subdomain of it.
        return any(c == value or c.endswith("." + value) for c in candidates)
    return False


def is_banned(conn, *, ip=None, asn=None, hostname=None, domain=None, now=None) -> bool:
    now = int(now or time.time())
    for ban in conn.execute("SELECT * FROM bans").fetchall():
        if ban["expires_at"] and ban["expires_at"] < now:
            continue
        if _matches(ban, ip, asn, hostname, domain):
            return True
    return False
