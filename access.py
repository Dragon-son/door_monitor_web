from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Callback import fDisConnect, fHaveReConnect, fMessCallBackEx1, fAnalyzerDataCallBack
from NetSDK.SDK_Struct import *
from NetSDK.SDK_Enum import *
from ctypes import sizeof, cast, POINTER, pointer, byref, create_string_buffer, c_void_p, c_char_p
from PIL import Image
from requests.auth import HTTPDigestAuth
import requests
import base64
import hashlib
import datetime
import time
import os
import sys
import io

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ===== 设备连接配置 =====
# 公开仓库中不要写入真实设备地址、账号或密码。
# 可通过环境变量覆盖：
#   DAHUA_DEVICE_IP / DAHUA_DEVICE_PORT / DAHUA_DEVICE_USERNAME / DAHUA_DEVICE_PASSWORD
DEVICE_IP   = os.environ.get("DAHUA_DEVICE_IP", "10.112.5.26")
DEVICE_PORT = int(os.environ.get("DAHUA_DEVICE_PORT", "37777"))
USERNAME    = os.environ.get("DAHUA_DEVICE_USERNAME", "admin")
PASSWORD    = os.environ.get("DAHUA_DEVICE_PASSWORD", "Aa123456")
# ========================

METHOD_MAP = {
    1: "刷卡", 2: "密码", 3: "卡+密码", 4: "指纹",
    5: "远程开门", 6: "按钮开门", 16: "人脸识别",
}

ERROR_CODE_MAP = {
    0x00: "没有错误",
    0x10: "未授权",
    0x11: "卡挂失或注销",
    0x12: "没有该门权限",
    0x13: "开门模式错误",
    0x14: "有效期错误",
    0x15: "防反潜模式",
    0x16: "胁迫报警未打开",
    0x17: "门常闭状态",
    0x18: "AB互锁状态",
    0x19: "巡逻卡",
    0x1A: "设备处于闯入报警状态",
    0x20: "时间段错误",
    0x21: "假期内开门时间段错误",
    0x30: "需要先验证有首卡权限的卡片",
    0x40: "卡片正确,输入密码错误",
    0x41: "卡片正确,输入密码超时",
    0x42: "卡片正确,输入错误",
    0x43: "卡片正确,输入超时",
    0x44: "验证正确,输入密码错误",
    0x45: "验证正确,输入密码超时",
    0x50: "组合开门顺序错误",
    0x51: "组合开门需要继续验证",
    0x60: "验证通过,控制台未授权",
    0x61: "卡片正确,人脸错误",
    0x62: "卡片正确,人脸超时",
    0x63: "重复进入",
    0x64: "未授权,需要后端平台识别",
    0x65: "温度过高",
    0x66: "未戴口罩",
    0x67: "健康码获取失败",
    0x68: "黄码禁止通行",
    0x69: "红码禁止通行",
    0x6A: "健康码无效",
    0x6B: "绿码验证通过",
    0x70: "获取健康码信息",
    0x71: "校验证件信息",
    0xA8: "未佩戴安全帽",
}

STATUS_MAP = {
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_UNKNOWN): "未知",
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_OPEN): "门磁打开",
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_CLOSE): "门磁关闭",
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_ABNORMAL): "门磁异常",
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_FAKELOCKED): "假锁",
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_CLOSEALWAYS): "常闭",
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_OPENALWAYS): "常开",
    int(EM_A_NET_ACCESS_CTL_STATUS_TYPE.NET_ACCESS_CTL_STATUS_TYPE_NORMAL): "正常",
}

ACCESSORY_LOCK_STATE_MAP = {
    0: "未知/不适用",
    1: "未锁定",
    2: "锁定",
}

ACCESSORY_OUTPUT_STATUS_MAP = {
    0: "未知",
    1: "关闭",
    2: "开启",
}

ACCESSORY_OUTPUT_MODE_MAP = {
    0: "未知",
    1: "稳态",
    2: "脉冲",
}

OUTPUT_STATE_MAP = {
    False: "关闭",
    True: "打开",
}

SENSE_METHOD_MAP = {
    int(EM_A_NET_SENSE_METHOD.NET_SENSE_UNKNOWN): "未知",
    int(EM_A_NET_SENSE_METHOD.NET_SENSE_DOOR): "门磁",
    int(EM_A_NET_SENSE_METHOD.NET_SENSE_RSUDOOR): "门禁感应",
}

ALARM_ACTION_MAP = {
    0: "开始",
    1: "停止",
}

SENSOR_ABNORMAL_STATUS_MAP = {
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_UNKNOWN): "未知",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_SHORT): "短路",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_BREAK): "断路",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_INTRIDED): "被拆开",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_MASK): "遮挡",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_NORMAL): "正常",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_OFFLINE): "离线",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_ALARM): "报警",
    int(EM_SENSOR_ABNORMAL_STATUS.NET_SENSOR_ABNORMAL_STATUS_FAULT): "故障",
}

ALARM_COMMAND_MAP = {
    0x300C: "SDK未定义事件0x300C",
    0x3169: "SDK未定义事件0x3169",
    0x3177: "SDK未定义事件0x3177",
    0x31B3: "SDK未定义事件0x31B3",
    int(SDK_ALARM_TYPE.ALARM_ALARM_EX2): "本地报警",
    int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_EVENT): "门禁事件",
    int(SDK_ALARM_TYPE.ALARM_INPUT_SOURCE_SIGNAL): "报警输入源信号",
    int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_STATUS): "门禁状态",
    int(SDK_ALARM_TYPE.ALARM_SENSOR_ABNORMAL): "探测器异常",
    0x3475: "SDK未定义事件0x3475",
    0x3491: "SDK未定义事件0x3491",
}

def describe_error_code(error_code: int) -> str:
    return ERROR_CODE_MAP.get(int(error_code), f"未知错误(0x{int(error_code) & 0xff:02x})")


def format_access_status(success: bool, error_code: int) -> str:
    if success:
        return "成功"
    return f"失败(0x{int(error_code) & 0xff:02x} {describe_error_code(error_code)})"

# ─────────────────────────────────────────
# SDK 初始化
# ─────────────────────────────────────────
sdk = NetClient()
sdk.InitEx(fDisConnect(lambda a, b, c, d: None))
sdk.SetAutoReconnect(fHaveReConnect(lambda a, b, c, d: None))

