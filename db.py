# db.py — SQLite 数据库模块（替换所有 JSON 文件操作）
#
# 迁移后数据目录：
#   door_web/door_web.db     ← 数据库文件（自动创建）
#   door_web/faces/          ← 人脸缓存图片（不变）
#   door_web/persons.json    ← 可删除（迁移后不再使用）
#   door_web/users.json      ← 可删除
#   door_web/device_map.json ← 可删除
#   door_web/data/           ← 可删除

import sqlite3
import json
import os
import threading
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "door_web.db")

# ================= 用户权限 =================
PERMISSION_LABELS = {
    "page.access": "门禁控制",
    "page.persons": "人员管理",
    "page.monitor": "视频监控",
    "page.playback": "视频回放",
    "page.devices": "设备管理",
    "page.admin": "用户管理",
    "page.audit": "操作日志",
    "access.preview": "实时预览",
    "access.open_door": "远程开门",
    "access.set_door_mode": "门模式设置",
    "access.view_logs": "开门记录查看",
}

ALL_PERMISSIONS = set(PERMISSION_LABELS)
ACCESS_ACTION_PERMISSIONS = {
    "access.preview",
    "access.open_door",
    "access.set_door_mode",
    "access.view_logs",
}
DEFAULT_USER_PERMISSIONS = {
    "page.access",
    "access.preview",
    "access.open_door",
    "access.view_logs",
}

# 写锁：mutate_persons 模式需要 read→mutate→write 原子化
DB_LOCK = threading.RLock()

# ================= 数据库初始化 =================

def _get_conn():
    """获取一个线程安全的 SQLite 连接（每次调用都创建新连接，避免跨线程共享）"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    """建表（幂等）"""
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS device_map (
                ip TEXT NOT NULL,
                port INTEGER NOT NULL,
                device_id INTEGER NOT NULL UNIQUE,
                PRIMARY KEY (ip, port)
            );

            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                department TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS admin_department_scopes (
                admin_username TEXT NOT NULL,
                department TEXT NOT NULL,
                PRIMARY KEY (admin_username, department)
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER NOT NULL,
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                ip TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 37777,
                username_device TEXT NOT NULL DEFAULT 'admin',
                password TEXT NOT NULL DEFAULT '',
                area TEXT NOT NULL DEFAULT 'A区',
                note TEXT NOT NULL DEFAULT '',
                device_type TEXT NOT NULL DEFAULT 'access',
                channel_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (id, username)
            );

            CREATE TABLE IF NOT EXISTS nvr_channels (
                device_id INTEGER NOT NULL,
                channel_no INTEGER NOT NULL,
                channel_name TEXT NOT NULL,
                PRIMARY KEY (device_id, channel_no)
            );

            CREATE TABLE IF NOT EXISTS user_channel_permissions (
                username TEXT NOT NULL,
                device_id INTEGER NOT NULL,
                channel_no INTEGER NOT NULL,
                PRIMARY KEY (username, device_id, channel_no)
            );

            CREATE TABLE IF NOT EXISTS user_permissions (
                username TEXT NOT NULL,
                permission TEXT NOT NULL,
                PRIMARY KEY (username, permission)
            );

            CREATE TABLE IF NOT EXISTS areas (
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (username, name)
            );

            CREATE TABLE IF NOT EXISTS persons (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                valid_begin TEXT NOT NULL DEFAULT '2000-01-01',
                valid_end TEXT NOT NULL DEFAULT '2037-12-31'
            );

            CREATE TABLE IF NOT EXISTS person_devices (
                user_id TEXT NOT NULL,
                device_id INTEGER NOT NULL,
                status INTEGER NOT NULL DEFAULT 0,
                has_face INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, device_id),
                FOREIGN KEY (user_id) REFERENCES persons(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                username TEXT NOT NULL,
                ip TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                detail TEXT,
                result TEXT NOT NULL DEFAULT 'success',
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp_id
                ON audit_log(timestamp DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_log_username_timestamp
                ON audit_log(username, timestamp DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_log_action_timestamp
                ON audit_log(action, timestamp DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_devices_username_id
                ON devices(username, id);
            CREATE INDEX IF NOT EXISTS idx_user_channel_permissions_user_device
                ON user_channel_permissions(username, device_id);
            CREATE INDEX IF NOT EXISTS idx_user_permissions_username
                ON user_permissions(username);
            CREATE INDEX IF NOT EXISTS idx_admin_department_scopes_department
                ON admin_department_scopes(department);
            CREATE INDEX IF NOT EXISTS idx_areas_username_sort_order
                ON areas(username, sort_order);
            CREATE INDEX IF NOT EXISTS idx_person_devices_device_user
                ON person_devices(device_id, user_id);
        """)
        _migrate_user_schema(conn)
        _migrate_admin_department_scopes(conn)
        _migrate_device_schema(conn)
        _migrate_person_dates(conn)
        _migrate_person_schema(conn)
        conn.commit()
    finally:
        conn.close()

# ================= 设备全局 ID 映射 (device_map) =================

def load_device_map():
    """返回 {f'{ip}:{port}': device_id, ...}"""
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT ip, port, device_id FROM device_map").fetchall()
        return {f"{r['ip']}:{r['port']}": r['device_id'] for r in rows}
    finally:
        conn.close()

