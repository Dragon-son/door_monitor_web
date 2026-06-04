# device_client.py

import datetime
import time
import base64
import io
import threading
import requests
import concurrent.futures
from PIL import Image
from requests.auth import HTTPDigestAuth

# 新增导入：用于时区转换
from datetime import timezone, timedelta

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Callback import fDisConnect, fHaveReConnect, fRealDataCallBackEx2
from NetSDK.SDK_Struct import *
from NetSDK.SDK_Enum import *
from ctypes import sizeof, cast, POINTER, pointer, byref, c_void_p, c_ubyte, c_int, CFUNCTYPE, create_string_buffer

METHOD_MAP = {
    1: "刷卡", 2: "密码", 3: "卡+密码", 4: "指纹",
    5: "远程开门", 6: "按钮开门", 16: "人脸识别",
}


class FaceCGIError(Exception):
    """人脸 CGI 操作失败，携带结构化信息供上层做降级判断。"""
    def __init__(self, message, user_id=None, action=None, status_code=None):
        super().__init__(message)
        self.user_id = user_id
        self.action = action
        self.status_code = status_code


class _NetSdkRuntime:
    """Process-wide NetSDK lifecycle.

    The Python SDK manual requires InitEx/Cleanup to be paired once per process.
    Callback objects are kept as attributes so ctypes cannot garbage-collect them.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sdk = None
        self._disconnect_cb = None
        self._reconnect_cb = None
        self._initialized = False

    @staticmethod
    def _decode_ip(ip):
        if isinstance(ip, (bytes, bytearray)):
            return ip.decode(errors="ignore")
        return str(ip)

    def get_sdk(self):
        with self._lock:
            if self._initialized:
                return self._sdk

            sdk = NetClient()

            def _on_disconnect(login_id, ip, port, user):
                print(f"断线 {self._decode_ip(ip)}:{port}")

            def _on_reconnect(login_id, ip, port, user):
                print(f"重连 {self._decode_ip(ip)}:{port}")

            self._disconnect_cb = fDisConnect(_on_disconnect)
            self._reconnect_cb = fHaveReConnect(_on_reconnect)
            if not sdk.InitEx(self._disconnect_cb):
                raise Exception("NetSDK 初始化失败")
            sdk.SetAutoReconnect(self._reconnect_cb)

            self._sdk = sdk
            self._initialized = True
            return self._sdk

    def cleanup(self):
        with self._lock:
            if not self._initialized or self._sdk is None:
                return
            self._sdk.Cleanup()
            self._sdk = None
            self._disconnect_cb = None
            self._reconnect_cb = None
            self._initialized = False


_NETSDK_RUNTIME = _NetSdkRuntime()


def cleanup_netsdk_runtime():
    _NETSDK_RUNTIME.cleanup()


def make_net_time(dt):
    t = NET_TIME()
    t.dwYear = dt.year
    t.dwMonth = dt.month
    t.dwDay = dt.day
    t.dwHour = dt.hour
    t.dwMinute = dt.minute
    t.dwSecond = dt.second
    return t


def compress_image(path, max_kb=0, width=0, height=0, quality=0):
    img = Image.open(path).convert("RGB")
    if width > 0 and height > 0:
        img = img.resize((width, height))
    elif width > 0 or height > 0:
        img.thumbnail((width or 9999, height or 9999))
    q = quality if quality > 0 else 85
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q)
    data = buf.getvalue()
    if max_kb and len(data) > max_kb * 1024:
        # 逐步降低质量压缩，避免直接截断导致 JPEG 损坏
        while len(data) > max_kb * 1024 and q > 20:
            q -= 10
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=q)
            data = buf.getvalue()
    return data


def _decode_sdk_text(raw):
    data = bytes(raw).split(b'\x00', 1)[0].strip()
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode(errors="ignore").strip()


class DeviceClient:

    def __init__(self, ip, port, username, password):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password

        # 实时预览句柄注册表：handle(int) -> {callback, sdk_cb, channel, stream_type, diag_*}
        # 同一个 NetSDK login 上可挂多路 RealPlay，分别独立回调。
        self._preview_handles = {}
        self._preview_lock = threading.RLock()  # RLock 避免 SDK 回调线程与主线程死锁

        # 回放句柄注册表：handle(int) -> {callback, data_cb, pos_cb, channel, start_dt, end_dt, backward, diag_*}
        self._playback_handles = {}
        self._playback_lock = threading.RLock()

        self.sdk = _NETSDK_RUNTIME.get_sdk()
        self.loginID = 0
        self.channel_count = 0
        self.lock = threading.Lock()
        self.last_active = time.time()

        # RPC2 会话（懒加载）
        self._rpc_session = None
        self._rpc_id = 1000
        self._rpc_lock = threading.RLock()
        self._rpc_keepalive_interval = 60
        self._rpc_last_keepalive = 0

        self._login()

    def _login(self):
        stuIn = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuIn.dwSize = sizeof(stuIn)
        stuIn.szIP = self.ip.encode()
        stuIn.nPort = self.port
        stuIn.szUserName = self.username.encode()
        stuIn.szPassword = self.password.encode()
        stuIn.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP

        stuOut = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuOut.dwSize = sizeof(stuOut)

        loginID, device_info, err = self.sdk.LoginWithHighLevelSecurity(stuIn, stuOut)
        if loginID == 0:
            raise Exception(f"登录失败: {err}")

        self.loginID = loginID
        try:
            self.channel_count = int(getattr(device_info, "nChanNum", 0) or 0)
        except (TypeError, ValueError):
            self.channel_count = 0

    def ensure(self):
        if self.loginID == 0:
            self._login()

    def get_channel_names(self):
        """读取录像机通道名称，失败的通道由调用方回退到默认名称。"""
        with self.lock:
            self.ensure()
            count = max(0, int(self.channel_count or 0))
            names = {}
            for channel in range(count):
                try:
                    out_buffer = create_string_buffer(10240)
                    if not self.sdk.GetNewDevConfig(
                        self.loginID,
                        CFG_CMD_TYPE.CHANNELTITLE,
                        channel,
                        out_buffer,
                        10240,
                        0,
                        5000,
                    ):
                        continue
                    info = AV_CFG_ChannelName()
                    info.nStructSize = sizeof(AV_CFG_ChannelName)
                    if not self.sdk.ParseData(
                        CFG_CMD_TYPE.CHANNELTITLE,
                        out_buffer,
                        info,
                        sizeof(AV_CFG_ChannelName),
                        None,
                    ):
                        continue
                    name = _decode_sdk_text(info.szName)
                    if name:
                        names[channel] = name
                except Exception as e:
                    print(f"[nvr] 读取通道名称失败 channel={channel}: {e}")
            return names

    # ================= 开门 =================
    def open_door(self, channel=0):
        with self.lock:
            self.ensure()
            ctrl = NET_CTRL_ACCESS_OPEN()
            ctrl.dwSize = sizeof(ctrl)
            ctrl.nChannelID = channel
            ok = self.sdk.ControlDevice(self.loginID, CtrlType.ACCESS_OPEN, ctrl, 5000)
            self.last_active = time.time()
            if not ok:
                raise Exception(self.sdk.GetLastErrorMessage())
            return True

    def get_channel_states(self):
        """
        查询 NVR 各通道的"是否已配置摄像头"和"是否当前在线"。

        优先用 RPC2 LogicDeviceManager.getCameraStateAll(能拿到实时连接状态);
        若设备不支持(部分新版 NVR 返回 "Method not found!"),回退到
        LogicDeviceManager.getCameraAll(只能拿到"是否启用",无在线信息)。

        Returns:
            dict {channel_no: {"configured": bool, "online": bool}}
                configured=True : 该通道在 NVR 后台已配置接入了摄像头(在线或离线)
                online=True     : 当前实时连接成功(connectionState=Connected);
                                  若数据源是 getCameraAll, online 一律为 False
            None: 设备不支持任何已知 RPC —— 上层应保留原始行为(不过滤)
        """
        count = max(0, int(self.channel_count or 0))
        if count == 0:
            return {}

        # ---- 1) 先尝试 getCameraState*(有 connectionState,可识别在线) ----
        candidates = [
            ("LogicDeviceManager.getCameraStateAll", {}),
            ("LogicDeviceManager.getCameraState", {"uniqueChannels": [-1]}),
            ("LogicDeviceManager.getCameraState", {"uniqueChannels": list(range(count))}),
        ]
        for method, params in candidates:
            try:
                resp = self._rpc_call(method, params, timeout=8)
            except Exception as e:
                print(f"[nvr] {method} 异常 ip={self.ip}: {e}", flush=True)
                continue
            if not isinstance(resp, dict):
                continue
            if resp.get("result") is False:
                continue
            states_list = (resp.get("params") or {}).get("states") or []
            if not states_list:
                continue
            result = {}
            for st in states_list:
                ch = st.get("channel")
                if ch is None:
                    continue
                conn_state = (st.get("connectionState") or "").lower()
                # 已配置摄像头(Connected 在线 / Disconnected 离线)算有设备;
                # Empty / Unknown / 空字符串表示空槽位,要过滤掉。
                # (不同型号 NVR 用词不一:旧固件用 Unknown,新固件如 NVR5X-4K 用 Empty)
                configured = conn_state not in ("", "unknown", "empty")
                online = (conn_state == "connected")
                result[int(ch)] = {"configured": configured, "online": online}
            if result:
                return result

        # ---- 2) 回退到 getCameraAll(只能拿启用状态,识别不到在线) ----
        try:
            resp = self._rpc_call("LogicDeviceManager.getCameraAll", {}, timeout=8)
        except Exception as e:
            print(f"[nvr] LogicDeviceManager.getCameraAll 异常 ip={self.ip}: {e}", flush=True)
            return None
        if not isinstance(resp, dict) or resp.get("result") is False:
            return None
        cams = (resp.get("params") or {}).get("camera") or []
        if not cams:
            return None
        result = {}
        for c in cams:
            ch = c.get("UniqueChannel")
            if ch is None:
                ch = c.get("Channel")
            if ch is None:
                continue
            # Compose 类型是虚拟拼接通道(如 UniqueChannel=98),不是真实摄像头
            if (c.get("Type") or "") == "Compose":
                continue
            # Enable=True 即视为"已配置摄像头";Enable=False 是空槽位,过滤
            configured = bool(c.get("Enable"))
            result[int(ch)] = {"configured": configured, "online": False}
        return result if result else None

    # ================= 录像回放 =================
    def query_records(self, channel, start_dt, end_dt, stream_type='main'):
        """
        查询某通道在指定时间范围内的录像段。
        返回 [{'start': 'YYYY-MM-DD HH:MM:SS', 'end': ..., 'size_kb': int, 'type': int}]
        """
        with self.lock:
            self.ensure()
            # 设置查询的码流类型(主/辅)
            try:
                stream_idx = c_int(1 if stream_type == 'sub' else 0)
                self.sdk.SetDeviceMode(
                    self.loginID,
                    int(EM_USEDEV_MODE.RECORD_STREAM_TYPE),
                    stream_idx,
                )
            except Exception as e:
                print(f"[playback] SetDeviceMode 失败 {self.ip}: {e}", flush=True)

            start = NET_TIME()
            start.dwYear, start.dwMonth, start.dwDay = start_dt.year, start_dt.month, start_dt.day
            start.dwHour, start.dwMinute, start.dwSecond = start_dt.hour, start_dt.minute, start_dt.second
            end = NET_TIME()
            end.dwYear, end.dwMonth, end.dwDay = end_dt.year, end_dt.month, end_dt.day
            end.dwHour, end.dwMinute, end.dwSecond = end_dt.hour, end_dt.minute, end_dt.second

            try:
                result, file_count, infos = self.sdk.QueryRecordFile(
                    self.loginID, channel, int(EM_QUERY_RECORD_TYPE.ALL),
                    start, end, None, 5000, False,
                )
            except Exception as e:
                print(f"[playback] QueryRecordFile 异常 {self.ip}: {e}", flush=True)
                return []
            if not result or not infos or file_count <= 0:
                return []

            records = []
            for i in range(int(file_count)):
                try:
                    info = infos[i]
                    s, e = info.starttime, info.endtime
                    # 跳过明显非法时间(0000-00-00 等)
                    if s.dwYear < 1970 or e.dwYear < 1970:
                        continue
                    records.append({
                        "start": f"{s.dwYear:04d}-{s.dwMonth:02d}-{s.dwDay:02d} {s.dwHour:02d}:{s.dwMinute:02d}:{s.dwSecond:02d}",
                        "end":   f"{e.dwYear:04d}-{e.dwMonth:02d}-{e.dwDay:02d} {e.dwHour:02d}:{e.dwMinute:02d}:{e.dwSecond:02d}",
                        "size_kb": int(info.size),
                        "type": int(info.nRecordFileType),
                    })
                except Exception:
                    continue
            return records

    def start_playback(self, data_callback, channel, start_dt, end_dt,
                       stream_type='main', backward=False):
        """
        在当前 NetSDK login 上启动一路录像回放，数据通过 data_callback(bytes) 推送（SDK 回调线程中调用）。
        Returns: int  播放句柄(lPlayHandle)，后续 pause/fast/slow/set_direction/stop_playback 都按 handle 调用。
        """
        with self._playback_lock:
            self.ensure()

            # 设置回放码流类型（每次都设，避免多通道并发回放时使用错误的码流）
            try:
                stream_idx = c_int(1 if stream_type == 'sub' else 0)
                self.sdk.SetDeviceMode(
                    self.loginID,
                    int(EM_USEDEV_MODE.RECORD_STREAM_TYPE),
                    stream_idx,
                )
            except Exception as e:
                print(f"[playback] SetDeviceMode 失败 {self.ip}: {e}", flush=True)

            # 注意:必须用 CB_FUNCTYPE(SDK 内部已按平台选择 stdcall/cdecl),
            # 直接用 ctypes.CFUNCTYPE 在 Windows 上不匹配 SDK 调用约定。
            DataCBType = CB_FUNCTYPE(c_int, C_LLONG, C_DWORD, POINTER(c_ubyte), C_DWORD, C_LDWORD)
            PosCBType = CB_FUNCTYPE(None, C_LLONG, C_DWORD, C_DWORD, C_LDWORD)

            ctx = {
                "callback": data_callback,
                "channel": channel,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "backward": backward,
                "diag_data": 0,
                "diag_pos": 0,
            }

            def _data_cb(lPlayHandle, dwDataType, pBuffer, dwBufSize, dwUser):
                if ctx["diag_data"] < 10:
                    import sys as _sys
                    try:
                        head_len = min(16, int(dwBufSize))
                        head_hex = bytes(cast(pBuffer, POINTER(c_ubyte * head_len)).contents).hex() if head_len > 0 else ''
                        _sys.stderr.write(
                            f"[PB-DATA#{ctx['diag_data']}] ch={ctx['channel']} "
                            f"dwDataType={dwDataType} dwBufSize={dwBufSize} head={head_hex}\n"
                        )
                        _sys.stderr.flush()
                    except Exception:
                        pass
                    ctx["diag_data"] += 1
                # PlayBackByDataType + PRIVATE 回调里 dwDataType=0 是私有码流。
                # 兼容旧设备/旧实现里见过的 1004,真正的视频帧后续按 DHAV 帧类型过滤。
                if dwBufSize > 0 and dwDataType in (0, 1004):
                    cb = ctx["callback"]
                    if cb:
                        try:
                            data = bytes(cast(pBuffer, POINTER(c_ubyte * dwBufSize)).contents)
                            cb(data)
                        except Exception as e:
                            print(f"[playback] 数据回调异常 ch={ctx['channel']}: {e}", flush=True)
                # SDK Demo 的回放数据回调返回 1 表示本包处理成功并继续回放。
                return 1

            def _pos_cb(lPlayHandle, dwTotalSize, dwDownLoadSize, dwUser):
                if ctx["diag_pos"] < 10:
                    import sys as _sys
                    try:
                        _sys.stderr.write(
                            f"[PB-POS#{ctx['diag_pos']}] ch={ctx['channel']} "
                            f"total={dwTotalSize} downloaded={dwDownLoadSize}\n"
                        )
                        _sys.stderr.flush()
                    except Exception:
                        pass
                    ctx["diag_pos"] += 1
                return

            # 保持引用,避免被 GC 回收导致段错误
            ctx["data_cb"] = DataCBType(_data_cb)
            ctx["pos_cb"] = PosCBType(_pos_cb)

            inParam = NET_IN_PLAYBACK_BY_DATA_TYPE()
            inParam.dwSize = sizeof(NET_IN_PLAYBACK_BY_DATA_TYPE)
            inParam.nChannelID = channel
            inParam.stStartTime.dwYear = start_dt.year
            inParam.stStartTime.dwMonth = start_dt.month
            inParam.stStartTime.dwDay = start_dt.day
            inParam.stStartTime.dwHour = start_dt.hour
            inParam.stStartTime.dwMinute = start_dt.minute
            inParam.stStartTime.dwSecond = start_dt.second
            inParam.stStopTime.dwYear = end_dt.year
            inParam.stStopTime.dwMonth = end_dt.month
            inParam.stStopTime.dwDay = end_dt.day
            inParam.stStopTime.dwHour = end_dt.hour
            inParam.stStopTime.dwMinute = end_dt.minute
            inParam.stStopTime.dwSecond = end_dt.second
            inParam.hWnd = 0
            inParam.cbDownLoadPos = ctx["pos_cb"]
            inParam.fDownLoadDataCallBack = ctx["data_cb"]
            inParam.dwPosUser = 0
            inParam.dwDataUser = 0
            # PRIVATE = 0:原始 DHAV 直通,不强制 codec —— H.265 通道保留原分辨率,
            # H264 = 4 会让 SDK 对 H.265 源做 transcode 降分辨率。
            inParam.emDataType = int(EM_REAL_DATA_TYPE.PRIVATE)
            inParam.nPlayDirection = 1 if backward else 0
            inParam.emAudioType = int(EM_AUDIO_DATA_TYPE.DEFAULT)

            outParam = NET_OUT_PLAYBACK_BY_DATA_TYPE()
            outParam.dwSize = sizeof(NET_OUT_PLAYBACK_BY_DATA_TYPE)

            play_id = self.sdk.PlayBackByDataType(self.loginID, inParam, outParam, 5000)
            if not play_id:
                raise Exception(f"PlayBackByDataType 失败: {self.sdk.GetLastErrorMessage()}")

            self._playback_handles[play_id] = ctx
            self.last_active = time.time()
            print(f"[playback] 开启回放 {self.ip} ch={channel} {start_dt}~{end_dt} "
                  f"backward={backward} play_id={play_id}", flush=True)
            return play_id

    def pause_playback(self, handle, paused: bool):
        with self._playback_lock:
            if handle not in self._playback_handles:
                raise Exception("回放句柄不存在")
            ok = self.sdk.PausePlayBack(handle, bool(paused))
            if not ok:
                raise Exception(f"PausePlayBack 失败: {self.sdk.GetLastErrorMessage()}")

    def stop_playback(self, handle):
        """停止单路回放。handle 必须是 start_playback 返回的值。"""
        with self._playback_lock:
            ctx = self._playback_handles.pop(handle, None)
            if ctx is None:
                return
            try:
                self.sdk.StopPlayBack(handle)
                print(f"[playback] 停止回放 {self.ip} play_id={handle}", flush=True)
            except Exception as e:
                print(f"[playback] StopPlayBack 异常: {e}", flush=True)

    def is_playing_back(self):
        with self._playback_lock:
            return len(self._playback_handles) > 0

    # ================= 视频预览 =================
    def start_preview(self, data_callback, channel=0, output_format='h264', stream_type='main',
                      force_h264_transcode=False):
        """
        在当前 NetSDK login 上启动一路实时预览，转码后的数据通过 data_callback(bytes) 回调。
        data_callback 会在 SDK 回调线程中被调用，请勿在其中做耗时操作。

        Returns:
            int: SDK 返回的预览句柄(lRealHandle)，调用 stop_preview(handle) 释放。

        Args:
            data_callback: 数据回调函数 fn(bytes)
            channel: 通道号，默认 0
            output_format: 输出格式，'h264'(默认) 或 'dhav'(原始)
            stream_type: 'main' 主码流 或 'sub' 辅码流
        """
        with self._preview_lock:
            self.ensure()
            sdk_stream_type = SDK_RealPlayType.Realplay_1 if stream_type == 'sub' else SDK_RealPlayType.Realplay

            ctx = {
                "callback": data_callback,
                "channel": channel,
                "stream_type": stream_type,
                # 每条流独立的诊断计数器，避免多路互相吃配额
                "diag_cb": 0,
                "diag_chk": 0,
                "diag_call": 0,
            }

            # 每条流独立 closure，capture 自己的 ctx；防 GC 强引用 sdk_cb 存进 ctx
            def _raw_cb(lRealHandle, dwDataType, pBuffer, dwBufSize, param, dwUser):
                if ctx["diag_cb"] < 20:
                    import sys as _sys
                    try:
                        _sys.stderr.write(
                            f"[SDK-CB#{ctx['diag_cb']}] {self.ip} ch={ctx['channel']} "
                            f"dwDataType={dwDataType} dwBufSize={dwBufSize}\n"
                        )
                        _sys.stderr.flush()
                    except Exception:
                        pass
                    ctx["diag_cb"] += 1

                if dwDataType == 0 and dwBufSize > 0:
                    cb = ctx["callback"]
                    if ctx["diag_chk"] < 10:
                        import sys as _sys
                        try:
                            _sys.stderr.write(
                                f"[SDK-CB-CHK#{ctx['diag_chk']}] cb={'SET' if cb else 'NONE'} "
                                f"ch={ctx['channel']} cb_id={id(cb) if cb else 0}\n"
                            )
                            _sys.stderr.flush()
                        except Exception:
                            pass
                        ctx["diag_chk"] += 1
                    if cb:
                        try:
                            data = bytes(cast(pBuffer, POINTER(c_ubyte * dwBufSize)).contents)
                            if ctx["diag_call"] < 5:
                                import sys as _sys
                                _sys.stderr.write(f"[SDK] BEFORE cb #{ctx['diag_call']} ch={ctx['channel']} len={len(data)}\n")
                                _sys.stderr.flush()
                            cb(data)
                            if ctx["diag_call"] < 5:
                                _sys.stderr.write(f"[SDK] AFTER  cb #{ctx['diag_call']} ch={ctx['channel']}\n")
                                _sys.stderr.flush()
                                ctx["diag_call"] += 1
                        except Exception as e:
                            import sys as _sys
                            try:
                                _sys.stderr.write(f"[SDK-CB] cb 异常 ch={ctx['channel']}: {e!r}\n")
                                _sys.stderr.flush()
                            except Exception:
                                pass
                return 0

            ctx["sdk_cb"] = fRealDataCallBackEx2(_raw_cb)

            if output_format == 'h264':
                inParam = NET_IN_REALPLAY_BY_DATA_TYPE()
                inParam.dwSize = sizeof(NET_IN_REALPLAY_BY_DATA_TYPE)
                inParam.nChannelID = channel
                inParam.hWnd = 0
                inParam.rType = sdk_stream_type
                inParam.cbRealDataEx = ctx["sdk_cb"]
                # PRIVATE = 0:原始 DHAV 直通,不强制 codec —— SDK 不做 transcode,
                # H.265 摄像头原样输出 H.265 NAL,保留摄像头真实分辨率。
                # H264 = 4 会让 SDK 对 H.265 源做转码,常见降到 1280x720。
                # 门禁预览(force_h264_transcode=True)沿用 H264 强制模式,保持原有的音频/AU 行为。
                if force_h264_transcode:
                    inParam.emDataType = EM_REAL_DATA_TYPE.H264
                    em_label = "H264"
                else:
                    inParam.emDataType = EM_REAL_DATA_TYPE.PRIVATE
                    em_label = "PRIVATE"
                inParam.dwUser = 0
                inParam.szSaveFileName = None
                inParam.emAudioType = EM_AUDIO_DATA_TYPE.DEFAULT

                outParam = NET_OUT_REALPLAY_BY_DATA_TYPE()
                outParam.dwSize = sizeof(NET_OUT_REALPLAY_BY_DATA_TYPE)

                play_id = self.sdk.RealPlayByDataType(self.loginID, inParam, outParam, 5000)
                if not play_id:
                    raise Exception(f"RealPlayByDataType 失败: {self.sdk.GetLastErrorMessage()}")

                print(f"[preview] 开启预览成功(emDataType={em_label}) {self.ip} ch={channel} play_id={play_id}")
            else:
                play_id = self.sdk.RealPlayEx(self.loginID, channel, 0, sdk_stream_type)
                if not play_id:
                    raise Exception(f"RealPlayEx 失败: {self.sdk.GetLastErrorMessage()}")

                if not self.sdk.SetRealDataCallBackEx2(
                    play_id,
                    ctx["sdk_cb"],
                    None,
                    EM_REALDATA_FLAG.RAW_DATA
                ):
                    self.sdk.StopRealPlayEx(play_id)
                    raise Exception(f"SetRealDataCallBackEx2 失败: {self.sdk.GetLastErrorMessage()}")

                print(f"[preview] 开启 dhav 预览成功 {self.ip} ch={channel} play_id={play_id}")

            self._preview_handles[play_id] = ctx
            self.last_active = time.time()
            return play_id

    def stop_preview(self, handle):
        """停止单路实时预览。handle 必须是 start_preview 返回的值。"""
        with self._preview_lock:
            ctx = self._preview_handles.pop(handle, None)
            if ctx is None:
                return
            try:
                self.sdk.StopRealPlayEx(handle)
                print(f"[preview] 停止预览 {self.ip} play_id={handle}")
            except Exception as e:
                print(f"[preview] StopRealPlayEx 异常: {e}")

    def is_previewing(self):
        with self._preview_lock:
            return len(self._preview_handles) > 0
    # ================= 人员管理 =================
    def _parse_date(self, value):
        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.date):
            return datetime.datetime.combine(value, datetime.time.min)
        if isinstance(value, str):
            try:
                return datetime.datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"日期格式错误: {value}，应为 YYYY-MM-DD") from exc
        raise ValueError(f"不支持的日期类型: {type(value).__name__}")

    def _build_access_user_info(self, user_id, name, status=0, doors=None, valid_begin=None, valid_end=None):
        user = NET_ACCESS_USER_INFO()
        user.szUserID = user_id.encode()
        user.szName = name.encode("utf-8")
        user.nUserStatus = int(status)

        door_list = list(doors) if doors else [0]
        user.nDoorNum = len(door_list)
        for idx, door in enumerate(door_list):
            user.nDoors[idx] = int(door)

        begin_dt = self._parse_date(valid_begin or "2000-01-01")
        end_dt = self._parse_date(valid_end or "2037-12-31")
        user.stuValidBeginTime = make_net_time(begin_dt)
        user.stuValidEndTime = make_net_time(end_dt)
        return user

    def update_user(self, user_id, name, status=0, doors=None, valid_begin=None, valid_end=None):
        with self.lock:
            self.ensure()
            user = self._build_access_user_info(
                user_id=user_id,
                name=name,
                status=status,
                doors=doors,
                valid_begin=valid_begin,
                valid_end=valid_end,
            )

            fail_codes = (C_ENUM * 1)()
            inParam = NET_IN_ACCESS_USER_SERVICE_INSERT()
            inParam.dwSize = sizeof(inParam)
            inParam.nInfoNum = 1
            inParam.pUserInfo = pointer(user)
            outParam = NET_OUT_ACCESS_USER_SERVICE_INSERT()
            outParam.dwSize = sizeof(outParam)
            outParam.nMaxRetNum = 1
            outParam.pFailCode = cast(fail_codes, POINTER(C_ENUM))

            ok = self.sdk.OperateAccessUserService(
                self.loginID,
                EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_INSERT,
                inParam, outParam, 5000
            )
            self.last_active = time.time()
            if not ok:
                raise Exception(self.sdk.GetLastErrorMessage())
            return True

    def add_user(self, user_id, name, valid_begin="2000-01-01", valid_end="2037-12-31", status=0, doors=None):
        return self.update_user(
            user_id=user_id,
            name=name,
            status=status,
            doors=doors or [0],
            valid_begin=valid_begin or "2000-01-01",
            valid_end=valid_end or "2037-12-31",
        )

    def _require_existing_user(self, user_id):
        user = self.get_user_by_id(user_id)
        if not user:
            raise Exception("用户不存在")
        return user

    def freeze_user(self, user_id):
        user = self._require_existing_user(user_id)
        return self.update_user(
            user_id=user_id,
            name=user["name"],
            status=1,
            doors=user["doors"],
            valid_begin=user["valid_begin"],
            valid_end=user["valid_end"],
        )

    def unfreeze_user(self, user_id):
        user = self._require_existing_user(user_id)
        return self.update_user(
            user_id=user_id,
            name=user["name"],
            status=0,
            doors=user["doors"],
            valid_begin=user["valid_begin"],
            valid_end=user["valid_end"],
        )

    def update_user_validity(self, user_id, valid_begin, valid_end):
        user = self._require_existing_user(user_id)
        return self.update_user(
            user_id=user_id,
            name=user["name"],
            status=user["status"],
            doors=user["doors"],
            valid_begin=valid_begin,
            valid_end=valid_end,
        )

    def _get_user_by_id_nolock(self, user_id):
        """内部使用：不加锁的单用户查询（调用前必须已持有锁或确保单线程）"""
        self.ensure()
        fail_codes = (C_ENUM * 1)()
        users = (NET_ACCESS_USER_INFO * 1)()
        inParam = NET_IN_ACCESS_USER_SERVICE_GET()
        inParam.dwSize = sizeof(inParam)
        inParam.nUserNum = 1
        inParam.szUserID = user_id.encode().ljust(32, b'\x00')
        outParam = NET_OUT_ACCESS_USER_SERVICE_GET()
        outParam.dwSize = sizeof(outParam)
        outParam.nMaxRetNum = 1
        outParam.pUserInfo = cast(users, POINTER(NET_ACCESS_USER_INFO))
        outParam.pFailCode = cast(fail_codes, POINTER(C_ENUM))

        ok = self.sdk.OperateAccessUserService(
            self.loginID,
            EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_GET,
            inParam, outParam, 5000
        )
        self.last_active = time.time()
        if not ok:
            return None
        u = users[0]
        name = u.szName.decode('utf-8', errors='ignore').strip('\x00')
        begin = u.stuValidBeginTime
        end = u.stuValidEndTime
        return {
            "user_id": user_id,
            "name": name,
            "status": u.nUserStatus,
            "doors": [u.nDoors[i] for i in range(u.nDoorNum)],
            "valid_begin": f"{begin.dwYear}-{begin.dwMonth:02d}-{begin.dwDay:02d}",
            "valid_end": f"{end.dwYear}-{end.dwMonth:02d}-{end.dwDay:02d}",
        }

    def get_user_by_id(self, user_id):
        """按用户ID精确查询设备人员（线程安全）"""
        with self.lock:
            return self._get_user_by_id_nolock(user_id)

    def delete_user(self, user_id):
        with self.lock:
            self.ensure()
            fail_codes = (C_ENUM * 1)()
            inParam = NET_IN_ACCESS_USER_SERVICE_REMOVE()
            inParam.dwSize = sizeof(inParam)
            inParam.nUserNum = 1
            inParam.szUserID = user_id.encode().ljust(32, b'\x00')
            outParam = NET_OUT_ACCESS_USER_SERVICE_REMOVE()
            outParam.dwSize = sizeof(outParam)
            outParam.nMaxRetNum = 1
            outParam.pFailCode = cast(fail_codes, POINTER(C_ENUM))

            ok = self.sdk.OperateAccessUserService(
                self.loginID,
                EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_REMOVE,
                inParam, outParam, 5000
            )
            self.last_active = time.time()
            if not ok:
                raise Exception(self.sdk.GetLastErrorMessage())
            return True

    # ---------- 分页获取全部用户（修复死锁）----------
    def get_users_paginated(self, offset=0, limit=20):
        """分页获取设备人员（稳定版，避免死锁）。

        设备 SDK 没有直接的“列出所有人员 UserID”接口，这里通过设备记录集
        ACCESSCTLCARD 枚举 szUserID；这只是 SDK 数据入口，不表示系统在使用门禁卡业务。
        """
        def _do_query():
            print("[get_users_paginated] 开始获取用户ID列表...")
            with self.lock:
                all_user_ids = self._list_user_ids_from_device_records()

            total = len(all_user_ids)
            print(f"[get_users_paginated] 共找到 {total} 个 UserID")

            # 第二步：截取当前页的ID。get_user_by_id 每次只短暂持锁，避免整页查询阻塞其他 SDK 操作。
            page_ids = all_user_ids[offset:offset+limit]
            users = []
            for idx, uid in enumerate(page_ids):
                print(f"[get_users_paginated] 查询第 {idx+1}/{len(page_ids)} 个用户: {uid}")
                info = self.get_user_by_id(uid)
                if info:
                    info["hasFace"] = False
                    users.append(info)
            return total, users

        # 整体超时控制：最长等待 60 秒
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_query)
                return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            print("[get_users_paginated] 整体操作超时（60秒）")
            raise Exception("查询设备人员超时，请稍后重试")

    def _list_user_ids_from_device_records(self):
        """从设备记录集中枚举人员 UserID。调用方负责控制锁粒度。

        说明：当前大华 SDK 需要通过 ACCESSCTLCARD 记录集读取 szUserID，
        这里没有新增或依赖门禁卡业务，只是复用设备固件暴露的人员索引。
        """
        self.ensure()
        condition = NET_A_FIND_RECORD_ACCESSCTLCARD_CONDITION()
        condition.dwSize = sizeof(condition)
        condition.abCardNo = False
        condition.abUserID = False
        condition.abIsValid = False

        inParam = NET_IN_FIND_RECORD_PARAM()
        inParam.dwSize = sizeof(inParam)
        inParam.emType = EM_NET_RECORD_TYPE.ACCESSCTLCARD
        inParam.pQueryCondition = cast(byref(condition), c_void_p)

        outParam = NET_OUT_FIND_RECORD_PARAM()
        outParam.dwSize = sizeof(outParam)

        result = self.sdk.FindRecord(self.loginID, inParam, outParam, 5000)
        if not result:
            raise Exception(f"获取用户ID列表失败: {self.sdk.GetLastErrorMessage()}")

        findHandle = outParam.lFindeHandle
        all_user_ids = []
        seen = set()
        BATCH = 50
        try:
            while True:
                findIn = NET_IN_FIND_NEXT_RECORD_PARAM()
                findIn.dwSize = sizeof(findIn)
                findIn.lFindeHandle = findHandle
                findIn.nFileCount = BATCH

                records = (NET_RECORDSET_ACCESS_CTL_CARD * BATCH)()
                for rec in records:
                    rec.dwSize = sizeof(rec)

                findOut = NET_OUT_FIND_NEXT_RECORD_PARAM()
                findOut.dwSize = sizeof(findOut)
                findOut.pRecordList = cast(records, c_void_p)
                findOut.nMaxRecordNum = BATCH

                ret = self.sdk.FindNextRecord(findIn, findOut, 5000)
                got = findOut.nRetRecordNum
                if not ret or got == 0:
                    break

                for i in range(got):
                    uid = records[i].szUserID.decode('utf-8', errors='ignore').strip('\x00')
                    if uid and uid not in seen:
                        seen.add(uid)
                        all_user_ids.append(uid)
        finally:
            self.sdk.FindRecordClose(findHandle)
        self.last_active = time.time()
        return all_user_ids

    # ---------- 按姓名模糊搜索 ----------
    def search_users_by_name(self, keyword):
        """按姓名模糊搜索设备人员。

        只在单次 SDK 调用期间持锁，避免 500 人级别搜索时长时间占用
        self.lock 并阻塞开门、查日志等其他操作；外层线程池提供整体超时。
        """
        def _do_search():
            with self.lock:
                all_user_ids = self._list_user_ids_from_device_records()

            matched = []
            for uid in all_user_ids:
                # get_user_by_id 每次只短暂持锁，给其他 SDK 操作插队机会。
                info = self.get_user_by_id(uid)
                if info and keyword in info["name"]:
                    matched.append(info)
            return matched

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_search)
                return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            print("[search_users_by_name] 整体操作超时（60秒）")
            raise Exception("按姓名搜索设备人员超时，请稍后重试")

    # ================= 人脸 =================
    def add_face(self, user_id, path, max_kb=0, width=0, height=0, quality=0, force=False):
        img = compress_image(path, max_kb=max_kb, width=width, height=height, quality=quality)
        b64 = base64.b64encode(img).decode()
        url = f"http://{self.ip}/cgi-bin/FaceInfoManager.cgi?action=add"
        r = requests.post(
            url,
            json={"UserID": user_id, "Info": {"PhotoData": [b64]}},
            auth=HTTPDigestAuth(self.username, self.password),
            timeout=30
        )
        if r.status_code == 200:
            return True

        # CGI 400 且 force=True：调用方明确要求覆盖，降级到 update 接口直接覆盖旧人脸。
        # force=False 时不降级，因为 400 很可能是照片质量问题而非已有人脸。
        if r.status_code == 400 and force:
            try:
                url_update = f"http://{self.ip}/cgi-bin/FaceInfoManager.cgi?action=update"
                r_update = requests.post(
                    url_update,
                    json={"UserID": user_id, "Info": {"PhotoData": [b64]}},
                    auth=HTTPDigestAuth(self.username, self.password),
                    timeout=30
                )
                if r_update.status_code == 200:
                    return True
                # update 也失败，抛出 update 的错误（可能是照片质量问题）
                reason = self._parse_face_cgi_error("update", user_id, r_update.status_code, r_update.text)
                raise FaceCGIError(reason, user_id=user_id, action="update", status_code=r_update.status_code)
            except FaceCGIError:
                raise  # 重新抛出 update 的错误
            except Exception:
                pass  # 其他异常（网络等），继续抛原始 add 错误

        reason = self._parse_face_cgi_error("add", user_id, r.status_code, r.text)
        raise FaceCGIError(reason, user_id=user_id, action="add", status_code=r.status_code)

    def update_face(self, user_id, path, max_kb=0, width=0, height=0, quality=0):
        """更新用户人脸照片（直接覆盖已有照片）"""
        img = compress_image(path, max_kb=max_kb, width=width, height=height, quality=quality)
        b64 = base64.b64encode(img).decode()
        url = f"http://{self.ip}/cgi-bin/FaceInfoManager.cgi?action=update"
        r = requests.post(
            url,
            json={"UserID": user_id, "Info": {"PhotoData": [b64]}},
            auth=HTTPDigestAuth(self.username, self.password),
            timeout=30
        )
        if r.status_code == 200:
            return True
        
        reason = self._parse_face_cgi_error("update", user_id, r.status_code, r.text)
        raise FaceCGIError(reason, user_id=user_id, action="update", status_code=r.status_code)

    @staticmethod
    def _parse_face_cgi_error(action, user_id, status_code, body):
        """将 CGI 错误码转换为用户可读的中文原因"""
        if status_code == 400:
            return (f"下发人脸失败({status_code})：设备拒绝该请求，"
                    f"可能原因：1)该用户已有人脸数据 2)照片未通过设备人脸检测 3)照片数据过大或格式异常")
        if status_code == 501:
            return f"设备不支持人脸{action}操作({status_code})"
        return f"人脸{action}失败({status_code}): {body.strip()}"

    # ================= RPC2 人脸查询 =================
    def query_face_status(self, user_ids=None):
        """RPC2 AccessFace.list — 批量查询设备上的人脸状态

        Args:
            user_ids: list[str] | None
                None → 查询设备上所有有脸的用户
                ["id1","id2"] → 查指定用户

        Returns:
            dict[str, bool] | None: {user_id: has_face} 或 None（RPC2 失败）
        """
        params = {}
        if user_ids:
            params["UserIDList"] = user_ids
        r = self._rpc_call("AccessFace.list", params)
        if r and r.get("result"):
            face_list = r.get("params", {}).get("FaceDataList", [])
            result = {}
            for item in face_list:
                uid = item.get("UserID")
                if uid:
                    has = bool(item.get("PhotoData")) or bool(item.get("FaceData"))
                    result[uid] = has
            return result
        return None

    def query_face_info(self, user_id):
        """RPC2 AccessFace.startFind/doFind — 单用户人脸详细信息（含MD5）

        Args:
            user_id: str

        Returns:
            dict | None: 人脸信息（含 MD5），无脸时返回 {"has_face": False}
        """
        r = self._rpc_call("AccessFace.startFind", {
            "Condition": {"UserID": ["==", user_id]}
        })
        if not r or not r.get("result"):
            return None
        token = r.get("params", {}).get("Token", -1)
        if token < 0:
            return None
        try:
            r2 = self._rpc_call("AccessFace.doFind", {
                "Token": token, "Offset": 0, "Count": 1
            })
            if r2 and r2.get("result"):
                info = r2.get("params", {}).get("Info", [])
                if info:
                    item = info[0]
                    item["has_face"] = True
                    return item
                return {"has_face": False}
            return None
        finally:
            self._rpc_call("AccessFace.stopFind", {"Token": token})

    def fetch_face_photos(self, user_ids=None):
        """从设备 RPC2 拉取人脸照片并保存到本地 faces/ 目录。

        重要：指定 user_ids 时逐个调用 AccessFace.list(UserIDList=[uid])。
        实测部分设备一次传几百个 UserID 会直接失败；查 ID 路径单个获取稳定。

        Args:
            user_ids: list[str] | None
                None → 拉取设备上所有有脸用户的照片
                ["id1","id2"] → 逐个拉取指定用户

        Returns:
            dict[str, str]: {user_id: 本地路径} 或 {user_id: ""}（失败/无照片/设备未返回）
        """
        import base64 as _b64
        faces_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faces")
        os.makedirs(faces_dir, exist_ok=True)

        requested_ids = [str(uid) for uid in user_ids] if user_ids else None
        results = {}

        def _save_face_item(item):
            uid = str(item.get("UserID") or "")
            if not uid:
                return None, ""
            photos = item.get("PhotoData", []) or []
            if not photos:
                print(f"[fetch_face_photos] {uid} no PhotoData")
                return uid, ""
            try:
                img_data = _b64.b64decode(photos[0])
                if not img_data:
                    raise ValueError("empty decoded image")
                path = os.path.join(faces_dir, f"{uid}.jpg")
                with open(path, "wb") as f:
                    f.write(img_data)
                return uid, path
            except Exception as e:
                print(f"[fetch_face_photos] {uid} decode/save failed: {e}")
                return uid, ""

        if requested_ids:
            print(f"[fetch_face_photos] one-by-one requested={len(requested_ids)}")
            for idx, uid in enumerate(requested_ids, 1):
                print(f"[fetch_face_photos] 查询第 {idx}/{len(requested_ids)} 个用户人脸: {uid}")
                r = self._rpc_call("AccessFace.list", {"UserIDList": [uid]})
                if not r or not r.get("result"):
                    print(f"[fetch_face_photos] RPC2 AccessFace.list failed, uid={uid}")
                    results[uid] = ""
                    continue
                face_list = r.get("params", {}).get("FaceDataList", []) or []
                if not face_list:
                    print(f"[fetch_face_photos] device did not return requested user, uid={uid}")
                    results[uid] = ""
                    continue
                saved = ""
                for item in face_list:
                    item_uid, path = _save_face_item(item)
                    if item_uid == uid:
                        saved = path
                        break
                results[uid] = saved
        else:
            r = self._rpc_call("AccessFace.list", {})
            if not r or not r.get("result"):
                print("[fetch_face_photos] RPC2 AccessFace.list failed, requested=ALL")
                return {}
            face_list = r.get("params", {}).get("FaceDataList", []) or []
            print(f"[fetch_face_photos] requested=ALL, returned={len(face_list)}")
            for item in face_list:
                uid, path = _save_face_item(item)
                if uid:
                    results[uid] = path

        failed = [uid for uid, path in results.items() if not path]
        if failed:
            print(f"[fetch_face_photos] failed/no-photo={len(failed)}, sample={failed[:20]}")
        return results

    # ================= 门状态（CGI） =================
    def get_door_status(self, channel=1):
        """查询门状态，返回 'Open' / 'Close'，失败返回 None"""
        import re
        urls = [
            f"http://{self.ip}/cgi-bin/accessControl.cgi?action=getLockStatus&channel={channel}",
            f"http://{self.ip}/cgi-bin/accessControl.cgi?action=getDoorStatus&channel={channel}"
        ]
        for url in urls:
            try:
                r = requests.get(url, auth=HTTPDigestAuth(self.username, self.password), timeout=10)
                if r.status_code == 200:
                    text = r.text.strip()
                    # 用正则精确匹配 status=Open / status=Close，避免 split 抓到多余内容
                    m = re.search(r'\bstatus=(\w+)', text)
                    if m:
                        val = m.group(1)
                        if val in ('Open', 'Close'):
                            return val
            except Exception:
                continue
        return None

    def get_door_status_rpc(self, channel=0):
        """通过 RPC2 查询门状态(读门磁信号),返回 'Open'/'Close',失败返回 None。

        跟 get_door_status() 的 CGI 'getLockStatus' 不是同一个信号源:
          - CGI getLockStatus 读"电锁通断电指令"(逻辑态)
          - RPC2 getDoorStatus  读"门磁触点输入"(物理态);设备网页主页的门图标走这条
        多数情况两者一致,门半开/常开/门磁未接线时会差。

        channel: accessControl.factory.instance 的通道号,网页主页用 0;
        若你的 API 仍按 1 起的门号传入,调用端自行 `channel - 1`。
        """
        r = self._rpc_call("accessControl.factory.instance", {"channel": channel})
        if not r or not isinstance(r.get("result"), int):
            return None
        obj = r["result"]
        r = self._rpc_call("accessControl.getDoorStatus", obj=obj)
        if not r:
            return None
        info = (r.get("params") or {}).get("Info") or {}
        status = info.get("status")
        return status if status in ("Open", "Close") else None

    # ================= 门模式（RPC2） =================
    def _ensure_rpc_attrs(self):
        """补齐旧缓存实例缺失的 RPC2 状态字段。"""
        if not hasattr(self, '_rpc_lock'):
            self._rpc_lock = threading.RLock()
        if not hasattr(self, '_rpc_id'):
            self._rpc_id = 1000
        if not hasattr(self, '_rpc_session'):
            self._rpc_session = None
        if not hasattr(self, '_rpc_keepalive_interval'):
            self._rpc_keepalive_interval = 60
        if not hasattr(self, '_rpc_last_keepalive'):
            self._rpc_last_keepalive = 0

    def _next_rpc_id_locked(self):
        self._rpc_id += 1
        return self._rpc_id

    def _rpc_login_locked(self, force=False):
        """RPC2 登录。调用方需持有 _rpc_lock。"""
        if not force and getattr(self, '_rpc_session', None) is not None:
            return self._rpc_session
        self._rpc_session = None
        import hashlib
        login_url = f"http://{self.ip}/RPC2_Login"
        try:
            # Step 1: 空密码获取 realm + random
            r = requests.post(login_url, json={
                "method": "global.login",
                "params": {"userName": self.username, "password": "", "clientType": "Web3.0"},
                "id": self._next_rpc_id_locked(), "session": 0
            }, timeout=10)
            data = r.json()
            params = data.get("params") or {}
            realm = params["realm"]
            random_key = params["random"]
            session = data["session"]

            # Step 2: 计算认证哈希
            pwd_md5 = hashlib.md5(f"{self.username}:{realm}:{self.password}".encode()).hexdigest().upper()
            login_hash = hashlib.md5(f"{self.username}:{random_key}:{pwd_md5}".encode()).hexdigest().upper()

            r2 = requests.post(login_url, json={
                "method": "global.login",
                "params": {"userName": self.username, "password": login_hash, "clientType": "Web3.0",
                           "authorityType": "Default", "passwordType": "Default"},
                "id": self._next_rpc_id_locked(), "session": session
            }, timeout=10)
            data2 = r2.json()
            if data2.get("result") and data2.get("session"):
                self._rpc_session = data2["session"]
                keepalive = (data2.get("params") or {}).get("keepAliveInterval")
                try:
                    keepalive = int(keepalive)
                    if keepalive > 0:
                        self._rpc_keepalive_interval = keepalive
                except (TypeError, ValueError):
                    pass
                self._rpc_last_keepalive = time.time()
                return self._rpc_session
            print(f"[rpc_login] failed ip={self.ip}, resp={data2}")
        except Exception as e:
            print(f"[rpc_login] failed ip={self.ip}: {e}")
        self._rpc_session = None
        return None

    def _rpc_login(self, force=False):
        """懒加载 RPC2 登录（兼容旧缓存实例）"""
        self._ensure_rpc_attrs()
        with self._rpc_lock:
            return self._rpc_login_locked(force=force)

    def _rpc_post_locked(self, method, params=None, timeout=30, obj=None):
        """发送一次 RPC2 请求。调用方需持有 _rpc_lock。

        obj: 透传到 RPC2 body 的 `object` 字段(由 `*.factory.instance` 返回的句柄)。
        像 accessControl.getDoorStatus / accessControl.openDoor 都要求带上;
        configManager.setConfig 等不需要,留 None 即可。
        """
        body = {
            "method": method,
            "params": params or {},
            "id": self._next_rpc_id_locked(),
            "session": self._rpc_session,
        }
        if obj is not None:
            body["object"] = obj
        r = requests.post(f"http://{self.ip}/RPC2", json=body, timeout=timeout)
        self.last_active = time.time()
        return r.json()

    @staticmethod
    def _rpc_response_needs_relogin(resp):
        """判断 RPC2 响应是否表示会话已失效。"""
        if not isinstance(resp, dict) or resp.get("result") is True:
            return False
        error = resp.get("error")
        parts = []
        if isinstance(error, dict):
            parts.append(str(error.get("message", "")))
            parts.append(str(error.get("code", "")))
        elif error is not None:
            parts.append(str(error))
        parts.append(str(resp.get("message", "")))
        text = " ".join(parts).lower()
        keywords = (
            "session", "login", "challenge", "authorize", "authorization",
            "authentication", "unauthorized", "forbidden", "timeout",
            "expired", "not logged",
        )
        if any(k in text for k in keywords):
            return True
        try:
            return int((error or {}).get("code")) == 268632079
        except (TypeError, ValueError, AttributeError):
            return False

    def _rpc_keepalive_locked(self):
        """按设备返回的 keepAliveInterval 做一次按需续期。调用方需持有 _rpc_lock。"""
        if not getattr(self, '_rpc_session', None):
            return False
        try:
            interval = int(getattr(self, '_rpc_keepalive_interval', 60) or 60)
        except (TypeError, ValueError):
            interval = 60
        due_after = max(10, interval - 5)
        now = time.time()
        if now - getattr(self, '_rpc_last_keepalive', 0) < due_after:
            return True

        try:
            resp = self._rpc_post_locked("global.keepAlive", {
                "timeout": 300,
                "active": True,
            }, timeout=8)
        except (requests.RequestException, ValueError) as e:
            print(f"[rpc_keepalive] failed ip={self.ip}: {e}")
            self._rpc_session = None
            return False

        if resp and resp.get("result"):
            self._rpc_last_keepalive = now
            return True
        if self._rpc_response_needs_relogin(resp):
            print(f"[rpc_keepalive] session expired ip={self.ip}, resp={resp}")
            self._rpc_session = None
            return False

        # 部分固件不支持 keepAlive 或返回非标准结果，不能因此阻断真实业务调用。
        self._rpc_last_keepalive = now
        return True

    def _rpc_call(self, method, params=None, timeout=30, obj=None):
        """RPC2 调用，遇到会话失效时自动重登并重试一次。

        obj: 透传到 `object` 字段;详见 _rpc_post_locked。
        """
        self._ensure_rpc_attrs()
        for attempt in range(2):
            with self._rpc_lock:
                if getattr(self, '_rpc_session', None) is None:
                    if self._rpc_login_locked(force=True) is None:
                        return None

                if method != "global.keepAlive" and not self._rpc_keepalive_locked():
                    if self._rpc_login_locked(force=True) is None:
                        return None

                try:
                    resp = self._rpc_post_locked(method, params or {}, timeout=timeout, obj=obj)
                except (requests.RequestException, ValueError) as e:
                    if attempt == 0:
                        print(f"[rpc_call] {method} failed ip={self.ip}: {e}; relogin and retry")
                        self._rpc_session = None
                        continue
                    print(f"[rpc_call] {method} failed after relogin ip={self.ip}: {e}")
                    return None

                if attempt == 0 and self._rpc_response_needs_relogin(resp):
                    print(f"[rpc_call] {method} session expired ip={self.ip}, resp={resp}; relogin and retry")
                    self._rpc_session = None
                    continue
                return resp
        return None

    def _build_door_mode_patch(self, mode, state_variant=None):
        """构造门模式差异字段。

        设备网页后台写入 AccessControl 时会发送完整配置表。这里只返回需要覆盖的字段，
        set_door_mode() 会先读当前完整表，再把这些字段合并进去整表写回。
        """
        if mode == "AlwaysOpen":
            return {
                "DoorMode": "AlwaysOpen",
                "AlwaysOpen": True,
                "DoorOpenMode": "Always",
                "Mode": 4,
                # 设备后台常开实际使用 State="OpenAlways"，优先用这个值
                "State": state_variant or "OpenAlways",
            }
        if mode == "Normal":
            return {
                "DoorMode": "Normal",
                "AlwaysOpen": False,
                "DoorOpenMode": "Normal",
                "Mode": 0,
                "State": "Normal",
            }
        if mode == "AlwaysClose":
            return {
                "DoorMode": "AlwaysClose",
                "AlwaysOpen": False,
                "DoorOpenMode": "Normal",
                "Mode": 0,
                # 设备后台常闭实际使用 State="CloseAlways"，优先用这个值
                "State": state_variant or "CloseAlways",
            }
        raise ValueError(f"不支持的门模式: {mode}")

    def get_door_mode_config(self):
        """读取 AccessControl 门模式配置，过滤掉 RPC2 元数据字段。

        设备固件 getConfig 返回的配置表里混入了 RPC2 元数据字段
        （id/method/params/result/session/table），写回 setConfig 时
        会导致 Request length error，因此读表时必须过滤掉。
        """
        r = self._rpc_call("configManager.getConfig", {
            "name": "AccessControl",
            "onlyLocal": False,
        }, timeout=8)
        if not r or not r.get("result"):
            return None
        table = r.get("params", {}).get("table", [])
        if isinstance(table, list) and table:
            cfg = table[0]
        elif isinstance(table, dict):
            cfg = table
        else:
            return None
        # 过滤掉 RPC2 元数据字段（设备固件 bug 混进来的）
        meta_keys = {"id", "method", "params", "result", "session", "table"}
        return {k: v for k, v in cfg.items() if k not in meta_keys}

    def set_door_mode(self, mode):
        """设置门模式: 'Normal' | 'AlwaysOpen' | 'AlwaysClose'

        设备后台抓包显示 setConfig 会发送完整 AccessControl 表。只发 5 个模式字段时
        RPC2 可能返回 result=true，但常开/常闭在设备后台不变化。因此这里先读完整配置，
        合并模式字段后整表写回，并读回校验关键字段。
        """
        current_table = self.get_door_mode_config()
        if not current_table:
            print(f"[set_door_mode] get current AccessControl config failed mode={mode}")
            return False

        state_variants = [None]
        if mode == "AlwaysOpen":
            state_variants = ["OpenAlways", "Normal"]
        elif mode == "AlwaysClose":
            state_variants = ["CloseAlways", "Normal"]
        else:  # Normal
            state_variants = ["Normal"]

        last_resp = None
        for state_variant in state_variants:
            patch = self._build_door_mode_patch(mode, state_variant=state_variant)
            table = dict(current_table)
            table.update(patch)

            print(f"[set_door_mode] set mode={mode}, patch={patch}, full_table_keys={len(table)}")
            r = self._rpc_call("configManager.setConfig", {
                "name": "AccessControl",
                "table": [table],
                "options": [],
            })
            last_resp = r
            if not (r and r.get("result")):
                print(f"[set_door_mode] setConfig failed mode={mode}, patch={patch}, resp={r}")
                continue

            time.sleep(0.5)
            readback = self.get_door_mode_config()
            if not readback:
                print(f"[set_door_mode] readback failed mode={mode}, patch={patch}")
                continue

            ok = all(readback.get(k) == v for k, v in patch.items())
            if ok:
                return True

            got = {k: readback.get(k) for k in patch.keys()}
            print(f"[set_door_mode] readback mismatch mode={mode}, expected={patch}, got={got}")

        print(f"[set_door_mode] all variants failed mode={mode}, last_resp={last_resp}")
        return False

    # ================= 日志（已加时区转换） =================
    def query_log(self, start, end):
        with self.lock:
            self.ensure()
            records = []

            cond = NET_FIND_RECORD_ACCESSCTLCARDREC_CONDITION_EX()
            cond.dwSize = sizeof(cond)
            cond.bTimeEnable = True
            cond.stStartTime = make_net_time(start)
            cond.stEndTime = make_net_time(end)

            inParam = NET_IN_FIND_RECORD_PARAM()
            inParam.dwSize = sizeof(inParam)
            inParam.emType = EM_NET_RECORD_TYPE.ACCESSCTLCARDREC_EX
            inParam.pQueryCondition = cast(byref(cond), c_void_p)

            outParam = NET_OUT_FIND_RECORD_PARAM()
            outParam.dwSize = sizeof(outParam)

            if not self.sdk.FindRecord(self.loginID, inParam, outParam, 5000):
                raise Exception(self.sdk.GetLastErrorMessage())

            handle = outParam.lFindeHandle

            try:
                while True:
                    findIn = NET_IN_FIND_NEXT_RECORD_PARAM()
                    findIn.dwSize = sizeof(findIn)
                    findIn.lFindeHandle = handle
                    findIn.nFileCount = 20

                    recs = (NET_RECORDSET_ACCESS_CTL_CARDREC * 20)()
                    for r in recs:
                        r.dwSize = sizeof(r)

                    findOut = NET_OUT_FIND_NEXT_RECORD_PARAM()
                    findOut.dwSize = sizeof(findOut)
                    findOut.pRecordList = cast(recs, c_void_p)
                    findOut.nMaxRecordNum = 20

                    if not self.sdk.FindNextRecord(findIn, findOut, 5000):
                        break

                    if findOut.nRetRecordNum == 0:
                        break

                    for i in range(findOut.nRetRecordNum):
                        rec = recs[i]
                        t = rec.stuTime

                        # UTC -> 东八区
                        utc_time = datetime.datetime(
                            t.dwYear, t.dwMonth, t.dwDay,
                            t.dwHour, t.dwMinute, t.dwSecond,
                            tzinfo=timezone.utc
                        )
                        local_tz = timezone(timedelta(hours=8))
                        local_time = utc_time.astimezone(local_tz)

                        records.append({
                            "time": local_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "door": rec.nDoor,
                            "user_id": _decode_sdk_text(rec.szUserID),
                            "name": _decode_sdk_text(rec.szCardName),
                            "status": "成功" if rec.bStatus else "失败",
                            "method": METHOD_MAP.get(rec.emMethod, str(rec.emMethod))
                        })
            finally:
                self.sdk.FindRecordClose(handle)
            return records

    def is_busy(self):
        """是否有任何活跃的预览或回放句柄。用于 device_manager 空闲回收判断。"""
        return self.is_previewing() or self.is_playing_back()

    def close(self):
        # 关闭所有活跃的预览句柄
        with self._preview_lock:
            for handle in list(self._preview_handles.keys()):
                try:
                    self.sdk.StopRealPlayEx(handle)
                except Exception as e:
                    print(f"[preview] close 时停预览异常 play_id={handle}: {e}")
            self._preview_handles.clear()
        # 关闭所有活跃的回放句柄
        with self._playback_lock:
            for handle in list(self._playback_handles.keys()):
                try:
                    self.sdk.StopPlayBack(handle)
                except Exception as e:
                    print(f"[playback] close 时停回放异常 play_id={handle}: {e}")
            self._playback_handles.clear()
        with self.lock:
            if self.loginID:
                self.sdk.Logout(self.loginID)
                self.loginID = 0
            # 注意：sdk.Cleanup() 是全局调用，会影响所有设备连接，
            # 不能在单个客户端关闭时调用，应由进程退出时统一清理。