stuIn = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
stuIn.dwSize     = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
stuIn.szIP       = DEVICE_IP.encode()
stuIn.nPort      = DEVICE_PORT
stuIn.szUserName = USERNAME.encode()
stuIn.szPassword = PASSWORD.encode()
stuIn.emSpecCap  = EM_LOGIN_SPAC_CAP_TYPE.TCP
stuOut = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
stuOut.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

loginID, _, error_msg = sdk.LoginWithHighLevelSecurity(stuIn, stuOut)
if loginID == 0:
    print(f"❌ SDK 登录失败: {error_msg}")
    sdk.Cleanup()
    exit(1)
print(f"✅ SDK 登录成功")

# ─────────────────────────────────────────
# RPC2 登录（保留，供其他功能备用）
# ─────────────────────────────────────────
RPC_LOGIN_URL = f"http://{DEVICE_IP}/RPC2_Login"
RPC_BASE_URL  = f"http://{DEVICE_IP}/RPC2"
rpc_session   = None
rpc_id        = 1


def rpc_login():
    global rpc_session, rpc_id
    try:
        r = requests.post(RPC_LOGIN_URL, json={
            "method": "global.login",
            "params": {"userName": USERNAME, "password": "", "clientType": "Web3.0"},
            "id": rpc_id,
            "session": 0
        }, timeout=10)
        rpc_id += 1
        data = r.json()

        realm      = data["params"]["realm"]
        random_key = data["params"]["random"]
        session    = data["session"]

        pwd_md5    = hashlib.md5(f"{USERNAME}:{realm}:{PASSWORD}".encode()).hexdigest().upper()
        login_hash = hashlib.md5(f"{USERNAME}:{random_key}:{pwd_md5}".encode()).hexdigest().upper()

        r2 = requests.post(RPC_LOGIN_URL, json={
            "method": "global.login",
            "params": {
                "userName": USERNAME,
                "password": login_hash,
                "clientType": "Web3.0",
                "authorityType": "Default",
                "passwordType": "Default"
            },
            "id": rpc_id,
            "session": session
        }, timeout=10)
        rpc_id += 1
        data2 = r2.json()

        if data2.get("result"):
            rpc_session = data2["session"]
            print(f"✅ RPC 登录成功")
            return True
        else:
            print(f"❌ RPC 登录失败: {data2}")
            return False
    except Exception as e:
        print(f"❌ RPC 登录异常: {e}")
        return False


def rpc_call(method, params):
    global rpc_id
    r = requests.post(RPC_BASE_URL, json={
        "method": method,
        "params": params,
        "id": rpc_id,
        "session": rpc_session
    }, timeout=30)
    rpc_id += 1
    return r.json()


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────
def make_net_time(dt: datetime.datetime) -> NET_TIME:
    t = NET_TIME()
    t.dwYear   = dt.year
    t.dwMonth  = dt.month
    t.dwDay    = dt.day
    t.dwHour   = dt.hour
    t.dwMinute = dt.minute
    t.dwSecond = dt.second
    return t


def compress_image(image_path: str, max_size: int = 0, width: int = 0, height: int = 0, quality: int = 0) -> bytes:
    """
    max_size : 限制文件大小（KB），0表示不限制
    width    : 缩放宽度，0表示不缩放
    height   : 缩放高度，0表示不缩放
    quality  : JPEG质量 1-95，0表示不压缩直接读原图
    """
    if max_size == 0 and width == 0 and height == 0 and quality == 0:
        # 默认不压缩，直接读取原图
        with open(image_path, "rb") as f:
            data = f.read()
        print(f"  原图大小: {len(data)} 字节")
        return data

    img = Image.open(image_path).convert("RGB")

    if width > 0 and height > 0:
        img = img.resize((width, height))
    elif width > 0 or height > 0:
        img.thumbnail((width or 9999, height or 9999))

    q = quality if quality > 0 else 85
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q)
    data = buf.getvalue()
    print(f"  压缩后大小: {len(data)} 字节 (quality={q})")
    return data


# ─────────────────────────────────────────
# 人员管理
# ─────────────────────────────────────────
def insert_user(user_id: str, name: str,
                door_list: list = None,
                valid_begin: datetime.datetime = None,
                valid_end: datetime.datetime = None):
    if door_list is None:
        door_list = [0]
    if valid_begin is None:
        valid_begin = datetime.datetime(2000, 1, 1)
    if valid_end is None:
        valid_end = datetime.datetime(2037, 12, 31)

    user = NET_ACCESS_USER_INFO()
    user.szUserID        = user_id.encode()
    user.szName          = name.encode('utf-8')
    user.emUserType      = EM_A_NET_ENUM_USER_TYPE.NET_ENUM_USER_TYPE_NORMAL
    user.nUserStatus     = 0
    user.nDoorNum        = len(door_list)
    for i, d in enumerate(door_list):
        user.nDoors[i] = d
    user.stuValidBeginTime = make_net_time(valid_begin)
    user.stuValidEndTime   = make_net_time(valid_end)

    fail_codes = (C_ENUM * 1)()
    inParam = NET_IN_ACCESS_USER_SERVICE_INSERT()
    inParam.dwSize    = sizeof(NET_IN_ACCESS_USER_SERVICE_INSERT)
    inParam.nInfoNum  = 1
    inParam.pUserInfo = pointer(user)
    outParam = NET_OUT_ACCESS_USER_SERVICE_INSERT()
    outParam.dwSize     = sizeof(NET_OUT_ACCESS_USER_SERVICE_INSERT)
    outParam.nMaxRetNum = 1
    outParam.pFailCode  = cast(fail_codes, POINTER(C_ENUM))

    result = sdk.OperateAccessUserService(
        loginID,
        EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_INSERT,
        inParam, outParam, 5000
    )
    if result:
        print(f"✅ 添加人员成功: {user_id} / {name}")
    else:
        print(f"❌ 添加人员失败: {sdk.GetLastErrorMessage()} "
              f"(错误码:{sdk.GetLastError()} FailCode:{fail_codes[0]})")


