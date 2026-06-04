"""设备日志查询"""

import hashlib
import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from flask import Blueprint, Response, request

import access_log_cache

from routes._app import manager
from routes.helpers import (
    ok,
    err,
    get_current_user,
    extract_device,
    load_devices,
    _access_devices,
    require_permission,
)


bp = Blueprint("logs", __name__)


_RPC2_METHOD_MAP = {
    1: "刷卡",
    2: "密码",
    3: "卡+密码",
    4: "指纹",
    5: "远程开门",
    6: "按钮开门",
    15: "人脸识别",
    16: "人脸识别",
}
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_SNAPSHOT_PAGE_SIZE = 1000
_SNAPSHOT_MAX_SCAN = 80000

# 同设备 RPC2 扫描并发锁：防止多个请求同时对同一设备发起全量扫描
_device_rpc2_locks = {}
_device_rpc2_locks_guard = threading.Lock()


def _get_device_rpc2_lock(dev):
    """获取设备级别的 RPC2 扫描锁（懒创建）。"""
    key = str(dev.get("id") or dev.get("ip") or "unknown")
    with _device_rpc2_locks_guard:
        lock = _device_rpc2_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _device_rpc2_locks[key] = lock
        return lock


class _Rpc2Client:
    """Small RPC2 client used only for access-log snapshot lookup."""

    def __init__(self, dev, timeout=10):
        self.ip = dev["ip"]
        self.username = dev.get("username", "admin")
        self.password = dev.get("password", "")
        self.timeout = timeout
        self.session = None
        self._id = 100
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Connection": "close",
        })

    def login(self):
        url = f"http://{self.ip}/RPC2_Login"
        first = self.http.post(
            url,
            json={
                "method": "global.login",
                "params": {
                    "userName": self.username,
                    "password": "",
                    "clientType": "Web3.0",
                },
                "id": 1,
                "session": 0,
            },
            timeout=self.timeout,
        )
        first.raise_for_status()
        first_data = first.json()
        params = first_data.get("params") or {}
        realm = params["realm"]
        random_key = params["random"]
        session = first_data["session"]

        pwd_md5 = hashlib.md5(
            f"{self.username}:{realm}:{self.password}".encode()
        ).hexdigest().upper()
        login_hash = hashlib.md5(
            f"{self.username}:{random_key}:{pwd_md5}".encode()
        ).hexdigest().upper()

        second = self.http.post(
            url,
            json={
                "method": "global.login",
                "params": {
                    "userName": self.username,
                    "password": login_hash,
                    "clientType": "Web3.0",
                    "authorityType": "Default",
                    "passwordType": "Default",
                },
                "id": 2,
                "session": session,
            },
            timeout=self.timeout,
        )
        second.raise_for_status()
        data = second.json()
        if not data.get("result"):
            raise RuntimeError(data.get("error") or data)
        self.session = data["session"]

    def call(self, method, params=None, obj=None, timeout=None):
        if self.session is None:
            raise RuntimeError("RPC2 未登录")
        self._id += 1
        body = {
            "method": method,
            "params": params,
            "id": self._id,
            "session": self.session,
        }
        if obj is not None:
            body["object"] = obj
        resp = self.http.post(
            f"http://{self.ip}/RPC2",
            json=body,
            timeout=timeout or self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def close(self):
        if self.session is None:
            return
        try:
            self.call("global.logout", {})
        except Exception:
            pass
        finally:
            self.session = None

    def download_snapshot(self, path):
        path = _validate_snapshot_path(path)
        url = f"http://{self.ip}/RPC2_Loadfile{quote(path, safe='/')}?timestamp={int(time.time() * 1000)}"
        resp = self.http.get(url, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            raise RuntimeError("设备返回空图片")
        return resp.content, resp.headers.get("Content-Type", "")


def _parse_time_arg(value):
    try:
        return datetime.strptime(value, _TIME_FORMAT)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d")


def _normalize_text(value):
    return str(value or "").strip()


def _normalize_door(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rpc_time_to_text(value):
    if not isinstance(value, int):
        return ""
    try:
        return datetime.fromtimestamp(value).strftime(_TIME_FORMAT)
    except (OSError, OverflowError, ValueError):
        return ""


def _status_label(value):
    if value in (1, "1", True, "true", "True", "success", "成功"):
        return "成功"
    if value in (0, "0", False, "false", "False", "fail", "失败"):
        return "失败"
    return _normalize_text(value)


def _method_label(value):
    if isinstance(value, int):
        return _RPC2_METHOD_MAP.get(value, str(value))
    text = _normalize_text(value)
    if text.isdigit():
        return _RPC2_METHOD_MAP.get(int(text), text)
    return text


def _validate_snapshot_path(path):
    path = _normalize_text(path)
    if not path:
        raise ValueError("抓拍路径为空")
    if path.startswith(("http://", "https://")):
        raise ValueError("非法抓拍路径")
    if "\\" in path or "\x00" in path:
        raise ValueError("非法抓拍路径")
    if not path.startswith("/"):
        path = "/" + path
    parts = [p for p in path.split("/") if p]
    if ".." in parts:
        raise ValueError("非法抓拍路径")
    if not path.startswith("/SnapShotFilePath/"):
        raise ValueError("非法抓拍路径")
    if os.path.splitext(path)[1].lower() not in (".jpg", ".jpeg"):
        raise ValueError("不支持的抓拍文件类型")
    return path


def _extract_rpc_records(resp):
    params = resp.get("params")
    if not isinstance(params, dict):
        return []
    for key in ("records", "Records", "items", "Items"):
        records = params.get(key)
        if isinstance(records, list):
            return records
    return []


def _normalize_rpc_record(record):
    snapshot_path = record.get("URL") or record.get("Url") or record.get("url") or ""
    try:
        snapshot_path = _validate_snapshot_path(snapshot_path) if snapshot_path else ""
    except ValueError:
        snapshot_path = ""
    return {
        "time": _rpc_time_to_text(record.get("CreateTime") or record.get("CreateTimeRealUTC")),
        "user_id": _normalize_text(record.get("UserID")),
        "name": _normalize_text(record.get("CardName")),
        "door": _normalize_door(record.get("Door")),
        "method": _method_label(record.get("Method")),
        "status": _status_label(record.get("Status")),
        "path": snapshot_path,
    }


def _snapshot_file_url(device_id, path):
    return (
        "/api/log/snapshot/file?"
        f"device_id={quote(str(device_id))}&"
        f"path={quote(path, safe='')}&"
        f"timestamp={int(time.time() * 1000)}"
    )


def _sync_device_logs_to_cache(dev, start, end):
    """同步单台设备日志到本地缓存。

    以设备 SDK 开门记录为准写入 DB；RPC2 仅作为抓拍路径补充来源。
    """
    # 1. SDK 记录 → INSERT（门禁记录的唯一来源）
    records = []
    try:
        records = manager.get(dev).query_log(start, end)
    except Exception as exc:
        print(f"[access_log_cache] SDK 同步失败 {dev.get('name') or dev.get('ip')}: {exc}")
    if records:
        access_log_cache.upsert_records(dev, records, source="sdk")
    return records


def _fetch_and_upsert_snapshot_paths(dev, start, end):
    records = _fetch_rpc_records_for_range(dict(dev), start, end)
    if records:
        access_log_cache.upsert_records(dict(dev), records, source="rpc2")
    return records


def _today_snapshot_range_from_state(device_id, fetch_start, fetch_end):
    if fetch_start.date() != fetch_end.date() or fetch_end.date() != datetime.now().date():
        return None, fetch_start, fetch_end
    log_date = fetch_end.strftime("%Y-%m-%d")
    last_query_time = access_log_cache.get_snapshot_query_time(device_id, log_date)
    if last_query_time:
        try:
            last_dt = datetime.strptime(last_query_time, _TIME_FORMAT) + timedelta(seconds=1)
            fetch_start = max(fetch_start, last_dt)
        except ValueError:
            pass
    return log_date, fetch_start, fetch_end


def _snapshot_range_for_record(device_id, row_time):
    if row_time.date() != datetime.now().date():
        return None, row_time.replace(hour=0, minute=0, second=0, microsecond=0), row_time.replace(hour=23, minute=59, second=59, microsecond=0)
    fetch_start = row_time.replace(hour=0, minute=0, second=0, microsecond=0)
    fetch_end = datetime.now().replace(microsecond=0)
    return _today_snapshot_range_from_state(device_id, fetch_start, fetch_end)


def _sync_snapshot_paths_for_range(dev, start, end):
    if start > end:
        return []
    rpc2_lock = _get_device_rpc2_lock(dev)
    with rpc2_lock:
        return _fetch_and_upsert_snapshot_paths(dev, start, end)


def _merge_snapshot_paths_async(dev, start, end):
    """后台补抓拍路径，不影响开门记录列表返回。"""
    raw_id = dev.get("id")
    if raw_id in (None, ""):
        return
    start_text = start.strftime(_TIME_FORMAT)
    end_text = end.strftime(_TIME_FORMAT)
    earliest, latest = access_log_cache.get_missing_snapshot_time_range(start_text, end_text, raw_id)
    if not earliest or not latest:
        return
    try:
        fetch_start = datetime.strptime(earliest, _TIME_FORMAT)
        fetch_end = datetime.strptime(latest, _TIME_FORMAT)
    except ValueError:
        return

    state_log_date, fetch_start, fetch_end = _today_snapshot_range_from_state(raw_id, fetch_start, fetch_end)
    if fetch_start > fetch_end:
        return

    rpc2_lock = _get_device_rpc2_lock(dev)
    if not rpc2_lock.acquire(blocking=False):
        return  # 同设备已有扫描在进行，跳过

    def worker():
        try:
            _fetch_and_upsert_snapshot_paths(dev, fetch_start, fetch_end)
            if state_log_date:
                access_log_cache.set_snapshot_query_time(raw_id, state_log_date, fetch_end.strftime(_TIME_FORMAT))
        except Exception as exc:
            print(f"[access_log_cache] 抓拍路径后台同步失败 {dev.get('name') or dev.get('ip')}: {exc}")
        finally:
            rpc2_lock.release()

    threading.Thread(target=worker, daemon=True).start()


def _fetch_snapshot_path_for_record(dev, data):
    target_time = _normalize_text(data.get("time"))
    if not target_time:
        return None
    try:
        row_time = datetime.strptime(target_time, _TIME_FORMAT)
    except ValueError:
        return None
    raw_id = dev.get("id")
    if raw_id in (None, ""):
        return None

    log_date, fetch_start, fetch_end = _snapshot_range_for_record(raw_id, row_time)

    if fetch_start <= fetch_end:
        try:
            _sync_snapshot_paths_for_range(dev, fetch_start, fetch_end)
            if log_date:
                access_log_cache.set_snapshot_query_time(raw_id, log_date, fetch_end.strftime(_TIME_FORMAT))
        except Exception as exc:
            print(f"[access_log_cache] 抓拍路径同步失败 {dev.get('name') or dev.get('ip')}: {exc}")

    # 无论同步是否成功，都检查 DB（后台线程可能已补好）
    cached = access_log_cache.find_snapshot_path(raw_id, data)
    if cached:
        return cached
    return None


def _fetch_rpc_records_for_range(dev, start, end):
    """从设备 RPC2 查询指定时间段的通行记录。

    使用 RecordFinder.startFind 的 condition（小写 c）+ CreateTime ["<>", start, end]
    语法实现服务端日期过滤，避免全量扫描。若设备固件不支持该条件格式，
    仍由客户端做时间范围过滤。
    """
    records = []
    scanned = 0
    obj = None
    client = _Rpc2Client(dev, timeout=20)
    try:
        client.login()
        created = client.call("RecordFinder.factory.create", {"name": "AccessControlCardRec"})
        obj = created.get("result")
        if not isinstance(obj, int):
            raise RuntimeError(f"RecordFinder 创建失败: {created}")
        # 服务端日期过滤：condition（小写 c）+ ["<>", start_ts, end_ts]
        # 经验证大华固件仅识别小写 condition，大写 Condition 会被静默忽略
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        find_params = {
            "condition": {
                "CreateTime": ["<>", start_ts, end_ts],
                "Orders": [{"Field": "CreateTime", "Type": "Descent"}],
            }
        }
        client.call("RecordFinder.startFind", find_params, obj=obj)
        while scanned < _SNAPSHOT_MAX_SCAN:
            resp = client.call(
                "RecordFinder.doFind",
                {"offset": 0, "count": _SNAPSHOT_PAGE_SIZE},
                obj=obj,
                timeout=20,
            )
            page_records = _extract_rpc_records(resp)
            if not page_records:
                break
            scanned += len(page_records)
            passed_end = False
            for raw in page_records:
                item = _normalize_rpc_record(raw)
                if not item["time"]:
                    continue
                try:
                    item_time = datetime.strptime(item["time"], _TIME_FORMAT)
                except ValueError:
                    continue
                if start <= item_time <= end:
                    records.append(item)
                elif item_time > end:
                    passed_end = True
            if passed_end:
                break
        return records
    finally:
        if obj is not None:
            try:
                client.call("RecordFinder.destroy", None, obj=obj)
            except Exception:
                pass
        client.close()


def _sync_device_logs_incremental_async(dev, start, end):
    thread = threading.Thread(
        target=_sync_device_logs_incremental_then_merge_snapshots,
        args=(dict(dev), start, end),
        daemon=True,
    )
    thread.start()


def _sync_device_logs_incremental_then_merge_snapshots(dev, start, end):
    _sync_device_logs_incremental(dev, start, end)
    _merge_snapshot_paths_async(dev, start, end)


def _sync_device_logs_incremental(dev, start, end):
    raw_id = dev.get("id")
    if raw_id in (None, ""):
        return _sync_device_logs_to_cache(dev, start, end)
    latest = access_log_cache.get_latest_time(raw_id)
    sync_start = start
    if latest:
        try:
            latest_dt = datetime.strptime(latest, _TIME_FORMAT) + timedelta(seconds=1)
            sync_start = max(start, latest_dt)
        except ValueError:
            pass
    if sync_start > end:
        return []
    return _sync_device_logs_to_cache(dev, sync_start, end)


@bp.route("/api/log", methods=["GET", "POST"])
def log():
    try:
        require_permission("access.view_logs")
        data = request.get_json(silent=True) or {}
        dev = extract_device()
        start_str = data.get("start") or request.args.get("start") or None
        end_str = data.get("end") or request.args.get("end") or None
        if not start_str or not end_str:
            return err("缺少 start 或 end 参数")
        start = _parse_time_arg(start_str)
        try:
            end = datetime.strptime(end_str, _TIME_FORMAT)
        except ValueError:
            end = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

        start_text = start.strftime(_TIME_FORMAT)
        end_text = end.strftime(_TIME_FORMAT)
        raw_device_id = dev.get("id")
        refresh = str(data.get("refresh") or request.args.get("refresh") or "").lower() in ("1", "true", "yes")
        if not refresh and raw_device_id not in (None, "") and access_log_cache.has_records(start_text, end_text, raw_device_id):
            records = access_log_cache.query_records(start_text, end_text, raw_device_id)
            _sync_device_logs_incremental_async(dev, start, end)
            return ok({"total": len(records), "records": records, "cached": True})

        records = _sync_device_logs_to_cache(dev, start, end)
        _merge_snapshot_paths_async(dev, start, end)
        return ok({"total": len(records), "records": records, "cached": False})
    except Exception as e:
        return err(e, 500)


@bp.route("/api/log/all", methods=["GET", "POST"])
def log_all():
    """查询当前用户所有设备的日志：以设备 SDK 实时记录为准，写入本地库补抓拍路径。"""
    try:
        require_permission("access.view_logs")
        username = get_current_user()
        data = request.get_json(silent=True) or {}
        start_str = data.get("start") or request.args.get("start") or None
        end_str = data.get("end") or request.args.get("end") or None

        if not start_str or not end_str:
            return err("缺少 start 或 end 参数")

        start = _parse_time_arg(start_str)
        try:
            end = datetime.strptime(end_str, _TIME_FORMAT)
        except ValueError:
            end = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

        devs = _access_devices(load_devices(username))
        use_cache = str(data.get("cache") or request.args.get("cache") or "").lower() in ("1", "true", "yes")
        if use_cache:
            device_ids = set()
            for d in devs:
                raw_id = d.get("id")
                if raw_id in (None, ""):
                    continue
                try:
                    device_ids.add(int(raw_id))
                except (TypeError, ValueError):
                    continue
            # device_id → device_name 映射，补充缓存记录中可能为空的 device_name
            dev_name_map = {}
            for d in devs:
                raw_id = d.get("id")
                if raw_id not in (None, ""):
                    try:
                        dev_name_map[int(raw_id)] = d.get("name", "")
                    except (TypeError, ValueError):
                        pass
            cached_records = access_log_cache.query_records(
                start.strftime(_TIME_FORMAT),
                end.strftime(_TIME_FORMAT),
            )
            filtered_records = [r for r in cached_records if int(r.get("device_id") or -1) in device_ids]
            # 用最新设备名称补充缓存中可能为空的 device_name
            for r in filtered_records:
                did = int(r.get("device_id") or -1)
                name = dev_name_map.get(did, "")
                if name and not r.get("device_name"):
                    r["device_name"] = name
            filtered_records.sort(key=lambda x: x.get("time", ""), reverse=True)
            for dev in devs:
                _sync_device_logs_incremental_async(dev, start, end)
            return ok({"total": len(filtered_records), "records": filtered_records, "cached": True})

        all_records = []
        failed_devices = []
        for dev in devs:
            try:
                records = _sync_device_logs_to_cache(dev, start, end)
                _merge_snapshot_paths_async(dev, start, end)
                for r in records:
                    r["device_name"] = dev.get("name", "")
                    r["device_id"] = dev.get("id")
                all_records.extend(records)
            except Exception as exc:
                print(f"[log_all] 查询设备 {dev.get('name') or dev.get('ip')} 日志失败: {exc}")
                failed_devices.append({
                    "id": dev.get("id"),
                    "name": dev.get("name") or dev.get("ip") or "未知设备",
                    "ip": dev.get("ip"),
                    "error": str(exc),
                })
                continue

        filtered_records = []
        for r in all_records:
            try:
                record_time = datetime.strptime(r.get("time", ""), _TIME_FORMAT)
                if start <= record_time <= end:
                    filtered_records.append(r)
            except ValueError:
                continue
        filtered_records.sort(key=lambda x: x.get("time", ""), reverse=True)
        return ok({"total": len(filtered_records), "records": filtered_records, "cached": False, "failed_devices": failed_devices})
    except Exception as e:
        return err(str(e), 500)


@bp.route("/api/log/snapshot", methods=["POST"])
def log_snapshot():
    """按日志行查对应抓拍路径：优先查 access_logs.db，未命中再小范围补查设备。"""
    try:
        require_permission("access.view_logs")
        data = request.get_json(silent=True) or {}
        device_id = data.get("device_id")
        if device_id in (None, ""):
            return err("缺少 device_id")
        dev = extract_device()
        row_time = _normalize_text(data.get("time"))
        if not row_time:
            return err("缺少记录时间")

        cached_path = access_log_cache.find_snapshot_path(device_id, data)
        if cached_path:
            return ok({
                "ready": True,
                "has_image": True,
                "path": cached_path,
                "image_url": _snapshot_file_url(device_id, cached_path),
                "cached": True,
            })

        path = _fetch_snapshot_path_for_record(dev, data)
        if path:
            cached_path = access_log_cache.find_snapshot_path(device_id, data) or path
            return ok({
                "ready": True,
                "has_image": True,
                "path": cached_path,
                "image_url": _snapshot_file_url(device_id, cached_path),
                "cached": False,
            })
        return ok({
            "ready": True,
            "has_image": False,
            "message": "该通行记录没有抓拍图片",
        })
    except Exception as e:
        return err(e, 500)


@bp.route("/api/log/snapshot/file", methods=["GET"])
def log_snapshot_file():
    """代理设备后台 /RPC2_Loadfile 下的抓拍图片。"""
    try:
        require_permission("access.view_logs")
        dev = extract_device()
        path = _validate_snapshot_path(request.args.get("path", ""))
        client = _Rpc2Client(dev, timeout=10)
        try:
            client.login()
            content, _content_type = client.download_snapshot(path)
        finally:
            client.close()
        return Response(
            content,
            mimetype="image/jpeg",
            headers={"Cache-Control": "private, max-age=60"},
        )
    except Exception as e:
        return err(e, 500)
