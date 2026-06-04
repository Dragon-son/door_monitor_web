"""管理员账户与设备分配管理"""

from flask import Blueprint, request, session
from werkzeug.security import generate_password_hash

import db

from routes._app import DEVICE_TYPE_NVR
from routes.helpers import (
    ok,
    err,
    audit_err,
    log_audit,
    load_users,
    save_users,
    load_devices,
    save_devices,
    require_permission,
    can_view_user,
    can_manage_user,
    admin_department_in_scope,
)


bp = Blueprint("admin", __name__)


def _role_of(users, username):
    entry = users.get(username)
    return entry.get("role", "user") if isinstance(entry, dict) else "user"


def _clean_user_metadata(data):
    return {
        "department": str(data.get("department", "") or "").strip(),
        "name": str(data.get("name", "") or "").strip(),
    }


def _ensure_user_entry(entry):
    if not isinstance(entry, dict):
        return {"password": entry, "role": "user", "department": "", "name": ""}
    entry.setdefault("department", "")
    entry.setdefault("name", "")
    return entry


def _is_super_admin(username):
    return username == "admin"


def _can_self_service_only(current, current_role, target_username):
    return not _is_super_admin(current) and target_username == current


def _requested_permissions(data, fallback):
    permissions = data.get("permissions", fallback)
    return permissions if isinstance(permissions, list) else []


def _permissions_for_role(role, permissions):
    normalized = set(db.normalize_user_permissions(permissions))
    return sorted(normalized)


def _bounded_permissions(current, requested, role="user"):
    if _is_super_admin(current):
        return _permissions_for_role(role, requested)
    allowed = set(db.load_user_permissions(current))
    return _permissions_for_role(role, set(requested) & allowed)


@bp.route("/api/admin/users", methods=["GET"])
def admin_list_users():
    try:
        require_permission("page.admin")
    except Exception as e:
        return err(str(e), 403)
    current = session.get("username")
    users = load_users()
    current_role = _role_of(users, current)
    all_scopes = db.load_all_admin_department_scopes()
    result = []
    for uname, entry in users.items():
        role = _role_of(users, uname)
        if not can_view_user(current, uname, users):
            continue
        entry = _ensure_user_entry(entry)
        result.append({
            "username": uname,
            "department": entry.get("department", ""),
            "name": entry.get("name", ""),
            "role": role,
            "department_scopes": None if uname == "admin" else all_scopes.get(uname, []),
            "permissions": db.load_user_permissions(uname),
            "device_count": len(load_devices(uname)),
        })
    return ok(result)


@bp.route("/api/admin/users", methods=["POST"])
def admin_create_user():
    try:
        require_permission("page.admin")
    except Exception as e:
        return audit_err("admin_create_user", str(e), 403)
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")
    if not username or not password:
        return audit_err("admin_create_user", "用户名和密码不能为空", target=username)
    if role not in ("admin", "user"):
        return audit_err("admin_create_user", "角色必须是 admin 或 user", target=username, detail={"role": role})
    # 只有超级管理员可以创建管理员账户
    current = session.get("username")
    users = load_users()
    current_role = _role_of(users, current)
    if current_role != "admin" and current != "admin":
        return audit_err("admin_create_user", "普通用户不能创建用户", 403, target=username)
    if role == "admin" and current != "admin":
        return audit_err("admin_create_user", "只有超级管理员可以创建管理员账户", 403, target=username, detail={"role": role})
    users = load_users()
    if username in users:
        return audit_err("admin_create_user", "用户名已存在", target=username)
    metadata = _clean_user_metadata(data)
    if current != "admin":
        if role == "admin":
            return audit_err("admin_create_user", "只有超级管理员可以创建管理员账户", 403, target=username, detail={"role": role})
        if not admin_department_in_scope(current, metadata["department"]):
            return audit_err("admin_create_user", "只能创建可管理部门内的用户", 403, target=username, detail={"department": metadata["department"]})
    department_scopes = []
    if current == "admin" and role == "admin":
        department_scopes = db.normalize_department_scopes(data.get("department_scopes") or [metadata["department"]])
    users[username] = {
        "password": generate_password_hash(password),
        "role": role,
        "department": metadata["department"],
        "name": metadata["name"],
    }
    save_users(users)
    if role == "admin":
        department_scopes = db.save_admin_department_scopes(username, department_scopes)
    else:
        db.delete_admin_department_scopes(username)
    requested = _requested_permissions(data, db.DEFAULT_USER_PERMISSIONS if role == "user" else db.ALL_PERMISSIONS)
    permissions = _permissions_for_role(role, db.ALL_PERMISSIONS) if role == "admin" else _bounded_permissions(current, requested, role)
    saved_permissions = db.save_user_permissions(username, permissions)
    log_audit("admin_create_user", target=username, detail={"role": role, "department": metadata["department"], "name": metadata["name"], "department_scopes": department_scopes, "permissions": saved_permissions})
    return ok({"username": username, "department": metadata["department"], "name": metadata["name"], "role": role, "department_scopes": department_scopes if role == "admin" else [], "permissions": saved_permissions})


