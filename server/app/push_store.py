# server/app/push_store.py
import time

# Mirrors the device's ConflictStrategy. Anything unrecognised falls back to
# "rename" rather than being rejected, matching how the app parses the value:
# the safest outcome for a surprise is to keep both copies, never to clobber.
CONFLICT_STRATEGIES = ("replace", "skip", "rename")


def normalise_conflict(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in CONFLICT_STRATEGIES else "rename"


def create_push(conn, push_id: str, target_device: str, challenge_key: str = "",
                target_folder: str = "", conflict: str = "rename") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pushes (push_id, target_device, challenge_key, date, status,"
        " target_folder, conflict) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (push_id, target_device, challenge_key, int(time.time()),
         target_folder, normalise_conflict(conflict)),
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
    """Pushes for the admin list, with everything the row displays.

    `status` is derived from the per-file statuses rather than read from
    `pushes.status`: that column has a 'pending' default and nothing ever
    updated it, so it reported "pending" for every push forever. Deriving here
    keeps the page honest without needing a write on every ack.
    """
    rows = [
        dict(r) for r in conn.execute(
            "SELECT p.push_id, p.target_device, p.date, p.target_folder, p.conflict,"
            " COUNT(f.file_id) AS file_count,"
            " COALESCE(SUM(CASE WHEN f.status = 'acked' THEN 1 ELSE 0 END), 0) AS acked_count,"
            " COALESCE(SUM(CASE WHEN f.status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,"
            " COALESCE(SUM(CASE WHEN f.status = 'exhausted' THEN 1 ELSE 0 END), 0) AS exhausted_count,"
            " COALESCE(GROUP_CONCAT(f.file_name, ', '), '') AS file_names"
            " FROM pushes p LEFT JOIN push_files f ON f.push_id = p.push_id"
            " GROUP BY p.push_id ORDER BY p.date DESC"
        ).fetchall()
    ]
    for r in rows:
        r["status"] = _rollup_status(
            r["file_count"], r["pending_count"], r["exhausted_count"]
        )
    return rows


def _rollup_status(file_count: int, pending: int, exhausted: int) -> str:
    if file_count == 0:
        return "empty"
    if pending:
        return "pending"
    if exhausted:
        return "failed"
    return "acked"


def list_pending_pushes(conn):
    """Outstanding files, grouped by the push they came in on.

    The admin acts on whole pushes — remove, re-push — so the grouping rule
    lives here rather than being re-derived in the template. Groups follow the
    query order, so the newest push is first.
    """
    rows = conn.execute(
        "SELECT f.*, p.target_device, p.target_folder, p.conflict, p.date AS push_date"
        " FROM push_files f JOIN pushes p ON p.push_id = f.push_id"
        " WHERE f.status = 'pending'"
        " ORDER BY p.date DESC, f.created_at"
    ).fetchall()
    grouped: dict[str, dict] = {}
    for r in rows:
        group = grouped.setdefault(r["push_id"], {
            "push_id": r["push_id"],
            "target_device": r["target_device"],
            "target_folder": r["target_folder"],
            "conflict": r["conflict"],
            "date": r["push_date"],
            "files": [],
        })
        group["files"].append(dict(r))
    return list(grouped.values())


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


def delete_push(conn, push_id) -> bool:
    """Remove a push and every file row that belongs to it.

    Returns whether the push existed, which the caller uses to decide if the
    on-disk tree is worth removing: `stored_path` is nulled on ack and on
    exhaustion, so the database can no longer find bytes that are still sitting
    under the storage root, but `storage_dir/<push_id>/` always can.
    """
    cur = conn.execute("DELETE FROM pushes WHERE push_id = ?", (push_id,))
    conn.execute("DELETE FROM push_files WHERE push_id = ?", (push_id,))
    conn.commit()
    return cur.rowcount > 0
