"""审计日志查询"""

import json
import re
from datetime import datetime, timedelta

from flask import Blueprint, request, session

import db

from routes.helpers import ok, err, get_current_user, load_devices, require_permission


bp = Blueprint("audit", __name__)


_NON_ADMIN_AUDIT_SCAN_LIMIT = 5000
_AUDIT_DEVICE_ID_RE = re.compile(r"(?:device_id=|device_)(\d+)")
_AUDIT_ENDPOINT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?")


def _parse_audit_detail(detail):
    if not detail:
        return None
    if isinstance(detail, dict):
        return detail
    try:
        parsed = json.loads(detail)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def _audit_row_device_refs(row):
    """从一条审计日志中提取可能的设备引用: ids/endpoints。"""
    ids = set()
    endpoints = set()

    def add_id(value):
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add_id(item)
            return
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            pass

    def add_endpoint(value):
        if not value:
            return
        text = str(value)
        match = _AUDIT_ENDPOINT_RE.search(text)
        if not match:
            return
        ip = match.group(1)
        port = int(match.group(2) or 37777)
        endpoints.add((ip, port))

    target = str(row.get("target") or "")
    for match in _AUDIT_DEVICE_ID_RE.finditer(target):
        add_id(match.group(1))
    add_endpoint(target)

    detail = _parse_audit_detail(row.get("detail"))
    if detail:
        for key in ("device_id", "removed", "removed_device"):
            add_id(detail.get(key))
        for key in ("device_ids", "assigned_ids", "doors"):
            add_id(detail.get(key))
        for key in ("device", "endpoint"):
            add_endpoint(detail.get(key))
        ip = detail.get("ip")
        if ip:
            try:
                port = int(detail.get("port") or 37777)
            except (TypeError, ValueError):
                port = 37777
            endpoints.add((str(ip), port))

    return ids, endpoints


def _audit_row_visible_for_devices(row, allowed_device_ids, allowed_endpoints):
    ids, endpoints = _audit_row_device_refs(row)
    if ids or endpoints:
        return bool(ids & allowed_device_ids or endpoints & allowed_endpoints)
    # 没有设备引用的日志只允许看自己的操作，避免管理员互相看到纯账号/系统类日志。
    return row.get("username") == session.get("username")


def _query_audit_logs_for_user(viewer_username, page, page_size, action, target_user, start_time, end_time, end_exclusive):
    offset = (page - 1) * page_size
    if viewer_username == "admin":
        rows, total = db.query_audit_logs(
            limit=page_size,
            offset=offset,
            action=action,
            username=target_user,
            start_time=start_time,
            end_time=end_time,
            end_exclusive=end_exclusive,
        )
        return rows, total, False

    user_devices = load_devices(viewer_username)
    allowed_device_ids = {int(d["id"]) for d in user_devices}
    allowed_endpoints = {
        (str(d["ip"]), int(d.get("port", 37777)))
        for d in user_devices
        if d.get("ip")
    }

    candidate_rows, candidate_total = db.query_audit_logs_rows(
        limit=_NON_ADMIN_AUDIT_SCAN_LIMIT,
        offset=0,
        action=action,
        username=target_user,
        start_time=start_time,
        end_time=end_time,
        end_exclusive=end_exclusive,
    )
    visible_rows = [
        row for row in candidate_rows
        if _audit_row_visible_for_devices(row, allowed_device_ids, allowed_endpoints)
    ]
    limited = candidate_total > len(candidate_rows)
    return visible_rows[offset:offset + page_size], len(visible_rows), limited


@bp.route("/api/audit_logs", methods=["GET"])
def get_audit_logs():
    """分页查询审计日志，支持筛选"""
    try:
        require_permission("page.audit")
        username = get_current_user()
    except Exception as e:
        return err(str(e), 401)

    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 50, type=int)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 50
    if page_size > 200:
        page_size = 200

    action = request.args.get("action") or None
    target_user = request.args.get("username") or None
    start_time = request.args.get("start_time") or None
    end_time = request.args.get("end_time") or None
    end_exclusive = False
    if end_time and end_time.endswith("T23:59:59"):
        try:
            end_time = (datetime.fromisoformat(end_time) + timedelta(seconds=1)).isoformat()
            end_exclusive = True
        except ValueError:
            pass

    rows, total, limited = _query_audit_logs_for_user(
        username,
        page,
        page_size,
        action=action,
        target_user=target_user,
        start_time=start_time,
        end_time=end_time,
        end_exclusive=end_exclusive,
    )
    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": rows,
        "limited": limited,
        "scan_limit": _NON_ADMIN_AUDIT_SCAN_LIMIT if limited else None,
    })
