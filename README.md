# 大华门禁 Web 管理系统

基于 Flask 的大华门禁设备 Web 管理界面，支持设备管理、人员管理、门禁控制、实时预览等功能。

## 项目规模

- **代码量**: 约 20,000 行（Python + JavaScript + CSS）
- **架构**: Flask 3.0 + Flask-Sock + SQLite + Dahua NetSDK + FFmpeg + JSMpeg
- **数据库**: `door_web.db` SQLite（设备、人员、权限、审计日志）
- **人脸库**: `faces/` 目录（JPEG 照片存储）
- **模块**: 17+ JavaScript 模块 + 8+ Python 核心模块

## 功能特性

---

## 1. 功能概览

- **登录与注册**：支持 Web 用户登录、注册、退出和当前用户状态检查。
- **角色权限**：支持 `admin` 管理员和 `user` 普通用户。
- **区域管理**：按区域分组门禁设备，支持新增、删除区域。
- **设备管理**：维护大华门禁设备名称、IP、端口、账号、密码、区域和备注；显示在线状态。
- **人员管理**：本地维护人员列表，支持按全部设备或指定设备查看、搜索、新增、编辑、删除、冻结/解冻、有效期维护。
- **同步设备人员**：从指定设备读取人员并同步到本地数据库。
- **人脸管理**：上传、缓存、下发和删除人员人脸照片；支持跨设备自动同步本地缓存照片。
- **批量导入**：通过包含 `user.xlsx` 和图片文件的文件夹批量导入人员和人脸。
- **门禁控制**：按设备开门，并查询门开/关状态。
- **日志查询**：查询指定设备指定日期范围的通行记录。
- **实时视频预览**:通过 Flask-Sock 提供 WebSocket 连接;支持 H.264 / H.265 摄像头(服务端自动嗅探 codec),WebCodecs API 直通硬解,不支持时降级 FFmpeg → MPEG-1 + jsmpg.js。
- **录像机（NVR）管理**：支持添加大华录像机设备，读取通道名称和在线状态，查看各通道实时预览和历史录像回放。
- **管理员模块**：管理员可创建/编辑/删除用户，并为普通用户分配设备。

---

## 2. 项目目录结构

```text
door_monitor_web/
├── server.py                 # 项目入口：加载 routes._app 中的 app/manager，注册 11 个蓝图，启动后台清理线程，解析参数并运行
├── routes/
│   ├── _app.py              # Flask app + Sock + DeviceManager 单例; 加载 secret_key; db.init_db(); @app.before_request 登录检查
│   ├── helpers.py           # 跨蓝图工具(ok/err、log_audit、extract_device、设备查找、缓存、sync_device_across_users)
│   ├── preview_state.py     # 4 个 sock 路由共享的会话表 + 工厂/销毁
│   ├── stream_ws.py         # @sock.route × 4(预览/音频/监控/回放) WebSocket 端点
│   ├── auth.py              # 登录/注册/退出/用户状态
│   ├── devices.py           # 设备增删改查、NVR 通道探测、批量门模式
│   ├── persons.py           # 人员查询、同步、精确/模糊搜索
│   ├── door.py              # 开门、门状态查询(RPC2/CGI)、门模式切换
│   ├── users.py             # 用户设备分配
│   ├── face.py              # 人脸上传/更新/删除/获取
│   ├── logs.py              # 通行日志查询
│   ├── monitor.py           # 实时监控页面
│   ├── admin.py             # 管理员用户管理
│   ├── audit.py             # 审计日志
│   └── misc.py              # 杂项接口(健康检查、模板下载等)
├── db.py                     # SQLite 数据库操作封装
├── device_client.py          # 大华设备客户端封装：开门、人员、人脸、日志、预览等
├── device_manager.py         # 设备连接池与空闲清理
├── access.py                 # 本地人员/人脸操作脚本副本
├── README.md                 # 本使用手册
├── AGENTS.md                 # AI 代码助手参考文档（.gitignore 排除，不推送）
├── door_web.db               # SQLite 数据库（用户、设备、区域、人员、审计日志）
├── user.xlsx                 # 批量导入模板
├── faces/                    # 本地人脸照片缓存，通常为 faces/<用户编号>.jpg
├── static/
│   ├── js/
│   │   ├── access_control.js # 前端主逻辑
│   │   ├── admin.js          # 管理员模块
│   │   ├── device-mgmt.js    # 设备管理
│   │   ├── person-mgmt.js    # 人员管理（92KB，最大模块）
│   │   ├── door.js           # 门禁控制
│   │   ├── preview.js        # 视频预览逻辑
│   │   ├── monitor.js        # 实时监控
│   │   ├── playback.js       # 录像回放
│   │   ├── audit.js          # 审计日志
│   │   ├── home.js           # 首页
│   │   ├── tabs.js           # 标签页管理
│   │   ├── shared.js         # 共享工具函数
│   │   ├── globals.js        # 全局变量
│   │   ├── jsmpg.js          # MPEG-1 Web 播放器
│   │   ├── video-player.js  # codec-agnostic WebCodecs 播放器(H.264/H.265)
│   │   └── hls.min.js        # HLS 播放器
│   └── css/
│       ├── access_control.css # 主界面样式
│       └── preview.css        # 预览弹窗样式
└── tests/                    # 测试脚本
```