def save_device_map(mapping):
    """全量替换 device_map — 事务保护"""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM device_map")
        conn.executemany(
            "INSERT INTO device_map (ip, port, device_id) VALUES (?, ?, ?)",
            [(k.split(':')[0], int(k.split(':')[1]), v) for k, v in mapping.items()]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_or_create_global_device_id(ip, port):
    """查找或创建全局设备 ID（自动递增）"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT device_id FROM device_map WHERE ip=? AND port=?", (ip, port)
        ).fetchone()
        if row:
            return row['device_id']
        # 获取最大 ID + 1
        max_row = conn.execute("SELECT MAX(device_id) FROM device_map").fetchone()
        new_id = (max_row[0] or 0) + 1
        conn.execute(
            "INSERT INTO device_map (ip, port, device_id) VALUES (?, ?, ?)",
            (ip, port, new_id)
        )
        conn.commit()
        return new_id
    finally:
        conn.close()

# ================= 用户 (users) =================

def load_users():
    """返回 {username: {password, role, department, name}, ...}"""
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT username, password, role, department, name FROM users").fetchall()
        return {
            r['username']: {
                "password": r['password'],
                "role": r['role'],
                "department": r['department'] or "",
                "name": r['name'] or "",
            }
            for r in rows
        }
    finally:
        conn.close()

def save_users(users_dict):
    """全量替换 users — 事务保护"""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM users")
        conn.executemany(
            "INSERT INTO users (username, password, role, department, name) VALUES (?, ?, ?, ?, ?)",
            [(uname, entry.get("password", ""), entry.get("role", "user"),
              str(entry.get("department", "") or "").strip(), str(entry.get("name", "") or "").strip())
             if isinstance(entry, dict)
             else (uname, entry, "user", "", "")
             for uname, entry in users_dict.items()]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def normalize_department_scopes(departments):
    if isinstance(departments, str):
        departments = departments.replace("，", ",").replace("、", ",").split(",")
    return sorted({str(d or "").strip() for d in (departments or []) if str(d or "").strip()})


def load_admin_department_scopes(username):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT department FROM admin_department_scopes WHERE admin_username=? ORDER BY department",
            (username,)
        ).fetchall()
        return [r["department"] for r in rows]
    finally:
        conn.close()


def load_all_admin_department_scopes():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT admin_username, department FROM admin_department_scopes ORDER BY admin_username, department"
        ).fetchall()
        result = {}
        for row in rows:
            result.setdefault(row["admin_username"], []).append(row["department"])
        return result
    finally:
        conn.close()


def save_admin_department_scopes(username, departments):
    scopes = normalize_department_scopes(departments)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM admin_department_scopes WHERE admin_username=?", (username,))
        conn.executemany(
            "INSERT INTO admin_department_scopes (admin_username, department) VALUES (?, ?)",
            [(username, department) for department in scopes]
        )
        conn.commit()
        return scopes
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_admin_department_scopes(username):
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM admin_department_scopes WHERE admin_username=?", (username,))
        conn.commit()
    finally:
        conn.close()
def get_user_role(username):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        return row['role'] if row else None
    finally:
        conn.close()

def get_user_password_hash(username):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT password FROM users WHERE username=?", (username,)).fetchone()
        return row['password'] if row else None
    finally:
        conn.close()


def normalize_user_permissions(permissions):
    """清洗用户权限：未知 key 丢弃；无门禁页面时清掉门禁细化权限。"""
    normalized = {str(p) for p in (permissions or []) if str(p) in ALL_PERMISSIONS}
    if "page.access" not in normalized:
        normalized -= ACCESS_ACTION_PERMISSIONS
    return sorted(normalized)


def load_user_permissions(username):
    """返回用户权限列表；admin 用户（内置超级管理员）默认全权限。"""
    if username == "admin":
        return sorted(ALL_PERMISSIONS)
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT permission FROM user_permissions WHERE username=? ORDER BY permission",
            (username,)
        ).fetchall()
        permissions = [r["permission"] for r in rows]
        return normalize_user_permissions(permissions)
    finally:
        conn.close()


def save_user_permissions(username, permissions):
    """全量保存用户权限；admin 用户（内置超级管理员）始终全权限。"""
    if username == "admin":
        permissions = ALL_PERMISSIONS
    normalized = normalize_user_permissions(permissions)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM user_permissions WHERE username=?", (username,))
        conn.executemany(
            "INSERT INTO user_permissions (username, permission) VALUES (?, ?)",
            [(username, p) for p in normalized]
        )
        conn.commit()
        return normalized
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def user_has_permission(username, permission):
    if username == "admin":
        return True
    return permission in set(load_user_permissions(username))

# ================= 设备（按用户隔离） =================

def _normalize_device_type(value):
    return "nvr" if value == "nvr" else "access"


def load_devices(username):
    """返回设备列表，每项 dict 含 id, name, ip, port, username, password, area, note, device_type, channel_count"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, ip, port, username_device, password, area, note, device_type, channel_count "
            "FROM devices WHERE username=? ORDER BY id", (username,)
        ).fetchall()
        return [{
            "id": r['id'],
            "name": r['name'],
            "ip": r['ip'],
            "port": r['port'],
            "username": r['username_device'],
            "password": r['password'],
            "area": r['area'],
            "note": r['note'],
            "device_type": _normalize_device_type(r['device_type']),
            "channel_count": int(r['channel_count'] or 0),
        } for r in rows]
    finally:
        conn.close()

def save_devices(username, devs):
    """全量替换某个用户的设备列表 — 事务保护"""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM devices WHERE username=?", (username,))
        for d in devs:
            device_type = _normalize_device_type(d.get('device_type', 'access'))
            channel_count = int(d.get('channel_count') or 0) if device_type == 'nvr' else 0
            conn.execute(
                "INSERT INTO devices (id, username, name, ip, port, username_device, password, area, note, device_type, channel_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d['id'], username, d['name'], d['ip'], d.get('port', 37777),
                 d.get('username', 'admin'), d.get('password', ''),
                 d.get('area', 'A区'), d.get('note', ''),
                 device_type, channel_count)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ================= 录像机通道与权限 =================