@bp.route("/api/admin/users/<target_username>", methods=["PUT"])
def admin_update_user(target_username):
    try:
        require_permission("page.admin")
    except Exception as e:
        return audit_err("admin_update_user", str(e), 403, target=target_username)
    data = request.get_json(silent=True) or {}
    users = load_users()
    if target_username not in users:
        return audit_err("admin_update_user", "用户不存在", 404, target=target_username)
    current = session.get("username")
    entry = _ensure_user_entry(users[target_username])

    target_role_before = entry.get("role", "user")
    current_role = _role_of(users, current)
    self_service_only = _can_self_service_only(current, current_role, target_username)
    if self_service_only:
        forbidden_changes = [k for k in ("role", "permissions", "department", "department_scopes") if k in data]
        if forbidden_changes:
            return audit_err("admin_update_user", "只能修改自己的密码和资料", 403, target=target_username, detail={"forbidden_changes": forbidden_changes})
        has_password_change = "password" in data and bool(str(data.get("password", "")))
        has_metadata_change = any(k in data for k in ("department", "name"))
        if not has_password_change and not has_metadata_change:
            return audit_err("admin_update_user", "请输入新密码或修改资料", target=target_username)
    elif not can_manage_user(current, target_username, users):
        return audit_err("admin_update_user", "无权操作该用户", 403, target=target_username)

    if current != "admin" and "department_scopes" in data:
        return audit_err("admin_update_user", "只有超级管理员可以修改可管理部门", 403, target=target_username)

    if "role" in data:
        if data["role"] not in ("admin", "user"):
            return audit_err("admin_update_user", "角色必须是 admin 或 user", target=target_username, detail={"role": data.get("role")})
        # 只有超级管理员可以修改角色
        if current != "admin":
            return audit_err("admin_update_user", "只有超级管理员可以修改用户角色", 403, target=target_username, detail={"role": data.get("role")})
        # 超级管理员也不能降级自己（防误操作）
        if target_username == current and data["role"] == "user":
            return audit_err("admin_update_user", "不能降级自己的超级管理员角色", 403, target=target_username)
        entry["role"] = data["role"]
    if "password" in data and data["password"]:
        # 非超级管理员不能修改其他管理员的密码
        target_role = entry.get("role", "user")
        if current != "admin" and target_role == "admin" and target_username != current:
            return audit_err("admin_update_user", "只有超级管理员可以修改其他管理员的密码", 403, target=target_username)
        entry["password"] = generate_password_hash(data["password"])
    for field in ("department", "name"):
        if field in data:
            entry[field] = str(data.get(field, "") or "").strip()
    if current != "admin" and target_username != current:
        if not admin_department_in_scope(current, entry.get("department", "")):
            return audit_err("admin_update_user", "只能设置可管理部门内的用户部门", 403, target=target_username, detail={"department": entry.get("department", "")})
    users[target_username] = entry
    save_users(users)
    target_role_after = entry.get("role", "user")
    department_scopes = db.load_admin_department_scopes(target_username) if target_role_after == "admin" else []
    if current == "admin" and "department_scopes" in data:
        if target_role_after != "admin":
            department_scopes = []
            db.delete_admin_department_scopes(target_username)
        else:
            fallback_scope = [entry.get("department", "")] if entry.get("department", "") else []
            department_scopes = db.save_admin_department_scopes(target_username, data.get("department_scopes") or fallback_scope)
    elif target_role_after != "admin":
        db.delete_admin_department_scopes(target_username)

    saved_permissions = db.load_user_permissions(target_username)
    permissions_before = list(saved_permissions)
    permissions_changed = False
    if "permissions" in data:
        if current != "admin" and entry.get("role", "user") == "admin" and target_username != current:
            return audit_err("admin_update_user", "只有超级管理员可以修改其他管理员的权限", 403, target=target_username)
        if target_username != "admin":
            requested_permissions = data.get("permissions") if isinstance(data.get("permissions"), list) else []
            if target_username == current and "page.admin" not in requested_permissions:
                return audit_err("admin_update_user", "不能移除自己的用户管理权限", 403, target=target_username)
            target_role = entry.get("role", "user")
            if current == "admin" and target_username != current:
                # 超级管理员修改其他管理员：尊重请求的权限集，但普通用户不能拥有用户管理页
                bounded_permissions = _permissions_for_role(target_role, requested_permissions)
            elif target_role == "admin":
                # 非超管编辑自己（admin 角色）：不能提升到自己没有的权限
                bounded_permissions = _bounded_permissions(current, requested_permissions, target_role)
            else:
                bounded_permissions = _bounded_permissions(current, requested_permissions, target_role)
            saved_permissions = db.save_user_permissions(target_username, bounded_permissions)
            permissions_changed = sorted(permissions_before) != sorted(saved_permissions)
            if permissions_changed:
                before_set = set(permissions_before)
                after_set = set(saved_permissions)
                added = sorted(after_set - before_set)
                removed = sorted(before_set - after_set)
                permission_detail = {
                    "added": added,
                    "added_labels": [db.PERMISSION_LABELS.get(p, p) for p in added],
                    "removed": removed,
                    "removed_labels": [db.PERMISSION_LABELS.get(p, p) for p in removed],
                    "role": entry.get("role", "user"),
                }
                log_audit("admin_update_user_permissions", target=target_username, detail=permission_detail)
    changes = [k for k in data if k in ("role", "password", "permissions", "department", "name", "department_scopes")]
    update_detail = {"changes": changes}
    if permissions_changed:
        update_detail["permissions"] = permission_detail
    log_audit("admin_update_user", target=target_username, detail=update_detail)
    return ok({
        "username": target_username,
        "department": entry.get("department", ""),
        "name": entry.get("name", ""),
        "role": entry["role"],
        "department_scopes": department_scopes if entry["role"] == "admin" else [],
        "permissions": saved_permissions,
    })