> **注意：** `server.py` 在 v1.0 时曾包含全部路由逻辑，后拆分为 `routes/` 目录下的多个蓝图模块。当前 `server.py` 仅负责装配和启动，新增路由请去 `routes/` 对应蓝图模块中添加。

---

## 3. 环境依赖

### 3.1 Python 环境

项目使用系统 Python 3.11：

```bash
python3 --version  # Python 3.11.2
```

常用 Python 依赖包括：

- `flask`
- `flask-sock`
- `requests`
- `Pillow`
- `openpyxl`
- 大华 NetSDK Python 封装及其 Linux 动态库

### 3.2 系统依赖

实时视频预览依赖 `ffmpeg`，用于将大华 DHAV 私有码流转为浏览器可播放的 MPEG-1 视频流。

可检查：

```bash
ffmpeg -version
```

### 3.3 SDK 注意事项

实时预览功能需要完整的大华 NetSDK `.so` 动态库。如果普通开门、人员管理正常，但视频预览黑屏或无数据，应优先检查 NetSDK 动态库和 `ffmpeg`。

---

## 4. 启动方式

进入项目根目录下的虚拟环境：

```bash
source ./netsdk_env/bin/activate
```

进入项目目录：

```bash
cd door_monitor_web
```

启动服务：

```bash
python3 server.py
```

默认监听端口为 `15001`。

如需指定端口：

```bash
python3 server.py --port 15002
```

启动后访问：

```text
http://<服务器IP>:<端口>/
```

后端启动时会：

- 初始化 SQLite 数据库 `door_web.db`；
- 自动创建数据库表结构（用户、设备、区域、人员、审计日志等）；
- 启动设备连接清理线程，定期清理空闲设备连接；
- 通过 Flask-Sock 提供 `/ws/preview/<device_id>` 视频预览 WebSocket。

---

## 5. 登录、注册与角色

### 5.1 注册

在登录页点击注册，填写用户名和密码创建 Web 用户。

用户信息保存到 SQLite 数据库 `door_web.db` 的 `users` 表中。

> ⚠️ `door_web.db` 属于敏感文件，不要公开。

### 5.2 登录

登录成功后，后端使用 Session 保存登录状态，并返回当前用户名和角色。

除 `/`、`/api/login`、`/api/register`、`/api/health` 等接口外，其它 `/api/` 接口都要求已登录。

### 5.3 角色说明

| 角色 | 权限 |
|---|---|
| `admin` 管理员 | 可管理设备、区域、人员、门禁、日志、预览、用户和设备分配 |
| `user` 普通用户 | 可使用分配给自己的设备及相关人员/门禁功能 |

设备和区域按用户隔离，存储在 SQLite 数据库中；人员数据是全局共用，但在"全部设备"模式下会按当前用户拥有的设备过滤显示。

权限同时在前端和后端校验：包括 REST 接口和监控/回放 WebSocket 建流入口。即使直接访问 WebSocket URL，也必须具备对应页面权限、设备归属和通道授权。

---

## 6. 区域管理

区域用于给设备分组，方便在门禁控制页面按区域展示。

### 6.1 新增区域

在区域管理中输入区域名称后保存。

要求：

- 区域名称不能为空；
- 同一用户下区域名称不能重复。

### 6.2 删除区域

删除区域前，需要确认该区域下没有设备。

如果仍有设备属于该区域，系统会拒绝删除，并提示“该区域下有设备，无法删除”。

### 6.3 默认区域

新用户或无区域配置时，默认区域为：

```text
传动轴
```

---

## 7. 设备管理

设备配置保存在 SQLite 数据库 `door_web.db` 的 `devices` 表中。

每台设备通常包含：

- 设备 ID：自动生成的唯一标识符；
- 设备名称；
- IP 地址；
- 端口，默认 `37777`；
- 登录用户名；
- 登录密码；
- 所属区域；
- 备注；
- 在线状态。

> ⚠️ 数据库文件含有设备登录凭据，禁止公开。

### 7.1 查看设备

设备列表会并发检测设备在线状态，并使用短时间缓存减少页面加载等待。

### 7.2 添加设备

管理员可添加设备。需要填写设备名称、IP、端口、设备账号、设备密码、所属区域等信息。

同一 `IP:端口` 会映射到同一个全局设备 ID。

### 7.3 编辑设备

管理员可修改设备名称、IP、端口、账号、密码、区域和备注。

修改 IP/端口时会先校验目标 `IP:端口` 是否已映射到其它全局设备 ID；如果冲突会拒绝保存，避免设备表和全局映射不一致。

修改后会同步更新拥有该设备的其它用户设备配置。

**编辑优化：** 仅在 IP/端口/账号/密码变化时才会重新登录 NVR 获取通道名称；编辑名字/备注/区域时不会重复登录，避免不必要的设备连接。

### 7.4 删除设备

管理员可删除自己设备列表中的设备。

删除设备只移除该用户的设备配置，不会自动删除其它用户的同 ID 设备，也不会删除数据库中的人员记录。

---

## 8. 人员管理

人员数据保存在 SQLite 数据库 `door_web.db` 的 `persons` 表中。

人员主要字段包括：