def get_user(user_id: str):
    fail_codes = (C_ENUM * 1)()
    users = (NET_ACCESS_USER_INFO * 1)()
    inParam = NET_IN_ACCESS_USER_SERVICE_GET()
    inParam.dwSize   = sizeof(NET_IN_ACCESS_USER_SERVICE_GET)
    inParam.nUserNum = 1
    inParam.szUserID = user_id.encode().ljust(32, b'\x00')
    outParam = NET_OUT_ACCESS_USER_SERVICE_GET()
    outParam.dwSize     = sizeof(NET_OUT_ACCESS_USER_SERVICE_GET)
    outParam.nMaxRetNum = 1
    outParam.pUserInfo  = cast(users, POINTER(NET_ACCESS_USER_INFO))
    outParam.pFailCode  = cast(fail_codes, POINTER(C_ENUM))

    result = sdk.OperateAccessUserService(
        loginID,
        EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_GET,
        inParam, outParam, 5000
    )
    if result:
        u      = users[0]
        name   = u.szName.decode('utf-8', errors='ignore')
        status = "正常" if u.nUserStatus == 0 else "冻结"
        doors  = [u.nDoors[i] for i in range(u.nDoorNum)]
        b, e   = u.stuValidBeginTime, u.stuValidEndTime
        print(f"✅ 查询成功:")
        print(f"   ID    : {user_id}")
        print(f"   姓名  : {name}")
        print(f"   状态  : {status}")
        print(f"   门权限: {doors}")
        print(f"   有效期: {b.dwYear}-{b.dwMonth:02d}-{b.dwDay:02d} "
              f"~ {e.dwYear}-{e.dwMonth:02d}-{e.dwDay:02d}")
    else:
        print(f"❌ 查询失败: {sdk.GetLastErrorMessage()} "
              f"(错误码:{sdk.GetLastError()} FailCode:{fail_codes[0]})")


def remove_user(user_id: str):
    fail_codes = (C_ENUM * 1)()
    inParam = NET_IN_ACCESS_USER_SERVICE_REMOVE()
    inParam.dwSize   = sizeof(NET_IN_ACCESS_USER_SERVICE_REMOVE)
    inParam.nUserNum = 1
    inParam.szUserID = user_id.encode().ljust(32, b'\x00')
    outParam = NET_OUT_ACCESS_USER_SERVICE_REMOVE()
    outParam.dwSize     = sizeof(NET_OUT_ACCESS_USER_SERVICE_REMOVE)
    outParam.nMaxRetNum = 1
    outParam.pFailCode  = cast(fail_codes, POINTER(C_ENUM))

    result = sdk.OperateAccessUserService(
        loginID,
        EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_REMOVE,
        inParam, outParam, 5000
    )
    if result:
        print(f"✅ 删除人员成功: {user_id}")
    else:
        print(f"❌ 删除人员失败: {sdk.GetLastErrorMessage()} "
              f"(错误码:{sdk.GetLastError()} FailCode:{fail_codes[0]})")


def find_user_by_name(name: str) -> list:
    """按姓名查找人员（逐个查询，避免批量限制）"""
    condition = NET_A_FIND_RECORD_ACCESSCTLCARD_CONDITION()
    condition.dwSize = sizeof(NET_A_FIND_RECORD_ACCESSCTLCARD_CONDITION)
    condition.abCardNo = False
    condition.abUserID = False
    condition.abIsValid = False

    inParam = NET_IN_FIND_RECORD_PARAM()
    inParam.dwSize = sizeof(NET_IN_FIND_RECORD_PARAM)
    inParam.emType = EM_NET_RECORD_TYPE.ACCESSCTLCARD
    inParam.pQueryCondition = cast(byref(condition), c_void_p)

    outParam = NET_OUT_FIND_RECORD_PARAM()
    outParam.dwSize = sizeof(NET_OUT_FIND_RECORD_PARAM)

    result = sdk.FindRecord(loginID, inParam, outParam, 5000)
    if not result:
        print(f"❌ FindRecord 失败: {sdk.GetLastErrorMessage()}")
        return []

    findHandle = outParam.lFindeHandle
    all_user_ids = []
    batch = 50

    while True:
        findIn = NET_IN_FIND_NEXT_RECORD_PARAM()
        findIn.dwSize = sizeof(NET_IN_FIND_NEXT_RECORD_PARAM)
        findIn.lFindeHandle = findHandle
        findIn.nFileCount = batch

        records = (NET_RECORDSET_ACCESS_CTL_CARD * batch)()
        for rec in records:
            rec.dwSize = sizeof(NET_RECORDSET_ACCESS_CTL_CARD)

        findOut = NET_OUT_FIND_NEXT_RECORD_PARAM()
        findOut.dwSize = sizeof(NET_OUT_FIND_NEXT_RECORD_PARAM)
        findOut.pRecordList = cast(records, c_void_p)
        findOut.nMaxRecordNum = batch

        ret = sdk.FindNextRecord(findIn, findOut, 5000)
        got = findOut.nRetRecordNum
        if not ret or got == 0:
            break

        for i in range(got):
            uid = records[i].szUserID.decode('utf-8', errors='ignore').strip('\x00')
            if uid and uid not in all_user_ids:
                all_user_ids.append(uid)

    sdk.FindRecordClose(findHandle)
    print(f"  共找到 {len(all_user_ids)} 个 UserID，开始逐个查询详情...")

    matched = []
    for idx, uid in enumerate(all_user_ids):
        if (idx + 1) % 10 == 0:
            print(f"    进度: {idx + 1}/{len(all_user_ids)}")

        fail_codes = (C_ENUM * 1)()
        users = (NET_ACCESS_USER_INFO * 1)()

        inParam = NET_IN_ACCESS_USER_SERVICE_GET()
        inParam.dwSize = sizeof(NET_IN_ACCESS_USER_SERVICE_GET)
        inParam.nUserNum = 1
        inParam.szUserID = uid.encode().ljust(32, b'\x00')

        outParam = NET_OUT_ACCESS_USER_SERVICE_GET()
        outParam.dwSize = sizeof(NET_OUT_ACCESS_USER_SERVICE_GET)
        outParam.nMaxRetNum = 1
        outParam.pUserInfo = cast(users, POINTER(NET_ACCESS_USER_INFO))
        outParam.pFailCode = cast(fail_codes, POINTER(C_ENUM))

        ok = sdk.OperateAccessUserService(
            loginID,
            EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_GET,
            inParam, outParam, 5000
        )
        if not ok:
            continue

        u = users[0]
        u_name = u.szName.decode('utf-8', errors='ignore').strip('\x00')
        if name in u_name:
            matched.append({
                "id": uid,
                "name": u_name,
                "status": "正常" if u.nUserStatus == 0 else "冻结",
                "doors": [u.nDoors[k] for k in range(u.nDoorNum)],
            })

    return matched


