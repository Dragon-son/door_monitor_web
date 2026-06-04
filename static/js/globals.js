const API = window.location.origin;

// 校验日期字符串是否合法（处理 SDK 返回的 "0-00-00" 等无效值）
function safeDate(str, fallback) {
    if (!str || typeof str !== 'string') return fallback;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(str)) return fallback;
    const d = new Date(str);
    if (isNaN(d.getTime())) return fallback;
    return str;
}

let devices = [];
let areas = [];
let currentDev = null;
let currentDoor = 0;
let currentPDevObj = null;
let editDevId = null;
let editPersonId = null;
let faceUid = null;
let facePendingDevices = null;  // savePerson 上下文中待下发设备列表
let _pendingPersonSave = null;   // 编辑延迟保存上下文
let logData = [];
let logFilter = '';
let logRefreshTimer = null;
let doorStatusTimer = null;
let devicePageMode = false;
let currentDevicePageData = [];
let deviceUserTotal = 0;
let currentRole = 'user';   // 'admin' | 'user'
let currentUsername = '';   // 当前登录用户名
let currentPermissions = new Set(); // 当前用户权限 key 集合
let adminEditUsername = null; // 当前编辑的用户名（null=新建）
let adminEditOriginalRole = 'user'; // 编辑前原始角色，用于判断是否变更
let assignTargetUsername = null;
let _devicesLoaded = false;  // 设备列表是否已加载完毕（区分"加载中"和"真没有"）
let deviceStatusTimer = null;
let _deviceStatusRefreshing = false;
let assignCurrentUserDevIds = new Set();
let userLoginTime = null;  // 用户打开页面的时间，用于全局日志查询起始时间
let currentPageName = 'home'; // 当前激活页面，用于避免初始化时把门禁批量顶栏显示到主页