| 字段 | 说明 |
|---|---|
| `user_id` | 用户编号/人员编号 |
| `name` | 姓名 |
| `valid_begin` | 有效期开始日期 |
| `valid_end` | 有效期结束日期 |
| `doors` | 关联设备 ID 列表 |
| `status` | 按设备保存的状态字典，`0` 正常、`1` 冻结 |
| `has_face` | 按设备保存的人脸状态字典，`true` 表示该设备已有脸 |

### 8.1 全部设备模式

人员管理页默认可查看当前用户拥有设备下的全部人员。

此模式下：

- 后端按当前用户设备 ID 过滤人员；
- 人脸数量只统计当前用户自己的设备；
- 状态显示为：只要任一当前用户设备上冻结，则显示冻结。

### 8.2 指定设备模式

选择具体设备后，只显示关联该设备的人员。

可用于：

- 从该设备同步人员；
- 查询该设备上的人员；
- 对指定设备执行冻结、解冻、人脸上传、删除等操作。

### 8.3 新增人员

新增人员时填写：

- 用户编号；
- 姓名；
- 有效期；
- 所属设备；
- 可选人脸照片。

保存时会将人员写入选中的在线设备，并同步写入本地数据库。

如果新增时选择了人脸照片，系统会在人员创建后自动下发到对应设备，并缓存到 `faces/` 目录。

### 8.4 编辑人员

编辑人员可修改：

- 姓名；
- 有效期；
- 所属设备；
- 人脸同步状态。

**更新人脸照片：** 编辑人员时可选择上传新的人脸照片，点击"更新人脸"按钮后，系统会通过 CGI `FaceInfoManager.cgi?action=update` 直接覆盖该人员在所有已关联设备上的人脸，并更新本地缓存。无需删除重建用户，保留原有权限配置。

如果给人员新增设备，系统会尝试使用本地 `faces/<用户编号>.jpg` 缓存照片自动下发到新设备。

如果本地没有缓存照片，界面会提示上传照片；用户取消上传时，不应继续完成依赖人脸的保存流程。

### 8.5 删除人员

删除人员可以从一个或多个设备移除。

删除逻辑会同时更新本地数据库：

- 如果人员仍关联其它设备，则只移除被删除设备的权限；
- 如果人员不再关联任何设备，则本地人员记录会被删除。

### 8.6 冻结/解冻

人员状态按设备保存。冻结/解冻会先对所选在线设备执行设备端操作，再同步本地状态；如果设备操作成功但本地状态更新失败，界面会提示刷新后核对。离线设备会明确提示跳过。

人员状态按设备保存：

```json
"status": {"1": 0, "3": 1}
```

含义：

- `0`：正常；
- `1`：冻结。

在全部设备模式下，可选择多个设备进行冻结/解冻。

### 8.7 有效期维护

可修改人员有效期开始和结束日期。

系统兼容大华设备可能返回的异常日期占位值，例如 `0-00-00`，前端会将无效日期安全处理为默认值或空显示。

---

## 9. 同步设备人员 / 同步规则

"同步设备人员"用于从当前选中设备读取设备内已有人员，并同步到本地数据库。

后端接口：

```text
POST /api/persons/import
```

### 9.1 当前设备返回的人

| 场景 | 同步行为 |
|------|----------|
| 本地不存在该人员 | 新增人员，`doors=[当前设备ID]`，`has_face[当前设备ID]=true` |
| 本地已存在，但没有当前设备权限 | 追加当前设备 ID 到 `doors`，并设置 `has_face[当前设备ID]=true` |
| 本地已存在，且已有当前设备权限 | 更新基础信息，但不强制覆盖原来的 `has_face[当前设备ID]` |

**搜索模式：** 后端支持 `search_mode` 参数：
- `search_mode=fuzzy`（默认）：模糊匹配 `user_id` 和 `name`，适合搜索姓名
- `search_mode=exact`：精确匹配 `user_id`，适合已知编号的精确查找。前端的人脸更新、人员查询等操作均使用精确搜索，避免模糊搜索导致匹配到多个用户（如搜索 "z" 匹配到 "CDZ101"）。

### 9.2 当前设备没有返回的人

如果本地人员原本关联当前设备，但本次设备人员列表中已经不存在该人员，则系统会清理该设备相关数据：

- 从 `doors` 中移除当前设备 ID；
- 删除 `has_face[当前设备ID]`；
- 删除 `status[当前设备ID]`；
- 保留人员在其它设备上的权限、人脸状态和冻结状态。

> 也就是说，同步删除只影响当前设备，不会删除人员本体，也不会影响其它设备。

### 9.3 人脸默认状态

从设备加载到的人员默认认为该设备已有人员和人脸，因此新人员或该设备新增权限人员会设置：

```json
"has_face": {"当前设备ID": true}
```

但如果该人员本来已经有关联当前设备，系统不会把原本的 `false` 强制改成 `true`。

---

## 10. 人脸照片管理

人脸缓存目录：

```text
faces/
```

### 10.1 上传人脸

上传人脸时，系统会：

1. 校验当前用户是否能看到该人员，以及目标设备是否属于当前用户的门禁设备；
2. 检查本地 `has_face[设备ID]`；
3. 如果该设备已标记有人脸且没有强制下发，则跳过重复上传；
4. 调用设备接口下发人脸；
5. 成功后将照片缓存到 `faces/<用户编号>.jpg`；
6. 更新数据库中对应设备的 `has_face[设备ID]=true`。