# ─────────────────────────────────────────
# 人脸管理（CGI 接口 - 推荐，更稳定，支持更大图片）
# ─────────────────────────────────────────
def cgi_post(action: str, payload: dict):
    """通用的 CGI POST（带 Digest 认证）"""
    url = f"http://{DEVICE_IP}/cgi-bin/FaceInfoManager.cgi?action={action}"
    try:
        r = requests.post(
            url,
            json=payload,
            auth=HTTPDigestAuth(USERNAME, PASSWORD),
            timeout=30
        )
        text = r.text.strip()
        # 大华 CGI 成功通常返回 "OK" 或包含 "result":true 的 JSON
        if r.status_code == 200 and (text in ("OK", "ok") or '"result": true' in text.lower() or "success" in text.lower()):
            return True, text or "OK"
        else:
            return False, f"HTTP {r.status_code}: {text}"
    except Exception as e:
        return False, f"请求异常: {e}"


def insert_face(user_id: str, image_path: str,
                max_kb: int = 0, width: int = 0, height: int = 0, quality: int = 0):
    """下发人脸（CGI，默认不压缩原图）"""
    img_bytes = compress_image(image_path, max_kb, width, height, quality)
    img_b64   = base64.b64encode(img_bytes).decode("utf-8")

    payload = {
        "UserID": user_id,
        "Info": {
            "PhotoData": [img_b64]
            # 可选添加: "UserName": "姓名", "FaceData": [...] 等
        }
    }

    success, msg = cgi_post("add", payload)
    if success:
        print(f"✅ 人脸下发成功: {user_id} (CGI)")
    else:
        print(f"❌ 人脸下发失败: {msg}")


def remove_face(user_id: str):
    """删除人脸"""
    payload = {
        "UserIDList": [user_id]
    }
    success, msg = cgi_post("delete", payload)
    if success:
        print(f"✅ 人脸删除成功: {user_id} (CGI)")
    else:
        print(f"❌ 人脸删除失败: {msg}")
        # 如果你的设备 delete 不行，可取消下面一行的注释改用 remove
        # success, msg = cgi_post("remove", {"UserIDList": [user_id]})


# ─────────────────────────────────────────
# 远程开门
# ─────────────────────────────────────────
def open_door(channel: int = 0):
    ctrl = NET_CTRL_ACCESS_OPEN()
    ctrl.dwSize              = sizeof(NET_CTRL_ACCESS_OPEN)
    ctrl.nChannelID          = channel
    ctrl.szTargetID          = None
    ctrl.emOpenDoorType      = EM_OPEN_DOOR_TYPE.EM_OPEN_DOOR_TYPE_REMOTE
    ctrl.emOpenDoorDirection = EM_OPEN_DOOR_DIRECTION.EM_OPEN_DOOR_DIRECTION_UNKNOWN
    result = sdk.ControlDevice(loginID, CtrlType.ACCESS_OPEN, ctrl, 5000)
    if result:
        print(f"✅ 远程开门成功 (门{channel})")
    else:
        print(f"❌ 远程开门失败: {sdk.GetLastErrorMessage()} (错误码:{sdk.GetLastError()})")


def close_door(channel: int = 0):
    ctrl = NET_CTRL_ACCESS_CLOSE()
    ctrl.dwSize = sizeof(NET_CTRL_ACCESS_CLOSE)
    ctrl.nChannelID = channel
    result = sdk.ControlDevice(loginID, CtrlType.ACCESS_CLOSE, ctrl, 5000)
    if result:
        print(f"✅ 远程关门/上锁成功 (门{channel})")
    else:
        print(f"❌ 远程关门/上锁失败: {sdk.GetLastErrorMessage()} (错误码:{sdk.GetLastError()})")


# ─────────────────────────────────────────
# 实时抓图
# ─────────────────────────────────────────
def _default_snapshot_path(channel: int) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join("snapshots", f"door_snap_ch{channel}_{stamp}.jpg")


