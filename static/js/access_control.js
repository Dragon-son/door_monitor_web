// ========== 入口：登录 / 导航 / 初始化 ==========

// 登录相关
let loginSubmitting = false;
let registerSubmitting = false;

async function checkLogin() {
    try {
        const resp = await fetch('/api/user', { credentials: 'include' });
        const data = await resp.json();
        if (data.code === 0 && data.data && data.data.username) {
            currentUsername = data.data.username;
            currentPermissions = new Set(data.data.permissions || []);
            showMainApp(data.data.role || 'user');
        } else {
            showLoginPage();
        }
    } catch (e) {
        showLoginPage();
    }
}
function hideLoadingOverlay() {
    const el = document.getElementById('loading-overlay');
    if (el) el.classList.add('hidden');
}
function showLoginPage() {
    hideLoadingOverlay();
    document.getElementById('login-page').style.display = 'flex';
    document.getElementById('main-app').style.display = 'none';
    document.getElementById('login-username').focus();
}
function canManageUsers() {
    return currentUsername === 'admin' || currentRole === 'admin';
}
function hasPermission(permission) {
    if (currentUsername === 'admin') return true;
    return currentPermissions instanceof Set && currentPermissions.has(permission);
}

function applyPermissionUI() {
    document.querySelectorAll('[data-permission]').forEach(el => {
        const permission = el.getAttribute('data-permission');
        el.style.display = hasPermission(permission) ? '' : 'none';
    });
}

async function showMainApp(role) {
    currentRole = role || 'user';
    document.getElementById('login-page').style.display = 'none';
    document.getElementById('main-app').style.display = 'flex';
    applyRoleUI();
    applyPermissionUI();
    // 用户标签 + 顶栏 tab/主页渲染
    const userLabel = document.getElementById('userLabel');
    if (userLabel) userLabel.textContent = currentUsername ? `· ${currentUsername}` : '';
    if (typeof renderHomeCards === 'function') renderHomeCards();
    if (typeof renderTabbar === 'function') renderTabbar();
    // 等 initMain 全部完成（设备加载 + 门禁 tab 打开）之后再隐藏加载动画
    // 这样用户直接看到门禁页面，不会闪主页
    await initMain();
    hideLoadingOverlay();
}

function applyRoleUI() {
    const isAdmin = currentRole === 'admin';
    document.querySelectorAll('.admin-only').forEach(el => {
        el.style.display = isAdmin ? '' : 'none';
    });
}
async function doLogin() {
    if (loginSubmitting) return;
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    const msgEl = document.getElementById('login-msg');
    const btn = document.getElementById('loginBtn');
    if (!username || !password) { msgEl.textContent = '请输入用户名和密码'; return; }
    loginSubmitting = true;
    if (btn) { btn.disabled = true; btn.textContent = '登录中...'; }
    try {
        const resp = await fetch(`${API}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
        const data = await resp.json();
        if (data.code === 0) {
            currentUsername = data.data && data.data.username ? data.data.username : username;
            currentPermissions = new Set(data.data && data.data.permissions ? data.data.permissions : []);
            showMainApp(data.data && data.data.role ? data.data.role : 'user');
        }
        else msgEl.textContent = data.msg || '登录失败';
    } catch (e) { msgEl.textContent = '网络错误，登录失败'; }
    finally {
        loginSubmitting = false;
        if (btn) { btn.disabled = false; btn.textContent = '登录'; }
    }
}
async function doLogout() {
    try { await fetch(`${API}/api/logout`, { method: 'POST' }); } catch (e) {}
    document.cookie = "auth=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
    // 强制整页刷新，清空上一个用户残留的内存状态（设备/人员/区域缓存、定时器、预览 WebSocket 等）
    window.location.reload();
}
function showRegisterForm() {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('register-form').style.display = 'block';
    document.getElementById('login-msg').textContent = '';
}
function showLoginForm() {
    document.getElementById('login-form').style.display = 'block';
    document.getElementById('register-form').style.display = 'none';
    document.getElementById('login-msg').textContent = '';
}
async function doRegister() {
    if (registerSubmitting) return;
    const usernameEl = document.getElementById('reg-username');
    const passwordEl = document.getElementById('reg-password');
    const password2El = document.getElementById('reg-password2');
    const username = usernameEl.value.trim();
    const password = passwordEl.value;
    const password2 = password2El.value;
    const msgEl = document.getElementById('login-msg');
    const btn = document.getElementById('registerBtn');
    if (!username || !password) { msgEl.textContent = '用户名和密码不能为空'; return; }
    if (password !== password2) { msgEl.textContent = '两次密码输入不一致'; return; }
    registerSubmitting = true;
    if (btn) { btn.disabled = true; btn.textContent = '注册中...'; }
    try {
        const resp = await fetch(`${API}/api/register`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
        const data = await resp.json();
        if (data.code === 0) {
            usernameEl.value = '';
            passwordEl.value = '';
            password2El.value = '';
            if (typeof toast === 'function') toast('success', '注册成功，请登录');
            else msgEl.textContent = '注册成功，请登录';
            showLoginForm();
        }
        else msgEl.textContent = data.msg || '注册失败';
    } catch (e) { msgEl.textContent = '网络错误，注册失败'; }
    finally {
        registerSubmitting = false;
        if (btn) { btn.disabled = false; btn.textContent = '注册'; }
    }
}

// ========== 主初始化 ==========
async function initMain() {
    // 记录页面打开时间（本地时间），用于全局日志查询
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hour = String(now.getHours()).padStart(2, '0');
    const minute = String(now.getMinutes()).padStart(2, '0');
    const second = String(now.getSeconds()).padStart(2, '0');
    userLoginTime = `${year}-${month}-${day} ${hour}:${minute}:${second}`;
    
    await loadAreas();
    renderAccessGrid();
    // 批量操作顶栏只在门禁控制 tab 显示;首屏是主页,先隐藏
    const batchBar = document.getElementById('batchTopbar');
    if (batchBar) batchBar.style.display = 'none';
    checkHealth();
    setInterval(checkHealth, 10000);
    await loadDevicesFromServer();
    // 设备加载完成后，默认进入门禁控制页面
    if (typeof openTab === 'function' && hasPermission('page.access')) openTab('access');
    refreshDeviceStatusInBackground();
    startDeviceStatusAutoRefresh();
    // 设备加载完成后异步查询门模式并更新卡片 badge
    fetchAndUpdateDoorModeBadges();
    const sel = document.getElementById('personDevSel');
    if (sel && sel.value) onPersonDeviceChange();
    
    // 初始加载全局日志（在 userLoginTime 设置后）
    if (typeof fetchGlobalLogDataAndProcess === 'function'
        && (typeof canViewAccessLogs !== 'function' || canViewAccessLogs())) {
        fetchGlobalLogDataAndProcess(true);
    }
}

// ========== 回到顶部按钮 ==========
document.addEventListener('DOMContentLoaded', function() {
    const backToTopBtn = document.getElementById('backToTop');
    if (!backToTopBtn) return;

    // 找到真正的滚动容器（.content）
    const contentEl = document.querySelector('.content');
    if (!contentEl) return;

    // 监听滚动事件
    contentEl.addEventListener('scroll', function() {
        if (contentEl.scrollTop > 300) {
            backToTopBtn.classList.add('show');
        } else {
            backToTopBtn.classList.remove('show');
        }
    });

    // 点击回到顶部
    backToTopBtn.addEventListener('click', function() {
        contentEl.scrollTo({
            top: 0,
            behavior: 'smooth'
        });
    });
});

checkLogin();