如果下发前预占了 `has_face` 但后续失败，系统会回滚本地状态，避免出现“本地显示已有脸但设备实际没有”的假状态。

### 10.2 检查本地照片是否存在

系统提供轻量检查接口：

```text
GET /api/face/<用户编号>/exists
```

该接口只返回是否存在本地照片，不传输图片内容。返回格式与其它接口一致：`{ "code": 0, "msg": "ok", "data": { "exists": true/false } }`。

该接口会同时校验当前用户是否有权限查看该人员。

### 10.3 获取本地缓存照片

```text
GET /api/face/<用户编号>
```

如果存在 `faces/<用户编号>.jpg`，返回图片；否则返回未找到。读取前同样会校验当前用户是否有权限查看该人员。

### 10.4 删除人脸

删除人脸会调用设备接口，并将本地数据库中该设备的 `has_face[设备ID]` 标记为 `false`。

> 注意：不同大华固件对人脸删除接口支持程度不同。本系统以本地 `has_face` 字典作为前端显示和重复上传判断依据。（当前门禁固件SDK和CGI等删除人脸方法都不支持）

---

## 11. 批量导入人员

批量导入接口：

```text
POST /api/batch_import
```

前端使用文件夹上传，字段名为 `files`。

### 11.1 文件夹要求

上传文件夹中必须包含：

```text
user.xlsx
```

同时可包含人员照片，支持格式：

- `.jpg`
- `.jpeg`
- `.png`
- `.bmp`

### 11.2 Excel 格式要求

`user.xlsx` 至少包含：

- 第一行：说明行；
- 第二行：列名；
- 第三行起：人员数据。

必需列：

| 列名 | 说明 |
|---|---|
| `用户编号` | 人员编号 |
| `姓名` | 人员姓名 |

可选列：

| 列名 | 说明 |
|---|---|
| `有效期结束` | 人员有效期结束日期 |
| `人脸图片名称` | 对应图片文件名 |
| `门` | 目标设备名称或 `IP:端口` |

### 11.3 门字段匹配

`门` 字段可通过以下方式匹配当前用户设备：

- 设备名称；
- `IP:端口`。

如果匹配不到设备，该条数据会导入失败或跳过对应设备。

### 11.4 导入结果

导入完成后返回统计信息，包括：

- 总条数；
- 成功条数；
- 失败条数；
- 人脸成功数；
- 人脸失败数；
- 详细错误信息。

---

## 12. 门禁控制

门禁控制页面按区域展示设备卡片。

### 12.1 开门

点击设备对应的开门按钮后，后端调用：

```text
POST /api/open
```

默认通道为 `0`(SDK方法)。

请求会携带设备信息，后端通过 `DeviceClient.open_door()` 执行开门。

### 12.2 门状态查询

门状态接口：

```text
GET /api/door/status
POST /api/door/status
```

**参数**：

- `channel`：门禁通道号，默认为 `1`（API 入参从 1 起）；
- `source`：信号源选择，可选值：
  - `rpc2`（默认）：通过 RPC2 读取门磁物理状态；
  - `cgi`：通过 CGI 读取电锁逻辑状态。

**返回**：

- `status`：设备返回状态，`Open` / `Close`；
- `is_open`：布尔值，表示是否开门；
- `source`：实际使用的信号源。

**信号源差异**：

| 方式 | 信号源 | 说明 |
|------|--------|------|
| **RPC2** `getDoorStatus` | 门磁触点输入（物理态） | 读取门磁传感器实际状态，设备网页主页的门图标走这条 |
| **CGI** `getLockStatus` | 电锁通断电指令（逻辑态） | 读取电锁控制指令状态 |

多数情况下两者一致，但在以下场景会有差异：

- 门半开（门磁未完全闭合）；
- 常开模式（电锁断电但门磁可能闭合）；
- 门磁未接线或损坏。

**默认行为**：

系统默认使用 **RPC2 方式**查询门磁物理状态，与设备网页主页行为一致。

如需查询电锁逻辑状态，可显式指定 `source=cgi`。

**前端显示**：

- 🔓 门已开；
- 🔒 门已关。

**RPC2 实现细节**：

```python
# 1. 获取 accessControl 实例对象
r = client._rpc_call("accessControl.factory.instance", {"channel": 0})
obj = r["result"]

# 2. 查询门状态
r = client._rpc_call("accessControl.getDoorStatus", obj=obj)
info = r["params"]["Info"]
status = info["status"]  # "Open" / "Close"
```

详细实现见 `device_client.py` 的 `get_door_status_rpc()` 方法。

### 12.3 自动刷新

打开设备详情后,前端会周期性刷新门状态和日志,关闭详情时停止刷新。

### 12.4 设备操作方法

系统支持对门禁设备进行多种操作，包括常开、常闭、正常模式切换等。

#### 12.4.1 操作类型

| 操作 | 说明 | RPC2 模式值 |
|------|------|-------------|
| **常开** | 设备保持开门状态，不验证权限 | `AlwaysOpen` |
| **常闭** | 设备保持关门状态，禁止通行 | `AlwaysClose` |
| **正常** | 恢复正常门禁模式，按权限验证 | `Normal` |

#### 12.4.2 操作流程

