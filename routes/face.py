"""人脸相关:上传、查询缓存、查询设备状态、批量拉取"""

import os
import shutil

from flask import Blueprint, Response, request

import db
from device_client import FaceCGIError

from routes._app import BASE, manager
from routes.helpers import (
    ok,
    err,
    log_audit,
    extract_device,
    get_current_user,
    load_devices,
    get_or_create_global_device_id,
    _access_devices,
    _local_face_exists,
    require_permission,
)


bp = Blueprint("face", __name__)


def _uid_visible_to_current_user(uid):
    username = get_current_user()
    device_ids = [d["id"] for d in _access_devices(load_devices(username))]
    persons = db.load_persons_for_devices(device_ids, search=str(uid), search_mode="exact")
    return any(str(p.get("user_id")) == str(uid) for p in persons)


def _require_visible_uid(uid):
    if not _uid_visible_to_current_user(uid):
        raise PermissionError("人员不存在或无权限")


def _sync_face_status_to_db(device_id, faces_map):
    """把设备 RPC 真实人脸状态写回本地 DB。

    person_devices 行不存在时返回 'person_not_found',静默跳过——
    这种情况是人员还没 import 到该设备,后续 import 会创建行。
    """
    for uid, has in faces_map.items():
        try:
            db.set_person_face(str(uid), device_id, bool(has))
        except Exception as e:
            print(f"[face-status] sync DB failed device={device_id} uid={uid}: {e}")


@bp.route("/api/face/<uid>", methods=["POST"])
def add_face(uid):
    try:
        require_permission("page.persons")
        _require_visible_uid(uid)
        if "file" not in request.files:
            return err("请选择人脸图片")
        dev = extract_device()
        did = get_or_create_global_device_id(dev["ip"], dev["port"])
        force = request.form.get("force")
        max_kb = int(request.form.get("max_kb") or 0)
        width = int(request.form.get("width") or 0)
        height = int(request.form.get("height") or 0)
        quality = int(request.form.get("quality") or 0)

        # 在锁内原子地检查并预占 has_face，避免并发重复下发
        pre_check = None
        path = os.path.join(BASE, f"_tmp_{uid}_{did}.jpg")
        try:
            if force != "1":
                pre_check = db.set_person_face(uid, did, True)
                if pre_check == "already_has_face":
                    return ok({"skipped": True})
                # pre_check == "updated" → 已原子设为 True
                # pre_check == "person_not_found" → 无 person_devices 行，下发后补写

            f = request.files["file"]
            f.save(path)
            manager.get(dev).add_face(uid, path, max_kb=max_kb, width=width, height=height, quality=quality, force=(force == "1"))

            # 缓存到本地，方便后续同步到其他设备
            face_dir = os.path.join(BASE, "faces")
            os.makedirs(face_dir, exist_ok=True)
            shutil.copy(path, os.path.join(face_dir, f"{uid}.jpg"))

            # force=1 时跳过了预占；person_not_found 时预占未生效 → 都需要补写 has_face
            if force == "1" or pre_check == "person_not_found":
                db.set_person_face(uid, did, True)
        except Exception:
            if force != "1" and pre_check == "updated":
                db.set_person_face(uid, did, False)
            raise
        finally:
            if os.path.exists(path):
                os.remove(path)

        log_audit("add_face", target=uid, detail={"device": f"{dev['ip']}:{dev['port']}", "force": force})
        return ok()
    except PermissionError as e:
        return err(str(e), 403)
    except FaceCGIError as e:
        return err(str(e), 500)
    except Exception as e:
        return err(e, 500)