def ensure_nvr_channels(device_id, channel_count, channel_names=None):
    """确保录像机通道元数据存在，保留已有通道名称；传入名称时同步更新。"""
    did = int(device_id)
    count = max(0, int(channel_count or 0))
    channel_names = channel_names or {}
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM nvr_channels WHERE device_id=? AND channel_no>=?",
            (did, count)
        )
        conn.execute(
            "DELETE FROM user_channel_permissions WHERE device_id=? AND channel_no>=?",
            (did, count)
        )
        for channel_no in range(count):
            default_name = f"通道 {channel_no + 1}"
            channel_name = str(channel_names.get(channel_no) or channel_names.get(str(channel_no)) or "").strip()
            conn.execute(
                "INSERT OR IGNORE INTO nvr_channels (device_id, channel_no, channel_name) VALUES (?, ?, ?)",
                (did, channel_no, channel_name or default_name)
            )
            if channel_name:
                conn.execute(
                    "UPDATE nvr_channels SET channel_name=? WHERE device_id=? AND channel_no=?",
                    (channel_name, did, channel_no)
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_nvr_channels(device_id):
    did = int(device_id)
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT channel_no, channel_name FROM nvr_channels WHERE device_id=? ORDER BY channel_no",
            (did,)
        ).fetchall()
        return [
            {"channel_no": int(r["channel_no"]), "channel_name": r["channel_name"]}
            for r in rows
        ]
    finally:
        conn.close()


def remove_nvr_device_metadata(device_id):
    did = int(device_id)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM user_channel_permissions WHERE device_id=?", (did,))
        conn.execute("DELETE FROM nvr_channels WHERE device_id=?", (did,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_user_channel_permissions(username, device_id=None):
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if device_id is None:
            conn.execute("DELETE FROM user_channel_permissions WHERE username=?", (username,))
        else:
            conn.execute(
                "DELETE FROM user_channel_permissions WHERE username=? AND device_id=?",
                (username, int(device_id))
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_user_channel_permissions(username, device_id, channel_nos):
    did = int(device_id)
    channels = sorted({int(ch) for ch in (channel_nos or [])})
    valid_rows = load_nvr_channels(did)
    valid_channels = {int(row["channel_no"]) for row in valid_rows}
    channels = [ch for ch in channels if ch in valid_channels]
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM user_channel_permissions WHERE username=? AND device_id=?",
            (username, did)
        )
        for ch in channels:
            conn.execute(
                "INSERT OR REPLACE INTO user_channel_permissions (username, device_id, channel_no) VALUES (?, ?, ?)",
                (username, did, ch)
            )
        conn.commit()
        return channels
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def grant_all_channels(username, device_id):
    channels = [row["channel_no"] for row in load_nvr_channels(device_id)]
    return set_user_channel_permissions(username, device_id, channels)


def load_user_channel_permissions(username, device_id=None):
    conn = _get_conn()
    try:
        if device_id is None:
            rows = conn.execute(
                "SELECT device_id, channel_no FROM user_channel_permissions WHERE username=? ORDER BY device_id, channel_no",
                (username,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT device_id, channel_no FROM user_channel_permissions WHERE username=? AND device_id=? ORDER BY channel_no",
                (username, int(device_id))
            ).fetchall()
        result = {}
        for row in rows:
            result.setdefault(int(row["device_id"]), []).append(int(row["channel_no"]))
        return result
    finally:
        conn.close()


def user_has_channel_permission(username, device_id, channel_no):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM user_channel_permissions WHERE username=? AND device_id=? AND channel_no=?",
            (username, int(device_id), int(channel_no))
        ).fetchone()
        return row is not None
    finally:
        conn.close()

# ================= 区域（按用户隔离） =================

def load_areas(username):
    """返回区域名称列表"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM areas WHERE username=? ORDER BY sort_order", (username,)
        ).fetchall()
        return [r['name'] for r in rows]
    finally:
        conn.close()

def save_areas(username, names):
    """全量替换某个用户的区域列表 — 事务保护"""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM areas WHERE username=?", (username,))
        for i, name in enumerate(names):
            conn.execute(
                "INSERT INTO areas (username, name, sort_order) VALUES (?, ?, ?)",
                (username, name, i)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
# ================= 审计日志 =================

def add_audit_log(username, ip, action, target=None, detail=None, result='success', error=None):
    """写入一条审计日志"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO audit_log (timestamp, username, ip, action, target, detail, result, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.datetime.now().isoformat(),
                username,
                ip,
                action,
                target,
                json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                result,
                str(error) if error is not None else None,
            )
        )
        conn.commit()
    finally:
        conn.close()

def query_audit_logs(limit=100, offset=0, action=None, username=None, start_time=None, end_time=None, end_exclusive=False):
    """分页查询审计日志，支持按 action/username/时间范围筛选"""
    return query_audit_logs_rows(
        limit=limit,
        offset=offset,
        action=action,
        username=username,
        start_time=start_time,
        end_time=end_time,
        end_exclusive=end_exclusive,
    )


def query_audit_logs_rows(limit=100, offset=0, action=None, username=None, start_time=None, end_time=None, end_exclusive=False):
    """返回审计日志行和总数。limit=None 时返回全部候选行。"""
    conn = _get_conn()
    try:
        conditions = []
        params = []
        if action:
            conditions.append("action=?")
            params.append(action)
        if username:
            conditions.append("username=?")
            params.append(username)
        if start_time:
            conditions.append("timestamp>=?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp<?" if end_exclusive else "timestamp<=?")
            params.append(end_time)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM audit_log{where} ORDER BY timestamp DESC, id DESC"
        sql_params = list(params)
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            sql_params.extend([limit, offset])
        rows = conn.execute(sql, sql_params).fetchall()
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM audit_log{where}",
            params
        ).fetchone()
        return [dict(r) for r in rows], count_row[0]
    finally:
        conn.close()

