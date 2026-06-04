"""预览/监控/回放会话的共享状态与构造/销毁函数。

四个 sock 路由(实时预览视频/实时预览音频/NVR 监控/录像回放)共用同一份
``_preview_sessions`` 字典 + 同一把 ``_preview_lock`` 锁,因此必须放在
同一个模块里。任何蓝图模块都通过 `from routes.preview_state import ...`
访问,严禁在别处再创建一份。
"""

import queue
import select
import struct as _struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from routes._app import manager
from routes.codec_parsers import sniff_codec, make_parser


# 全局预览会话管理：device_id -> {ws_set, client, ffmpeg_proc, thread}
_preview_sessions = {}
_preview_lock = threading.Lock()


# 回放客户端 ID 单调递增
_playback_id_counter = [0]
_playback_id_lock = threading.Lock()


# ================= 每个 ws 的发送锁 =================
# simple_websocket.send() 内部对 wsproto(含 PerMessageDeflate 压缩窗口)和
# self.sock.send() 都没加锁。我们这边广播线程、初始 first_msg 发送、ping/pong
# 应答、回放控制 ack 等会从不同线程对同一个 ws 并发 send,导致:
#   1) PerMessageDeflate 压缩上下文被两个线程同时修改 -> 字节流损坏
#   2) socket 字节在 wire 上交错 -> 浏览器 "Invalid frame header"
# 表现为浏览器收到的 H.264 数据被损坏 -> VideoDecoder "Decoding error",
# 反复重建 decoder 后帧头错位,WS 整条挂掉。
# 修复:给每个 ws 加一把发送锁,所有 ws.send 都走 _safe_send 串行化。
_ws_send_locks = {}
_ws_send_locks_meta = threading.Lock()


def _ws_lock(ws):
    """获取/创建此 ws 的发送锁。同一个 ws 始终拿到同一把锁。"""
    key = id(ws)
    with _ws_send_locks_meta:
        lock = _ws_send_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _ws_send_locks[key] = lock
        return lock


def _drop_ws_lock(ws):
    """ws 退出时清掉锁条目,防止字典无界增长。幂等。"""
    with _ws_send_locks_meta:
        _ws_send_locks.pop(id(ws), None)


def _safe_send(ws, data):
    """线程安全的 ws.send。所有从应用层发往 ws 的数据都应走这里。"""
    with _ws_lock(ws):
        ws.send(data)


# ================= key 构造 =================
def _preview_key(device_id):
    return str(device_id)


def _monitor_preview_key(device_id, channel_no, stream_type):
    return f"monitor:{device_id}:{channel_no}:{stream_type}"


def _playback_key(device_id, channel_no, stream_type, client_id):
    """每个客户端独立会话:回放不共享。"""
    return f"playback:{device_id}:{channel_no}:{stream_type}:{client_id}"


def _next_playback_client_id():
    with _playback_id_lock:
        _playback_id_counter[0] += 1
        return _playback_id_counter[0]


# ================= MPEG-1 序列头解析 =================
def _parse_mpeg_sequence_header(data):
    """在 MPEG-1 视频 ES 数据中查找序列头 (00 00 01 B3)，返回 (width, height, offset) 或 (None, None, -1)"""
    idx = data.find(b'\x00\x00\x01\xb3')
    if idx < 0 or idx + 8 > len(data):
        return None, None, -1
    # MPEG-1 sequence header layout (after 4-byte start code):
    # 12 bits: horizontal_size
    # 12 bits: vertical_size
    hdr = data[idx + 4:idx + 8]
    width = (hdr[0] << 4) | (hdr[1] >> 4)
    height = ((hdr[1] & 0x0F) << 8) | hdr[2]
    return width, height, idx


# ================= 会话工厂(高层封装) =================
def _create_preview_session(device_id, device_info):
    # 门禁预览沿用 emDataType=H264(SDK 会对 H.265 源做转码),保留原有的音频/AU 行为
    return _create_video_preview_session(
        label=f"device_id={device_id}",
        device_info=device_info,
        channel=0,
        stream_type="main",
        include_audio=True,
        force_h264_transcode=True,
    )


