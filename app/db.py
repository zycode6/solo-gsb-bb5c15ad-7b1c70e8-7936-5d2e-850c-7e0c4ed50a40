import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Optional

from .chain import GENESIS_HASH, compute_record_hash

DB_PATH = os.getenv("DB_PATH", "events.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- 首次接收顺序
    event_id    TEXT NOT NULL UNIQUE,               -- 幂等键
    occurred_at TEXT NOT NULL,                      -- 事件发生时间(UTC ISO8601)
    payload     TEXT NOT NULL,                      -- 原始 JSON
    received_at TEXT NOT NULL,                      -- 首次接收时间(UTC ISO8601)
    prev_hash   TEXT,                               -- 上一条记录哈希(首条为 GENESIS_HASH)
    record_hash TEXT                                -- 本记录哈希(覆盖全部字段 + prev_hash)
);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events (occurred_at);
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """兼容旧库: 缺少链字段列时补列, 并按接收顺序回填哈希链(同一事务内)。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "prev_hash" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN prev_hash TEXT")
    if "record_hash" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN record_hash TEXT")
    backfill_chain(conn)


def backfill_chain(conn: sqlite3.Connection) -> int:
    """为缺失 record_hash 的记录按 id 顺序回填链字段, 返回回填条数。

    已有哈希的记录原样保留并作为后续回填的链头, 因此重复执行是幂等的。
    """
    rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    prev_hash = GENESIS_HASH
    filled = 0
    for row in rows:
        if row["record_hash"] is not None:
            prev_hash = row["record_hash"]
            continue
        record_hash = compute_record_hash(
            row["id"], row["event_id"], row["occurred_at"],
            row["payload"], row["received_at"], prev_hash,
        )
        conn.execute(
            "UPDATE events SET prev_hash = ?, record_hash = ? WHERE id = ?",
            (prev_hash, record_hash, row["id"]),
        )
        prev_hash = record_hash
        filled += 1
    return filled


def verify_chain(conn: sqlite3.Connection) -> dict[str, Any]:
    """从最早记录开始校验哈希链: 序号连续性、prev_hash 链接、记录哈希与事件字段一致。

    返回 {"verified", "checked", "first_break"}; checked 为通过校验的记录数,
    首个断点给出 seq/event_id/reason; 空库 verified=True。
    """
    rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    checked = 0
    prev_hash = GENESIS_HASH
    prev_seq: Optional[int] = None

    def broken(seq: int, event_id: str, reason: str) -> dict[str, Any]:
        return {
            "verified": False,
            "checked": checked,
            "first_break": {"seq": seq, "event_id": event_id, "reason": reason},
        }

    for row in rows:
        seq, event_id = row["id"], row["event_id"]
        if prev_seq is not None and seq != prev_seq + 1:
            return broken(seq, event_id, "seq_not_continuous")
        if row["prev_hash"] is None or row["record_hash"] is None:
            return broken(seq, event_id, "missing_hash_fields")
        if row["prev_hash"] != prev_hash:
            return broken(seq, event_id, "prev_hash_mismatch")
        expected = compute_record_hash(
            seq, event_id, row["occurred_at"],
            row["payload"], row["received_at"], row["prev_hash"],
        )
        if row["record_hash"] != expected:
            return broken(seq, event_id, "record_hash_mismatch")
        prev_hash, prev_seq = row["record_hash"], seq
        checked += 1

    return {"verified": True, "checked": checked, "first_break": None}


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
