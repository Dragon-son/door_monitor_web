"""本地通行记录缓存库。

独立于主业务库 door_web.db，避免大量通行记录影响设备/人员等配置表。
"""

import os
import sqlite3
import threading
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "access_logs.db")
DB_LOCK = threading.RLock()


def _get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS access_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    device_ip TEXT,
                    device_name TEXT,
                    door INTEGER,
                    time TEXT NOT NULL,
                    user_id TEXT,
                    name TEXT,
                    method TEXT,
                    status TEXT,
                    snapshot_path TEXT,
                    source TEXT DEFAULT 'sdk',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(device_id, time, door, user_id, method, status)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_access_logs_device_time ON access_logs(device_id, time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_access_logs_time ON access_logs(time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_access_logs_user ON access_logs(user_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshot_query_state (
                    device_id INTEGER NOT NULL,
                    log_date TEXT NOT NULL,
                    last_query_time TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (device_id, log_date)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_record(device, record, source="sdk"):
    device_id = device.get("id")
    if device_id in (None, ""):
        device_id = device.get("ip") or "0"
    try:
        device_id = int(device_id)
    except (TypeError, ValueError):
        device_id = abs(hash(str(device_id))) % 2147483647
    return {
        "device_id": device_id,
        "device_ip": str(device.get("ip") or ""),
        "device_name": str(device.get("name") or ""),
        "door": record.get("door"),
        "time": str(record.get("time") or ""),
        "user_id": str(record.get("user_id") or ""),
        "name": str(record.get("name") or ""),
        "method": str(record.get("method") or ""),
        "status": str(record.get("status") or ""),
        "snapshot_path": str(record.get("path") or record.get("snapshot_path") or ""),
        "source": source,
    }


def upsert_records(device, records, source="sdk"):
    """写入/更新设备通行记录。返回写入记录数。

    source="rpc2" 时只更新已有记录的 snapshot_path，不新增门禁记录。
    RPC2 记录字段（door/method/status 等）可能与 SDK 不一致，
    若走 INSERT ON CONFLICT 会因 UNIQUE key 不匹配而插入幽灵行。
    """
    if not records:
        return 0
    init_db()
    now = _now()
    rows = []
    for r in records:
        row = _normalize_record(device, r, source=source)
        if not row["time"]:
            continue
        rows.append(row)
    if not rows:
        return 0
    with DB_LOCK:
        conn = _get_conn()
        try:
            if source == "rpc2":
                # RPC2 只补 snapshot_path，不插入新行。
                # 不使用 method/status 匹配：设备 RPC2 与 SDK 的开门方式文本可能不一致。
                updated = 0
                for r in rows:
                    if not r["snapshot_path"]:
                        continue
                    candidates = conn.execute(
                        """
                        SELECT id, door, user_id, name FROM access_logs
                        WHERE device_id=? AND time=?
                          AND (snapshot_path IS NULL OR snapshot_path='')
                          AND (source IS NULL OR source != 'rpc2')
                        """,
                        (r["device_id"], r["time"]),
                    ).fetchall()
                    if len(candidates) != 1:
                        filtered = candidates
                        if r.get("door") not in (None, ""):
                            filtered = [row for row in filtered if row["door"] == r["door"]]
                        if r.get("user_id"):
                            filtered = [row for row in filtered if str(row["user_id"] or "") == r["user_id"]]
                        if r.get("name"):
                            filtered = [row for row in filtered if str(row["name"] or "") == r["name"]]
                        candidates = filtered
                    if len(candidates) != 1:
                        continue
                    cur = conn.execute(
                        """
                        UPDATE access_logs
                        SET snapshot_path=?, updated_at=?
                        WHERE id=?
                        """,
                        (r["snapshot_path"], now, candidates[0]["id"]),
                    )
                    updated += cur.rowcount
                conn.commit()
                return updated
            else:
                conn.executemany(
                    """
                    INSERT INTO access_logs (
                        device_id, device_ip, device_name, door, time,
                        user_id, name, method, status, snapshot_path, source,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, time, door, user_id, method, status) DO UPDATE SET
                        device_ip=excluded.device_ip,
                        device_name=excluded.device_name,
                        name=COALESCE(NULLIF(excluded.name, ''), access_logs.name),
                        snapshot_path=COALESCE(NULLIF(excluded.snapshot_path, ''), access_logs.snapshot_path),
                        source=CASE
                            WHEN excluded.snapshot_path != '' THEN excluded.source
                            ELSE access_logs.source
                        END,
                        updated_at=excluded.updated_at
                    """,
                    [
                        (
                            r["device_id"], r["device_ip"], r["device_name"], r["door"], r["time"],
                            r["user_id"], r["name"], r["method"], r["status"], r["snapshot_path"], r["source"],
                            now, now,
                        )
                        for r in rows
                    ],
                )
                conn.commit()
                return len(rows)
        finally:
            conn.close()


def query_records(start, end, device_id=None):
    init_db()
    sql = "SELECT * FROM access_logs WHERE time >= ? AND time <= ?"
    params = [start, end]
    if device_id not in (None, ""):
        sql += " AND device_id = ?"
        params.append(int(device_id))
    sql += " ORDER BY time DESC"
    with DB_LOCK:
        conn = _get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_record(r) for r in rows]
        finally:
            conn.close()


def has_records(start, end, device_id=None):
    init_db()
    sql = "SELECT 1 FROM access_logs WHERE time >= ? AND time <= ?"
    params = [start, end]
    if device_id not in (None, ""):
        sql += " AND device_id = ?"
        params.append(int(device_id))
    sql += " LIMIT 1"
    with DB_LOCK:
        conn = _get_conn()
        try:
            return conn.execute(sql, params).fetchone() is not None
        finally:
            conn.close()


def get_missing_snapshot_time_range(start, end, device_id=None):
    init_db()
    sql = """
        SELECT MIN(time) AS earliest, MAX(time) AS latest FROM access_logs
        WHERE time >= ? AND time <= ?
          AND (snapshot_path IS NULL OR snapshot_path = '')
    """
    params = [start, end]
    if device_id not in (None, ""):
        sql += " AND device_id = ?"
        params.append(int(device_id))
    with DB_LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(sql, params).fetchone()
            if not row or not row["earliest"] or not row["latest"]:
                return None, None
            return row["earliest"], row["latest"]
        finally:
            conn.close()


def get_latest_time(device_id):
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(time) AS latest FROM access_logs WHERE device_id=?",
                (int(device_id),),
            ).fetchone()
            return row["latest"] if row and row["latest"] else None
        finally:
            conn.close()


def get_snapshot_query_time(device_id, log_date):
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT last_query_time FROM snapshot_query_state
                WHERE device_id=? AND log_date=?
                """,
                (int(device_id), str(log_date)),
            ).fetchone()
            return row["last_query_time"] if row and row["last_query_time"] else None
        finally:
            conn.close()


def set_snapshot_query_time(device_id, log_date, last_query_time):
    init_db()
    now = _now()
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO snapshot_query_state (device_id, log_date, last_query_time, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id, log_date) DO UPDATE SET
                    last_query_time=excluded.last_query_time,
                    updated_at=excluded.updated_at
                """,
                (int(device_id), str(log_date), str(last_query_time), now),
            )
            conn.commit()
        finally:
            conn.close()


def find_snapshot_path(device_id, data):
    """按前端日志行查本地 snapshot_path。"""
    init_db()
    target_time = str(data.get("time") or "")
    if not target_time:
        return None
    params = [int(device_id), target_time]
    sql = """
        SELECT snapshot_path FROM access_logs
        WHERE device_id=? AND time=? AND snapshot_path IS NOT NULL AND snapshot_path != ''
    """
    for field in ("door", "user_id", "method", "status"):
        value = data.get(field)
        if value not in (None, ""):
            sql += f" AND {field}=?"
            params.append(str(value) if field != "door" else value)
    sql += " ORDER BY updated_at DESC LIMIT 1"
    with DB_LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(sql, params).fetchone()
            if row and row["snapshot_path"]:
                return row["snapshot_path"]
            # 宽松兜底：只要求同设备同时间有图
            row = conn.execute(
                """
                SELECT snapshot_path FROM access_logs
                WHERE device_id=? AND time=? AND snapshot_path IS NOT NULL AND snapshot_path != ''
                ORDER BY updated_at DESC LIMIT 1
                """,
                (int(device_id), target_time),
            ).fetchone()
            return row["snapshot_path"] if row and row["snapshot_path"] else None
        finally:
            conn.close()


def _row_to_record(row):
    record = {
        "time": row["time"],
        "door": row["door"],
        "user_id": row["user_id"],
        "name": row["name"],
        "method": row["method"],
        "status": row["status"],
        "snapshot_path": row["snapshot_path"] or "",
    }
    if "device_id" in row.keys():
        record["device_id"] = row["device_id"]
    if "device_name" in row.keys():
        record["device_name"] = row["device_name"]
    return record