def _create_monitor_preview_session(device_id, device_info, channel_no, stream_type):
    return _create_video_preview_session(
        label=f"monitor device_id={device_id} channel={channel_no} stream={stream_type}",
        device_info=device_info,
        channel=channel_no,
        stream_type=stream_type,
        include_audio=False,
    )


def _create_playback_session(device_id, device_info, channel_no, stream_type,
                              start_dt, end_dt, backward=False):
    return _create_video_preview_session(
        label=f"playback device_id={device_id} channel={channel_no} stream={stream_type}",
        device_info=device_info,
        channel=channel_no,
        stream_type=stream_type,
        include_audio=False,
        playback_args={
            "start_dt": start_dt,
            "end_dt": end_dt,
            "backward": backward,
        },
    )


# ================= 核心会话构造 =================
def _create_video_preview_session(label, device_info, channel=0, stream_type="main",
                                   include_audio=True, playback_args=None,
                                   force_h264_transcode=False):
    """
    创建预览会话(codec-agnostic,支持 H.264 / H.265):
    1. SDK 回调首包先嗅探 codec,确定后启动视频 ffmpeg(显式 -f h264 / -f hevc)
    2. NAL 解析与 access unit 拼装由 codec_parsers 抽象层完成
    3. WebCodecs 直通客户端拿到的是拼好的 AU(含 SPS/PPS/(VPS)+IDR)
    4. 音频路径保持喂原始 DHAV 数据(只在 include_audio=True 时启用)
    5. playback_args 非空时,走录像回放(PlayBackByDataType)
       格式: {"start_dt": datetime, "end_dt": datetime, "backward": bool}
    """
    print(f"[preview] 创建会话 v3(codec-agnostic) {label}"
          + (" [PLAYBACK]" if playback_args else ""))

    audio_proc = None
    if include_audio:
        # ---- 音频 FFmpeg(从 DHAV 包装里抽 PCM s16le 8kHz mono) ----
        # 输入留给 ffmpeg 自动嗅探:门禁机当前都是 H.264 DHAV,音频载荷格式不随
        # 视频 codec 变化,继续喂原始 data 即可。
        audio_ffmpeg_cmd = [
            "ffmpeg", "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-i", "pipe:0",
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "8000",
            "-ac", "1",
            "-f", "s16le",
            "pipe:1"
        ]
        audio_proc = subprocess.Popen(
            audio_ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    video_ws_set = set()
    audio_ws_set = set()
    webcodecs_ws_set = set()  # WebCodecs 直通客户端
    stop_event = threading.Event()
    video_proc_ready = threading.Event()  # codec 嗅探完成 + ffmpeg 启动后 set
    video_queue = queue.Queue(maxsize=500)  # AU → mpeg1 ffmpeg 输入队列
    audio_queue = queue.Queue(maxsize=500) if include_audio else None
    webcodecs_queue = queue.Queue(maxsize=500)  # AU → WebCodecs 客户端广播队列

    session_obj = {
        "video_ws_set": video_ws_set,
        "audio_ws_set": audio_ws_set,
        "webcodecs_ws_set": webcodecs_ws_set,
        "video_proc": None,        # codec 嗅探完成后才启动
        "audio_proc": audio_proc,
        "stop_event": stop_event,
        "video_proc_ready": video_proc_ready,
        "device_info": device_info,
        "label": label,
        # codec 嗅探状态
        "codec": None,             # None / "h264" / "h265"
        "parser": None,            # H264Parser / H265Parser 实例
        "sniff_buffer": b"",       # 嗅探阶段累积的 NAL 字节
        # 直通客户端的初始化 AU(SPS+PPS+(VPS)+IDR)
        "webcodecs_init": b"",
        "sps_nal": b"",
        "pps_nal": b"",
        "vps_nal": b"",            # H.265 用,H.264 永远空
        # 丢帧统计
        "frame_count": 0,
        "video_drop_count": 0,
        "webcodecs_drop_count": 0,
        "audio_drop_count": 0,
        # ffmpeg 延迟启动:只有第一个 mpeg1 客户端连接时才启动
        "video_ffmpeg_started": False,
        # 线程函数引用(延迟启动时才创建线程)
        "_video_writer_func": None,
        "_video_broadcast_func": None,
    }

    def _start_video_ffmpeg(codec_name):
        """codec 嗅探完成后启动视频转码 ffmpeg(显式 -f h264 / -f hevc)。

        启动失败(没装 ffmpeg / PATH 不通)时优雅降级:video_proc 留 None,
        mpeg1 客户端拿不到数据,但 WebCodecs 直通路径继续正常工作。
        """
        input_format = "hevc" if codec_name == "h265" else "h264"
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-fflags", "nobuffer",
            "-f", input_format,
            "-i", "pipe:0",
            "-f", "mpeg1video",
            "-vcodec", "mpeg1video",
            "-an",
            "pipe:1",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            print(f"[preview] 未找到 ffmpeg,mpeg1 路径不可用 {label}", flush=True)
            session_obj["video_proc"] = None
            video_proc_ready.set()  # 唤醒等待线程,让它们自然退出
            return None
        except Exception as e:
            print(f"[preview] 启动视频 ffmpeg 失败 {label}: {e}", flush=True)
            session_obj["video_proc"] = None
            video_proc_ready.set()
            return None
        session_obj["video_proc"] = proc
        video_proc_ready.set()
        print(f"[preview] 视频 ffmpeg 已启动 codec={codec_name} {label}", flush=True)
        return proc

    session_obj["_start_video_ffmpeg"] = _start_video_ffmpeg

    def _process_with_parser(nal_data):
        """已知 codec 后的标准处理路径:拼 carry,按 access unit 切并广播。

        返回 list[(au_payload_bytes, contains_idr)] —— 上层负责落队列。
        """
        parser = session_obj["parser"]
        carry = session_obj.get("nal_carry", b"")
        combined = carry + nal_data if carry else nal_data

        try:
            nals = parser.extract_nals(combined)
        except Exception as e:
            print(f"[preview] extract_nals 异常 {label}: {e}", flush=True)
            nals = []

        for nal_type, s, e in nals:
            if parser.is_sps(nal_type):
                session_obj["sps_nal"] = bytes(combined[s:e])
            elif parser.is_pps(nal_type):
                session_obj["pps_nal"] = bytes(combined[s:e])
            elif parser.is_vps(nal_type):
                session_obj["vps_nal"] = bytes(combined[s:e])

        # 完整 NAL 集合 = 除最后一个外(最后一个可能在 NAL 中段截断)
        if len(nals) >= 2:
            completed_nals = nals[:-1]
            new_carry = bytes(combined[nals[-1][1]:])
        elif len(nals) == 1:
            completed_nals = []
            new_carry = bytes(combined[nals[0][1]:])
        else:
            completed_nals = []
            new_carry = bytes(combined)

        au_buffer = session_obj.get("au_buffer", b"")
        au_has_vcl = session_obj.get("au_has_vcl", False)
        au_has_idr = session_obj.get("au_has_idr", False)
        au_has_sps = session_obj.get("au_has_sps", False)
        au_has_pps = session_obj.get("au_has_pps", False)
        au_has_vps = session_obj.get("au_has_vps", False)
        au_last_vcl = session_obj.get("au_last_vcl", False)

        aus_to_send = []
        is_h265 = (parser.codec_name == "h265")

        for nal_type, start, end in completed_nals:
            nal_bytes = bytes(combined[start:end])
            is_vcl = parser.is_vcl(nal_type)

            flush_au = False
            if au_buffer:
                if is_vcl:
                    if au_has_vcl and parser.is_new_picture_first_slice(nal_bytes):
                        flush_au = True
                else:
                    if au_last_vcl:
                        flush_au = True

            if flush_au:
                payload = au_buffer
                # IDR AU 缺 SPS/PPS/(VPS) 时补上,确保 decoder 一定能 configure
                if au_has_idr:
                    needs_h265_vps = is_h265 and not au_has_vps
                    needs_sps = not au_has_sps
                    needs_pps = not au_has_pps
                    if needs_sps or needs_pps or needs_h265_vps:
                        vps = session_obj.get("vps_nal", b"") if is_h265 else b""
                        sps = session_obj.get("sps_nal", b"")
                        pps = session_obj.get("pps_nal", b"")
                        prefix = b""
                        if needs_h265_vps and vps:
                            prefix += vps
                        if needs_sps and sps:
                            prefix += sps
                        if needs_pps and pps:
                            prefix += pps
                        if prefix:
                            payload = prefix + payload
                aus_to_send.append((payload, au_has_idr))
                au_buffer = b""
                au_has_vcl = False
                au_has_idr = False
                au_has_sps = False
                au_has_pps = False
                au_has_vps = False

            au_buffer += nal_bytes
            if is_vcl:
                au_has_vcl = True
                if parser.is_idr(nal_type):
                    au_has_idr = True
            if parser.is_sps(nal_type):
                au_has_sps = True
            if parser.is_pps(nal_type):
                au_has_pps = True
            if parser.is_vps(nal_type):
                au_has_vps = True
            au_last_vcl = is_vcl

        # carry / au_buffer 上限保护
        if len(new_carry) > 1024 * 1024:
            print(f"[preview] NAL carry 过长 {label} size={len(new_carry)},重置", flush=True)
            new_carry = b""
        if len(au_buffer) > 4 * 1024 * 1024:
            print(f"[preview] AU buffer 过长 {label} size={len(au_buffer)},重置", flush=True)
            au_buffer = b""
            au_has_vcl = False
            au_has_idr = False
            au_has_sps = False
            au_has_pps = False
            au_has_vps = False
            au_last_vcl = False

        session_obj["nal_carry"] = new_carry
        session_obj["au_buffer"] = au_buffer
        session_obj["au_has_vcl"] = au_has_vcl
        session_obj["au_has_idr"] = au_has_idr
        session_obj["au_has_sps"] = au_has_sps
        session_obj["au_has_pps"] = au_has_pps
        session_obj["au_has_vps"] = au_has_vps
        session_obj["au_last_vcl"] = au_last_vcl

        return aus_to_send

    def on_video_data(data: bytes):
        try:
            if stop_event.is_set():
                return

            # ---- DHAV 包头/包尾剥离 + 帧类型过滤 ----
            # RealPlayByDataType 回调中 dwDataType=0 的数据是 DHAV 容器,前 40 字节
            # 是 DHAV 帧头,末 8 字节 "dhav"+4B 是包尾。byte4 是帧类型:
            #   0xfd = 视频 I 帧, 0xfc = 视频 P 帧
            #   0xf0 = 音频(PCM/AAC等), 0xf1/0xfb/0xfe = info/metadata
            # 必须按帧类型过滤,否则音频 PCM 样本和 JSON metadata 会被当 NAL 数据塞
            # 进 parser,在噪声里凑巧匹配到 0x000001 起始码,污染 AU 让解码器拒帧。
            raw = bytes(data)
            nal_data = b""
            if len(raw) >= 5 and raw[:4] == b'DHAV':
                frame_type = raw[4]
                if frame_type in (0xfd, 0xfc):
                    strip_tail = 0
                    if len(raw) >= 48 and raw[-8:-4] == b'dhav':
                        strip_tail = 8
                    nal_data = raw[40: len(raw) - strip_tail] if strip_tail else raw[40:]
                # 0xf0 音频 / 0xf1 info 不进视频 pipeline;音频路径仍走下面 raw 喂 ffmpeg。
            elif raw:
                # 兜底:非 DHAV 容器(理论上不会发生)直接当 NAL 处理
                nal_data = raw

            # ---- 音频路径(原始 DHAV 数据,让 ffmpeg 自动嗅探) ----
            # 跟 codec 嗅探无关,可立即推送。
            if include_audio and audio_queue is not None:
                if audio_queue.full():
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    session_obj["audio_drop_count"] += 1
                audio_queue.put_nowait(raw)

            # ---- 视频路径:codec 嗅探 ----
            if session_obj["codec"] is None:
                buf = session_obj["sniff_buffer"] + nal_data
                codec = sniff_codec(buf)
                if codec is None:
                    if len(buf) > 2 * 1024 * 1024:
                        # 嗅探始终失败:兜底默认 H.264,继续走
                        print(f"[preview] codec 嗅探失败,兜底 h264 {label}", flush=True)
                        codec = "h264"
                    else:
                        session_obj["sniff_buffer"] = buf
                        return
                session_obj["codec"] = codec
                session_obj["parser"] = make_parser(codec)
                print(f"[preview] codec 嗅探完成 codec={codec} sniff_buffer_size={len(buf)} {label}", flush=True)
                if session_obj.get("video_ffmpeg_started") and session_obj.get("video_proc") is None:
                    session_obj["_start_video_ffmpeg"](codec)
                    _start_video_threads_once(session_obj)
                # 用嗅探缓冲 + 当前包统一进入正常处理
                data_to_process = buf
                session_obj["sniff_buffer"] = b""
            else:
                data_to_process = nal_data

            aus_to_send = _process_with_parser(data_to_process)

            # 更新初始化 AU(最后一个含 IDR 的完整 AU)
            for payload, contains_idr in aus_to_send:
                if contains_idr:
                    session_obj["webcodecs_init"] = payload

            # 视频 ffmpeg 队列(转码到 mpeg1)。仅在有 mpeg1 客户端时才启动 ffmpeg，
            # 无 mpeg1 客户端时跳过写入，避免无谓的 ffmpeg 进程和内存占用。
            if session_obj.get("video_ffmpeg_started"):
                for payload, _contains_idr in aus_to_send:
                    if video_queue.full():
                        try:
                            video_queue.get_nowait()  # 丢最旧帧腾空间
                        except queue.Empty:
                            pass
                        session_obj["video_drop_count"] += 1
                    video_queue.put_nowait(payload)

            # WebCodecs 直通队列
            for payload, _contains_idr in aus_to_send:
                if webcodecs_queue.full():
                    try:
                        webcodecs_queue.get_nowait()  # 丢最旧帧腾空间
                    except queue.Empty:
                        pass
                    session_obj["webcodecs_drop_count"] += 1
                webcodecs_queue.put_nowait(payload)

            # 帧计数和统计日志(每100帧输出一次)
            session_obj["frame_count"] += 1
            if session_obj["frame_count"] % 100 == 0:
                print(
                    f"[preview] {label} 统计: "
                    f"codec={session_obj['codec']} "
                    f"总帧数={session_obj['frame_count']} "
                    f"video丢帧={session_obj['video_drop_count']} "
                    f"webcodecs丢帧={session_obj['webcodecs_drop_count']} "
                    f"audio丢帧={session_obj['audio_drop_count']}",
                    flush=True
                )
        except Exception as e:
            print(f"[preview] on_video_data 异常 {label}: {e}", flush=True)

    # 视频写入线程:等 video_proc 启动后再开始(由 _ensure_video_ffmpeg 按需启动)
    def _video_writer_thread():
        # 等 codec 嗅探完成 + ffmpeg 启动(最多 10 秒,期间允许 stop_event 中断)
        while not stop_event.is_set():
            if video_proc_ready.wait(timeout=0.5):
                break
        if stop_event.is_set():
            return
        proc = session_obj["video_proc"]
        if proc is None:
            return  # ffmpeg 启动失败,优雅退出
        while not stop_event.is_set():
            try:
                data = video_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except Exception as e:
                print(f"[preview] 视频 pipe write error: {e}")
                stop_event.set()
                break

    session_obj["_video_writer_func"] = _video_writer_thread

    if include_audio and audio_proc is not None and audio_queue is not None:
        def _audio_writer_thread():
            while not stop_event.is_set():
                try:
                    data = audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    audio_proc.stdin.write(data)
                    audio_proc.stdin.flush()
                except Exception as e:
                    print(f"[preview] 音频 pipe write error: {e}")
                    stop_event.set()
                    break

        awt = threading.Thread(target=_audio_writer_thread, daemon=True, name=f"audio-writer-{label}")
        awt.start()
        session_obj["audio_writer_thread"] = awt

    # 统一从 device_manager 拿 client:同一台 NVR 上多通道预览 / 多客户端回放
    # 共享一个 NetSDK login,SDK 端通过句柄区分各路码流。
    client = manager.get(device_info)

    if playback_args:
        sdk_handle = client.start_playback(
            on_video_data,
            channel=channel,
            start_dt=playback_args["start_dt"],
            end_dt=playback_args["end_dt"],
            stream_type=stream_type,
            backward=playback_args.get("backward", False),
        )
    else:
        sdk_handle = client.start_preview(
            on_video_data, channel=channel,
            output_format='h264', stream_type=stream_type,
            force_h264_transcode=force_h264_transcode,
        )
    session_obj["client"] = client
    session_obj["sdk_handle"] = sdk_handle
    session_obj["is_playback"] = bool(playback_args)

    # --- 视频推流线程 ---
    session_obj["jsmp_header"] = b""
    session_obj["pre_buffer"] = b""
    session_obj["header_ready"] = threading.Event()

    def _send_to_targets(data, targets, ws_set):
        if not data or not targets:
            return
        dead = set()
        for ws_client in targets:
            try:
                _safe_send(ws_client, data)
            except Exception:
                dead.add(ws_client)
        if dead:
            with _preview_lock:
                ws_set.difference_update(dead)

    def _video_broadcast():
        # 等 ffmpeg 启动
        while not stop_event.is_set():
            if video_proc_ready.wait(timeout=0.5):
                break
        if stop_event.is_set():
            return
        proc = session_obj["video_proc"]
        if proc is None:
            return  # ffmpeg 启动失败,mpeg1 路径不可用
        CHUNK = 4096
        initial_buffer = b""
        jsmp_header = None
        pre_buffer = b""
        deadline = time.time() + 10

        while not stop_event.is_set() and time.time() < deadline:
            try:
                remaining = max(0, deadline - time.time())
                readable, _, _ = select.select([proc.stdout], [], [], min(0.5, remaining))
                if not readable:
                    continue
                chunk = proc.stdout.read(CHUNK)
            except Exception as e:
                if not stop_event.is_set():
                    print(f"[preview] video header read error: {e}")
                break
            if not chunk:
                break
            initial_buffer += chunk
            width, height, idx = _parse_mpeg_sequence_header(initial_buffer)
            if width is not None:
                jsmp_header = b"jsmp" + _struct.pack(">HH", width, height)
                pre_buffer = initial_buffer[idx:]
                print(f"[preview] 视频分辨率: {width}x{height} {label}")
                break

        if jsmp_header is None:
            print(f"[preview] 使用默认 1280x720 {label}")
            jsmp_header = b"jsmp" + _struct.pack(">HH", 1280, 720)
            pre_buffer = initial_buffer if initial_buffer else b""

        first_msg = jsmp_header + pre_buffer
        with _preview_lock:
            session_obj["jsmp_header"] = jsmp_header
            session_obj["pre_buffer"] = pre_buffer
            session_obj["header_ready"].set()
            targets = set(video_ws_set)
        _send_to_targets(first_msg, targets, video_ws_set)

        while not stop_event.is_set():
            try:
                chunk = proc.stdout.read(CHUNK)
                if not chunk:
                    break
                with _preview_lock:
                    targets = set(video_ws_set)
                _send_to_targets(chunk, targets, video_ws_set)
            except Exception as e:
                if not stop_event.is_set():
                    print(f"[preview] video broadcast error: {e}")
                break
        print(f"[preview] 视频推流线程退出 {label}")

    session_obj["_video_broadcast_func"] = _video_broadcast

    def _audio_broadcast():
        if not include_audio or audio_proc is None:
            return
        CHUNK = 4096
        while not stop_event.is_set():
            try:
                readable, _, _ = select.select([audio_proc.stdout], [], [], 0.5)
                if not readable:
                    continue
                chunk = audio_proc.stdout.read(CHUNK)
                if not chunk:
                    break
                with _preview_lock:
                    targets = set(audio_ws_set)
                _send_to_targets(chunk, targets, audio_ws_set)
            except Exception as e:
                if not stop_event.is_set():
                    print(f"[preview] audio broadcast error: {e}")
                break
        print(f"[preview] 音频推流线程退出 {label}")

    def _webcodecs_broadcast():
        while not stop_event.is_set():
            try:
                data = webcodecs_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with _preview_lock:
                targets = set(webcodecs_ws_set)
            if not targets:
                continue
            _send_to_targets(data, targets, webcodecs_ws_set)

    # 注意:视频写入线程和视频推流线程由 _ensure_video_ffmpeg 按需启动
    # (只有第一个 mpeg1 客户端连接时才启动,WebCodecs 客户端不需要)

    if include_audio:
        at = threading.Thread(target=_audio_broadcast, daemon=True, name=f"audio-{label}")
        at.start()
        session_obj["audio_thread"] = at
    wct = threading.Thread(target=_webcodecs_broadcast, daemon=True, name=f"webcodecs-{label}")
    wct.start()
    session_obj["webcodecs_thread"] = wct

    return session_obj


def _start_video_threads_once(session_obj):
    """启动 mpeg1 视频写入/广播线程；已启动则不重复创建。"""
    label = session_obj.get("label", "")

    writer_func = session_obj.get("_video_writer_func")
    if writer_func and not session_obj.get("video_writer_thread"):
        t = threading.Thread(target=writer_func, daemon=True, name=f"video-writer-{label}")
        t.start()
        session_obj["video_writer_thread"] = t

    broadcast_func = session_obj.get("_video_broadcast_func")
    if broadcast_func and not session_obj.get("video_thread"):
        t = threading.Thread(target=broadcast_func, daemon=True, name=f"video-{label}")
        t.start()
        session_obj["video_thread"] = t


def _ensure_video_ffmpeg(session_obj):
    """按需启动视频 ffmpeg + 写入/推流线程。

    只在第一个 mpeg1 客户端连接时调用。WebCodecs 直通客户端不需要 ffmpeg。
    已启动时幂等,不会重复创建进程/线程。
    """
    if session_obj.get("video_ffmpeg_started"):
        if session_obj.get("video_proc") is not None:
            _start_video_threads_once(session_obj)
        return
    session_obj["video_ffmpeg_started"] = True

    codec = session_obj.get("codec")
    if codec is None:
        # codec 尚未嗅探完成,ffmpeg 将在嗅探完成后由 on_video_data 启动。
        return

    session_obj["_start_video_ffmpeg"](codec)
    _start_video_threads_once(session_obj)


def _stop_preview_session(key, session_obj):
    """停止预览会话：按句柄停一路 SDK 流（不释放共享 client），并行终止两个 ffmpeg"""
    session_obj["stop_event"].set()
    # 只停本会话的 SDK 句柄；client 由 device_manager 池统一管理 LoginID 生命周期，
    # 同一 client 上其它会话的 handle 不能被波及。
    client = session_obj.get("client")
    handle = session_obj.get("sdk_handle")
    is_playback = session_obj.get("is_playback", False)
    if client and handle:
        try:
            if is_playback:
                client.stop_playback(handle)
            else:
                client.stop_preview(handle)
        except Exception as e:
            print(f"[preview] stop sdk handle error: {e}")

    def _close_proc(proc, label):
        """关闭 stdin → SIGTERM → wait(5s) → 超时则 SIGKILL 兜底"""
        if not proc:
            return
        try:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # ffmpeg 偶尔不响应 SIGTERM，强杀兜底，避免会话关闭卡 5 秒
                print(f"[preview] {label} ffmpeg 未响应 SIGTERM，kill 兜底")
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    print(f"[preview] {label} ffmpeg kill 后仍未退出，放弃等待")
        except Exception as e:
            print(f"[preview] {label} ffmpeg terminate error: {e}")

    # 并行关闭视频和音频 FFmpeg
    procs = [
        (session_obj.get("video_proc"), "video"),
        (session_obj.get("audio_proc"), "audio"),
    ]
    procs = [(p, label) for p, label in procs if p is not None]
    with ThreadPoolExecutor(max_workers=max(1, len(procs))) as ex:
        for p, label in procs:
            ex.submit(_close_proc, p, label)
