"""WebSocket 视频/音频预览、NVR 监控预览、录像回放。

四个 sock 路由共享 ``preview_state._preview_sessions``。本模块只负责
WebSocket 握手、客户端会话注册/移除,以及把控制信令转发给 SDK。
真正的会话构造/销毁全在 preview_state 里。

模块被 ``server.py`` 用 `import` 一次以触发 `@sock.route` 装饰器注册。

发送线程安全:
    本模块以及 preview_state 里所有发往 ws 的数据都必须走 `_safe_send`。
    simple_websocket.send() 内部完全没有锁,广播线程跟主路由线程并发 send
    会损坏 PerMessageDeflate 压缩状态 + 帧字节交错,浏览器表现为
    "Invalid frame header" 与 WebCodecs "Decoding error"。
    每个 handler 在最外层用 try/finally 调用 `_drop_ws_lock(ws)` 清掉锁条目。
"""

import json
from datetime import datetime

from flask import request, session

from routes._app import sock
from routes.helpers import (
    _find_access_device_by_id,
    _find_nvr_device_by_id,
    _monitor_channel_allowed,
    require_permission,
)
from routes.preview_state import (
    _preview_sessions,
    _preview_lock,
    _preview_key,
    _monitor_preview_key,
    _playback_key,
    _next_playback_client_id,
    _create_preview_session,
    _create_monitor_preview_session,
    _create_playback_session,
    _ensure_video_ffmpeg,
    _stop_preview_session,
    _safe_send,
    _drop_ws_lock,
)


@sock.route("/ws/preview/<int:device_id>")
def ws_preview(ws, device_id):
    """
    WebSocket 视频预览端点。
    每个设备只启动一个 SDK 预览 + 两个 ffmpeg 进程，
    多个客户端连接同一设备时共享同一路码流。

    查询参数 codec:
      - 不传 / mpeg1 : 兼容 jsmpg，服务端推 mpeg1video（经过 ffmpeg 转码）
      - h264         : 直通 NAL 给 WebCodecs 客户端,不经 ffmpeg(实际 codec 由服务端嗅探)
    """
    try:
        # 从 cookie 验证登录（WebSocket 握手携带 cookie）
        username = session.get("username")
        if not username:
            _safe_send(ws, json.dumps({"type": "error", "msg": "未登录"}))
            return

        try:
            require_permission("access.preview")
        except Exception as e:
            _safe_send(ws, json.dumps({"type": "error", "msg": str(e) or "无实时预览权限"}))
            return

        # 查找设备信息（遍历所有用户，只要 device_id 匹配即可）
        device_info = _find_access_device_by_id(device_id, username)
        if not device_info:
            _safe_send(ws, json.dumps({"type": "error", "msg": "设备不存在或无权限"}))
            return

        codec = (request.args.get("codec") or "mpeg1").lower()
        use_webcodecs = (codec == "h264")
        key = _preview_key(device_id)

        with _preview_lock:
            if key not in _preview_sessions:
                # 第一个连接：创建预览会话
                try:
                    session_obj = _create_preview_session(device_id, device_info)
                    _preview_sessions[key] = session_obj
                    print(f"[ws_preview] 新建预览会话 device_id={device_id}")
                except Exception as e:
                    _safe_send(ws, json.dumps({"type": "error", "msg": f"启动预览失败: {e}"}))
                    return
            else:
                session_obj = _preview_sessions[key]

            target_set = session_obj["webcodecs_ws_set"] if use_webcodecs else session_obj["video_ws_set"]
            target_set.add(ws)
            print(f"[ws_preview] {'WebCodecs' if use_webcodecs else 'mpeg1'} 客户端加入 device_id={device_id} "
                  f"当前={len(target_set)}")

            # mpeg1 客户端首次连接时按需启动 ffmpeg + 推流线程
            if not use_webcodecs:
                _ensure_video_ffmpeg(session_obj)

        # 新客户端：发送初始数据
        if use_webcodecs:
            first_msg = session_obj.get("webcodecs_init", b"")
        else:
            header = session_obj.get("jsmp_header", b"")
            pre_buf = session_obj.get("pre_buffer", b"")
            first_msg = header + pre_buf
        if first_msg:
            try:
                _safe_send(ws, first_msg)
            except Exception:
                print(f"[ws_preview] 发送初始数据失败 device_id={device_id}")
                with _preview_lock:
                    if key in _preview_sessions:
                        target_set_now = (_preview_sessions[key]["webcodecs_ws_set"]
                                           if use_webcodecs else _preview_sessions[key]["video_ws_set"])
                        target_set_now.discard(ws)
                return

        try:
            # 保持连接，等待客户端断开
            while True:
                try:
                    msg = ws.receive(timeout=30)
                    if msg is None:
                        break
                    if msg == "ping":
                        _safe_send(ws, "pong")
                except Exception:
                    break
        finally:
            with _preview_lock:
                if key in _preview_sessions:
                    session_obj = _preview_sessions[key]
                    if use_webcodecs:
                        session_obj["webcodecs_ws_set"].discard(ws)
                    else:
                        session_obj["video_ws_set"].discard(ws)
                    remaining_mpeg = len(session_obj["video_ws_set"])
                    remaining_webcodecs = len(session_obj["webcodecs_ws_set"])
                    remaining_audio = len(session_obj["audio_ws_set"])
                    print(f"[ws_preview] 视频客户端离开 device_id={device_id} "
                          f"mpeg1={remaining_mpeg} webcodecs={remaining_webcodecs} audio={remaining_audio}")
                    if remaining_mpeg == 0 and remaining_webcodecs == 0 and remaining_audio == 0:
                        _stop_preview_session(key, session_obj)
                        del _preview_sessions[key]
                        print(f"[ws_preview] 预览会话已关闭 device_id={device_id}")
            try:
                ws.close()
            except Exception:
                pass
    finally:
        _drop_ws_lock(ws)