def capture_snapshot(channel: int = 0, save_path: str | None = None,
                     quality: int = 1, image_size: int = 2,
                     wait_ms: int = 5000, buffer_size: int = 8 * 1024 * 1024):
    """抓取指定通道的一帧实时图像并保存为 JPG。"""
    if save_path is None:
        save_path = _default_snapshot_path(channel)

    abs_path = os.path.abspath(save_path)
    directory = os.path.dirname(abs_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    path_bytes = abs_path.encode("gbk", errors="ignore")
    if len(path_bytes) >= 260:
        raise ValueError(f"保存路径过长，SDK 路径字段最多 259 字节: {abs_path}")

    snap = SNAP_PARAMS()
    snap.Channel = channel
    snap.Quality = max(1, min(int(quality), 6))
    snap.ImageSize = int(image_size)
    snap.mode = 0
    snap.CmdSerial = int(time.time() * 1000) & 0xFFFF

    in_param = NET_IN_SNAP_PIC_TO_FILE_PARAM()
    in_param.dwSize = sizeof(NET_IN_SNAP_PIC_TO_FILE_PARAM)
    in_param.stuParam = snap
    in_param.szFilePath = path_bytes

    out_param = NET_OUT_SNAP_PIC_TO_FILE_PARAM()
    out_param.dwSize = sizeof(NET_OUT_SNAP_PIC_TO_FILE_PARAM)
    out_buf = create_string_buffer(buffer_size)
    out_param.szPicBuf = cast(out_buf, c_char_p)
    out_param.dwPicBufLen = buffer_size

    result = sdk.SnapPictureToFile(loginID, in_param, out_param, wait_ms)
    if not result:
        print(f"❌ 抓图失败: {sdk.GetLastErrorMessage()} (错误码:{sdk.GetLastError()})")
        return None

    if os.path.exists(abs_path) and os.path.getsize(abs_path) > 0:
        print(f"✅ 抓图成功: {abs_path} ({os.path.getsize(abs_path)} 字节)")
        return abs_path

    ret_len = int(out_param.dwPicBufRetLen)
    if ret_len > 0:
        with open(abs_path, "wb") as f:
            f.write(out_buf.raw[:ret_len])
        print(f"✅ 抓图成功: {abs_path} ({ret_len} 字节)")
        return abs_path

    print("❌ 抓图接口返回成功，但没有生成图片文件或图片数据")
    return None


# ─────────────────────────────────────────
# 查询开门日志
# ─────────────────────────────────────────
def query_log(start: datetime.datetime, end: datetime.datetime):
    condition = NET_FIND_RECORD_ACCESSCTLCARDREC_CONDITION_EX()
    condition.dwSize      = sizeof(NET_FIND_RECORD_ACCESSCTLCARDREC_CONDITION_EX)
    condition.bTimeEnable = True
    condition.stStartTime = make_net_time(start)
    condition.stEndTime   = make_net_time(end)
    condition.nOrderNum   = 0

    inParam = NET_IN_FIND_RECORD_PARAM()
    inParam.dwSize          = sizeof(NET_IN_FIND_RECORD_PARAM)
    inParam.emType          = EM_NET_RECORD_TYPE.ACCESSCTLCARDREC_EX
    inParam.pQueryCondition = cast(byref(condition), c_void_p)
    outParam = NET_OUT_FIND_RECORD_PARAM()
    outParam.dwSize = sizeof(NET_OUT_FIND_RECORD_PARAM)

    result = sdk.FindRecord(loginID, inParam, outParam, 5000)
    if not result:
        print(f"❌ FindRecord 失败: {sdk.GetLastErrorMessage()}")
        return

    findHandle = outParam.lFindeHandle
    print(f"✅ 开始查询 {start.date()} ~ {end.date()}")
    total = 0
    BATCH = 20

    while True:
        findIn = NET_IN_FIND_NEXT_RECORD_PARAM()
        findIn.dwSize       = sizeof(NET_IN_FIND_NEXT_RECORD_PARAM)
        findIn.lFindeHandle = findHandle
        findIn.nFileCount   = BATCH

        records = (NET_RECORDSET_ACCESS_CTL_CARDREC * BATCH)()
        for rec in records:
            rec.dwSize = sizeof(NET_RECORDSET_ACCESS_CTL_CARDREC)

        findOut = NET_OUT_FIND_NEXT_RECORD_PARAM()
        findOut.dwSize        = sizeof(NET_OUT_FIND_NEXT_RECORD_PARAM)
        findOut.pRecordList   = cast(records, c_void_p)
        findOut.nMaxRecordNum = BATCH

        ret = sdk.FindNextRecord(findIn, findOut, 5000)
        got = findOut.nRetRecordNum
        if not ret or got == 0:
            break

        for i in range(got):
            rec    = records[i]
            t      = rec.stuTime
            dt_str = (f"{t.dwYear}-{t.dwMonth:02d}-{t.dwDay:02d} "
                      f"{t.dwHour:02d}:{t.dwMinute:02d}:{t.dwSecond:02d}")
            status = format_access_status(rec.bStatus, getattr(rec, 'nErrorCode', 0))
            card   = rec.szCardNo.decode('utf-8', errors='ignore')
            user   = rec.szUserID.decode('utf-8', errors='ignore')
            name   = rec.szCardName.decode('utf-8', errors='ignore')
            method = METHOD_MAP.get(rec.emMethod, f"未知({rec.emMethod})")
            print(f"  [{dt_str}] 门{rec.nDoor} | 用户:{user} | 姓名:{name} "
                  f"| 卡:{card} | {status} | {method}")
            total += 1

    print(f"\n共查到 {total} 条记录")
    sdk.FindRecordClose(findHandle)


# ─────────────────────────────────────────
# 实时监听开门事件
# ─────────────────────────────────────────
def on_alarm(lCommand, lLoginID, pBuf, dwBufLen, pchDVRIP, nDVRPort,
             bAlarmAckFlag, nEventHandle, dwUser):
    try:
        if lCommand == 0x00000204:
            event  = cast(pBuf, POINTER(DEV_EVENT_ACCESS_CTL_INFO)).contents
            t      = event.UTC
            dt_str = _net_time_to_text(t)
            name   = event.szName.decode('utf-8', errors='ignore')
            card   = event.szCardNo.decode('utf-8', errors='ignore')
            user   = event.szUserID.decode('utf-8', errors='ignore')
            status = format_access_status(event.bStatus, event.nErrorCode)
            method = METHOD_MAP.get(int(event.emOpenMethod), f"未知({event.emOpenMethod})")
            print(f"🔔 [消息] [{dt_str}] 门{event.nChannelID} | 用户:{user} "
                  f"| {name} | 卡:{card} | {status} | {method}")
        elif lCommand == int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_EVENT):
            event = cast(pBuf, POINTER(NET_A_ALARM_ACCESS_CTL_EVENT_INFO)).contents
            t = event.RealUTC if getattr(event, 'bRealUTC', False) else event.stuTime
            dt_str = _net_time_to_text(t)
            name = event.szCardName.decode('utf-8', errors='ignore').strip('\x00')
            card = event.szCardNo.decode('utf-8', errors='ignore').strip('\x00')
            user = event.szUserID.decode('utf-8', errors='ignore').strip('\x00')
            status = format_access_status(event.bStatus, event.nErrorCode)
            method = METHOD_MAP.get(int(event.emOpenMethod), f"未知({event.emOpenMethod})")
            print(f"🔔 [门禁] [{dt_str}] 门{event.nDoor} | 事件:{int(event.emEventType)} "
                  f"| 用户:{user} | {name} | 卡:{card} | {status} | {method}")
        elif lCommand == int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_STATUS):
            event = cast(pBuf, POINTER(NET_A_ALARM_ACCESS_CTL_STATUS_INFO)).contents
            t = event.RealUTC if getattr(event, 'bRealUTC', False) else event.stuTime
            dt_str = _net_time_to_text(t)
            status = STATUS_MAP.get(int(event.emStatus), f"未知({event.emStatus})")
            serial = event.szSerialNumber.decode('utf-8', errors='ignore').strip('\x00')
            extra = f" | 序列号:{serial}" if serial else ""
            print(f"🔔 [状态] [{dt_str}] 门{event.nDoor} | {status}{extra}")
        elif lCommand == int(SDK_ALARM_TYPE.ALARM_ALARM_EX2):
            event = cast(pBuf, POINTER(NET_A_ALARM_ALARM_INFO_EX2)).contents
            t = event.stuTime
            dt_str = _net_time_to_text(t)
            action = ALARM_ACTION_MAP.get(int(event.nAction), f"动作{event.nAction}")
            sense = SENSE_METHOD_MAP.get(int(event.emSenseType), f"类型{int(event.emSenseType)}")
            name = event.szName.decode('utf-8', errors='ignore').strip('\x00')
            print(f"🚨 [本地报警] [{dt_str}] 通道{event.nChannelID} | {action} | {sense} | {name}")
        elif lCommand == int(SDK_ALARM_TYPE.ALARM_INPUT_SOURCE_SIGNAL):
            event = cast(pBuf, POINTER(NET_A_ALARM_INPUT_SOURCE_SIGNAL_INFO)).contents
            t = event.stuTime
            dt_str = _net_time_to_text(t)
            action = ALARM_ACTION_MAP.get(int(event.nAction), f"动作{event.nAction}")
            print(f"🚨 [报警输入源] [{dt_str}] 通道{event.nChannelID} | {action}")
        elif lCommand == int(SDK_ALARM_TYPE.ALARM_SENSOR_ABNORMAL):
            event = cast(pBuf, POINTER(NET_A_ALARM_SENSOR_ABNORMAL_INFO)).contents
            t = event.stuTime
            dt_str = _net_time_to_text(t)
            action = ALARM_ACTION_MAP.get(int(event.nAction), f"动作{event.nAction}")
            status = SENSOR_ABNORMAL_STATUS_MAP.get(int(event.emStatus), f"状态{int(event.emStatus)}")
            sense = SENSE_METHOD_MAP.get(int(event.emSenseMethod), f"类型{int(event.emSenseMethod)}")
            print(f"🚨 [探测器异常] [{dt_str}] 通道{event.nChannelID} | {action} | {sense} | {status}")
        else:
            _print_unknown_alarm(lCommand, pBuf, dwBufLen, bAlarmAckFlag, nEventHandle)
    except Exception as e:
        print(f"⚠️  消息回调异常: {e} | command=0x{int(lCommand) & 0xffffffff:08x} | len={int(dwBufLen)}")


