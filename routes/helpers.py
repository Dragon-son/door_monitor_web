"""被多个蓝图复用的工具函数集合。

只放“跨蓝图调用”的代码;只在单个蓝图里用的助手留在该蓝图模块内部。
"""

import os
import socket
import time
import threading

from flask import request, session, jsonify

import db
from device_client import DeviceClient

from routes._app import (
    BASE,
    DEVICE_TYPE_ACCESS,
    DEVICE_TYPE_NVR,
    manager,
)


# ================= 响应包装 =================
def ok(data=None):
    return jsonify({"code": 0, "msg": "ok", "data": data})


def err(msg, code=400):
    return jsonify({"code": -1, "msg": str(msg)}), code


# ================= 设备 payload =================
def device_payload(d):
    """返回设备的安全 payload（不含密码）"""
    payload = {
        "id": d["id"],
        "name": d["name"],
        "ip": d["ip"],
        "port": d.get("port", 37777),
        "username": d.get("username", ""),
        "area": d.get("area", "A区"),
        "note": d.get("note", ""),
        "online": d.get("online", None),
        "device_type": d.get("device_type", DEVICE_TYPE_ACCESS),
        "channel_count": int(d.get("channel_count") or 0),
    }
    if payload["device_type"] == DEVICE_TYPE_NVR:
        payload["channels"] = db.load_nvr_channels(d["id"])
    return payload


def _normalize_device_type(value):
    return DEVICE_TYPE_NVR if value == DEVICE_TYPE_NVR else DEVICE_TYPE_ACCESS


def _access_devices(devs):
    return [d for d in devs if d.get("device_type", DEVICE_TYPE_ACCESS) == DEVICE_TYPE_ACCESS]


def _nvr_devices(devs):
    return [d for d in devs if d.get("device_type") == DEVICE_TYPE_NVR]


def _probe_nvr(ip, port, username, password):
    client = DeviceClient(ip, port, username, password)
    try:
        channel_count = max(0, int(client.channel_count or 0))
        channel_names = client.get_channel_names() if channel_count > 0 else {}
        return channel_count, channel_names
    finally:
        client.close()


# ================= 用户/权限 =================
def load_users():
    return db.load_users()


def save_users(users):
    db.save_users(users)


def get_user_role(username):
    return db.get_user_role(username)


def get_user_password_hash(username):
    return db.get_user_password_hash(username)


def get_user_permissions(username):
    return set(db.load_user_permissions(username))


def has_permission(username, permission):
    return db.user_has_permission(username, permission)


def require_permission(permission):
    username = session.get("username")
    if not username:
        raise Exception("未登录")
    if not has_permission(username, permission):
        raise Exception("无权限访问")


def permission_labels():
    return dict(db.PERMISSION_LABELS)


def is_admin(username):
    role = get_user_role(username)
    return role == "admin"


def require_admin():
    username = session.get("username")
    if not username or not is_admin(username):
        raise Exception("需要管理员权限")


def is_super_admin():
    """只有内置 admin 用户是超级管理员"""
    return session.get("username") == "admin"


def require_super_admin():
    if not is_super_admin():
        raise Exception("需要超级管理员权限")


def is_super_admin_username(username):
    return username == "admin"


def _user_role_of(users, username):
    entry = users.get(username)
    return entry.get("role", "user") if isinstance(entry, dict) else "user"


def _user_department_of(users, username):
    entry = users.get(username)
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("department", "") or "").strip()


def admin_department_in_scope(username, department):
    if is_super_admin_username(username):
        return True
    return str(department or "").strip() in set(db.load_admin_department_scopes(username))


def can_view_user(current, target_username, users=None):
    users = users or load_users()
    current_role = _user_role_of(users, current)
    target_role = _user_role_of(users, target_username)
    if is_super_admin_username(current):
        return True
    if target_username == current:
        return True
    if current_role == "admin":
        return target_username != "admin" and target_role != "admin" and admin_department_in_scope(current, _user_department_of(users, target_username))
    return False


def can_manage_user(current, target_username, users=None):
    users = users or load_users()
    target_role = _user_role_of(users, target_username)
    if is_super_admin_username(current):
        return True
    if db.get_user_role(current) != "admin":
        return False
    return (
        target_username != "admin"
        and target_username != current
        and target_role != "admin"
        and admin_department_in_scope(current, _user_department_of(users, target_username))
    )


# ---------- 全局设备ID映射 ----------
def load_device_map():
    return db.load_device_map()


def save_device_map(mapping):
    db.save_device_map(mapping)


def get_or_create_global_device_id(ip, port):
    return db.get_or_create_global_device_id(ip, port)


# ---------- 设备（按用户隔离） ----------
def load_devices(username):
    return db.load_devices(username)


def save_devices(username, devs):
    db.save_devices(username, devs)