@sock.route("/ws/preview/<int:device_id>/audio")
def ws_preview_audio(ws, device_id):
    """
    WebSocket 音频预览端点（PCM s16le 8kHz mono）。
    与视频共享同一路 SDK 预览会话。
    """
    try:
        username = session.get("username")
        if not username:
            _safe_send(ws, json.dumps({"type": "error", "msg": "未登录"}))
            return

        try:
            require_permission("access.preview")
        except Exception as e:
            _safe_send(ws, json.dumps({"type": "error", "msg": str(e) or "无实时预览权限"}))
            return

        device_info = _find_access_device_by_id(device_id, username)
        if not device_info:
            _safe_send(ws, json.dumps({"type": "error", "msg": "设备不存在或无权限"}))
            return

        key = _preview_key(device_id)

        with _preview_lock:
            if key not in _preview_sessions:
                try:
                    session_obj = _create_preview_session(device_id, device_info)
                    _preview_sessions[key] = session_obj
                    print(f"[ws_audio] 新建预览会话 device_id={device_id}")
                except Exception as e:
                    _safe_send(ws, json.dumps({"type": "error", "msg": f"启动预览失败: {e}"}))
                    return
            else:
                session_obj = _preview_sessions[key]

            session_obj["audio_ws_set"].add(ws)
            print(f"[ws_audio] 音频客户端加入 device_id={device_id} 当前={len(session_obj['audio_ws_set'])}")

        try:
            while True:
                try:
                    msg = ws.receive(timeout=30)
                    if msg is None:
                        break
                except Exception:
                    break
        finally:
            with _preview_lock:
                if key in _preview_sessions:
                    session_obj = _preview_sessions[key]
                    session_obj["audio_ws_set"].discard(ws)
                    remaining_mpeg = len(session_obj["video_ws_set"])
                    remaining_webcodecs = len(session_obj["webcodecs_ws_set"])
                    remaining_audio = len(session_obj["audio_ws_set"])
                    print(f"[ws_audio] 音频客户端离开 device_id={device_id} "
                          f"mpeg1={remaining_mpeg} webcodecs={remaining_webcodecs} audio={remaining_audio}")
                    if remaining_mpeg == 0 and remaining_webcodecs == 0 and remaining_audio == 0:
                        _stop_preview_session(key, session_obj)
                        del _preview_sessions[key]
                        print(f"[ws_audio] 预览会话已关闭 device_id={device_id}")
            try:
                ws.close()
            except Exception:
                pass
    finally:
        _drop_ws_lock(ws)