def on_analyzer(lAnalyzerHandle, dwEventType, pEventInfo, pBuffer,
                dwBufSize, dwUser, nSequence, reserved):
    try:
        if dwEventType == 0x00000204:
            event  = cast(pEventInfo, POINTER(DEV_EVENT_ACCESS_CTL_INFO)).contents
            t      = event.UTC
            dt_str = (f"{t.dwYear}-{t.dwMonth:02d}-{t.dwDay:02d} "
                      f"{t.dwHour:02d}:{t.dwMinute:02d}:{t.dwSecond:02d}")
            name   = event.szName.decode('utf-8', errors='ignore')
            card   = event.szCardNo.decode('utf-8', errors='ignore')
            user   = event.szUserID.decode('utf-8', errors='ignore')
            status = format_access_status(event.bStatus, event.nErrorCode)
            method = METHOD_MAP.get(int(event.emOpenMethod), f"未知({event.emOpenMethod})")
            print(f"🔔 [智能] [{dt_str}] 门{event.nChannelID} | 用户:{user} "
                  f"| {name} | 卡:{card} | {status} | {method}")
    except Exception as e:
        print(f"⚠️  智能回调异常: {e}")


def start_listen():
    msg_cb = fMessCallBackEx1(on_alarm)
    start_listen._msg_cb = msg_cb
    sdk.SetDVRMessCallBackEx1(msg_cb, 0)

    ana_cb = fAnalyzerDataCallBack(on_analyzer)
    start_listen._ana_cb = ana_cb
    handle = sdk.RealLoadPictureEx(loginID, 0, 0x00000204, 1, ana_cb, 0, None)
    start_listen._handle = handle
    print(f"  智能订阅句柄: {handle}")

    result = sdk.StartListenEx(loginID)
    if result:
        print("✅ 监听已启动，按 Ctrl+C 退出...")
    else:
        print(f"❌ 监听启动失败: {sdk.GetLastErrorMessage()}")
    return result


def start_alarm_listen():
    print("ℹ️  订阅 SDK 通用报警消息：本地报警、报警输入源、探测器异常、门禁状态、未知原始报警都会打印。")
    return start_listen()


def _decode_bytes(value) -> str:
    return bytes(value).split(b"\x00", 1)[0].decode("utf-8", errors="ignore")


def _net_time_to_text(t) -> str:
    return (f"{t.dwYear}-{t.dwMonth:02d}-{t.dwDay:02d} "
            f"{t.dwHour:02d}:{t.dwMinute:02d}:{t.dwSecond:02d}")


def _buffer_hex(pBuf, length: int, limit: int = 96) -> str:
    raw = bytes(cast(pBuf, POINTER(c_ubyte * min(length, limit))).contents)
    return raw.hex(" ")


def _buffer_bytes(pBuf, length: int, limit: int = 4096) -> bytes:
    return bytes(cast(pBuf, POINTER(c_ubyte * min(length, limit))).contents)


def _extract_ascii_strings(raw: bytes, min_len: int = 4) -> list[str]:
    strings = []
    current = bytearray()
    for value in raw:
        if 32 <= value <= 126:
            current.append(value)
            continue
        if len(current) >= min_len:
            strings.append(current.decode("ascii", errors="ignore"))
        current.clear()
    if len(current) >= min_len:
        strings.append(current.decode("ascii", errors="ignore"))
    return strings


def _unknown_alarm_hint(command: int, values: list[int], raw: bytes) -> str:
    if command == 0x3169 and len(values) >= 9:
        iface = raw[36:68].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        return (f"疑似网络接口状态: 状态/动作={values[2]} | "
                f"时间={values[3]}-{values[4]:02d}-{values[5]:02d} "
                f"{values[6]:02d}:{values[7]:02d}:{values[8]:02d} | 接口={iface or '-'}")
    if command == 0x3177 and len(values) >= 3:
        return f"疑似设备状态/能力通知: dwSize={values[0]} | 子码=0x{values[2] & 0xffffffff:08x}"
    if command == 0x3491 and len(values) >= 8:
        return (f"疑似带时间的设备/门禁事件: 时间={values[2]}-{values[3]:02d}-{values[4]:02d} "
                f"{values[5]:02d}:{values[6]:02d}:{values[7]:02d} | 字段={values[:12]}")
    if command == 0x31B3:
        paths = [s for s in _extract_ascii_strings(raw) if "/" in s or "\\" in s]
        return f"疑似图片/抓拍路径事件: {paths[0] if paths else '未提取到路径'}"
    if command == 0x3475:
        strings = _extract_ascii_strings(raw)
        return f"疑似令牌/随机串事件: {strings[0] if strings else '未提取到字符串'}"
    if command == 0x300C:
        return "负载当前全 0，暂不能判断含义"
    return ""