@bp.route("/api/admin/users/<target_username>", methods=["DELETE"])
def admin_delete_user(target_username):
    try:
        require_permission("page.admin")
    except Exception as e:
        return audit_err("admin_delete_user", str(e), 403, target=target_username)
    current = session.get("username")
    if target_username == current:
        return audit_err("admin_delete_user", "不能删除自己", target=target_username)
    if target_username == "admin":
        return audit_err("admin_delete_user", "系统内置管理员 admin 不可被删除", 403, target=target_username)
    users = load_users()
    if target_username not in users:
        return audit_err("admin_delete_user", "用户不存在", 404, target=target_username)
    target_role = _role_of(users, target_username)
    if not can_manage_user(current, target_username, users):
        return audit_err("admin_delete_user", "无权操作该用户", 403, target=target_username)

    # 删除前收集审计详情
    target_devs = load_devices(target_username)
    detail = {
        "device_count": len(target_devs),
        "device_ids": [d["id"] for d in target_devs],
    }

    try:
        db.delete_user(target_username)
    except Exception as e:
        return audit_err("admin_delete_user", f"删除失败：{str(e) or '数据库操作异常'}", 500, target=target_username, detail=detail)

    log_audit("admin_delete_user", target=target_username, detail=detail)
    return ok()


@bp.route("/api/admin/users/<target_username>/devices", methods=["POST"])
def admin_assign_devices(target_username):
    try:
        admin_username = session.get("username")
        require_permission("page.admin")
    except Exception as e:
        return audit_err("admin_assign_devices", str(e), 403, target=target_username)
    users = load_users()
    if target_username not in users:
        return audit_err("admin_assign_devices", "目标用户不存在", 404, target=target_username)
    # 非超级管理员不能为其他管理员分配设备
    target_entry = users[target_username]
    target_role = target_entry.get("role", "user") if isinstance(target_entry, dict) else "user"
    if not can_manage_user(admin_username, target_username, users):
        return audit_err("admin_assign_devices", "无权操作该用户", 403, target=target_username)
    data = request.get_json(silent=True) or {}
    device_ids = data.get("device_ids", [])
    if not isinstance(device_ids, list):
        return audit_err("admin_assign_devices", "device_ids 必须是数组", target=target_username)
    raw_channel_permissions = data.get("channel_permissions", {})
    if not isinstance(raw_channel_permissions, dict):
        return audit_err("admin_assign_devices", "channel_permissions 必须是对象", target=target_username)

    admin_devs = load_devices(admin_username)
    target_devs = load_devices(target_username)
    target_index = {d["id"]: d for d in target_devs}

    assigned = []
    for did in device_ids:
        try:
            did = int(did)
        except (TypeError, ValueError):
            continue
        src = next((d for d in admin_devs if d["id"] == did), None)
        if not src:
            continue
        if did not in target_index:
            target_devs.append(dict(src))
            assigned.append(did)
        else:
            target_index[did].update({k: src[k] for k in ('name', 'ip', 'port', 'username', 'password', 'area', 'note', 'device_type', 'channel_count') if k in src})
            assigned.append(did)

    save_devices(target_username, target_devs)
    # 非超级管理员只能在自己授权的通道范围内分配；超级管理员不受限。
    admin_channel_permissions = db.load_user_channel_permissions(admin_username) if admin_username != "admin" else None
    assigned_channels = {}
    for did in assigned:
        src = next((d for d in admin_devs if int(d["id"]) == int(did)), None)
        if not src or src.get("device_type") != DEVICE_TYPE_NVR:
            continue
        admin_allowed = set(admin_channel_permissions.get(int(did), [])) if admin_channel_permissions is not None else None
        raw_channels = raw_channel_permissions.get(str(did), raw_channel_permissions.get(did))
        if raw_channels is None:
            # 未显式传通道 → 默认授予"当前管理员能给的全部通道"
            if admin_allowed is None:
                saved = db.grant_all_channels(target_username, did)
            else:
                saved = db.set_user_channel_permissions(target_username, did, sorted(admin_allowed))
        elif isinstance(raw_channels, list):
            try:
                channel_nums = [int(ch) for ch in raw_channels]
                if admin_allowed is not None:
                    channel_nums = [c for c in channel_nums if c in admin_allowed]
                saved = db.set_user_channel_permissions(target_username, did, channel_nums)
            except (TypeError, ValueError):
                saved = []
        else:
            saved = []
        assigned_channels[str(did)] = saved

    # 根据已分配设备的区域同步用户区域列表
    existing_areas = set(db.load_areas(target_username))
    new_areas = {d["area"] for d in target_devs if d.get("area")}
    areas_to_add = new_areas - existing_areas
    if areas_to_add:
        merged = list(existing_areas) + sorted(areas_to_add)
        db.save_areas(target_username, merged)

    log_audit("admin_assign_devices", target=target_username, detail={"assigned_ids": assigned, "channel_permissions": assigned_channels})
    return ok({"assigned": assigned, "channel_permissions": assigned_channels})


