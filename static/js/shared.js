// ========== 通用 API 请求（携带设备凭证）==========
function devicePayload(device) {
    if (!device) return {};
    if (device.id !== undefined && device.id !== null && device.id !== '') {
        return { device_id: device.id };
    }
    // 所有持久化设备都有 id，走到这里说明传入了未保存的临时对象，
    // 直接报错而不是把凭证放进请求体，避免密码意外泄露。
    console.error('[devicePayload] 设备缺少 id，请检查调用方：', device);
    throw new Error('设备信息不完整，无法发起请求');
}

async function apiRequest(method, path, device, bodyData = null, isFormData = false, signal = null) {
    let url = API + path;
    let options = { method, headers: {}, signal };
    const devData = devicePayload(device);

    if (method === 'GET') {
        const queryData = { ...(bodyData || {}) };
        if (device && devData.device_id !== undefined) queryData.device_id = devData.device_id;
        if (Object.keys(queryData).length) {
            const params = new URLSearchParams(queryData);
            url += (url.includes('?') ? '&' : '?') + params.toString();
        }
    } else if (isFormData) {
        const form = bodyData || new FormData();
        Object.entries(devData).forEach(([k, v]) => form.append(k, v));
        options.body = form;
    } else {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify({ ...(bodyData || {}), ...devData });
    }

    const resp = await fetch(url, options);
    return resp.json();
}

// ========== 健康检查 ==========
async function checkHealth() {
    let ok = false;
    try { const r = await fetch(API + '/api/health'); const data = await r.json(); ok = data.code === 0; } catch(e) { console.warn('Health check failed', e); }
    document.getElementById('srvDot').className = ok ? 'status-dot' : 'status-dot offline';
    document.getElementById('srvLabel').textContent = ok ? '服务已连接' : '服务未连接';
    document.querySelectorAll('[id^="banner-"]').forEach(banner => {
        banner.classList.toggle('show', !ok);
    });
}

// ========== 页面导航(走 tab 系统) ==========
// _showPageInternal 由 tabs.openTab() 在切换到某个 page 后调用,
// 只负责该 page 的"打开时刷新数据"等业务逻辑,不再操作 sidebar/breadcrumb DOM。
function _showPageInternal(name) {
    stopAutoRefresh();
    if (name !== 'access' && typeof stopGlobalLogAutoRefresh === 'function') {
        stopGlobalLogAutoRefresh();
    }
    if (name !== 'playback' && typeof stopPlaybackIfActive === 'function') {
        stopPlaybackIfActive();
    }
    const actions = document.getElementById('topbarActions');
    if (actions) actions.innerHTML = '';
    if (name === 'home') { /* nothing */ }
    if (name === 'devices') { if (areas.length === 0) { loadAreas().then(() => renderDevTable()); } else { renderDevTable(); } }
    if (name === 'persons') {
        fillPersonDevSel();
        document.getElementById('personContent').style.display = 'block';
        document.getElementById('personPH').style.display = 'none';
        currentPDevObj = null;
        currentViewMode = 'local';
        document.getElementById('searchUserId').value = '';
        document.getElementById('searchUserName').value = '';
        loadAllPersons();
    }
    if (name === 'access')   { showAccessList(); }
    if (name === 'monitor')  { loadMonitorDevices(); }
    if (name === 'playback') { loadPlaybackDevices(); }
    if (name === 'admin')    { loadAdminUsers(); }
    if (name === 'audit')    { loadAuditLogs(1); }
    // 批量操作顶栏仅门禁控制页面 + 有门模式设置权限时显示
    const batchBar = document.getElementById('batchTopbar');
    const canSetMode = typeof canSetAccessDoorMode !== 'function' || canSetAccessDoorMode();
    if (batchBar) batchBar.style.display = (name === 'access' && canSetMode) ? 'flex' : 'none';
    checkHealth();
}

// 旧调用方仍然能用 showPage(name) —— 转发到 openTab。
function showPage(name) {
    if (typeof openTab === 'function') openTab(name);
    else _showPageInternal(name);   // 兜底(理论上不会发生)
}

// ========== 日志日期工具 ==========
function getLocalDateString(date = new Date()) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

// ========== 通用工具函数 ==========
let _modalZIndexSeed = 3000;