@sock.route("/ws/monitor/preview/<int:device_id>/<int:channel_no>")
def ws_monitor_preview(ws, device_id, channel_no):
    try:
        username = session.get("username")
        if not username:
            _safe_send(ws, json.dumps({"type": "error", "msg": "未登录"}))
            return
        try:
            require_permission("page.monitor")
        except Exception as e:
            _safe_send(ws, json.dumps({"type": "error", "msg": str(e)}))
            return

        device_info = _find_nvr_device_by_id(device_id, username)
        if not device_info:
            _safe_send(ws, json.dumps({"type": "error", "msg": "录像机不存在或无权限"}))
            return
        if channel_no < 0 or channel_no >= int(device_info.get("channel_count") or 0):
            _safe_send(ws, json.dumps({"type": "error", "msg": "通道不存在"}))
            return
        if not _monitor_channel_allowed(username, device_id, channel_no):
            _safe_send(ws, json.dumps({"type": "error", "msg": "无通道权限"}))
            return

        stream_type = (request.args.get("stream") or "sub").lower()
        if stream_type not in ("main", "sub"):
            stream_type = "sub"
        codec = (request.args.get("codec") or "mpeg1").lower()
        use_webcodecs = (codec == "h264")
        key = _monitor_preview_key(device_id, channel_no, stream_type)

        with _preview_lock:
            if key not in _preview_sessions:
                try:
                    session_obj = _create_monitor_preview_session(device_id, device_info, channel_no, stream_type)
                    _preview_sessions[key] = session_obj
                    print(f"[ws_monitor] 新建预览会话 {key}")
                except Exception as e:
                    _safe_send(ws, json.dumps({"type": "error", "msg": f"启动预览失败: {e}"}))
                    return
            else:
                session_obj = _preview_sessions[key]

            target_set = session_obj["webcodecs_ws_set"] if use_webcodecs else session_obj["video_ws_set"]
            target_set.add(ws)
            print(f"[ws_monitor] {'WebCodecs' if use_webcodecs else 'mpeg1'} 客户端加入 {key} 当前={len(target_set)}")

            # mpeg1 客户端首次连接时按需启动 ffmpeg + 推流线程
            if not use_webcodecs:
                _ensure_video_ffmpeg(session_obj)

        if use_webcodecs:
            first_msg = session_obj.get("webcodecs_init", b"")
        else:
            first_msg = session_obj.get("jsmp_header", b"") + session_obj.get("pre_buffer", b"")
        if first_msg:
            try:
                _safe_send(ws, first_msg)
            except Exception:
                with _preview_lock:
                    if key in _preview_sessions:
                        target_set_now = (_preview_sessions[key]["webcodecs_ws_set"]
                                           if use_webcodecs else _preview_sessions[key]["video_ws_set"])
                        target_set_now.discard(ws)
                return

        try:
            while True:
                try:
                    msg = ws.receive(timeout=30)
                    if msg is None:
                        break
                    if msg == "ping":
                        _safe_send(ws, "pong")
                except Exception:
                    break
        finally:
            with _preview_lock:
                if key in _preview_sessions:
                    session_obj = _preview_sessions[key]
                    if use_webcodecs:
                        session_obj["webcodecs_ws_set"].discard(ws)
                    else:
                        session_obj["video_ws_set"].discard(ws)
                    remaining = len(session_obj["video_ws_set"]) + len(session_obj["webcodecs_ws_set"])
                    print(f"[ws_monitor] 客户端离开 {key} 剩余={remaining}")
                    if remaining == 0:
                        _stop_preview_session(key, session_obj)
                        del _preview_sessions[key]
                        print(f"[ws_monitor] 预览会话已关闭 {key}")
            try:
                ws.close()
            except Exception:
                pass
    finally:
        _drop_ws_lock(ws)