1. **选择设备**：在设备管理或门禁控制页面选择目标设备；
2. **选择操作**：点击对应的操作按钮（常开/常闭/正常）；
3. **确认执行**：系统通过 RPC2 接口发送配置到设备；
4. **查看结果**：操作成功后显示确认消息。

#### 12.4.3 批量操作

系统支持批量设备操作：

1. 在设备列表中勾选多个设备；
2. 点击批量操作按钮；
3. 选择目标状态（常开/常闭/正常）；
4. 系统并发执行操作并返回每台设备的结果。

**注意：** 批量操作时，每台设备独立执行 `setConfig`，操作前会使用系统统一确认框；失败时会提示失败数量和部分失败设备名称。

#### 12.4.4 注意事项

- **常开模式**：设备将不验证人员权限，任何人都可通行，请谨慎使用；
- **常闭模式**：设备将拒绝所有通行请求，包括已授权人员；
- **正常模式**：恢复标准门禁验证流程；
- **离线设备**：离线设备无法执行操作，系统会跳过并提示；
- **操作日志**：所有设备操作会记录到审计日志中。

#### 12.4.5 RPC2 接口说明

设备操作使用大华 RPC2 接口 `configManager.setConfig`，配置表名为 `AccessControl`。

**关键实现细节**：

只发送部分模式字段时，RPC2 可能返回 `result=true`，但设备后台的常开/常闭状态实际不会变化。

因此系统采用以下流程：

1. 先通过 `configManager.getConfig` 读取完整 `AccessControl` 配置表；
2. 合并目标模式字段到完整配置；
3. 通过 `configManager.setConfig` 整表写回；
4. 读回配置校验关键字段是否生效。

**模式字段映射**：

- `AlwaysOpen` 常开：尝试 `State="OpenAlways"` 或 `State="Normal"` + 其它字段组合；
- `AlwaysClose` 常闭：尝试 `State="CloseAlways"` 或 `State="Normal"` + 其它字段组合；
- `Normal` 正常：`State="Normal"` + 其它字段恢复默认。

**Python 示例**（简化版）：

```python
# 1. 读取完整配置
current_table = client._rpc_call("configManager.getConfig", {
    "name": "AccessControl"
})["params"]["table"][0]

# 2. 构建模式补丁
patch = {
    "Mode": "AlwaysOpen",
    "State": "OpenAlways",
    # ... 其它必需字段
}

# 3. 合并并写回
table = dict(current_table)
table.update(patch)
client._rpc_call("configManager.setConfig", {
    "name": "AccessControl",
    "table": [table],
    "options": []
})

# 4. 读回校验
readback = client._rpc_call("configManager.getConfig", {
    "name": "AccessControl"
})["params"]["table"][0]
assert all(readback.get(k) == v for k, v in patch.items())
```

详细实现见 `device_client.py` 的 `set_door_mode()` 方法。

---

## 13. 通行日志

日志接口：

```text
GET /api/log
POST /api/log/all
```

`/api/log` 查询单台设备，`/api/log/all` 查询当前用户所有门禁设备。

参数：

- `start`：开始时间，可为 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS`；
- `end`：结束时间，可为 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS`；
- 设备连接参数。

返回指定设备在时间范围内的通行记录。

全局日志查询会在部分设备失败时继续返回已成功设备的记录，并在响应中携带 `failed_devices` 供前端提示；自动刷新模式只读缓存并异步刷新，不弹出失败提示。

前端会展示日志列表及相关统计信息。

---

## 14. 实时视频预览

系统支持两种视频预览模式,且 **同时支持 H.264 / H.265(HEVC)摄像头**——无需手动改 NVR 编码格式,服务端自动嗅探。

### 14.1 预览模式

| 模式 | 编码格式 | 适用场景 | 浏览器兼容性 |
|------|----------|----------|--------------|
| **WebCodecs 直通**(默认) | H.264 或 H.265 裸流 | 现代浏览器,低延迟 | Chrome/Edge 107+(H.265 需要)、Chrome 94+(H.264);页面必须是 secure context(HTTPS / localhost) |
| **MPEG-1 兼容** | MPEG-1 video | 不支持 WebCodecs 的浏览器降级 | 所有浏览器 (jsmpg.js) |

**默认行为:** 前端会自动检测浏览器是否支持 WebCodecs API + secure context。支持则走 WebCodecs 直通(URL 加 `codec=h264`);不支持则自动降级为 MPEG-1 兼容模式(`codec=mpeg1`)。具体码流是 H.264 还是 H.265 由服务端嗅探确定,前端 `VideoPlayer` 收到首帧自己也跑双套 NAL 头规则决定 configure `avc1.*` 还是 `hev1.*` decoder——URL 参数里的 `codec=h264` **只表示前端的路径选择**(WebCodecs vs MPEG-1),不强制摄像头编码格式。

### 14.2 技术架构

**WebCodecs 直通模式**(默认,`codec=h264`):

```text
大华 NetSDK 预览回调(H.264 或 H.265 NAL)
  → 服务端 sniff_codec 嗅探 codec
  → H264Parser / H265Parser 拼 access unit
  → IDR 帧自动补 VPS/SPS/PPS,确保自包含可解码
  → WebSocket 直通
  → 浏览器 VideoPlayer 嗅探后 configure avc1.* / hev1.* VideoDecoder 硬解
```

**优化:** 服务端缓存最近一次含 IDR 的完整 AU(`webcodecs_init`),新客户端连接时立即下发,确保快速起播。

