"""设备 CRUD + 区域 CRUD"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, request

import db

from routes._app import DEVICE_TYPE_ACCESS, DEVICE_TYPE_NVR
from routes.helpers import (
    ok,
    err,
    audit_err,
    log_audit,
    get_current_user,
    is_admin,
    load_devices,
    save_devices,
    load_areas,
    save_areas,
    load_users,
    load_device_map,
    save_device_map,
    get_or_create_global_device_id,
    device_payload,
    _normalize_device_type,
    _probe_nvr,
    _check_device_online_cached,
    _get_cached_device_online,
    _get_cached_nvr_channel_states,
    _get_nvr_channel_states_cached,
    sync_device_across_users,
    require_permission,
)


bp = Blueprint("devices", __name__)


def _apply_channel_permissions(item, device_id, permissions):
    if permissions is None or not item.get("channels"):
        return item
    allowed = set(permissions.get(int(device_id), []))
    item["channels"] = [ch for ch in item["channels"] if int(ch["channel_no"]) in allowed]
    return item


def _apply_nvr_channel_states(item, states):
    if states is None or not item.get("channels"):
        return item
    filtered = []
    for ch in item["channels"]:
        st = states.get(int(ch["channel_no"]))
        if st is not None and not st.get("configured", True):
            continue
        ch_out = dict(ch)
        if st is not None:
            ch_out["online"] = bool(st.get("online", False))
        filtered.append(ch_out)
    item["channels"] = filtered
    return item


def _device_payloads_for_user(username, devs, online_by_id=None, states_by_id=None,
                              use_cached_status=False):
    permissions = db.load_user_channel_permissions(username) if username != "admin" else None
    payloads = []
    for d in devs:
        item = device_payload(d)
        did = int(d["id"])
        if online_by_id is not None and did in online_by_id:
            item["online"] = online_by_id[did]
        elif use_cached_status:
            item["online"] = _get_cached_device_online(d["ip"], d.get("port", 37777))

        if d.get("device_type") == DEVICE_TYPE_NVR:
            _apply_channel_permissions(item, did, permissions)
            states = None
            if states_by_id is not None and did in states_by_id:
                states = states_by_id[did]
            elif use_cached_status:
                states = _get_cached_nvr_channel_states(did)
            _apply_nvr_channel_states(item, states)
        payloads.append(item)
    return payloads


@bp.route("/api/devices", methods=["GET"])
def get_devices():
    username = get_current_user()
    devs = load_devices(username)
    # 快速返回本地设备列表 + 已有缓存；在线探测由 /api/devices/status 后台刷新。
    return ok(_device_payloads_for_user(username, devs, use_cached_status=True))


@bp.route("/api/devices/status", methods=["GET"])
def refresh_device_status():
    username = get_current_user()
    devs = load_devices(username)
    if not devs:
        return ok([])

    online_by_id = {}
    with ThreadPoolExecutor(max_workers=max(1, min(len(devs), 20))) as ex:
        futures = {ex.submit(_check_device_online_cached, d["ip"], d.get("port", 37777)): d for d in devs}
        for f in as_completed(futures):
            d = futures[f]
            online_by_id[int(d["id"])] = f.result()

    states_by_id = {}
    nvr_devs = [
        d for d in devs
        if d.get("device_type") == DEVICE_TYPE_NVR and online_by_id.get(int(d["id"])) is True
    ]
    if nvr_devs:
        with ThreadPoolExecutor(max_workers=max(1, min(len(nvr_devs), 4))) as ex:
            futures = {ex.submit(_get_nvr_channel_states_cached, d): d for d in nvr_devs}
            for f in as_completed(futures):
                d = futures[f]
                states_by_id[int(d["id"])] = f.result()

    return ok(_device_payloads_for_user(
        username,
        devs,
        online_by_id=online_by_id,
        states_by_id=states_by_id,
    ))


@bp.route("/api/devices", methods=["POST"])
def add_device():
    username = get_current_user()
    try:
        require_permission("page.devices")
    except Exception as e:
        return audit_err("add_device", str(e), 403)
    if not is_admin(username):
        return audit_err("add_device", "无权限：仅管理员可添加设备", 403)
    data = request.get_json()
    if not data.get("name") or not data.get("ip"):
        return audit_err("add_device", "name 和 ip 必填")

    ip = data["ip"].strip()
    port = int(data.get("port", 37777))
    device_type = _normalize_device_type(data.get("device_type", DEVICE_TYPE_ACCESS))
    device_username = data.get("username", "admin")
    device_password = data.get("password", "")

    # 全局唯一ID
    dev_id = get_or_create_global_device_id(ip, port)

    devs = load_devices(username)

    # 该用户是否已添加此设备
    existing = next((d for d in devs if d["id"] == dev_id), None)
    effective_password = device_password or (existing["password"] if existing else "")
    channel_count = 0
    channel_names = {}
    if device_type == DEVICE_TYPE_NVR:
        try:
            channel_count, channel_names = _probe_nvr(ip, port, device_username, effective_password)
        except Exception as e:
            return audit_err("add_device", f"录像机登录失败：{e}", 400, target=data.get("name"), detail={"ip": ip, "port": port})
        if channel_count <= 0:
            return audit_err("add_device", "录像机未返回有效通道数", 400, target=data.get("name"), detail={"ip": ip, "port": port})
    if existing:
        if existing.get("device_type", DEVICE_TYPE_ACCESS) != device_type:
            return audit_err("add_device", "设备已存在且类型不同，请删除后重新添加", 400, target=data.get("name"), detail={"device_id": dev_id})
        existing.update({
            "name": data["name"],
            "username": device_username,
            "password": effective_password,
            "area": data.get("area", "A区"),
            "note": data.get("note", ""),
            "device_type": device_type,
            "channel_count": channel_count if device_type == DEVICE_TYPE_NVR else 0,
        })
        save_devices(username, devs)
        if device_type == DEVICE_TYPE_NVR:
            db.ensure_nvr_channels(dev_id, existing.get("channel_count", 0), channel_names)
            db.grant_all_channels(username, dev_id)
        # 同步给其他用户
        sync_device_across_users(dev_id, existing, current_username=username)
        return ok(device_payload(existing))

    new_dev = {
        "id": dev_id,
        "name": data["name"],
        "ip": ip,
        "port": port,
        "username": device_username,
        "password": effective_password,
        "area": data.get("area", "A区"),
        "note": data.get("note", ""),
        "device_type": device_type,
        "channel_count": channel_count if device_type == DEVICE_TYPE_NVR else 0,
    }
    devs.append(new_dev)
    save_devices(username, devs)
    if device_type == DEVICE_TYPE_NVR:
        db.ensure_nvr_channels(dev_id, channel_count, channel_names)
        db.grant_all_channels(username, dev_id)
    log_audit("add_device", target=data["name"], detail={"ip": ip, "port": port, "area": data.get("area", "A区"), "device_type": device_type, "channel_count": channel_count})
    return ok(device_payload(new_dev))


@bp.route("/api/devices/<int:device_id>", methods=["PUT"])
def update_device(device_id):
    username = get_current_user()
    try:
        require_permission("page.devices")
    except Exception as e:
        return audit_err("update_device", str(e), 403, target=str(device_id))
    if not is_admin(username):
        return audit_err("update_device", "无权限：仅管理员可修改设备", 403, target=str(device_id))
    data = request.get_json()
    devs = load_devices(username)
    for d in devs:
        if d["id"] == device_id:
            old_name = d["name"]
            old_ip = d["ip"]
            old_port = d["port"]
            device_type = d.get("device_type", DEVICE_TYPE_ACCESS)
            requested_type = _normalize_device_type(data.get("device_type", device_type))
            if requested_type != device_type:
                return audit_err("update_device", "设备类型不可修改，请删除后重新添加", 400, target=str(device_id))
            new_ip = data.get("ip", d["ip"])
            new_port = int(data.get("port", d["port"]))
            new_username = data.get("username", d["username"])
            new_password = data.get("password") or d["password"]
            channel_count = int(d.get("channel_count") or 0)
            channel_names = {}
            if device_type == DEVICE_TYPE_NVR and (
                old_ip != new_ip or old_port != new_port or new_username != d.get("username") or new_password != d.get("password")
            ):
                try:
                    channel_count, channel_names = _probe_nvr(new_ip, new_port, new_username, new_password)
                except Exception as e:
                    return audit_err("update_device", f"录像机登录失败:{e}", 400, target=str(device_id))
                if channel_count <= 0:
                    return audit_err("update_device", "录像机未返回有效通道数", 400, target=str(device_id))

            device_map = None
            old_key = None
            new_key = None
            if old_ip != new_ip or old_port != new_port:
                device_map = load_device_map()
                old_key = f"{old_ip}:{old_port}"
                new_key = f"{new_ip}:{new_port}"
                mapped_id = device_map.get(new_key)
                if mapped_id is not None and int(mapped_id) != int(device_id):
                    return audit_err(
                        "update_device",
                        f"目标 IP:端口 {new_key} 已被其他设备使用",
                        400,
                        target=str(device_id),
                        detail={"device_id": device_id, "endpoint": new_key, "mapped_device_id": mapped_id},
                    )

            d.update({
                "name": data.get("name", d["name"]),
                "ip": new_ip,
                "port": new_port,
                "username": new_username,
                "password": new_password,
                "area": data.get("area", d.get("area", "A区")),
                "note": data.get("note", d.get("note", "")),
                "device_type": device_type,
                "channel_count": channel_count if device_type == DEVICE_TYPE_NVR else 0,
            })
            save_devices(username, devs)
            if device_type == DEVICE_TYPE_NVR:
                # 仅在凭据/IP/端口变化时(上面 _probe_nvr 已跑)才有新 channel_names,
                # 否则传空 dict —— ensure_nvr_channels 对已存在的通道是 INSERT OR IGNORE,
                # 不会清掉旧名字,避免编辑名字/备注/区域时白白再登录一次 NVR。
                db.ensure_nvr_channels(device_id, channel_count, channel_names)
                db.grant_all_channels(username, device_id)

            # 如果 IP 或端口变化，更新 device_map
            if device_map is not None:
                # 删除旧映射，添加新映射（保持相同的 device_id）
                if old_key in device_map:
                    device_map.pop(old_key)
                device_map[new_key] = device_id
                save_device_map(device_map)

            # 同步给其他用户
            sync_device_across_users(device_id, d, current_username=username)
            log_audit("update_device", target=old_name, detail={"name": d["name"], "ip": d["ip"], "area": d.get("area"), "device_type": device_type, "channel_count": channel_count})
            return ok(device_payload(d))
    return audit_err("update_device", "设备不存在", 404, target=str(device_id))


@bp.route("/api/devices/<int:device_id>", methods=["DELETE"])
def delete_device(device_id):
    username = get_current_user()
    try:
        require_permission("page.devices")
    except Exception as e:
        return audit_err("delete_device", str(e), 403, target=str(device_id))
    if not is_admin(username):
        return audit_err("delete_device", "无权限：仅管理员可删除设备", 403, target=str(device_id))
    devs = load_devices(username)
    deleted_dev = next((d for d in devs if d["id"] == device_id), None)
    new_devs = [d for d in devs if d["id"] != device_id]
    if len(new_devs) == len(devs):
        return audit_err("delete_device", "设备不存在", 404, target=str(device_id))
    save_devices(username, new_devs)

    # 检查该设备是否还被其他用户使用
    device_still_in_use = False
    users = load_users()
    for other_username in users:
        if other_username == username:
            continue  # 跳过当前管理员
        other_devs = load_devices(other_username)
        if any(d["id"] == device_id for d in other_devs):
            device_still_in_use = True
            break

    # 只有当设备不再被任何用户使用时，才清理全局人员关联
    if not device_still_in_use:
        db.remove_device_from_all_persons(device_id)
        db.remove_nvr_device_metadata(device_id)
    else:
        db.remove_user_channel_permissions(username, device_id)

    log_audit("delete_device", target=deleted_dev["name"] if deleted_dev else str(device_id), detail={"device_id": device_id})
    return ok({"deleted_id": device_id})


# ================= 区域 =================
@bp.route("/api/areas", methods=["GET"])
def get_areas():
    username = get_current_user()
    return ok(load_areas(username))


@bp.route("/api/areas", methods=["POST"])
def add_area():
    username = get_current_user()
    try:
        require_permission("page.devices")
    except Exception as e:
        return audit_err("add_area", str(e), 403)
    name = request.json.get("name", "").strip()
    if not name:
        return err("区域名称不能为空")
    areas = load_areas(username)
    if name in areas:
        return err("区域已存在")
    areas.append(name)
    save_areas(username, areas)
    log_audit("add_area", target=name)
    return ok(areas)


@bp.route("/api/areas/<name>", methods=["DELETE"])
def delete_area(name):
    username = get_current_user()
    try:
        require_permission("page.devices")
    except Exception as e:
        return audit_err("delete_area", str(e), 403, target=name)
    areas = load_areas(username)
    if name not in areas:
        return err("区域不存在", 404)
    devs = load_devices(username)
    if any(d.get("area") == name for d in devs):
        return err("该区域下有设备，无法删除")
    areas.remove(name)
    save_areas(username, areas)
    log_audit("delete_area", target=name)
    return ok({"deleted": name})