@sock.route("/ws/playback/<int:device_id>/<int:channel_no>")
def ws_playback(ws, device_id, channel_no):
    """
    WebSocket 录像回放。
    URL 参数:
      start=YYYY-MM-DD HH:MM:SS    (必填)
      end=YYYY-MM-DD HH:MM:SS      (必填)
      stream=main|sub
      direction=forward|backward
      codec=h264|mpeg1

    控制信令(客户端发文本):
      pause / resume          暂停 / 恢复
    """
    try:
        username = session.get("username")
        if not username:
            _safe_send(ws, json.dumps({"type": "error", "msg": "未登录"}))
            return
        try:
            require_permission("page.playback")
        except Exception as e:
            _safe_send(ws, json.dumps({"type": "error", "msg": str(e)}))
            return

        device_info = _find_nvr_device_by_id(device_id, username)
        if not device_info:
            _safe_send(ws, json.dumps({"type": "error", "msg": "录像机不存在或无权限"}))
            return
        if channel_no < 0 or channel_no >= int(device_info.get("channel_count") or 0):
            _safe_send(ws, json.dumps({"type": "error", "msg": "通道不存在"}))
            return
        if not _monitor_channel_allowed(username, device_id, channel_no):
            _safe_send(ws, json.dumps({"type": "error", "msg": "无通道权限"}))
            return

        start_str = (request.args.get("start") or "").strip()
        end_str = (request.args.get("end") or "").strip()
        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            _safe_send(ws, json.dumps({"type": "error", "msg": "时间参数格式错误,需要 YYYY-MM-DD HH:MM:SS"}))
            return

        stream_type = (request.args.get("stream") or "main").lower()
        if stream_type not in ("main", "sub"):
            stream_type = "main"
        direction = (request.args.get("direction") or "forward").lower()
        backward = (direction == "backward")
        codec = (request.args.get("codec") or "h264").lower()
        use_webcodecs = (codec == "h264")

        client_id = _next_playback_client_id()
        key = _playback_key(device_id, channel_no, stream_type, client_id)

        with _preview_lock:
            try:
                session_obj = _create_playback_session(
                    device_id, device_info, channel_no, stream_type,
                    start_dt, end_dt, backward=backward,
                )
                _preview_sessions[key] = session_obj
            except Exception as e:
                _safe_send(ws, json.dumps({"type": "error", "msg": f"启动回放失败: {e}"}))
                return

            target_set = session_obj["webcodecs_ws_set"] if use_webcodecs else session_obj["video_ws_set"]
            target_set.add(ws)
            print(f"[ws_playback] {'WebCodecs' if use_webcodecs else 'mpeg1'} 客户端加入 {key}", flush=True)

        # 发送初始帧(同实时预览)
        if use_webcodecs:
            first_msg = session_obj.get("webcodecs_init", b"")
        else:
            first_msg = session_obj.get("jsmp_header", b"") + session_obj.get("pre_buffer", b"")
        if first_msg:
            try:
                _safe_send(ws, first_msg)
            except Exception:
                pass

        try:
            while True:
                try:
                    msg = ws.receive(timeout=60)
                except Exception:
                    break
                if msg is None:
                    break
                if not isinstance(msg, str):
                    continue
                text = msg.strip().lower()
                if text == "ping":
                    try:
                        _safe_send(ws, "pong")
                    except Exception:
                        pass
                    continue

                # 控制信令
                client = session_obj.get("client")
                handle = session_obj.get("sdk_handle")
                if client is None or handle is None:
                    continue
                try:
                    if text == "pause":
                        client.pause_playback(handle, True)
                        try:
                            _safe_send(ws, json.dumps({"type": "ack", "cmd": "pause"}))
                        except Exception:
                            pass
                    elif text == "resume":
                        client.pause_playback(handle, False)
                        try:
                            _safe_send(ws, json.dumps({"type": "ack", "cmd": "resume"}))
                        except Exception:
                            pass

                except Exception as e:
                    try:
                        _safe_send(ws, json.dumps({"type": "error", "msg": f"{text} 失败: {e}"}))
                    except Exception:
                        pass
        finally:
            with _preview_lock:
                if key in _preview_sessions:
                    sess = _preview_sessions[key]
                    sess["video_ws_set"].discard(ws)
                    sess["webcodecs_ws_set"].discard(ws)
                    # 回放是单客户端独占,只要 WS 断开就关闭会话
                    _stop_preview_session(key, sess)
                    del _preview_sessions[key]
                    print(f"[ws_playback] 回放会话已关闭 {key}", flush=True)
            try:
                ws.close()
            except Exception:
                pass
    finally:
        _drop_ws_lock(ws)
