"""核心应用对象与全局单例。

放在这里的东西必须在任何蓝图模块或 helper 被导入之前完成初始化:
- Flask app + Flask-Sock 实例
- DeviceManager 单例
- 数据库初始化
- 全局 before_request 登录检查
"""

import os
import threading
from datetime import timedelta

from flask import Flask, request, session, jsonify
from flask_sock import Sock
from simple_websocket import ws as _sw_ws

import db
from device_manager import DeviceManager


# ============================================================
# simple-websocket 1.1.0 线程安全补丁
#
# 问题: simple_websocket.Base 中,wsproto.WSConnection 状态机
# (self.ws) 被多个线程同时操作 -> 状态损坏 -> 序列化出来的字节是
# 坏的 WS 帧 / 错乱的压缩输出 -> 浏览器报 "Invalid frame header"
# 或 H264 解码错误 -> 视频卡死。涉及路径:
#   - 应用 broadcast 线程: Base.send -> self.ws.send(Message)
#   - 后台 _thread 主循环: self.ws.receive_data(in_data)
#   - 后台 _thread 调用: _handle_events -> self.ws.events() +
#     self.ws.send(event.response()) 自动响应 PING/CLOSE
# wsproto 文档明确不支持多线程,所有 send/receive_data/events 必须
# 串行。
#
# 修复: 给每个 Base 实例一把 RLock,把所有访问 self.ws 的入口
# (send / close / _handle_events / _thread 主循环) 都串行化。
# RLock 避免内部回调二次进入死锁。
# ============================================================
_orig_base_init = _sw_ws.Base.__init__
_orig_base_send = _sw_ws.Base.send
_orig_base_close = _sw_ws.Base.close
_orig_handle_events = _sw_ws.Base._handle_events


def _patched_base_init(self, *args, **kwargs):
    self._ws_lock = threading.RLock()
    _orig_base_init(self, *args, **kwargs)


def _patched_base_send(self, data):
    with self._ws_lock:
        return _orig_base_send(self, data)


def _patched_base_close(self, *args, **kwargs):
    with self._ws_lock:
        return _orig_base_close(self, *args, **kwargs)


def _patched_handle_events(self):
    with self._ws_lock:
        return _orig_handle_events(self)


def _patched_thread(self):
    """重写 simple_websocket.Base._thread,在访问 self.ws 时拿 self._ws_lock。
    保持原有行为,只把对 wsproto 状态机的访问串行化。
    """
    import selectors
    from time import time
    from wsproto.frame_protocol import CloseReason
    from wsproto.events import Ping
    from wsproto.utilities import LocalProtocolError

    sel = None
    if self.ping_interval:
        next_ping = time() + self.ping_interval
        sel = self.selector_class()
        try:
            sel.register(self.sock, selectors.EVENT_READ, True)
        except ValueError:
            self.connected = False

    while self.connected:
        try:
            if sel:
                now = time()
                if next_ping <= now or not sel.select(next_ping - now):
                    if not self.pong_received:
                        self.close(reason=CloseReason.POLICY_VIOLATION,
                                   message='Ping/Pong timeout')
                        self.event.set()
                        break
                    self.pong_received = False
                    with self._ws_lock:
                        out = self.ws.send(Ping())
                        self.sock.send(out)
                    next_ping = max(now, next_ping) + self.ping_interval
                    continue
            in_data = self.sock.recv(self.receive_bytes)
            if len(in_data) == 0:
                raise OSError()
            with self._ws_lock:
                self.ws.receive_data(in_data)
            self.connected = self._handle_events()
        except (OSError, ConnectionResetError, LocalProtocolError):
            self.connected = False
            self.event.set()
            break
    sel.close() if sel else None
    self.sock.close()


_sw_ws.Base.__init__ = _patched_base_init
_sw_ws.Base.send = _patched_base_send
_sw_ws.Base.close = _patched_base_close
_sw_ws.Base._handle_events = _patched_handle_events
_sw_ws.Base._thread = _patched_thread


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.path.join(BASE, "data")

DEVICE_TYPE_ACCESS = "access"
DEVICE_TYPE_NVR = "nvr"


def _load_or_create_secret_key():
    env_key = os.environ.get('DAHUA_DOOR_WEB_SECRET_KEY')
    if env_key:
        return env_key
    key_file = os.path.join(BASE, 'secret_key')
    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            return f.read()
    key = os.urandom(32)
    with open(key_file, 'wb') as f:
        f.write(key)
    return key


app = Flask(
    __name__,
    static_folder=os.path.join(BASE, 'static'),
    static_url_path='/static',
)
sock = Sock(app)

app.secret_key = _load_or_create_secret_key()
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

manager = DeviceManager()

db.init_db()


@app.before_request
def check_login():
    if request.path in ["/", "/api/login", "/api/register", "/api/health"]:
        return
    if request.path.startswith("/api/"):
        if "username" not in session:
            return jsonify({"code": -1, "msg": "未登录"}), 401
