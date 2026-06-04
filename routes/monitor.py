"""NVR 监控:设备列表、通道权限、录像回放记录查询"""

from datetime import datetime

from flask import Blueprint, request, session

import db
from device_client import DeviceClient

from routes._app import DEVICE_TYPE_NVR
from routes.helpers import (
    ok,
    err,
    audit_err,
    log_audit,
    get_current_user,
    load_users,
    load_devices,
    save_devices,
    device_payload,
    _nvr_devices,
    _probe_nvr,
    _check_device_online_cached,
    _get_cached_device_online,
    _get_cached_nvr_channel_states,
    _get_nvr_channel_states_cached,
    _find_nvr_device_by_id,
    _monitor_channel_allowed,
    require_permission,
    can_manage_user,
)


bp = Blueprint("monitor", __name__)


def _role_of(users, username):
    entry = users.get(username)
    return entry.get("role", "user") if isinstance(entry, dict) else "user"



@bp.route("/api/monitor/devices", methods=["GET"])
def get_monitor_devices():
    try:
        require_permission("page.monitor")
        username = get_current_user()
    except Exception as e:
        return err(str(e), 401)

    refresh = (request.args.get("refresh") or "").lower() in ("1", "true", "yes")
    devs = _nvr_devices(load_devices(username))
    permissions = db.load_user_channel_permissions(username)
    result = []
    for dev in devs:
        channels = db.load_nvr_channels(dev["id"])
        if refresh and channels and any(ch.get("channel_name") == f"通道 {int(ch.get('channel_no', 0)) + 1}" for ch in channels):
            try:
                _, channel_names = _probe_nvr(
                    dev["ip"],
                    int(dev.get("port", 37777)),
                    dev.get("username", "admin"),
                    dev.get("password", ""),
                )
                if channel_names:
                    db.ensure_nvr_channels(dev["id"], int(dev.get("channel_count") or len(channels)), channel_names)
                    channels = db.load_nvr_channels(dev["id"])
            except Exception as e:
                print(f"[monitor] 刷新通道名称失败 device_id={dev.get('id')}: {e}")
        if username != "admin":
            allowed = set(permissions.get(int(dev["id"]), []))
            channels = [ch for ch in channels if int(ch["channel_no"]) in allowed]
        if not channels:
            continue
        if refresh:
            dev["online"] = _check_device_online_cached(dev["ip"], int(dev.get("port", 37777)))
        else:
            dev["online"] = _get_cached_device_online(dev["ip"], int(dev.get("port", 37777)))

        # 仅显示真实接入摄像头的通道(NVR 在线时才查询；离线时保留原样)
        if dev["online"]:
            states = _get_nvr_channel_states_cached(dev) if refresh else _get_cached_nvr_channel_states(dev["id"])
            if states is not None:
                filtered = []
                for ch in channels:
                    st = states.get(int(ch["channel_no"]))
                    # st 是 dict {configured, online},None 表示未在 NVR 状态列表中
                    if st is not None and not st.get("configured", True):
                        continue
                    ch_out = dict(ch)
                    if st is not None:
                        ch_out["online"] = bool(st.get("online", False))
                    filtered.append(ch_out)
                channels = filtered
                if not channels:
                    continue

        item = device_payload(dev)
        item["channels"] = channels
        result.append(item)
    return ok(result)


@bp.route("/api/admin/users/<target_username>/channels", methods=["GET"])
def admin_get_user_channels(target_username):
    try:
        require_permission("page.admin")
    except Exception as e:
        return audit_err("admin_get_user_channels", str(e), 403, target=target_username)
    current = session.get("username")
    users = load_users()
    if target_username not in users:
        return audit_err("admin_get_user_channels", "目标用户不存在", 404, target=target_username)
    target_role = _role_of(users, target_username)
    if target_username != current and not can_manage_user(current, target_username, users):
        return audit_err("admin_get_user_channels", "无权操作该用户", 403, target=target_username)
    return ok(db.load_user_channel_permissions(target_username))


