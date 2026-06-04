"""开门 / 门状态 / 门模式"""

from flask import Blueprint, request

from routes._app import manager
from routes.helpers import ok, err, log_audit, extract_device, require_permission


bp = Blueprint("door", __name__)


@bp.route("/api/open", methods=["POST"])
def open_door():
    try:
        require_permission("access.open_door")
        dev = extract_device()
        channel = int((request.get_json() or {}).get("channel", 0))
        manager.get(dev).open_door(channel)
        log_audit("open_door", target=f"{dev['ip']}:{dev['port']}", detail={"channel": channel})
        return ok({"channel": channel})
    except Exception as e:
        log_audit("open_door", result="fail", error=str(e))
        return err(e, 500)


@bp.route("/api/door/status", methods=["GET", "POST"])
def door_status():
    try:
        require_permission("page.access")
        data = request.get_json(silent=True) or {}
        dev = extract_device()
        channel = int(data.get("channel", request.args.get("channel", 1)))
        if channel < 1:
            channel = 1
        # source 选择信号源: 'rpc2'(默认,读门磁) / 'cgi'(旧,读电锁通断电)
        source = (data.get("source") or request.args.get("source") or "rpc2").lower()
        client = manager.get(dev)
        if source == "cgi":
            status = client.get_door_status(channel)
        else:
            # RPC2 通道从 0 起;API 入参为 1 起的门号,做一次转换
            status = client.get_door_status_rpc(max(channel - 1, 0))
        if status is None:
            return err("查询门状态失败", 500)
        is_open = status.lower() == "open"
        return ok({"status": status, "is_open": is_open, "source": source})
    except Exception as e:
        return err(str(e), 500)


@bp.route("/api/door/mode", methods=["POST"])
def door_mode():
    try:
        require_permission("access.set_door_mode")
        dev = extract_device()
        mode = (request.get_json() or {}).get("mode", "")
        if mode not in ("Normal", "AlwaysOpen", "AlwaysClose"):
            return err("无效的门模式，仅支持 Normal/AlwaysOpen/AlwaysClose")
        ok_result = manager.get(dev).set_door_mode(mode)
        if not ok_result:
            return err("设置门模式失败，设备可能不支持该模式", 500)
        log_audit("door_mode", target=f"{dev['ip']}:{dev['port']}", detail={"mode": mode})
        return ok({"mode": mode})
    except Exception as e:
        log_audit("door_mode", result="fail", error=str(e))
        return err(str(e), 500)


@bp.route("/api/door/mode/query", methods=["POST"])
def door_mode_query():
    """查询设备当前门模式"""
    try:
        require_permission("page.access")
        dev = extract_device()
        cfg = manager.get(dev).get_door_mode_config()
        if cfg is None:
            return err("查询门模式失败", 500)
        # 从配置表中提取门模式
        mode = "Normal"
        door_mode = cfg.get("DoorMode", "")
        state = cfg.get("State", "")
        always_open = cfg.get("AlwaysOpen", "")
        if door_mode == "AlwaysOpen" or state == "OpenAlways" or always_open is True:
            mode = "AlwaysOpen"
        elif door_mode == "AlwaysClose" or state == "CloseAlways":
            mode = "AlwaysClose"
        return ok({"mode": mode, "config": cfg})
    except Exception as e:
        return err(str(e), 500)