**MPEG-1 兼容模式**(降级,`codec=mpeg1`):

```text
大华 NetSDK 预览回调(H.264 或 H.265 NAL)
  → 服务端嗅探 + AU 拼装(同上)
  → FFmpeg 子进程(显式 -f h264 或 -f hevc)→ MPEG-1 video
  → WebSocket 广播
  → 浏览器 jsmpg.js 软解渲染到 canvas
```

### 14.3 WebSocket 端点

**视频预览**:

```text
/ws/preview/<设备ID>?codec=h264    # WebCodecs 直通(默认)
/ws/preview/<设备ID>?codec=mpeg1   # MPEG-1 兼容
/ws/preview/<设备ID>                # 默认 mpeg1(向后兼容)
```

**音频预览**(可选):

```text
/ws/preview/<设备ID>/audio
```

音频格式:PCM s16le 8kHz mono

### 14.4 会话共享

每个设备只启动一个 SDK 预览会话 + FFmpeg 进程,多个客户端连接同一设备时共享同一路码流。

会话管理:

- 第一个客户端连接时创建预览会话;
- 后续客户端复用现有会话;
- 所有客户端断开后自动关闭会话。

### 14.5 前端文件

- `static/js/preview.js`:创建 WebSocket、维护心跳、打开/关闭预览;
- `static/js/video-player.js`:codec-agnostic WebCodecs 播放器,自动识别 H.264 / H.265;
- `static/js/jsmpg.js`:MPEG-1 软解播放器(降级);
- `static/css/preview.css`:预览弹窗样式。

### 14.6 预览操作

在设备卡片或详情中点击"实时预览"后:

1. 打开预览弹窗;
2. 检测浏览器能力(WebCodecs API 支持 + secure context);
3. 建立 WebSocket(WebCodecs 直通或 MPEG-1);
4. 服务端启动或复用该设备的预览会话,嗅探 codec;
5. 浏览器显示"连接中…";
6. 收到首帧后显示"直播中"。

### 14.7 全屏

预览弹窗支持切换全屏。

### 14.8 常见问题

如果预览黑屏或连接失败,检查:

- 是否已安装 `flask-sock`;
- 是否能正常启动 `server.py`;
- 是否安装 `ffmpeg`(MPEG-1 模式需要;WebCodecs 直通不依赖 ffmpeg);
- NetSDK 动态库是否完整;
- 当前设备是否在线;
- 浏览器是否能访问 `/ws/preview/<设备ID>`;
- 服务器日志是否出现 ffmpeg / SDK 预览 / `codec 嗅探` 相关错误;
- WebCodecs 模式:浏览器是否支持 WebCodecs API,**且**页面是 secure context(HTTPS / localhost / 127.0.0.1);
- H.265 摄像头:浏览器是否 Chrome/Edge 107+(早期版本不支持 hev1.*)。

### 14.9 实现细节

详细实现见:
- `routes/codec_parsers.py` — codec-agnostic NAL 解析层(H.264 / H.265 双套 NAL 头规则)
- `routes/preview_state.py` — `_create_video_preview_session()` 核心管线
- `routes/stream_ws.py` — 四个 sock 路由

---

## 15. 管理员用户管理

管理员页面仅管理员可用。

### 15.1 用户列表

管理员可查看：

- 用户名；
- 角色；
- 已分配设备数量；
- 操作按钮。

### 15.2 新建用户

管理员可创建普通用户或管理员用户。

新建用户必须填写用户名和密码。

### 15.3 编辑用户

管理员可修改用户角色和密码。

编辑时密码留空表示不修改密码。

非超级管理员编辑自己时只允许修改自己的密码，不能修改自己的角色或权限；普通管理员也可以查看自己的设备分配。

### 15.4 删除用户

管理员可删除指定用户。

> 删除用户不可恢复，请谨慎操作。

### 15.5 分配设备

管理员可为目标用户勾选设备。

保存后：

- 勾选的设备会复制/分配到目标用户设备列表；
- 取消勾选的设备会从目标用户设备列表移除；
- 目标用户登录后只能看到分配给自己的设备。

---

## 16. 数据文件说明

| 文件/目录 | 说明 | 是否敏感 |
|---|---|---|
| `door_web.db` | SQLite 数据库，包含用户、设备、区域、人员、审计日志等 | 是 |
| `faces/` | 本地人脸照片缓存 | 是 |
| `user.xlsx` | 批量导入模板或样例 | 可能敏感 |
| `secret_key` | Flask session 密钥 | 是 |

建议备份：

```text
door_web.db
faces/
user.xlsx（如已定制模板）
secret_key
```

---

## 17. 常用 API 速查

### 17.1 登录与用户

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/register` | 注册用户 |
| `POST` | `/api/login` | 登录 |
| `POST` | `/api/logout` | 退出 |
| `GET` | `/api/user` | 获取当前登录用户 |
| `GET` | `/api/health` | 健康检查 |

### 17.2 设备与区域

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/devices` | 获取当前用户设备列表，并检测在线状态 |
| `POST` | `/api/devices` | 添加设备（管理员） |
| `PUT` | `/api/devices/<device_id>` | 修改设备（管理员） |
| `DELETE` | `/api/devices/<device_id>` | 删除设备（管理员） |
| `POST` | `/api/devices/batch-mode` | 批量设置门模式（常开/常闭/正常） |
| `GET` | `/api/areas` | 获取区域列表 |
| `POST` | `/api/areas` | 新增区域 |
| `DELETE` | `/api/areas/<name>` | 删除区域 |

