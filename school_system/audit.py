"""Permanent message archive (tamper-evident) and the "deleted items" trash.

* ``message_archive`` is append-only: SQLite triggers refuse UPDATE and DELETE, and every row carries a
  SHA-256 hash that includes the previous row's hash, so any later edit of the database file breaks the chain
  and is reported by :func:`verify_chain`.
* ``deleted_items`` is the trash. Nothing is removed from it automatically; only the Super Admin can empty it.
"""
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64
EVENTS = ("created", "edited", "deleted", "restored", "trash_emptied")
# columns that feed the hash, in this exact order
_HASHED = (
    "kind", "source_id", "event", "title", "body", "audience",
    "sender_id", "sender_name", "sender_role", "occurred_at", "snapshot",
)
RESTORABLE_TABLES = {"announcements", "dashboard_blocks", "service_requests"}


def now_text():
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _digest(previous, values):
    payload = "\x1f".join([previous] + ["" if v is None else str(v) for v in values])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def actor(user):
    """(id, name, role) snapshot of the signed-in user."""
    if user is None:
        return None, "system", "system"
    return user["id"], user["username"], user["role"]


def _lock_chain(db):
    # A write on a settings row takes SQLite's write lock first, so two workers never fork the chain.
    db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('audit_chain_lock','0')")
    db.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='audit_chain_lock'")


def log_message(db, kind, source_id, event, title, body, audience, user, snapshot=None):
    """Append one entry to the permanent archive (the caller commits)."""
    if event not in EVENTS:
        raise ValueError(event)
    _lock_chain(db)
    last = db.execute("SELECT row_hash FROM message_archive ORDER BY id DESC LIMIT 1").fetchone()
    previous = last["row_hash"] if last else GENESIS
    sender_id, sender_name, sender_role = actor(user)
    snap = "" if snapshot is None else json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    values = (kind, source_id, event, title or "", body or "", audience or "",
              sender_id, sender_name, sender_role, now_text(), snap)
    db.execute(
        "INSERT INTO message_archive (" + ",".join(_HASHED) + ",prev_hash,row_hash) VALUES ("
        + ",".join("?" * (len(_HASHED) + 2)) + ")",
        values + (previous, _digest(previous, values)),
    )


def verify_chain(db):
    """(ok, entries, first_bad_id, head_hash). Recomputes every hash from the stored text."""
    previous, count = GENESIS, 0
    for row in db.execute("SELECT * FROM message_archive ORDER BY id").fetchall():
        values = tuple(row[c] for c in _HASHED)
        if row["prev_hash"] != previous or row["row_hash"] != _digest(previous, values):
            return False, count, row["id"], previous
        previous, count = row["row_hash"], count + 1
    return True, count, None, previous


def move_to_trash(db, kind, table, row, user, title, body, audience, author_name="", created_at=""):
    """Put a record into the trash (what / who / when) and log the deletion permanently."""
    snapshot = {k: row[k] for k in row.keys()}
    uid, name, role = actor(user)
    db.execute(
        "INSERT INTO deleted_items (kind,source_table,source_id,title,body,audience,snapshot,"
        "deleted_by_id,deleted_by_name,deleted_by_role,deleted_at,created_by_name,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, table, row["id"], title, body, audience,
         json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str),
         uid, name, role, now_text(), author_name, created_at),
    )
    log_message(db, kind, row["id"], "deleted", title, body, audience, user, snapshot)


def restore_row(db, item):
    """Re-create the original row from a trash entry; returns the new id."""
    table = item["source_table"]
    if table not in RESTORABLE_TABLES:
        raise ValueError(table)
    data = json.loads(item["snapshot"])
    columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
    data = {k: v for k, v in data.items() if k in columns and k != "id"}
    names = list(data)
    cursor = db.execute(
        f"INSERT INTO {table} (" + ",".join(names) + ") VALUES (" + ",".join("?" * len(names)) + ")",
        [data[n] for n in names],
    )
    return cursor.lastrowid