@bp.route("/api/face/<uid>", methods=["PUT"])
def update_face(uid):
    """更新用户人脸照片（覆盖旧照片）"""
    try:
        require_permission("page.persons")
        _require_visible_uid(uid)
        if "file" not in request.files:
            return err("请选择人脸图片")
        dev = extract_device()
        did = get_or_create_global_device_id(dev["ip"], dev["port"])
        max_kb = int(request.form.get("max_kb") or 0)
        width = int(request.form.get("width") or 0)
        height = int(request.form.get("height") or 0)
        quality = int(request.form.get("quality") or 0)

        path = os.path.join(BASE, f"_tmp_{uid}_{did}.jpg")
        try:
            f = request.files["file"]
            f.save(path)
            manager.get(dev).update_face(uid, path, max_kb=max_kb, width=width, height=height, quality=quality)

            # 更新本地缓存
            face_dir = os.path.join(BASE, "faces")
            os.makedirs(face_dir, exist_ok=True)
            shutil.copy(path, os.path.join(face_dir, f"{uid}.jpg"))

            # 更新 DB 状态
            db.set_person_face(uid, did, True)
        finally:
            if os.path.exists(path):
                os.remove(path)

        log_audit("update_face", target=uid, detail={"device": f"{dev['ip']}:{dev['port']}"})
        return ok()
    except PermissionError as e:
        return err(str(e), 403)
    except FaceCGIError as e:
        return err(str(e), 500)
    except Exception as e:
        return err(e, 500)


@bp.route("/api/face/<uid>", methods=["GET"])
def get_cached_face(uid):
    """获取本地缓存的人脸照片"""
    try:
        require_permission("page.persons")
        _require_visible_uid(uid)
        cache_path = os.path.join(BASE, "faces", f"{uid}.jpg")
        if not os.path.exists(cache_path):
            return err("未找到人脸缓存", 404)
        with open(cache_path, "rb") as f:
            return Response(f.read(), mimetype="image/jpeg")
    except PermissionError as e:
        return err(str(e), 403)
    except Exception as e:
        return err(e, 500)


@bp.route("/api/face/<uid>/exists", methods=["GET"])
def face_exists(uid):
    """检查本地 faces 文件夹是否有此人照片"""
    try:
        require_permission("page.persons")
        _require_visible_uid(uid)
    except Exception as e:
        return err(str(e), 403)
    for ext in (".jpg", ".png", ".jpeg"):
        if os.path.exists(os.path.join(BASE, "faces", f"{uid}{ext}")):
            return ok({"exists": True})
    return ok({"exists": False})


@bp.route("/api/device/<int:device_id>/face-status", methods=["GET"])
def device_face_status(device_id):
    """RPC2 查询设备上全部用户的人脸状态"""
    try:
        require_permission("page.persons")
        username = get_current_user()
        devices = _access_devices(load_devices(username))
        dev_cfg = next((d for d in devices if d["id"] == device_id), None)
        if not dev_cfg:
            return err("设备不存在", 404)
        client = manager.get(dev_cfg)
        result = client.query_face_status()
        if result is None:
            # RPC2 失败，回退到本地缓存
            persons = db.load_persons_for_device(device_id)
            result = {}
            for p in persons:
                did_str = str(device_id)
                result[p["user_id"]] = bool(p.get("has_face", {}).get(did_str, False))
            return ok({"source": "local", "faces": result})
        _sync_face_status_to_db(device_id, result)
        return ok({"source": "device", "faces": result})
    except Exception as e:
        return err(str(e), 500)


@bp.route("/api/device/<int:device_id>/face-status", methods=["POST"])
def device_face_status_batch(device_id):
    """RPC2 查询指定用户列表的人脸状态"""
    try:
        require_permission("page.persons")
        data = request.get_json() or {}
        user_ids = data.get("user_ids", [])
        if not isinstance(user_ids, list) or not user_ids:
            return err("请提供 user_ids 列表")
        username = get_current_user()
        devices = _access_devices(load_devices(username))
        dev_cfg = next((d for d in devices if d["id"] == device_id), None)
        if not dev_cfg:
            return err("设备不存在", 404)
        client = manager.get(dev_cfg)
        result = client.query_face_status(user_ids)
        if result is None:
            # RPC2 失败，回退到本地缓存
            persons = db.load_persons_for_device(device_id)
            local_map = {}
            for p in persons:
                did_str = str(device_id)
                local_map[p["user_id"]] = bool(p.get("has_face", {}).get(did_str, False))
            result = {uid: local_map.get(uid, False) for uid in user_ids}
            return ok({"source": "local", "faces": result})
        _sync_face_status_to_db(device_id, result)
        return ok({"source": "device", "faces": result})
    except Exception as e:
        return err(str(e), 500)