### 17.3 本地人员缓存

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/persons` | 获取当前用户设备下的人员 |
| `GET` | `/api/persons?device_id=<id>` | 获取指定设备人员 |
| `GET` | `/api/persons?search_mode=exact&user_id=<uid>` | 精确搜索人员（按 user_id） |
| `GET` | `/api/persons?search_mode=fuzzy&keyword=<kw>` | 模糊搜索人员（按 user_id 和 name） |
| `POST` | `/api/persons/import` | 从设备加载/同步人员到本地 |
| `PUT` | `/api/persons/<user_id>` | 更新本地人员缓存 |
| `DELETE` | `/api/persons/<user_id>` | 删除本地人员 |
| `DELETE` | `/api/persons/<user_id>/devices/<device_id>` | 移除人员与某设备的关联 |

### 17.4 设备人员、人脸、门禁、日志

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/user` | 在设备上新增人员 |
| `PUT` | `/api/user/<uid>` | 更新设备人员 |
| `DELETE` | `/api/user/<uid>` | 删除设备人员 |
| `POST` | `/api/user/<uid>/freeze` | 冻结设备人员 |
| `POST` | `/api/user/<uid>/unfreeze` | 解冻设备人员 |
| `PUT` | `/api/user/<uid>/validity` | 更新设备人员有效期 |
| `GET` | `/api/device/user/id/<uid>` | 按编号查询设备人员 |
| `GET` | `/api/device/users/search` | 按姓名搜索设备人员 |
| `GET` | `/api/device/users/all` | 分页获取设备人员 |
| `POST` | `/api/face/<uid>` | 上传并下发人脸 |
| `PUT` | `/api/face/<uid>` | 更新人员在所有设备上的人脸照片 |
| `GET` | `/api/face/<uid>` | 获取本地缓存人脸照片 |
| `GET` | `/api/face/<uid>/exists` | 检查本地人脸照片是否存在 |
| `DELETE` | `/api/face/<uid>` | 删除设备人脸并更新本地状态 |
| `POST` | `/api/open` | 开门 |
| `GET` | `/api/door/status` | 查询门状态 |
| `GET` | `/api/log` | 查询通行日志 |
| `POST` | `/api/batch_import` | 批量导入人员和人脸 |
| `GET` | `/download/template` | 下载 `user.xlsx` 模板 |
| `WS` | `/ws/preview/<device_id>` | 实时视频预览 |

