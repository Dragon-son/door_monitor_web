"""设备端用户管理:增删改、冻结、查询、搜索、分页列表"""

from flask import Blueprint, jsonify, request

from routes._app import manager
from routes.helpers import ok, err, log_audit, extract_device, require_permission


bp = Blueprint("users", __name__)


@bp.route("/api/user", methods=["POST"])
def add_user():
    try:
        require_permission("page.persons")
        dev = extract_device()
        data = request.get_json(silent=True) or {}
        user_id = (data.get("user_id") or "").strip()
        name = (data.get("name") or "").strip()
        if not user_id or not name:
            return err("缺少 user_id / name", 400)

        valid_begin = (data.get("valid_begin") or "").strip() or "2000-01-01"
        valid_end = (data.get("valid_end") or "").strip() or "2037-12-31"
        status = data.get("status")
        if status is not None:
            status = int(status)
        else:
            status = 0
        doors = data.get("doors")
        if doors is not None:
            if not isinstance(doors, list) or not doors:
                return err("doors 必须是非空数组", 400)
            doors = [int(item) for item in doors]
        else:
            doors = [0]

        manager.get(dev).add_user(user_id, name, valid_begin=valid_begin, valid_end=valid_end, status=status, doors=doors)
        log_audit("device_add_user", target=f"{user_id}@{dev['ip']}", detail={"name": name, "valid_begin": valid_begin, "valid_end": valid_end, "status": status, "doors": doors})
        return ok({"user_id": user_id, "name": name, "valid_begin": valid_begin, "valid_end": valid_end, "status": status, "doors": doors})
    except Exception as e:
        return err(e, 500)


@bp.route("/api/user/<uid>", methods=["PUT"])
def update_user(uid):
    try:
        require_permission("page.persons")
        dev = extract_device()
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return err("缺少 name", 400)

        doors = data.get("doors")
        if doors is not None:
            if not isinstance(doors, list) or not doors:
                return err("doors 必须是非空数组", 400)
            doors = [int(item) for item in doors]

        valid_begin = (data.get("valid_begin") or "").strip() or None
        valid_end = (data.get("valid_end") or "").strip() or None
        status = data.get("status")
        if status is not None:
            status = int(status)

        manager.get(dev).update_user(
            uid,
            name,
            status=0 if status is None else status,
            doors=doors,
            valid_begin=valid_begin,
            valid_end=valid_end,
        )
        log_audit("device_update_user", target=f"{uid}@{dev['ip']}", detail={"name": name, "doors": doors, "status": status})
        return ok({
            "user_id": uid,
            "name": name,
            "doors": doors,
            "valid_begin": valid_begin,
            "valid_end": valid_end,
            "status": 0 if status is None else status,
        })
    except Exception as e:
        log_audit("device_update_user", target=f"{uid}", result="fail", error=str(e))
        return err(e, 500)


@bp.route("/api/user/<uid>", methods=["DELETE"])
def del_user(uid):
    try:
        require_permission("page.persons")
        dev = extract_device()
        manager.get(dev).delete_user(uid)
        log_audit("device_delete_user", target=f"{uid}@{dev['ip']}")
        return ok()
    except Exception as e:
        return err(e, 500)


@bp.route("/api/user/<uid>/freeze", methods=["POST"])
def freeze_user(uid):
    try:
        require_permission("page.persons")
        dev = extract_device()
        manager.get(dev).freeze_user(uid)
        log_audit("device_freeze_user", target=f"{uid}@{dev['ip']}")
        return ok()
    except Exception as e:
        log_audit("device_freeze_user", target=f"{uid}", result="fail", error=str(e))
        return err(e, 500)


@bp.route("/api/user/<uid>/unfreeze", methods=["POST"])
def unfreeze_user(uid):
    try:
        require_permission("page.persons")
        dev = extract_device()
        manager.get(dev).unfreeze_user(uid)
        log_audit("device_unfreeze_user", target=f"{uid}@{dev['ip']}")
        return ok()
    except Exception as e:
        log_audit("device_unfreeze_user", target=f"{uid}", result="fail", error=str(e))
        return err(e, 500)


@bp.route("/api/user/<uid>/validity", methods=["PUT"])
def update_user_validity(uid):
    try:
        require_permission("page.persons")
        dev = extract_device()
        data = request.get_json(silent=True) or {}
        valid_begin = (data.get("valid_begin") or "").strip()
        valid_end = (data.get("valid_end") or "").strip()
        if not valid_begin or not valid_end:
            return err("缺少 valid_begin / valid_end", 400)
        manager.get(dev).update_user_validity(uid, valid_begin, valid_end)
        log_audit("device_update_validity", target=f"{uid}@{dev['ip']}", detail={"valid_begin": valid_begin, "valid_end": valid_end})
        return ok({"valid_begin": valid_begin, "valid_end": valid_end})
    except Exception as e:
        log_audit("device_update_validity", target=f"{uid}", result="fail", error=str(e))
        return err(e, 500)


@bp.route("/api/device/user/id/<uid>", methods=["GET", "POST"])
def get_device_user_by_id(uid):
    try:
        require_permission("page.persons")
        dev = extract_device()
        user = manager.get(dev).get_user_by_id(uid)
        if user:
            return ok(user)
        # 明确语义:设备已成功响应但未返回该用户。前端按 reason 判断,与网络/权限错误区分。
        return jsonify({"code": -1, "msg": "用户不存在", "reason": "not_found"}), 404
    except Exception as e:
        return err(str(e), 500)


@bp.route("/api/device/users/search", methods=["GET", "POST"])
def search_device_users():
    try:
        require_permission("page.persons")
        data = request.get_json(silent=True) or {}
        dev = extract_device()
        keyword = (data.get("keyword") or request.args.get("keyword", "")).strip()
        if not keyword:
            return err("缺少 keyword")
        users = manager.get(dev).search_users_by_name(keyword)
        return ok({"total": len(users), "items": users})
    except Exception as e:
        return err(str(e), 500)


@bp.route("/api/device/users/all", methods=["GET", "POST"])
def get_all_device_users():
    try:
        require_permission("page.persons")
        data = request.get_json(silent=True) or {}
        dev = extract_device()
        page = int(data.get("page", request.args.get("page", 1)))
        page_size = int(data.get("page_size", request.args.get("page_size", 20)))
        if page < 1:
            page = 1
        if page_size <= 0:
            page_size = 10000  # 获取全部
        offset = (page - 1) * page_size
        total, users = manager.get(dev).get_users_paginated(offset, page_size)
        return ok({"total": total, "page": page, "page_size": page_size, "items": users})
    except Exception as e:
        return err(str(e), 500)