@bp.route("/api/face/<uid>/device-status", methods=["GET"])
def person_face_device_status(uid):
    """查某人在各设备上的人脸状态（RPC2 → 本地回退）"""
    try:
        require_permission("page.persons")
        username = get_current_user()
        devices = _access_devices(load_devices(username))
        person = db.load_person(uid)
        result = {}
        for dev_cfg in devices:
            did = dev_cfg["id"]
            # 先查 RPC2
            client = manager.get(dev_cfg)
            device_result = client.query_face_status([uid])
            if device_result and uid in device_result:
                _sync_face_status_to_db(did, {uid: device_result[uid]})
                result[did] = {
                    "has_face": device_result[uid],
                    "source": "device",
                    "name": dev_cfg.get("name", ""),
                    "ip": dev_cfg["ip"]
                }
            else:
                # 回退到本地
                local_has = False
                if person:
                    local_has = bool(person.get("has_face", {}).get(str(did), False))
                result[did] = {
                    "has_face": local_has,
                    "source": "local",
                    "name": dev_cfg.get("name", ""),
                    "ip": dev_cfg["ip"]
                }
        return ok(result)
    except Exception as e:
        return err(str(e), 500)


@bp.route("/api/device/<int:device_id>/face-photos", methods=["POST"])
def device_fetch_face_photos(device_id):
    """RPC2 从设备拉取人脸照片并保存到本地 faces/ 目录。

    指定 user_ids 时，先过滤掉本地已有照片的 ID，只对本地缺失的 ID 逐个向设备获取。
    """
    try:
        require_permission("page.persons")
        username = get_current_user()
        devices = _access_devices(load_devices(username))
        dev_cfg = next((d for d in devices if d["id"] == device_id), None)
        if not dev_cfg:
            return err("设备不存在", 404)
        data = request.get_json() or {}
        user_ids = data.get("user_ids")  # None 表示拉全部
        force = data.get("force", False)  # 强制覆盖本地照片
        if user_ids is not None and (not isinstance(user_ids, list) or not user_ids):
            return err("user_ids 格式错误")

        requested_ids = [str(uid) for uid in user_ids] if user_ids else None
        fetch_ids = requested_ids
        skipped_existing = 0
        existing_status = {}
        if requested_ids and not force:
            fetch_ids = []
            for uid in requested_ids:
                if _local_face_exists(uid):
                    skipped_existing += 1
                    existing_status[uid] = True
                else:
                    fetch_ids.append(uid)
            print(
                f"[face-photos] device_id={device_id}, requested={len(requested_ids)}, "
                f"local_exists={skipped_existing}, missing={len(fetch_ids)}"
            )
            if not fetch_ids:
                return ok({
                    "total": len(requested_ids),
                    "requested": len(requested_ids),
                    "checked_missing": 0,
                    "skipped_existing": skipped_existing,
                    "saved": 0,
                    "failed": 0,
                    "status": existing_status
                })

        client = manager.get(dev_cfg)
        result = client.fetch_face_photos(fetch_ids)
        saved = sum(1 for v in result.values() if v)
        failed = [uid for uid, v in result.items() if not v]
        status = {**existing_status, **{uid: bool(v) for uid, v in result.items()}}
        # 本地已有照片 + 本次成功拉到照片的 uid,DB has_face 都必须是 True;
        # 拉取失败的 uid 不动 DB,避免把已经标记为 True 的状态误改成 False。
        synced_map = {uid: True for uid in existing_status}
        synced_map.update({uid: True for uid, v in result.items() if v})
        if synced_map:
            _sync_face_status_to_db(device_id, synced_map)
        print(
            f"[face-photos] device_id={device_id}, requested={len(requested_ids) if requested_ids else 'ALL'}, "
            f"checked_missing={len(fetch_ids) if fetch_ids else 'ALL'}, skipped_existing={skipped_existing}, "
            f"saved={saved}, failed={len(failed)}, failed_sample={failed[:20]}"
        )
        # 只返回是否成功，不暴露服务器路径
        return ok({
            "total": len(status) if requested_ids else len(result),
            "requested": len(requested_ids) if requested_ids else None,
            "checked_missing": len(fetch_ids) if fetch_ids else None,
            "skipped_existing": skipped_existing,
            "saved": saved,
            "failed": len(failed),
            "status": status
        })
    except Exception as e:
        return err(str(e), 500)