def _print_unknown_alarm(lCommand, pBuf, dwBufLen, bAlarmAckFlag, nEventHandle):
    command = int(lCommand) & 0xffffffff
    length = int(dwBufLen)
    command_name = ALARM_COMMAND_MAP.get(command, f"未知报警(0x{command:08x})")
    print(f"🔔 [原始报警] {command_name} | len={length} | ack={bool(bAlarmAckFlag)} | event={int(nEventHandle)}")
    raw = _buffer_bytes(pBuf, length)
    values = []
    if length >= 24:
        try:
            fields = cast(pBuf, POINTER(c_int * min(12, length // 4))).contents
            values = [int(fields[i]) for i in range(len(fields))]
            print(f"    i32: {values}")
        except Exception as e:
            print(f"    i32解析失败: {e}")
    hint = _unknown_alarm_hint(command, values, raw)
    if hint:
        print(f"    hint: {hint}")
    strings = _extract_ascii_strings(raw)
    if strings:
        print(f"    str: {strings[:6]}")
    if length > 0:
        print(f"    hex: {_buffer_hex(pBuf, length)}")


def query_alarm_output_state():
    in_param = NET_IN_GET_OUTPUT_STATE()
    in_param.dwSize = sizeof(NET_IN_GET_OUTPUT_STATE)
    in_param.emType = EM_OUTPUT_TYPE.EM_OUTPUT_TYPE_ALARMOUT

    out_param = NET_OUT_GET_OUTPUT_STATE()
    out_param.dwSize = sizeof(NET_OUT_GET_OUTPUT_STATE)

    result = sdk.GetAlarmRegionInfo(
        loginID,
        EM_A_NET_EM_GET_ALARMREGION_INFO.NET_EM_GET_ALARMREGION_INFO_OUTPUTSTATE,
        in_param,
        out_param,
        5000,
    )
    if not result:
        print(f"❌ 查询报警输出状态失败: {sdk.GetLastErrorMessage()} (错误码:{sdk.GetLastError()})")
        return False

    count = int(out_param.nStateRetEx or out_param.nStateRet)
    states = out_param.arrStatesEx if out_param.nStateRetEx else out_param.arrStates
    print(f"✅ 报警输出通道状态: {count} 个")
    if count <= 0:
        print("  未返回输出通道。磁力锁如果未接到报警输出，可能不会出现在这里。")
        return True

    for index in range(min(count, len(states))):
        state = bool(states[index])
        print(f"  输出{index}: {OUTPUT_STATE_MAP[state]} ({state})")
    return True


def query_accessory_lock_state(max_items: int = 32):
    infos = (NET_WPAN_ACCESSORY_INFO * max_items)()
    for info in infos:
        info.dwSize = sizeof(NET_WPAN_ACCESSORY_INFO)

    query = NET_GET_ACCESSORY_INFO()
    query.dwSize = sizeof(NET_GET_ACCESSORY_INFO)
    query.nSNNum = 0
    query.nMaxInfoNum = max_items
    query.pstuInfo = cast(infos, POINTER(NET_WPAN_ACCESSORY_INFO))
    query.bFilterValidField = False

    result = sdk.QueryDevState(
        loginID,
        EM_QUERY_DEV_STATE_TYPE.GET_ACCESSORY_INFO,
        query,
        sizeof(query),
        0,
        5000,
    )
    if not result:
        print(f"❌ 查询配件/锁状态失败: {sdk.GetLastErrorMessage()} (错误码:{sdk.GetLastError()})")
        return False

    count = int(query.nInfoNum)
    print(f"✅ 配件状态: {count} 个")
    if count <= 0:
        print("  设备未返回配件状态。普通有线磁力锁可能只体现为报警输出通道。")
        return True

    for index in range(min(count, max_items)):
        info = infos[index]
        name = _decode_bytes(info.szName)
        serial = _decode_bytes(info.szSN)
        model = _decode_bytes(info.szModel)
        sense = SENSE_METHOD_MAP.get(int(info.emType), f"类型{int(info.emType)}")
        lock_state = ACCESSORY_LOCK_STATE_MAP.get(int(info.byLockState), f"未知({int(info.byLockState)})")
        output = info.stuOutput
        output_status = ACCESSORY_OUTPUT_STATUS_MAP.get(int(output.nStatus), f"未知({int(output.nStatus)})")
        output_mode = ACCESSORY_OUTPUT_MODE_MAP.get(int(output.nMode), f"未知({int(output.nMode)})")

        print(f"  配件{index}: {name or '-'} SN:{serial or '-'} 型号:{model or '-'} 类型:{sense}")
        print(f"    锁定状态: {lock_state} | 输出: {output_status} | 输出模式: {output_mode}")
        print(f"    电压: {float(info.fInputVoltage):.1f}V | 电流: {float(info.fElectricCurrent):.1f}A | 功率: {float(info.fPower):.1f}W")
        if int(info.nExternalSensorCount) > 0:
            sensor_count = min(int(info.nExternalSensorCount), len(info.stuExternalSensor))
            for sensor_index in range(sensor_count):
                sensor = info.stuExternalSensor[sensor_index]
                input_type = _decode_bytes(sensor.szInputType)
                print(f"    外接探测器{sensor_index}: 类型:{input_type or '-'} 状态:{int(sensor.nStatus)}")
    return True


def query_magnetic_lock_state():
    print("ℹ️  已检查当前 NetSDK Python 封装中的门禁相关结构。")
    print("ℹ️  参考文章中的 DH_CTRL_DOOR_STATUS / NET_CTRL_DOOR_STATUS 在当前 Python NetSDK 中没有对应定义。")
    print("ℹ️  当前封装提供的是 CtrlType.ACCESS_OPEN / ACCESS_CLOSE，只能下发开门/关门控制，不能返回磁力锁吸合反馈。")
    print("ℹ️  没有找到可主动查询“磁力锁是否吸合/锁住”的门禁专用接口。")
    print("ℹ️  SDK 中最接近的是 ALARM_ACCESS_CTL_STATUS 事件里的“假锁/异常/开/关/常开/常闭”，但这是事件上报，不是主动查询。")
    print("ℹ️  已实测报警输出状态和配件状态主动查询在当前设备返回 1080，不适用于这台门禁设备。")
    print("❌ 当前 SDK 路径下暂不能可靠主动读取磁力锁锁住状态。")
    return False


def get_door_status_cgi(channel: int = 1):
    """查询门状态（优先 getLockStatus）"""
    urls = [
        f"http://{DEVICE_IP}/cgi-bin/accessControl.cgi?action=getLockStatus&channel={channel}",
        f"http://{DEVICE_IP}/cgi-bin/accessControl.cgi?action=getDoorStatus&channel={channel}"
    ]
    
    for url in urls:
        try:
            r = requests.get(
                url,
                auth=HTTPDigestAuth(USERNAME, PASSWORD),
                timeout=10
            )
            text = r.text.strip()
            print(f"调试 [{url}] 返回: {text}")
            
            if r.status_code == 200:
                # 解析 status=Open / Close
                if 'status=' in text:
                    status = text.split('status=')[-1].split('\n')[0].strip()
                    return True, status
                # 解析 Info.status=Open
                elif 'Info.status=' in text:
                    status = text.split('Info.status=')[-1].split('\n')[0].strip()
                    return True, status
                return True, text  # 返回原始内容
        except Exception as e:
            print(f"请求异常: {e}")
            continue
    return False, "查询失败（请尝试其他 channel）"

# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────
USAGE = """
用法:
  python3 access.py open [门号]                  # SDK 远程开门，默认门0
  python3 access.py close [门号]                 # SDK 远程关门/上锁，默认门0
  python3 access.py snap [通道] [保存路径]        # SDK 实时抓图，默认通道0，默认保存到 snapshots/
  python3 access.py doorstatus [门号]            # CGI 查询门状态，默认门1
  python3 access.py status                       # 查看 SDK 是否支持主动查询磁力锁状态
  python3 access.py alarms                       # SDK 通用报警/异常事件监听
  python3 access.py adduser <ID> <姓名>          # 添加人员
  python3 access.py getuser <ID>                 # 查询人员
  python3 access.py finduser <姓名>              # 按姓名查找人员
  python3 access.py deluser <ID>                 # 删除人员
  python3 access.py face   <ID> <图片路径>       # 下发人脸（CGI，默认不压缩原图）
  python3 access.py dface  <ID>                  # 删除人脸
  python3 access.py log [开始日期] [结束日期]    # 查询日志 格式: 2026-04-21
  python3 access.py listen                       # 实时监听开门事件（含门状态事件）

人脸命令额外参数（可选）:
  face <ID> <图片> [max_kb] [宽] [高] [quality]   # 例如: face 1001 photo.jpg 0 400 400 75
"""

if len(sys.argv) < 2:
    print(USAGE)
    sdk.Logout(loginID)
    sdk.Cleanup()
    exit(0)

cmd = sys.argv[1]

if cmd == "open":
    ch = int(sys.argv[2]) if len(sys.argv) >= 3 else 0
    open_door(ch)

elif cmd == "close":
    ch = int(sys.argv[2]) if len(sys.argv) >= 3 else 0
    close_door(ch)

elif cmd == "snap":
    ch = int(sys.argv[2]) if len(sys.argv) >= 3 else 0
    save_path = sys.argv[3] if len(sys.argv) >= 4 else None
    capture_snapshot(ch, save_path)
    
elif cmd == "doorstatus":
    ch = int(sys.argv[2]) if len(sys.argv) >= 3 else 1
    success, status = get_door_status_cgi(ch)
    if success:
        print(f"✅ 门{ch} 状态: {status}")
    else:
        print(f"❌ {status}")

elif cmd == "adduser" and len(sys.argv) >= 4:
    insert_user(sys.argv[2], sys.argv[3])

elif cmd == "getuser" and len(sys.argv) >= 3:
    get_user(sys.argv[2])

elif cmd == "finduser" and len(sys.argv) >= 3:
    results = find_user_by_name(sys.argv[2])
    if results:
        print(f"✅ 找到 {len(results)} 条结果:")
        for item in results:
            print(f"   ID:{item['id']} | 姓名:{item['name']} | 状态:{item['status']} | 门权限:{item['doors']}")
    else:
        print("❌ 未找到匹配人员")

elif cmd == "deluser" and len(sys.argv) >= 3:
    remove_user(sys.argv[2])

elif cmd == "face" and len(sys.argv) >= 4:
    # 用法: face <ID> <图片> [max_kb] [宽] [高] [quality]
    # 默认不压缩（max_kb=0, width=0, height=0, quality=0）
    max_kb  = int(sys.argv[4]) if len(sys.argv) >= 5 else 0
    w       = int(sys.argv[5]) if len(sys.argv) >= 6 else 0
    h       = int(sys.argv[6]) if len(sys.argv) >= 7 else 0
    q       = int(sys.argv[7]) if len(sys.argv) >= 8 else 0
    insert_face(sys.argv[2], sys.argv[3], max_kb, w, h, q)

elif cmd == "dface" and len(sys.argv) >= 3:
    remove_face(sys.argv[2])

elif cmd == "log":
    today = datetime.datetime.now()
    start = datetime.datetime.strptime(sys.argv[2], "%Y-%m-%d") \
            if len(sys.argv) >= 3 else today.replace(hour=0, minute=0, second=0)
    end   = datetime.datetime.strptime(sys.argv[3], "%Y-%m-%d").replace(hour=23, minute=59, second=59) \
            if len(sys.argv) >= 4 else today.replace(hour=23, minute=59, second=59)
    query_log(start, end)
elif cmd == "status":
    query_magnetic_lock_state()

elif cmd == "alarms":
    if start_alarm_listen():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sdk.StopListen(loginID)
            print("\n报警监听已停止")
    
elif cmd == "listen":
    if start_listen():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sdk.StopListen(loginID)
            print("\n监听已停止")

else:
    print(USAGE)

sdk.Logout(loginID)
sdk.Cleanup()