### 17.5 管理员

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/admin/users` | 用户列表 |
| `POST` | `/api/admin/users` | 创建用户 |
| `PUT` | `/api/admin/users/<username>` | 修改用户角色/密码 |
| `DELETE` | `/api/admin/users/<username>` | 删除用户 |
| `GET` | `/api/admin/users/<username>/devices` | 获取用户已分配设备 |
| `POST` | `/api/admin/users/<username>/devices` | 给用户分配设备 |
| `DELETE` | `/api/admin/users/<username>/devices/<device_id>` | 移除用户设备 |

---

## 18. 维护与排查

### 18.1 语法检查

```bash
cd door_monitor_web
python3 -m py_compile server.py device_client.py device_manager.py db.py
```

### 18.2 健康检查

```bash
curl http://127.0.0.1:15001/api/health
```

如果使用了其它端口，请替换 `15001`。

### 18.3 页面加载慢

可能原因：

- 离线设备较多，在线检测等待；
- 后端服务未启动或端口不对；
- 服务器资源不足。

当前设备在线检测已使用并发和短缓存，正常情况下不会因多个离线设备线性阻塞很久。

### 18.4 人脸重复上传失败

系统使用本地 `has_face` 字典判断是否已有人脸。若本地状态不准，可能出现：

- 本地显示已有，实际设备没有；
- 本地显示没有，实际设备已有，重复上传失败。

处理方式：

- 重新上传人脸；
- 重新从设备加载人员；
- 必要时通过管理员工具修正数据库中对应人员的 `has_face` 字段。

### 18.5 同步设备人员后本地人员变少

这是当前同步逻辑的正常行为：

- 如果某人不在当前设备返回列表中，系统会移除该人员对当前设备的权限；
- 如果该人员仍属于其它设备，本地人员记录会保留；
- 如果查看的是指定设备人员列表，该人员会从当前设备列表消失。

### 18.6 视频预览黑屏

优先检查：

```bash
ffmpeg -version
```

以及 NetSDK 动态库是否完整、设备是否在线、后端日志是否有 `preview` / `ffmpeg` 相关错误。

**WebCodecs 模式额外检查:**
- 浏览器是否支持 WebCodecs API(Chrome 94+ / Edge / Safari);H.265 摄像头需要 Chrome/Edge 107+
- 页面必须是 secure context(HTTPS / localhost / 127.0.0.1),否则 WebCodecs 会被禁用
- 如果 WebCodecs 模式黑屏,可手动切换为 MPEG-1 模式(预览弹窗右上角设置)

### 18.7 生产数据安全

不要公开以下文件或目录：

```text
door_web.db
faces/
secret_key
```

---

## 19. 版本说明

### v1.5 — 2026.05

**安全与一致性修复：**
- 监控 / 回放 WebSocket 建流前补齐页面权限、设备归属和通道授权校验，避免绕过前端直接拉流。
- 人脸上传、更新、缓存读取和存在性检查会校验人员可见范围；通过 `device_ip` 操作人脸时也必须匹配当前用户已有门禁设备。
- 编辑设备 IP/端口时先检查全局 `device_map` 冲突，再保存设备配置，避免半更新。
- 人脸下发预占 `has_face` 后如果失败会回滚本地状态。

**体验与健壮性：**
- 登录、注册和用户管理关键接口使用容错 JSON 解析，非 JSON / 空请求不再触发 500。
- 非超级管理员可自助修改自己的密码；普通管理员可查看自己的设备分配，但不能修改自己的角色或权限。
- 人员冻结/解冻会提示本地状态更新失败和离线跳过情况。
- 批量门模式改用统一确认框，并提示失败设备摘要。
- 统一 `warn` toast 样式；审计扫描限制、人员同步冲突、批量操作提示使用一致的警告样式。
- 全局日志查询在部分设备失败时继续显示成功记录，并提示失败设备。

### v1.4 — 2026.05

**新增功能:**
- **H.264 / H.265 自适应预览**:预览/监控/回放管线全部 codec-agnostic,服务端嗅探 NAL 头自动识别 H.264 或 H.265 摄像头,无需手动改 NVR 编码格式。前端 `VideoPlayer` 自己也跑双套 NAL 规则,configure 对应的 `avc1.*` / `hev1.*` VideoDecoder。
- **新增 `routes/codec_parsers.py`** 抽象 NAL 解析:`H264Parser` / `H265Parser` / `sniff_codec` / `make_parser`,统一处理起始码扫描、VCL/IDR/SPS/PPS/VPS 判定、access unit 边界。
- **`static/js/h264-player.js` 重命名为 `static/js/video-player.js`**,类名 `H264Player` → `VideoPlayer`(语义同,只是更准确)。

**改动:**
- session 字段统一改名:`h264_ws_set` → `webcodecs_ws_set`、`h264_init` → `webcodecs_init`、`h264_queue` → `webcodecs_queue`、`h264_carry` → `nal_carry`。新增 `codec` / `parser` / `sniff_buffer` / `vps_nal` / `au_has_vps` 字段。
- WebSocket URL 参数 `?codec=h264|mpeg1` 保持不变(向后兼容),但语义现在是 **前端路径选择**(WebCodecs 直通 vs MPEG-1 转码),不强制摄像头编码。
- 视频 ffmpeg 转码进程延后到 codec 嗅探完成才启动,命令显式带 `-f h264` / `-f hevc`;启动失败时优雅降级,WebCodecs 路径继续工作。

### v1.3 — 2026.05

**新增功能：**
- **人脸照片更新**：编辑人员时可一键更新所有设备上的人脸照片（`PUT /api/face/<uid>`），通过 CGI `FaceInfoManager.cgi?action=update` 直接覆盖，无需删除重建用户
- **精确搜索**：人员搜索支持 `search_mode=exact`（精确匹配 user_id）和 `search_mode=fuzzy`（模糊匹配，默认），避免模糊搜索导致匹配到多个用户
- **设备编辑优化**：编辑设备时仅在 IP/端口/账号/密码变化时才重新 probe NVR 通道，避免编辑名字/备注/区域时重复登录
- **批量门模式操作**：支持勾选多个设备，一键批量设置常开/常闭/正常模式
- **H.264 直通模式**：实时预览默认使用 WebCodecs API 硬解 H.264 裸流，低延迟、低 CPU 占用；自动缓存 SPS/PPS/IDR 确保快速起播

**优化：**
- 视频回放页 NVR 通道数显示统一为 "在线/总数"（与监控页一致）
- 设备原区域被删除时，编辑界面显示 "孤儿 option" 避免静默改区域
- 保存设备按钮增加 loading 状态（"保存中…" / "添加中…"）
- 移除冗余的 playback channel overlay，简化布局

**修复：**
- 人脸更新时搜索逻辑修复：`submitFace` / `fetchLocalPersonById` / `updatePersonFace` 均改用精确搜索，避免搜索 "z" 匹配到 "CDZ101" 导致只更新部分设备

### v1.2 — 2026.04

- 实时视频预览支持 H.264 直通 + MPEG-1 兼容双模式
- NVR 录像回放功能
- 批量导入人员（Excel + 图片）

### v1.1 — 2026.03

- 人员管理（增删改查、冻结/解冻、有效期）
- 人脸上传/下发/删除
- 门禁控制（开门、门状态查询、门模式切换）
- 通行日志查询

### v1.0 — 2026.02

- 初始版本：设备管理、区域管理、用户/角色系统、实时预览

---

本文档根据当前 `door_web` 代码功能编写，覆盖范围包括：

- Flask 后端 `server.py`；
- SQLite 数据层 `db.py`；
- 前端页面 `access_control.html`；
- 主前端逻辑 `static/js/access_control.js`；
- 视频预览逻辑 `static/js/preview.js`；
- 大华设备封装 `device_client.py` / `device_manager.py`。

如后续新增功能或调整 API，请同步更新本 README。