function closeTopEscModal() {
    const escModals = Array.from(document.querySelectorAll('.modal-overlay.open[data-close-on-esc="true"]'));
    const top = escModals.sort((a, b) => Number(b.style.zIndex || 0) - Number(a.style.zIndex || 0))[0];
    if (!top) return;
    const closeFn = top.dataset.closeFunction;
    if (closeFn && typeof window[closeFn] === 'function') window[closeFn]();
    else closeModal(top.id);
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeTopEscModal();
});

function openModal(id) {
    const overlay = document.getElementById(id);
    if (!overlay) return;
    overlay.style.zIndex = String(++_modalZIndexSeed);
    overlay.classList.add('open');

    if (!overlay.dataset.modalClickBound) {
        overlay.dataset.modalClickBound = '1';
        overlay.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            if (e.target === overlay && overlay.dataset.closeOnBackdrop === 'true') {
                const closeFn = overlay.dataset.closeFunction;
                if (closeFn && typeof window[closeFn] === 'function') window[closeFn]();
                else closeModal(id);
            }
        });
        const modal = overlay.querySelector('.modal');
        if (modal) {
            modal.addEventListener('click', function(e) {
                e.stopPropagation();
            });
        }
    }
}

function showAppQr() {
    openModal('appQrModal');
}
function closeModal(id, keepPending = false) {
    const overlay = document.getElementById(id);
    if (!overlay) return;
    overlay.classList.remove('open');
    overlay.style.zIndex = '';
    if (id === 'faceModal' && !keepPending) {
        _pendingPersonSave = null;
        facePendingDevices = null;
    }
    if (id === 'personModal' && !keepPending) {
        _pendingPersonSave = null;
    }
    if (id === 'freezeDeviceModal' && !keepPending && typeof _pendingFreezeDeviceAction !== 'undefined') {
        _pendingFreezeDeviceAction = null;
    }
    if (id === 'deleteDeviceModal' && !keepPending && typeof _pendingDeleteDeviceAction !== 'undefined') {
        _pendingDeleteDeviceAction = null;
    }
    // 新增：关闭同步设备模态框时清空选中状态
    if (id === 'syncDeviceModal') {
        if (typeof _selectedSyncDeviceIds !== 'undefined') _selectedSyncDeviceIds.clear();
        const allCb = document.getElementById('syncDeviceSelectAll');
        if (allCb) {
            allCb.checked = false;
            allCb.indeterminate = false;
        }
    }
}
function setConfirm(title, html, cb) {
    document.getElementById('confirmTitle').textContent = title;
    document.getElementById('confirmText').innerHTML = html;
    document.getElementById('confirmCancel').style.display = '';
    document.getElementById('confirmOk').onclick = () => { closeModal('confirmModal'); cb(); };
    document.getElementById('confirmOk').className = 'btn btn-danger';
    openModal('confirmModal');
}
function showResultModal(title, html, callback) {
    document.getElementById('confirmTitle').textContent = title;
    document.getElementById('confirmText').innerHTML = html;
    document.getElementById('confirmCancel').style.display = 'none';
    const okBtn = document.getElementById('confirmOk');
    okBtn.textContent = '确定';
    okBtn.className = 'btn btn-primary';
    okBtn.onclick = function() {
        closeModal('confirmModal');
        document.getElementById('confirmCancel').style.display = '';
        okBtn.textContent = '确认';
        okBtn.className = 'btn btn-danger';
        if (callback) callback();
    };
    openModal('confirmModal');
}
function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[m]));
}
function toast(type, msg) {
    if (type === 'warning') type = 'warn';
    const container = document.getElementById('tc');
    if (!container) return;
    while (container.querySelectorAll('.toast').length >= 4) {
        container.querySelector('.toast')?.remove();
    }
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3100);
}

/**
 * 切换某个元素(通常是 .vp-canvas-wrap)的全屏。
 * 没有指定时退出全屏。
 */
function toggleCanvasFullscreen(el) {
    if (!document.fullscreenElement) {
        if (el && el.requestFullscreen) {
            el.requestFullscreen().catch(e => toast('error', '全屏失败: ' + (e && e.message || e)));
        }
    } else {
        document.exitFullscreen().catch(() => {});
    }
}
