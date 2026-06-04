# device_manager.py

import threading
import time
from device_client import DeviceClient, cleanup_netsdk_runtime


class DeviceManager:

    def __init__(self, idle_timeout=300):
        self.pool = {}
        self.lock = threading.Lock()
        self.idle_timeout = idle_timeout

    def _key(self, d):
        # 连接池key包含账号密码，避免凭据变更后复用旧连接
        return f"{d['ip']}:{d['port']}:{d['username']}:{d['password']}"

    def get(self, device):
        key = self._key(device)

        # 快速路径：无锁查缓存，命中直接返回
        client = self.pool.get(key)
        if client is not None:
            # 更新活跃时间，防止正在使用的 client 被 cleanup 删除
            client.last_active = time.time()
            return client

        # 慢速路径：锁外创建连接（网络 I/O），避免阻塞其他设备
        new_client = DeviceClient(
            device['ip'],
            device['port'],
            device['username'],
            device['password']
        )

        with self.lock:
            if key not in self.pool:
                self.pool[key] = new_client
                return new_client
            # 并发创建时别的线程先插入了，关掉本线程的副本
            new_client.close()
            return self.pool[key]

    def cleanup(self):
        now = time.time()
        with self.lock:
            for k in list(self.pool.keys()):
                c = self.pool[k]
                # 预览/回放句柄活跃的 client 不能按空闲超时关闭：
                # NetSDK RealPlayEx/PlayBackByDataType 回调还在使用 client/sdk；
                # 此时 close() 会 Logout 把所有正在运行的句柄一起冲掉。
                # 同一个 client 现在可能同时挂多路预览/回放，只要有一路活跃就视为忙。
                if hasattr(c, "is_busy") and c.is_busy():
                    c.last_active = now
                    continue
                if now - c.last_active > self.idle_timeout:
                    c.close()
                    del self.pool[k]

    def shutdown(self):
        """进程退出时调用：关闭所有连接并清理 SDK 全局资源"""
        with self.lock:
            for k, c in list(self.pool.items()):
                try:
                    c.close()
                except Exception as e:
                    print(f"[shutdown] 关闭 {k} 失败: {e}")
            self.pool.clear()
        try:
            cleanup_netsdk_runtime()
            print("[shutdown] SDK Cleanup 完成")
        except Exception as e:
            print(f"[shutdown] SDK Cleanup 失败（可忽略）: {e}")