# ================= 删除用户 =================
def delete_user(username):
    """
    删除用户及其关联数据：
    1. 删除用户记录
    2. 删除用户的设备和区域
    3. 清理不再被任何用户使用的设备人员关联
    """
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 1. 获取该用户的所有设备 ID 和所属部门
            user_device_rows = conn.execute(
                "SELECT id FROM devices WHERE username=?", (username,)
            ).fetchall()
            user_device_ids = {int(row[0]) for row in user_device_rows}
            user_row = conn.execute("SELECT department FROM users WHERE username=?", (username,)).fetchone()
            user_department = str(user_row["department"] or "").strip() if user_row else ""

            # 2. 删除用户、设备、区域
            conn.execute("DELETE FROM devices WHERE username=?", (username,))
            conn.execute("DELETE FROM areas WHERE username=?", (username,))
            conn.execute("DELETE FROM admin_department_scopes WHERE admin_username=?", (username,))
            if user_department:
                still_has_department = conn.execute(
                    "SELECT 1 FROM users WHERE username<>? AND department=? LIMIT 1",
                    (username, user_department)
                ).fetchone()
                if not still_has_department:
                    conn.execute("DELETE FROM admin_department_scopes WHERE department=?", (user_department,))
            conn.execute("DELETE FROM user_channel_permissions WHERE username=?", (username,))
            conn.execute("DELETE FROM user_permissions WHERE username=?", (username,))
            conn.execute("DELETE FROM users WHERE username=?", (username,))
            
            # 3. 仅清理删除后无人拥有的设备关联，避免影响其他用户仍在使用的同一全局设备
            if user_device_ids:
                placeholders = ",".join("?" for _ in user_device_ids)
                still_used_rows = conn.execute(
                    f"SELECT DISTINCT id FROM devices WHERE id IN ({placeholders})",
                    tuple(user_device_ids)
                ).fetchall()
                still_used_ids = {int(row[0]) for row in still_used_rows}
                remove_device_ids = sorted(user_device_ids - still_used_ids)
                if remove_device_ids:
                    remove_placeholders = ",".join("?" for _ in remove_device_ids)
                    conn.execute(
                        f"DELETE FROM person_devices WHERE device_id IN ({remove_placeholders})",
                        tuple(remove_device_ids)
                    )
                    conn.execute(
                        f"DELETE FROM nvr_channels WHERE device_id IN ({remove_placeholders})",
                        tuple(remove_device_ids)
                    )
                    conn.execute(
                        f"DELETE FROM user_channel_permissions WHERE device_id IN ({remove_placeholders})",
                        tuple(remove_device_ids)
                    )
                    conn.execute(
                        "DELETE FROM persons WHERE user_id NOT IN (SELECT DISTINCT user_id FROM person_devices)"
                    )
            
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
# ================= 人员（公用） =================

def _migrate_user_schema(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "department" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN department TEXT NOT NULL DEFAULT ''")
    if "name" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''")


def _migrate_admin_department_scopes(conn):
    migration_name = "admin_department_scopes_seeded"
    done = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name=?",
        (migration_name,)
    ).fetchone()
    if done:
        return

    rows = conn.execute(
        "SELECT username, department FROM users WHERE role='admin' AND username<>'admin'"
    ).fetchall()
    for row in rows:
        department = str(row["department"] or "").strip()
        if department:
            conn.execute(
                "INSERT OR IGNORE INTO admin_department_scopes (admin_username, department) VALUES (?, ?)",
                (row["username"], department)
            )
    conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (migration_name,))


