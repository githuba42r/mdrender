# server/app/push_store.py
import time


def create_push(conn, push_id: str, target_device: str, challenge_key: str = "") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pushes (push_id, target_device, challenge_key, date, status)"
        " VALUES (?, ?, ?, ?, 'pending')",
        (push_id, target_device, challenge_key, int(time.time())),
    )
    conn.commit()


def add_file(conn, *, file_id, push_id, file_name, file_path, size,
             retrieval_key, stored_path, created_at):
    conn.execute(
        "INSERT INTO push_files (file_id, push_id, file_name, file_path, size,"
        " retrieval_key, stored_path, status, retries, next_retry_at, acked_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, ?)",
        (file_id, push_id, file_name, file_path, size, retrieval_key, stored_path,
         created_at + 60, created_at),
    )
    conn.commit()


def get_push_files(conn, push_id):
    return conn.execute(
        "SELECT * FROM push_files WHERE push_id = ? ORDER BY created_at", (push_id,)
    ).fetchall()


def get_unacked_files(conn, push_id):
    """The rows a manifest for this push should contain right now.

    Deliberately computed from live state rather than from whatever was queued at
    push time: a re-rung doorbell re-reads this, so a file the phone already
    acknowledged can never be offered to it a second time. `exhausted` is
    excluded because those rows have had their bytes purged — advertising one
    would send the phone to a download that 404s.
    """
    return conn.execute(
        "SELECT * FROM push_files WHERE push_id = ? AND status = 'pending'"
        " ORDER BY created_at", (push_id,),
    ).fetchall()


def get_pending_files(conn, now):
    return conn.execute(
        "SELECT * FROM push_files WHERE status = 'pending'"
        " AND (next_retry_at IS NULL OR next_retry_at <= ?)", (now,)
    ).fetchall()


def mark_acked(conn, file_id, now) -> bool:
    cur = conn.execute(
        "UPDATE push_files SET status = 'acked', acked_at = ?, stored_path = NULL"
        " WHERE file_id = ?", (now, file_id),
    )
    conn.commit()
    return cur.rowcount > 0


def purge_expired_bytes(conn, ttl_hours, now) -> list[str]:
    cutoff = now - ttl_hours * 3600
    rows = conn.execute(
        "SELECT file_id, stored_path FROM push_files WHERE status != 'acked'"
        " AND created_at < ? AND stored_path IS NOT NULL", (cutoff,)
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE push_files SET stored_path = NULL WHERE file_id = ?",
                     (r["file_id"],))
    conn.commit()
    return [r["file_id"] for r in rows]


def list_pushes(conn):
    return conn.execute(
        "SELECT p.push_id, p.target_device, p.date, p.status,"
        " COUNT(f.file_id) AS file_count,"
        " COALESCE(SUM(CASE WHEN f.status = 'acked' THEN 1 ELSE 0 END), 0) AS acked_count"
        " FROM pushes p LEFT JOIN push_files f ON f.push_id = p.push_id"
        " GROUP BY p.push_id ORDER BY p.date DESC"
    ).fetchall()


def list_pending(conn):
    return conn.execute(
        "SELECT f.*, p.target_device FROM push_files f JOIN pushes p ON p.push_id = f.push_id"
        " WHERE f.status = 'pending' ORDER BY f.created_at DESC"
    ).fetchall()


def reset_push_retries(conn, push_id, now) -> None:
    conn.execute(
        "UPDATE push_files SET retries = 0, next_retry_at = ? WHERE push_id = ? AND status = 'pending'",
        (now, push_id),
    )
    conn.commit()


def mark_exhausted(conn, file_id) -> None:
    conn.execute("UPDATE push_files SET status = 'exhausted', stored_path = NULL WHERE file_id = ?",
                 (file_id,))
    conn.commit()


def increment_retry(conn, file_id, now, interval_minutes) -> None:
    conn.execute(
        "UPDATE push_files SET retries = retries + 1, next_retry_at = ? WHERE file_id = ?",
        (now + interval_minutes * 60, file_id),
    )
    conn.commit()


def get_push_by_id(conn, push_id):
    return conn.execute("SELECT * FROM pushes WHERE push_id = ?", (push_id,)).fetchone()