# ---------- 区域（按用户隔离） ----------
def load_areas(username):
    return db.load_areas(username)


def save_areas(username, areas):
    db.save_areas(username, areas)


# ---------- 人员 ----------
def load_persons_for_devices(device_ids, page=None, per_page=None, search=None, search_mode='fuzzy'):
    return db.load_persons_for_devices(device_ids, page=page, per_page=per_page, search=search, search_mode=search_mode)


def load_persons_for_device(device_id):
    return db.load_persons_for_device(device_id)


def upsert_person(person):
    return db.upsert_person(person)


def get_current_user():
    username = session.get("username")
    if not username:
        raise Exception("未登录")
    return username


# ================= 审计日志 =================
# 是否在反向代理后面（从环境变量读取，默认 False）
# 只有开启时才会信任 X-Forwarded-For 头
_TRUSTED_PROXY = os.environ.get("TRUSTED_PROXY", "").lower() in ("1", "true", "yes")


def get_client_ip():
    """获取请求来源 IP
    设置了 TRUSTED_PROXY=1 时信任 X-Forwarded-For（反向代理场景），
    否则直接使用 remote_addr 防止 IP 伪造。
    """
    if _TRUSTED_PROXY:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
            if ip:
                return ip
    return request.remote_addr or "unknown"


def log_audit(action, target=None, detail=None, result='success', error=None, username=None):
    """记录审计日志（自动获取当前用户和 IP）"""
    try:
        username = username or session.get("username", "anonymous")
        ip = get_client_ip()
        db.add_audit_log(username, ip, action, target, detail, result, error)
    except Exception as e:
        print(f"[audit] 写入审计日志失败: {e}")


def audit_err(action, msg, code=400, target=None, detail=None):
    log_audit(action, target=target, detail=detail, result="fail", error=msg)
    return err(msg, code)


# ================= 设备在线探测（带缓存） =================
_device_online_cache = {}
_device_online_lock = threading.Lock()
_device_online_ttl = 5  # 缓存有效期（秒）
_device_online_check_timeout = 1.5  # 单次TCP连接超时（秒）


def check_device_online(ip, port, timeout=2):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _check_device_online_cached(ip, port):
    """带缓存的设备在线检测，5秒内复用上次结果"""
    key = (ip, int(port or 37777))
    now = time.time()
    with _device_online_lock:
        cached = _device_online_cache.get(key)
    if cached:
        ts, status = cached
        if now - ts < _device_online_ttl:
            return status
    status = check_device_online(ip, port, timeout=_device_online_check_timeout)
    with _device_online_lock:
        _device_online_cache[key] = (now, status)
    return status


def _get_cached_device_online(ip, port, allow_stale=True):
    """只读取在线状态缓存，不发起网络探测。"""
    key = (ip, int(port or 37777))
    with _device_online_lock:
        cached = _device_online_cache.get(key)
    if not cached:
        return None
    ts, status = cached
    if not allow_stale and time.time() - ts >= _device_online_ttl:
        return None
    return status


# ================= NVR 通道连接状态短时缓存 =================
_nvr_channel_states_cache = {}  # device_id -> (timestamp, states_dict_or_None)
_nvr_channel_states_lock = threading.Lock()
_NVR_CHANNEL_STATES_TTL = 30  # 秒


def _get_cached_nvr_channel_states(device_id, allow_stale=True):
    """只读取 NVR 通道状态缓存，不登录设备。"""
    device_id = int(device_id)
    with _nvr_channel_states_lock:
        cached = _nvr_channel_states_cache.get(device_id)
    if not cached:
        return None
    ts, states = cached
    if not allow_stale and time.time() - ts >= _NVR_CHANNEL_STATES_TTL:
        return None
    return states


def _get_nvr_channel_states_cached(dev):
    """带短时缓存的通道连接状态查询,返回 {channel_no: connected_bool} 或 None。"""
    device_id = int(dev["id"])
    now = time.time()
    with _nvr_channel_states_lock:
        cached = _nvr_channel_states_cache.get(device_id)
        if cached and now - cached[0] < _NVR_CHANNEL_STATES_TTL:
            return cached[1]

    states = None
    client = None
    try:
        client = DeviceClient(
            dev["ip"],
            int(dev.get("port", 37777)),
            dev.get("username", "admin"),
            dev.get("password", ""),
        )
        states = client.get_channel_states()
    except Exception as e:
        print(f"[monitor] 查询通道状态失败 device_id={device_id}: {e}", flush=True)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    with _nvr_channel_states_lock:
        _nvr_channel_states_cache[device_id] = (now, states)
    return states


# ================= 设备查找 =================
def _find_device_by_id(device_id, username):
    """在用户的设备列表中查找 device_id 对应的设备信息"""
    devs = load_devices(username)
    return next((d for d in devs if d["id"] == device_id), None)


