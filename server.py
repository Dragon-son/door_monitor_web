"""项目入口:加载 app、注册全部蓝图、启动后台清理线程、解析参数并运行。

应用对象(app / sock / manager / db.init_db / before_request)都在
``routes/_app.py`` 完成,本文件只负责装配与启动。新增路由请到 ``routes/``
对应蓝图模块里加。
"""

import argparse
import atexit
import os
import threading
import time

# 第一行 import 必须是 _app —— 它在被导入时完成 db.init_db()、secret_key、
# manager 实例化、before_request 注册,任何蓝图模块都依赖这些已就绪。
from routes._app import app, manager, DATA_ROOT

from routes.auth import bp as bp_auth
from routes.devices import bp as bp_devices
from routes.persons import bp as bp_persons
from routes.door import bp as bp_door
from routes.users import bp as bp_users
from routes.face import bp as bp_face
from routes.logs import bp as bp_logs
from routes.monitor import bp as bp_monitor
from routes.admin import bp as bp_admin
from routes.audit import bp as bp_audit
from routes.misc import bp as bp_misc

# 触发 @sock.route 装饰器执行——本模块没有显式符号要用,只要导入即可。
from routes import stream_ws  # noqa: F401


for bp in (
    bp_auth, bp_devices, bp_persons, bp_door, bp_users,
    bp_face, bp_logs, bp_monitor, bp_admin, bp_audit, bp_misc,
):
    app.register_blueprint(bp)


def cleaner():
    while True:
        time.sleep(60)
        manager.cleanup()


threading.Thread(target=cleaner, daemon=True).start()


if __name__ == "__main__":
    if not os.path.exists(DATA_ROOT):
        os.makedirs(DATA_ROOT, exist_ok=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=15001)
    args = parser.parse_args()

    atexit.register(manager.shutdown)

    app.run("0.0.0.0", args.port, threaded=True)