@bp.route("/api/admin/users/<target_username>/channels", methods=["POST"])
def admin_set_user_channels(target_username):
    try:
        admin_username = session.get("username")
        require_permission("page.admin")
    except Exception as e:
        return audit_err("admin_set_user_channels", str(e), 403, target=target_username)
    users = load_users()
    if target_username not in users:
        return audit_err("admin_set_user_channels", "目标用户不存在", 404, target=target_username)
    target_role = _role_of(users, target_username)
    if not can_manage_user(admin_username, target_username, users):
        return audit_err("admin_set_user_channels", "无权操作该用户", 403, target=target_username)

    data = request.get_json() or {}
    device_id = data.get("device_id")
    channel_nos = data.get("channel_nos", [])
    if device_id is None or not isinstance(channel_nos, list):
        return audit_err("admin_set_user_channels", "device_id 和 channel_nos 必填", target=target_username)
    try:
        did = int(device_id)
        channels = [int(ch) for ch in channel_nos]
    except (TypeError, ValueError):
        return audit_err("admin_set_user_channels", "通道参数错误", target=target_username)

    admin_dev = next((d for d in load_devices(admin_username) if int(d["id"]) == did and d.get("device_type") == DEVICE_TYPE_NVR), None)
    if not admin_dev:
        return audit_err("admin_set_user_channels", "录像机不存在或无权限", 404, target=target_username, detail={"device_id": did})
    target_devs = load_devices(target_username)
    if not any(int(d["id"]) == did for d in target_devs):
        target_devs.append(dict(admin_dev))
        save_devices(target_username, target_devs)

    # 非超级管理员只能在自己授权的通道范围内分配
    if admin_username != "admin":
        admin_allowed = set(db.load_user_channel_permissions(admin_username).get(did, []))
        channels = [c for c in channels if c in admin_allowed]

    saved = db.set_user_channel_permissions(target_username, did, channels)
    log_audit("admin_set_user_channels", target=target_username, detail={"device_id": did, "channels": saved})
    return ok({"device_id": did, "channel_nos": saved})


@bp.route("/api/playback/records", methods=["GET"])
def api_playback_records():
    """查询某通道在指定日期(或时间范围)的录像段列表"""
    try:
        require_permission("page.playback")
        username = get_current_user()
    except Exception as e:
        return err(str(e), 401)

    try:
        device_id = int(request.args.get("device_id") or 0)
        channel_no = int(request.args.get("channel_no") or 0)
    except ValueError:
        return err("参数错误", 400)

    date_str = (request.args.get("date") or "").strip()
    start_str = (request.args.get("start") or "").strip()
    end_str = (request.args.get("end") or "").strip()
    stream_type = (request.args.get("stream") or "main").lower()
    if stream_type not in ("main", "sub"):
        stream_type = "main"

    try:
        if date_str:
            day = datetime.strptime(date_str, "%Y-%m-%d")
            start_dt = day.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = day.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            if not start_str or not end_str:
                return err("需要提供 date 或 start/end", 400)
            start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        return err(f"时间格式错误: {e}", 400)

    device_info = _find_nvr_device_by_id(device_id, username)
    if not device_info:
        return err("录像机不存在或无权限", 404)
    if channel_no < 0 or channel_no >= int(device_info.get("channel_count") or 0):
        return err("通道不存在", 404)
    if not _monitor_channel_allowed(username, device_id, channel_no):
        return err("无通道权限", 403)

    client = None
    try:
        client = DeviceClient(
            device_info["ip"],
            int(device_info.get("port", 37777)),
            device_info.get("username", "admin"),
            device_info.get("password", ""),
        )
        records = client.query_records(channel_no, start_dt, end_dt, stream_type=stream_type)
        return ok({
            "records": records,
            "device_id": device_id,
            "channel_no": channel_no,
            "start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        return err(f"查询录像失败: {e}", 500)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
