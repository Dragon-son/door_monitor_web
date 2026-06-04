"""认证相关:注册、登录、登出、查询当前用户"""

from flask import Blueprint, request, session
from werkzeug.security import generate_password_hash, check_password_hash

import db

from routes.helpers import (
    ok,
    err,
    load_users,
    save_users,
    get_user_role,
    get_user_password_hash,
    get_user_permissions,
    log_audit,
)

bp = Blueprint("auth", __name__)


@bp.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return err("用户名和密码不能为空")
    users = load_users()
    if username in users:
        return err("用户名已存在")

    # 角色：仅允许管理员（通过 /api/admin/users 接口）创建 admin；
    # 普通注册一律为 user
    role = "user"

    users[username] = {
        "password": generate_password_hash(password),
        "role": role,
        "department": "",
        "name": "",
    }
    save_users(users)
    db.save_user_permissions(username, db.DEFAULT_USER_PERMISSIONS)

    log_audit("register", target=username, detail={"role": role})
    return ok({"message": "注册成功"})


@bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    users = load_users()
    if username not in users:
        log_audit("login", target=username, result="fail", error="用户名不存在")
        return err("用户名或密码错误", 401)

    pw_hash = get_user_password_hash(username)
    if not check_password_hash(pw_hash, password):
        log_audit("login", target=username, result="fail", error="密码错误")
        return err("用户名或密码错误", 401)

    role = get_user_role(username)

    # 设置服务器 session
    session["username"] = username
    session.permanent = True

    resp = ok({"username": username, "role": role, "permissions": sorted(get_user_permissions(username))})
    # 清理旧版本遗留的明文 auth cookie；认证只依赖 Flask session。
    resp.delete_cookie("auth", path="/")

    log_audit("login", target=username, detail={"role": role})
    return resp


@bp.route("/api/logout", methods=["POST"])
def logout():
    username = session.get("username", "unknown")
    log_audit("logout", target=username, username=username)
    session.clear()
    resp = ok()
    resp.delete_cookie("auth", path="/")
    return resp


@bp.route("/api/user", methods=["GET"])
def current_user():
    username = session.get("username")
    role = get_user_role(username) if username else None
    permissions = sorted(get_user_permissions(username)) if username else []
    return ok({"username": username, "role": role, "permissions": permissions})