@bp.route("/api/admin/users/<target_username>/devices", methods=["GET"])
def admin_get_user_devices(target_username):
    """获取某个用户拥有的设备ID列表"""
    try:
        require_permission("page.admin")
    except Exception as e:
        return audit_err("admin_get_user_devices", str(e), 403, target=target_username)
    current = session.get("username")
    users = load_users()
    if target_username not in users:
        return audit_err("admin_get_user_devices", "目标用户不存在", 404, target=target_username)
    target_role = _role_of(users, target_username)
    current_role = _role_of(users, current)
    if target_username != current and not can_manage_user(current, target_username, users):
        return audit_err("admin_get_user_devices", "无权操作该用户", 403, target=target_username)
    if target_username == current:
        devs = load_devices(target_username)
        return ok([d["id"] for d in devs])
    devs = load_devices(target_username)
    return ok([d["id"] for d in devs])


@bp.route("/api/admin/users/<target_username>/devices/<int:device_id>", methods=["DELETE"])
def admin_remove_user_device(target_username, device_id):
    try:
        require_permission("page.admin")
        current = session.get("username")
    except Exception as e:
        return audit_err("admin_remove_user_device", str(e), 403, target=target_username, detail={"device_id": device_id})
    users = load_users()
    if target_username not in users:
        return audit_err("admin_remove_user_device", "目标用户不存在", 404, target=target_username, detail={"device_id": device_id})
    # 非超级管理员不能移除其他管理员的设备
    target_role = _role_of(users, target_username)
    if not can_manage_user(current, target_username, users):
        return audit_err("admin_remove_user_device", "无权操作该用户", 403, target=target_username, detail={"device_id": device_id})
    devs = load_devices(target_username)
    new_devs = [d for d in devs if d["id"] != device_id]
    if len(new_devs) == len(devs):
        return audit_err("admin_remove_user_device", "该用户没有此设备", 404, target=target_username, detail={"device_id": device_id})
    save_devices(target_username, new_devs)
    db.remove_user_channel_permissions(target_username, device_id)

    # 清理没有设备的区域
    used_areas = {d["area"] for d in new_devs if d.get("area")}
    all_areas = db.load_areas(target_username)
    remaining_areas = [a for a in all_areas if a in used_areas]
    db.save_areas(target_username, remaining_areas)

    # 检查该设备是否还被其他用户使用
    device_still_in_use = False
    for other_username in users:
        if other_username == target_username:
            continue  # 跳过当前被解绑的用户
        other_devs = load_devices(other_username)
        if any(d["id"] == device_id for d in other_devs):
            device_still_in_use = True
            break

    # 只有当设备不再被任何用户使用时，才清理全局人员关联
    if not device_still_in_use:
        db.remove_device_from_all_persons(device_id)
        db.remove_nvr_device_metadata(device_id)

    log_audit("admin_remove_user_device", target=target_username, detail={"device_id": device_id})
    return ok({"removed": device_id})