def _find_access_device_by_id(device_id, username):
    dev = _find_device_by_id(device_id, username)
    if not dev or dev.get("device_type", DEVICE_TYPE_ACCESS) != DEVICE_TYPE_ACCESS:
        return None
    return dev


def _find_nvr_device_by_id(device_id, username):
    dev = _find_device_by_id(device_id, username)
    if not dev or dev.get("device_type") != DEVICE_TYPE_NVR:
        return None
    return dev


def _monitor_channel_allowed(username, device_id, channel_no):
    if username == "admin":
        return True
    return db.user_has_channel_permission(username, device_id, channel_no)


# ================= 设备凭证抽取 =================
def extract_device():
    data = {}
    if request.is_json:
        data.update(request.get_json(silent=True) or {})
    if request.form:
        data.update(request.form.to_dict())
    if request.args:
        for k, v in request.args.items():
            if k not in data:
                data[k] = v

    # 优先使用 device_id 从后端设备配置查凭证，避免前端把密码放进 URL/请求体。
    device_id = data.get("device_id")
    if device_id not in (None, ""):
        try:
            device_id = int(device_id)
        except (TypeError, ValueError):
            raise Exception("device_id 格式错误")
        username = get_current_user()  # 未登录时直接 raise，无需再判断
        dev = next((d for d in load_devices(username) if int(d.get("id", -1)) == device_id), None)
        if not dev:
            raise PermissionError("设备不存在或无权限")
        if dev.get("device_type", DEVICE_TYPE_ACCESS) != DEVICE_TYPE_ACCESS:
            raise Exception("该设备不是门禁设备")
        return {
            "id": dev.get("id"),
            "ip": dev["ip"],
            "port": int(dev.get("port", 37777)),
            "username": dev.get("username", "admin"),
            "password": dev.get("password", ""),
            "device_type": dev.get("device_type", DEVICE_TYPE_ACCESS),
        }

    ip = data.get("device_ip")
    username = data.get("username")
    password = data.get("password")
    if not ip or not username or not password:
        raise Exception("缺少 device_id 或 device_ip / username / password")
    # 校验 IP 格式，防止注入
    import ipaddress
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise Exception(f"无效的 device_ip: {ip}")
    try:
        port = int(data.get("device_port", 37777))
    except (TypeError, ValueError):
        raise Exception("device_port 格式错误")

    current_user = get_current_user()
    dev = next((d for d in load_devices(current_user)
                if d.get("device_type", DEVICE_TYPE_ACCESS) == DEVICE_TYPE_ACCESS
                and d.get("ip") == ip
                and int(d.get("port", 37777)) == port), None)
    if not dev:
        raise PermissionError("设备不存在或无权限")
    return {
        "id": dev.get("id"),
        "ip": dev["ip"],
        "port": int(dev.get("port", 37777)),
        "username": dev.get("username", "admin"),
        "password": dev.get("password", ""),
        "device_type": dev.get("device_type", DEVICE_TYPE_ACCESS),
    }


# ================= 跨用户设备同步 =================
def sync_device_across_users(device_id, updated_data, current_username=None):
    """将设备信息同步到所有拥有该设备的用户（从数据库获取用户列表）

    current_username: 当前操作者，跳过该用户（其信息已由调用方更新）。
    若为 None，则尝试从 Flask session 获取；获取失败时仍同步所有用户。
    """
    if current_username is None:
        try:
            current_username = get_current_user()
        except Exception:
            current_username = None  # 无 session 上下文（如后台任务），同步全部用户

    users = load_users()
    for username in users:
        # 跳过操作者自己（其信息已由调用方更新）
        if current_username and username == current_username:
            continue

        devs = load_devices(username)
        modified = False
        for d in devs:
            if d["id"] == device_id:
                d.update({
                    "name": updated_data.get("name", d["name"]),
                    "ip": updated_data.get("ip", d["ip"]),
                    "port": int(updated_data.get("port", d["port"])),
                    "username": updated_data.get("username", d["username"]),
                    "password": updated_data.get("password", d["password"]),
                    "area": updated_data.get("area", d.get("area", "A区")),
                    "note": updated_data.get("note", d.get("note", "")),
                    "device_type": updated_data.get("device_type", d.get("device_type", DEVICE_TYPE_ACCESS)),
                    "channel_count": int(updated_data.get("channel_count", d.get("channel_count", 0)) or 0),
                })
                modified = True
        if modified:
            save_devices(username, devs)


# ================= 本地人脸缓存 =================
def _local_face_exists(uid):
    """本地 faces 目录是否已有该用户照片。"""
    uid = str(uid)
    for ext in (".jpg", ".png", ".jpeg"):
        if os.path.exists(os.path.join(BASE, "faces", f"{uid}{ext}")):
            return True
    return False