def _migrate_device_schema(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
    if "device_type" not in columns:
        conn.execute("ALTER TABLE devices ADD COLUMN device_type TEXT NOT NULL DEFAULT 'access'")
    if "channel_count" not in columns:
        conn.execute("ALTER TABLE devices ADD COLUMN channel_count INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE devices SET device_type='access' WHERE device_type IS NULL OR device_type NOT IN ('access', 'nvr')")
    conn.execute("UPDATE devices SET channel_count=0 WHERE device_type='access' OR channel_count IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_type_username ON devices(device_type, username)")

def _normalize_person(p):
    """规范化单个人的 status / has_face 字段（原地修改 p）"""
    doors = p.get("doors", []) or []
    modified = False

    # status
    status = p.get("status", {})
    if not isinstance(status, dict):
        try:
            old_status = int(status) if status else 0
        except (TypeError, ValueError):
            old_status = 0
        p["status"] = {str(d): old_status for d in doors} if doors else {}
        modified = True
    else:
        # 清理不在 doors 中的残留条目，并确保所有 door 都有值
        new_status = {}
        for d in doors:
            try:
                new_status[str(d)] = int(status.get(str(d), status.get(d, 0)))
            except (TypeError, ValueError):
                new_status[str(d)] = 0
        if new_status != status:
            p["status"] = new_status
            modified = True

    # has_face
    hf = p.get("has_face", {})
    if isinstance(hf, dict):
        new_hf = {}
        for d in doors:
            val = hf.get(str(d), hf.get(d, False))
            # 正确处理字符串 "false"/"0" 和布尔值
            if isinstance(val, str):
                new_hf[str(d)] = val.lower() not in ("false", "0", "")
            else:
                new_hf[str(d)] = bool(val)
    elif hf in (True, "true", "1", 1):
        new_hf = {str(d): True for d in doors}
    else:
        new_hf = {str(d): False for d in doors}
    if new_hf != p.get("has_face"):
        p["has_face"] = new_hf
        modified = True

    return modified


def _row_to_person(row):
    """兼容旧 persons JSON 字段的行解析。"""
    p = dict(row)
    for field in ('doors', 'status', 'has_face'):
        if field not in p:
            p[field] = [] if field == 'doors' else {}
            continue
        try:
            p[field] = json.loads(p[field]) if isinstance(p[field], str) else p[field]
        except (json.JSONDecodeError, TypeError):
            if field == 'doors':
                p[field] = []
            elif field == 'status':
                p[field] = {}
            elif field == 'has_face':
                p[field] = {}
    return p


def _person_base_row(p):
    """人员主表只保存人员基础信息。"""
    ve_map = p.get("valid_end_map", {}) or {}
    vb_map = p.get("valid_begin_map", {}) or {}
    # 全部设备视图显示最近过期日期 = min valid_end
    valid_ends = [v for v in ve_map.values() if v]
    if valid_ends:
        display_end = min(valid_ends)
    else:
        display_end = p.get("valid_end", "2037-12-31")
    # valid_begin 取最早开始日期
    valid_begins = [v for v in vb_map.values() if v]
    if valid_begins:
        display_begin = min(valid_begins)
    else:
        display_begin = p.get("valid_begin", "2000-01-01")
    return (
        p.get("user_id", ""),
        p.get("name", ""),
        display_begin,
        display_end,
    )


def _person_device_rows(p):
    """把外部人员对象中的 doors/status/has_face 拆成关系表行。"""
    _normalize_person(p)
    rows = []
    for device_id in p.get("doors", []) or []:
        did = int(device_id)
        key = str(did)
        try:
            status = int((p.get("status") or {}).get(key, 0))
        except (TypeError, ValueError):
            status = 0
        has_face = 1 if (p.get("has_face") or {}).get(key, False) else 0
        vb_map = p.get("valid_begin_map", {}) or {}
        ve_map = p.get("valid_end_map", {}) or {}
        valid_begin = vb_map.get(key) or p.get("valid_begin", None)
        valid_end = ve_map.get(key) or p.get("valid_end", None)
        rows.append((p.get("user_id", ""), did, status, has_face, valid_begin, valid_end))
    return rows


def _coerce_status(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value):
    if isinstance(value, str):
        return value.lower() not in ("false", "0", "")
    return bool(value)


def _load_persons_from_conn(conn):
    persons = []
    index = {}
    for row in conn.execute(
        "SELECT user_id, name, valid_begin, valid_end FROM persons ORDER BY user_id"
    ).fetchall():
        p = {
            "user_id": row["user_id"],
            "name": row["name"],
            "valid_begin": row["valid_begin"],
            "valid_end": row["valid_end"],
            "doors": [],
            "status": {},
            "has_face": {},
            "valid_begin_map": {},
            "valid_end_map": {},
        }
        persons.append(p)
        index[p["user_id"]] = p

    for row in conn.execute(
        "SELECT user_id, device_id, status, has_face, valid_begin, valid_end FROM person_devices ORDER BY user_id, device_id"
    ).fetchall():
        p = index.get(row["user_id"])
        if not p:
            continue
        did = int(row["device_id"])
        key = str(did)
        p["doors"].append(did)
        p["status"][key] = int(row["status"] or 0)
        p["has_face"][key] = bool(row["has_face"])
        vb = row["valid_begin"] if row["valid_begin"] is not None else p.get("valid_begin")
        ve = row["valid_end"] if row["valid_end"] is not None else p.get("valid_end")
        if vb:
            p["valid_begin_map"][key] = vb
        if ve:
            p["valid_end_map"][key] = ve
    return persons


def _replace_persons(conn, persons):
    """用规范化表全量替换人员数据。"""
    normalized = []
    relation_rows = []
    for person in persons:
        p = dict(person)
        _normalize_person(p)
        normalized.append(p)
        relation_rows.extend(_person_device_rows(p))

    conn.execute("DELETE FROM person_devices")
    conn.execute("DELETE FROM persons")
    if normalized:
        conn.executemany(
            "INSERT INTO persons (user_id, name, valid_begin, valid_end) VALUES (?, ?, ?, ?)",
            [_person_base_row(p) for p in normalized]
        )
    if relation_rows:
        conn.executemany(
            "INSERT INTO person_devices (user_id, device_id, status, has_face, valid_begin, valid_end) VALUES (?, ?, ?, ?, ?, ?)",
            relation_rows
        )


def _person_from_base_row(row, rel_rows):
    p = {
        "user_id": row["user_id"],
        "name": row["name"],
        "valid_begin": row["valid_begin"],
        "valid_end": row["valid_end"],
        "doors": [],
        "status": {},
        "has_face": {},
        "valid_begin_map": {},
        "valid_end_map": {},
    }
    for rel in rel_rows:
        did = int(rel["device_id"])
        key = str(did)
        p["doors"].append(did)
        p["status"][key] = int(rel["status"] or 0)
        p["has_face"][key] = bool(rel["has_face"])
        vb = rel["valid_begin"] if rel["valid_begin"] is not None else p.get("valid_begin")
        ve = rel["valid_end"] if rel["valid_end"] is not None else p.get("valid_end")
        if vb:
            p["valid_begin_map"][key] = vb
        if ve:
            p["valid_end_map"][key] = ve
    return p


def _load_person_from_conn(conn, user_id):
    row = conn.execute(
        "SELECT user_id, name, valid_begin, valid_end FROM persons WHERE user_id=?",
        (user_id,)
    ).fetchone()
    if not row:
        return None
    rel_rows = conn.execute(
        "SELECT device_id, status, has_face, valid_begin, valid_end FROM person_devices WHERE user_id=? ORDER BY device_id",
        (user_id,)
    ).fetchall()
    return _person_from_base_row(row, rel_rows)


def _upsert_person_conn(conn, person):
    p = dict(person)
    _normalize_person(p)
    conn.execute(
        """
        INSERT INTO persons (user_id, name, valid_begin, valid_end)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name=excluded.name,
            valid_begin=excluded.valid_begin,
            valid_end=excluded.valid_end
        """,
        _person_base_row(p)
    )
    conn.execute("DELETE FROM person_devices WHERE user_id=?", (p["user_id"],))
    rel_rows = _person_device_rows(p)
    if rel_rows:
        conn.executemany(
            "INSERT INTO person_devices (user_id, device_id, status, has_face, valid_begin, valid_end) VALUES (?, ?, ?, ?, ?, ?)",
            rel_rows
        )


def _migrate_person_schema(conn):
    """迁移旧 persons JSON 列到规范化表，并删除 JSON 列。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(persons)").fetchall()}
    if not {"doors", "status", "has_face"}.issubset(columns):
        return

    existing_rel_rows = conn.execute(
        "SELECT user_id, device_id, status, has_face, valid_begin, valid_end FROM person_devices"
    ).fetchall()
    rows = conn.execute("SELECT * FROM persons").fetchall()
    relation_rows = []
    base_rows = []
    for row in rows:
        p = _row_to_person(row)
        _normalize_person(p)
        base_rows.append(_person_base_row(p))
        relation_rows.extend(_person_device_rows(p))
    if existing_rel_rows:
        relation_rows = [
            (row["user_id"], int(row["device_id"]), int(row["status"] or 0), int(row["has_face"] or 0),
             row["valid_begin"] if "valid_begin" in row.keys() else None,
             row["valid_end"] if "valid_end" in row.keys() else None)
            for row in existing_rel_rows
        ]

    conn.execute("DELETE FROM person_devices")
    conn.execute("DROP TABLE persons")
    conn.execute("""
        CREATE TABLE persons (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            valid_begin TEXT NOT NULL DEFAULT '2000-01-01',
            valid_end TEXT NOT NULL DEFAULT '2037-12-31'
        )
    """)
    if base_rows:
        conn.executemany(
            "INSERT INTO persons (user_id, name, valid_begin, valid_end) VALUES (?, ?, ?, ?)",
            base_rows
        )
    if relation_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO person_devices (user_id, device_id, status, has_face, valid_begin, valid_end) VALUES (?, ?, ?, ?, ?, ?)",
            relation_rows
        )


def _migrate_person_dates(conn):
    pd_cols = {row["name"] for row in conn.execute("PRAGMA table_info(person_devices)").fetchall()}
    if "valid_begin" not in pd_cols:
        conn.execute("ALTER TABLE person_devices ADD COLUMN valid_begin TEXT DEFAULT NULL")
    if "valid_end" not in pd_cols:
        conn.execute("ALTER TABLE person_devices ADD COLUMN valid_end TEXT DEFAULT NULL")


def load_persons():
    """返回规范化后的人员列表。设备关系来自 person_devices。"""
    conn = _get_conn()
    try:
        return _load_persons_from_conn(conn)
    finally:
        conn.close()


def save_persons(persons):
    """全量替换人员数据，设备关系写入 person_devices。"""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _replace_persons(conn, persons)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_person(user_id):
    conn = _get_conn()
    try:
        return _load_person_from_conn(conn, user_id)
    finally:
        conn.close()


def load_persons_for_devices(device_ids, page=None, per_page=None, search=None, search_mode='fuzzy'):
    """返回指定设备上的人员列表。

    page/per_page 可选：提供时走服务端分页，返回 (list, int) (人员列表, 总数);
    不提供时返回 list（向后兼容）。
    search 可选：按 user_id 或 name 过滤。
    search_mode 可选：'exact' 精确匹配 user_id，'fuzzy' 模糊匹配 user_id 和 name（默认）。
    """
    ids = sorted({int(did) for did in device_ids})
    if not ids:
        return ([], 0) if page else []
    placeholders = ",".join("?" for _ in ids)
    conn = _get_conn()
    try:
        # --- 总数查询 ---
        where_clause = f"WHERE pd.device_id IN ({placeholders})"
        search_params = list(ids)
        if search:
            if search_mode == 'exact':
                where_clause += " AND p.user_id = ?"
                search_params.append(search)
            else:  # fuzzy
                where_clause += " AND (p.user_id LIKE ? OR p.name LIKE ?)"
                search_params.extend([f"%{search}%", f"%{search}%"])

        total = conn.execute(
            f"SELECT COUNT(DISTINCT p.user_id) FROM persons p JOIN person_devices pd ON pd.user_id=p.user_id {where_clause}",
            tuple(search_params)
        ).fetchone()[0]

        if page and total == 0:
            return ([], 0)

        # --- 分页数据查询 ---
        sql = f"""
            SELECT DISTINCT p.user_id, p.name, p.valid_begin, p.valid_end
            FROM persons p
            JOIN person_devices pd ON pd.user_id = p.user_id
            {where_clause}
            ORDER BY p.user_id
        """
        params = tuple(search_params)
        if page and per_page:
            sql += " LIMIT ? OFFSET ?"
            params = tuple(search_params) + (per_page, (page - 1) * per_page)

        rows = conn.execute(sql, params).fetchall()
        if not rows:
            return ([], total) if page else []

        user_ids = [row["user_id"] for row in rows]
        user_placeholders = ",".join("?" for _ in user_ids)
        rel_rows = conn.execute(
            f"""
            SELECT user_id, device_id, status, has_face, valid_begin, valid_end
            FROM person_devices
            WHERE user_id IN ({user_placeholders})
            ORDER BY user_id, device_id
            """,
            tuple(user_ids)
        ).fetchall()
        rel_index = {}
        for rel in rel_rows:
            rel_index.setdefault(rel["user_id"], []).append(rel)
        persons = [_person_from_base_row(row, rel_index.get(row["user_id"], [])) for row in rows]

        return (persons, total) if page else persons
    finally:
        conn.close()


def load_persons_for_device(device_id):
    return load_persons_for_devices([device_id])


def upsert_person(person):
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _upsert_person_conn(conn, person)
            conn.commit()
            return _load_person_from_conn(conn, person["user_id"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def update_person_for_devices(user_id, updates, managed_device_ids):
    """
    更新人员基础信息，并且只在 managed_device_ids 范围内改设备关系。

    前端在受限用户视角下只会提交可见设备，如果直接按全量 doors 写回，会
    删除该人员在其他设备上的本地关系。这里把提交的 doors 解释为“当前用户
    可管理设备范围内的新状态”，范围外关系保持不变。
    """
    managed_ids = {int(did) for did in (managed_device_ids or [])}
    updates = updates or {}

    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = _load_person_from_conn(conn, user_id)
            if existing is None:
                conn.rollback()
                return None

            # 必须至少在一个管辖设备上有关系才能改;否则视作越权(伪装成 not found)。
            existing_doors = {int(did) for did in (existing.get("doors") or [])}
            if not (existing_doors & managed_ids):
                conn.rollback()
                return None

            old_doors = [int(did) for did in existing.get("doors", []) or []]
            old_status = existing.get("status", {}) if isinstance(existing.get("status"), dict) else {}
            old_face = existing.get("has_face", {}) if isinstance(existing.get("has_face"), dict) else {}

            if "doors" in updates:
                submitted_doors = {int(did) for did in (updates.get("doors") or [])}
                scoped_doors = submitted_doors & managed_ids
            else:
                scoped_doors = {did for did in old_doors if did in managed_ids}
            preserved_doors = {did for did in old_doors if did not in managed_ids}
            final_doors = sorted(preserved_doors | scoped_doors)
            final_door_set = set(final_doors)

            status_map = {}
            face_map = {}
            vb_map = dict(existing.get("valid_begin_map", {}))
            ve_map = dict(existing.get("valid_end_map", {}))
            for did in final_doors:
                key = str(did)
                status_map[key] = _coerce_status(old_status.get(key, old_status.get(did, 0)), 0)
                face_map[key] = _coerce_bool(old_face.get(key, old_face.get(did, False)))
                # 确保 final_doors 中的每个 did 在日期 map 中都有值
                vb_map.setdefault(key, existing.get("valid_begin", "2000-01-01"))
                ve_map.setdefault(key, existing.get("valid_end", "2037-12-31"))

            scoped_final_doors = sorted(final_door_set & managed_ids)
            incoming_status = updates.get("status")
            if "status" in updates:
                if isinstance(incoming_status, dict):
                    for did in scoped_final_doors:
                        key = str(did)
                        old_value = status_map.get(key, 0)
                        status_map[key] = _coerce_status(
                            incoming_status.get(key, incoming_status.get(did, old_value)),
                            old_value,
                        )
                else:
                    flat_status = _coerce_status(incoming_status, 0)
                    for did in scoped_final_doors:
                        status_map[str(did)] = flat_status

            incoming_face = updates.get("has_face")
            if "has_face" in updates:
                if isinstance(incoming_face, dict):
                    for did in scoped_final_doors:
                        key = str(did)
                        if key in incoming_face:
                            face_map[key] = _coerce_bool(incoming_face[key])
                        elif did in incoming_face:
                            face_map[key] = _coerce_bool(incoming_face[did])
                else:
                    flat_face = _coerce_bool(incoming_face)
                    for did in scoped_final_doors:
                        face_map[str(did)] = flat_face

            if "has_face_device" in updates and "has_face_value" in updates:
                did = int(updates["has_face_device"])
                if did in managed_ids and did in final_door_set:
                    face_map[str(did)] = _coerce_bool(updates["has_face_value"])

            # per-device 日期合并
            incoming_vb_map = updates.get("valid_begin_map")
            incoming_ve_map = updates.get("valid_end_map")
            if isinstance(incoming_vb_map, dict):
                for did in scoped_final_doors:
                    key = str(did)
                    if key in incoming_vb_map:
                        vb_map[key] = incoming_vb_map[key]
            if isinstance(incoming_ve_map, dict):
                for did in scoped_final_doors:
                    key = str(did)
                    if key in incoming_ve_map:
                        ve_map[key] = incoming_ve_map[key]

            updated = {
                "user_id": user_id,
                "name": updates.get("name", existing.get("name")),
                "valid_begin": updates.get("valid_begin", existing.get("valid_begin")),
                "valid_end": updates.get("valid_end", existing.get("valid_end")),
                "doors": final_doors,
                "status": {str(did): status_map.get(str(did), 0) for did in final_doors},
                "has_face": {str(did): face_map.get(str(did), False) for did in final_doors},
                "valid_begin_map": vb_map,
                "valid_end_map": ve_map,
            }
            _upsert_person_conn(conn, updated)
            conn.commit()
            return _load_person_from_conn(conn, user_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def remove_person_device(user_id, device_id):
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            person_exists = conn.execute(
                "SELECT 1 FROM persons WHERE user_id=?", (user_id,)
            ).fetchone() is not None
            if not person_exists:
                conn.rollback()
                return {"status": "missing"}

            cur = conn.execute(
                "DELETE FROM person_devices WHERE user_id=? AND device_id=?",
                (user_id, int(device_id))
            )
            if cur.rowcount == 0:
                conn.rollback()
                return {"status": "not_on_device"}

            remaining_rows = conn.execute(
                "SELECT device_id FROM person_devices WHERE user_id=? ORDER BY device_id",
                (user_id,)
            ).fetchall()
            remaining_doors = [int(row["device_id"]) for row in remaining_rows]
            if not remaining_doors:
                conn.execute("DELETE FROM persons WHERE user_id=?", (user_id,))
            conn.commit()
            return {"status": "ok", "remaining_doors": remaining_doors}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def remove_device_from_all_persons(device_id):
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM person_devices WHERE device_id=?", (int(device_id),))
            conn.execute(
                "DELETE FROM persons WHERE user_id NOT IN (SELECT DISTINCT user_id FROM person_devices)"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def set_person_face(user_id, device_id, has_face):
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT has_face FROM person_devices WHERE user_id=? AND device_id=?",
                (user_id, int(device_id))
            ).fetchone()
            if not row:
                conn.rollback()
                return "person_not_found"
            if bool(row["has_face"]) and has_face:
                conn.rollback()
                return "already_has_face"
            conn.execute(
                "UPDATE person_devices SET has_face=? WHERE user_id=? AND device_id=?",
                (1 if has_face else 0, user_id, int(device_id))
            )
            conn.commit()
            return "updated"
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def import_persons_for_device(device_id, new_persons, no_purge=False, default_has_face=True, detect_orphans=False, detect_conflicts=False):
    """把 new_persons 合并写入 device_id 的 person_devices 关系表。

    no_purge=True(推荐用于同步场景): 不删除 device_id 上其他人员关系,只做合并。
    no_purge=False(全量替换): 删除 device_id 上不在 new_persons 中的人员关系,并清理孤儿 persons 行。
    detect_orphans=True: 返回结果 dict 的"orphans"字段;含义见下方。
        no_purge=True 时这些就是"本地有但设备没出现"的孤儿 uid;调用方据此提示用户处理。
        no_purge=False 时这些 uid 已经被 purge,返回的是被清掉的 uid 列表(用于审计)。
    detect_conflicts=True: 检测新数据与本地已有记录的差异(仅当 existing 不为空时)。
        当前检测字段: name。
        返回结果 dict 的"conflicts"字段,每条格式:
          { "user_id": str, "field": str, "existing": str, "incoming": str,
            "from_device": int, "device_name": str }
    返回 dict { "orphans": list[str], "conflicts": list[dict] }。
    """
    did = int(device_id)
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            imported_uids = set()
            conflicts = []
            for raw in new_persons:
                uid = raw["user_id"]
                imported_uids.add(uid)
                existing = _load_person_from_conn(conn, uid)
                conflicts_for_uid = []
                if existing:
                    person = existing
                    incoming_name = raw.get("name")
                    incoming_begin = raw.get("valid_begin")
                    incoming_end = raw.get("valid_end")
                    if detect_conflicts:
                        # 在覆盖前检测冲突（当前只检测 name，不检测日期）
                        if incoming_name and incoming_name != person["name"]:
                            conflicts_for_uid.append({
                                "user_id": uid,
                                "field": "name",
                                "existing": str(person["name"]),
                                "incoming": str(incoming_name),
                                "from_device": did,
                            })
                    # 只有 incoming 有值时覆盖，保留原有值
                    if incoming_name:
                        person["name"] = incoming_name
                    # 日期改为 per-device 存储（在下面的 target_doors 循环中写入），
                    # 不再覆写全局 valid_begin/valid_end
                    person.setdefault("valid_begin_map", {})
                    person.setdefault("valid_end_map", {})
                else:
                    person = {
                        "user_id": uid,
                        "name": raw.get("name", ""),
                        "valid_begin": raw.get("valid_begin", "2000-01-01"),
                        "valid_end": raw.get("valid_end", "2037-12-31"),
                        "doors": [],
                        "status": {},
                        "has_face": {},
                        "valid_begin_map": {},
                        "valid_end_map": {},
                    }

                incoming_doors = raw.get("doors")
                target_doors = [int(d) for d in incoming_doors] if isinstance(incoming_doors, list) and incoming_doors else [did]
                incoming_status = raw.get("status")
                incoming_face = raw.get("has_face")
                for target_did in target_doors:
                    key = str(target_did)
                    relation_exists = target_did in person["doors"]
                    if target_did not in person["doors"]:
                        person["doors"].append(target_did)
                    if isinstance(incoming_status, dict):
                        try:
                            person["status"][key] = int(incoming_status.get(key, incoming_status.get(target_did, person["status"].get(key, 0))))
                        except (TypeError, ValueError):
                            person["status"][key] = 0
                    elif incoming_status is not None:
                        person["status"][key] = int(incoming_status)
                    else:
                        person["status"].setdefault(key, 0)

                    if relation_exists:
                        person["has_face"].setdefault(key, False)
                    elif default_has_face:
                        person["has_face"][key] = True
                    elif isinstance(incoming_face, dict):
                        if key in incoming_face:
                            person["has_face"][key] = bool(incoming_face[key])
                        elif target_did in incoming_face:
                            person["has_face"][key] = bool(incoming_face[target_did])
                        else:
                            person["has_face"].setdefault(key, False)
                    elif incoming_face is not None:
                        person["has_face"][key] = bool(incoming_face)
                    else:
                        person["has_face"].setdefault(key, False)

                    # per-device 日期（只在 target_doors 循环中对当前目标设备写入）
                    if incoming_begin:
                        person["valid_begin_map"][key] = incoming_begin
                    elif key not in person["valid_begin_map"]:
                        person["valid_begin_map"][key] = person.get("valid_begin", "2000-01-01")
                    if incoming_end:
                        person["valid_end_map"][key] = incoming_end
                    elif key not in person["valid_end_map"]:
                        person["valid_end_map"][key] = person.get("valid_end", "2037-12-31")

                _upsert_person_conn(conn, person)
                if detect_conflicts and conflicts_for_uid:
                    conflicts.extend(conflicts_for_uid)

            orphans = None
            if detect_orphans:
                imported_placeholders = ",".join("?" for _ in imported_uids) if imported_uids else None
                if imported_placeholders:
                    rows = conn.execute(
                        f"SELECT user_id FROM person_devices WHERE device_id=? AND user_id NOT IN ({imported_placeholders})",
                        (did, *tuple(imported_uids)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT user_id FROM person_devices WHERE device_id=?",
                        (did,),
                    ).fetchall()
                orphans = [r["user_id"] for r in rows]

            if not no_purge:
                if imported_uids:
                    placeholders = ",".join("?" for _ in imported_uids)
                    conn.execute(
                        f"DELETE FROM person_devices WHERE device_id=? AND user_id NOT IN ({placeholders})",
                        (did, *tuple(imported_uids))
                    )
                conn.execute(
                    "DELETE FROM persons WHERE user_id NOT IN (SELECT DISTINCT user_id FROM person_devices)"
                )
            conn.commit()
            return {"orphans": orphans or [], "conflicts": conflicts or []}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def mutate_persons(mutator):
    """
    在事务内执行 load → mutate → save，返回 mutator 返回的 payload。
    mutator(persons) 接收并修改列表，返回 (modified_bool, payload) 或 bool。
    """
    with DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            persons = _load_persons_from_conn(conn)

            # mutate
            result = mutator(persons)
            mutator_should_save = result[0] if isinstance(result, tuple) else bool(result)
            payload = result[1] if isinstance(result, tuple) and len(result) > 1 else None

            if mutator_should_save:
                _replace_persons(conn, persons)
                conn.commit()

            return payload
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
