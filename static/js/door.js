// ========== 门禁控制模块 ==========
// 门模式 → 显示文字 / badge 类名 / 颜色映射
const DOOR_MODE_LABEL = { Normal: '正常', AlwaysOpen: '常开', AlwaysClose: '常闭' };
const DOOR_MODE_BADGE = { Normal: 'bg', AlwaysOpen: 'by', AlwaysClose: 'br' };
// 设备门模式缓存 { deviceId: 'Normal'|'AlwaysOpen'|'AlwaysClose' }
let doorModeMap = {};
let currentLogViewRows = [];
let currentGlobalLogViewRows = [];
let accessDetailRequestToken = 0;
let accessSnapshotRequestId = 0;
const accessCollapsedAreaIds = new Set();

function isFaceAccessMethod(method) {
    return String(method || '').trim() === '人脸识别';
}

function renderSnapshotCell(source, rowIndex, row) {
    if (String(row.method || '').trim() === '按钮开门') {
        return '<span style="color:var(--text3)">—</span>';
    }
    return `<button class="snapshot-btn" title="预览抓拍" aria-label="预览抓拍" onclick="openAccessSnapshot('${source}', ${rowIndex})">📷</button>`;
}

function getDoorModeBadge(mode) {
  const label = DOOR_MODE_LABEL[mode] || '正常';
  const cls = DOOR_MODE_BADGE[mode] || 'bg';
  return `<span class="badge ${cls}" style="font-size:10.5px">${label}</span>`;
}
function getAreaIcon(areaName) { const map = {'传动轴':'⚙️','部件':'🏗️','底零':'📦','精铸':'🏭','车轮':'🚗','其他':'📍'}; return map[areaName] || '📍'; }
function createDeviceCard(device) {
    const card = document.createElement('div');
    card.className = 'device-card';
    card.dataset.deviceId = device.id;
    const isOnline = device.online === true;
    if (!isOnline) {
        card.classList.add('offline');
        card.innerHTML = `<div class="dc-status"><span class="badge br" style="font-size:10.5px">离线</span></div><div class="dc-icon">🚪</div><div class="dc-name">${escapeHtml(device.name)}</div><div class="dc-ip">${device.ip}:${device.port}</div>${device.note ? `<div style="font-size:11.5px;color:var(--text3);margin-top:4px">${escapeHtml(device.note)}</div>` : ''}`;
    } else {
        card.onclick = () => openDetail(device);
        const mode = doorModeMap[device.id] || 'Normal';
        card.innerHTML = `<label class="card-checkbox" onclick="event.stopPropagation()"><input type="checkbox" onchange="onDeviceCheckChange('${device.id}', this.checked)"></label><div class="dc-status">${getDoorModeBadge(mode)}</div><div class="dc-icon">🚪</div><div class="dc-name">${escapeHtml(device.name)}</div><div class="dc-ip">${device.ip}:${device.port}</div>${device.note ? `<div style="font-size:11.5px;color:var(--text3);margin-top:4px">${escapeHtml(device.note)}</div>` : ''}`;
    }
    return card;
}

/** 异步查询所有设备的门模式并更新卡片右上角 badge */
async function fetchAndUpdateDoorModeBadges() {
    const tasks = getAccessDevices().map(async (dev) => {
        // 离线设备跳过门模式查询
        if (dev.online !== true) return;
        try {
            // 每个设备最多等 5 秒，避免离线设备卡住
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 5000);
            const r = await apiRequest('POST', '/api/door/mode/query', dev, null, false, controller.signal);
            clearTimeout(timer);
            if (r.code === 0 && r.data && r.data.mode) {
                doorModeMap[dev.id] = r.data.mode;
                updateCardBadge(dev.id, r.data.mode);
            }
        } catch (e) {
            // 静默失败，保持默认 Normal
        }
    });
    // 用 allSettled 确保个别失败不影响其他设备
    await Promise.allSettled(tasks);
}

/** 更新单个设备卡片的右上角 badge */
function updateCardBadge(deviceId, mode) {
    const card = document.querySelector(`.device-card[data-device-id="${deviceId}"]`);
    if (!card) return;
    const statusEl = card.querySelector('.dc-status');
    if (statusEl) statusEl.innerHTML = getDoorModeBadge(mode);
}

function updateToggleAllAreasButton() {
    const btn = document.getElementById('toggleAllAreasBtn');
    if (!btn) return;
    const grids = Array.from(document.querySelectorAll('#access-areas-container .device-grid'));
    const hasExpanded = grids.some(grid => grid.style.display !== 'none');
    btn.textContent = hasExpanded ? '全部折叠' : '全部展开';
}

function toggleAllAccessAreas() {
    const grids = Array.from(document.querySelectorAll('#access-areas-container .device-grid'));
    if (grids.length === 0) return;
    const hasExpanded = grids.some(grid => grid.style.display !== 'none');
    const collapseAll = hasExpanded;
    grids.forEach(grid => {
        const icon = document.getElementById(`icon-${grid.id}`);
        if (collapseAll) {
            grid.style.display = 'none';
            if (icon) icon.textContent = '▶';
            accessCollapsedAreaIds.add(grid.id);
        } else {
            grid.style.display = 'grid';
            if (icon) icon.textContent = '▼';
            accessCollapsedAreaIds.delete(grid.id);
        }
    });
    updateToggleAllAreasButton();
}

function toggleArea(gridId, iconId) {
    const grid = document.getElementById(gridId);
    const icon = document.getElementById(iconId);
    if (!grid || !icon) return;
    const isCollapsed = grid.style.display === 'none';
    if (isCollapsed) {
        grid.style.display = 'grid';
        icon.textContent = '▼';
        accessCollapsedAreaIds.delete(gridId);
    } else {
        grid.style.display = 'none';
        icon.textContent = '▶';
        accessCollapsedAreaIds.add(gridId);
    }
    updateToggleAllAreasButton();
}
function canViewAccessLogs() {
    return typeof hasPermission !== 'function' || hasPermission('access.view_logs');
}
function canSetAccessDoorMode() {
    return typeof hasPermission !== 'function' || hasPermission('access.set_door_mode');
}
function isAccessPageActive() {
    if (typeof currentPageName !== 'undefined') return currentPageName === 'access';
    return document.getElementById('page-access')?.classList.contains('active') === true;
}
function applyAccessPermissionUI() {
    const canPreview = typeof hasPermission !== 'function' || hasPermission('access.preview');
    const canOpen = typeof hasPermission !== 'function' || hasPermission('access.open_door');
    const canSetMode = canSetAccessDoorMode();
    const canViewLogs = canViewAccessLogs();
    const previewBtn = document.getElementById('previewBtn');
    if (previewBtn) previewBtn.style.display = canPreview ? '' : 'none';
    const openBtn = document.getElementById('openDoorBtn');
    if (openBtn) openBtn.style.display = canOpen ? '' : 'none';
    const previewOpenBtn = document.getElementById('previewOpenDoorBtn');
    if (previewOpenBtn) previewOpenBtn.style.display = canOpen ? '' : 'none';
    document.querySelectorAll('.btn-mode').forEach(btn => { btn.style.display = canSetMode ? '' : 'none'; });
    document.querySelectorAll('.area-checkbox, .card-checkbox').forEach(el => { el.style.display = canSetMode ? '' : 'none'; });
    const batchBar = document.getElementById('batchTopbar');
    if (batchBar) batchBar.style.display = canSetMode && isAccessPageActive() && document.getElementById('access-list')?.style.display !== 'none' ? 'flex' : 'none';
    document.querySelectorAll('.access-log-permission').forEach(el => { el.style.display = canViewLogs ? '' : 'none'; });
    const globalLogCard = document.getElementById('global-log-card');
    const accessList = document.getElementById('access-list');
    const accessDetail = document.getElementById('access-detail');
    const isListVisible = isAccessPageActive()
        && accessList?.style.display !== 'none'
        && !accessDetail?.classList.contains('active');
    if (globalLogCard) globalLogCard.style.display = canViewLogs && isListVisible ? 'block' : 'none';
}
function renderAccessGrid() {
    const container = document.getElementById('access-areas-container');
    if (!container) return;
    const accessDevices = getAccessDevices();
    if (accessDevices.length === 0) {
        if (!_devicesLoaded) {
            container.innerHTML = `<div class="empty-state"><div class="spinner"></div><p>正在加载设备…</p></div>`;
        } else {
            container.innerHTML = `<div class="empty-state"><div class="empty-icon">📡</div><p>暂无设备，请前往"设备管理"添加</p></div>`;
        }
        return;
    }

    // 保留用户手动折叠状态；设备状态定时刷新会重绘这里
    if (container.children.length > 0) {
        container.querySelectorAll('.device-grid').forEach(grid => {
            if (grid.style.display === 'none') {
                accessCollapsedAreaIds.add(grid.id);
            } else {
                accessCollapsedAreaIds.delete(grid.id);
            }
        });
    }

    const devicesByArea = {};
    accessDevices.forEach(d => { const area = d.area || '未分组'; if (!devicesByArea[area]) devicesByArea[area] = []; devicesByArea[area].push(d); });
let html = '';
    areas.forEach(areaName => {
        const areaDevices = devicesByArea[areaName] || [];
        if (areaDevices.length === 0) return;
        const onlineCount = areaDevices.filter(d => d.online === true).length;
        const gridId = `grid-${areaName.replace(/\s+/g, '_')}`;
        const iconId = `icon-${gridId}`;
        html += `<div class="area-section" data-area="${escapeHtml(areaName)}"><div class="area-title" onclick="toggleArea('${gridId}','${iconId}')"><label class="area-checkbox" onclick="event.stopPropagation()"><input type="checkbox" onchange="onAreaCheckChange('${escapeHtml(areaName)}', this.checked)"></label><span id="${iconId}" style="font-size:14px;margin-right:6px;">▼</span><span>${getAreaIcon(areaName)}</span> ${escapeHtml(areaName)}<span style="margin-left:8px;font-size:11px;color:var(--text3);">(${onlineCount}个设备)</span></div><div class="device-grid" id="${gridId}" style="display:grid;"></div></div>`;
    });
    const extraAreas = Object.keys(devicesByArea).filter(area => !areas.includes(area));
    extraAreas.forEach(areaName => {
        const areaDevices = devicesByArea[areaName];
        if (areaDevices.length === 0) return;
        const onlineCount = areaDevices.filter(d => d.online === true).length;
        const gridId = `grid-${areaName.replace(/\s+/g, '_')}`;
        const iconId = `icon-${gridId}`;
        html += `<div class="area-section" data-area="${escapeHtml(areaName)}"><div class="area-title" onclick="toggleArea('${gridId}','${iconId}')"><label class="area-checkbox" onclick="event.stopPropagation()"><input type="checkbox" onchange="onAreaCheckChange('${escapeHtml(areaName)}', this.checked)"></label><span id="${iconId}" style="font-size:14px;margin-right:6px;">▼</span><span>📁</span> ${escapeHtml(areaName)}<span style="margin-left:8px;font-size:11px;color:var(--text3);">(${onlineCount}个设备)</span></div><div class="device-grid" id="${gridId}" style="display:grid;"></div></div>`;
    });
    container.innerHTML = html;
    accessDevices.forEach(d => { const area = d.area || '未分组'; const gridId = `grid-${area.replace(/\s+/g, '_')}`; const grid = document.getElementById(gridId); if (grid) grid.appendChild(createDeviceCard(d)); });

    container.querySelectorAll('.device-grid').forEach(grid => {
        const icon = document.getElementById(`icon-${grid.id}`);
        if (accessCollapsedAreaIds.has(grid.id)) {
            grid.style.display = 'none';
            if (icon) icon.textContent = '▶';
        } else {
            grid.style.display = 'grid';
            if (icon) icon.textContent = '▼';
        }
    });
    updateToggleAllAreasButton();
    applyAccessPermissionUI();
}
function showAccessList() { 
    stopAutoRefresh(); 
    clearSelection();
    document.getElementById('access-list').style.display = 'block'; 
    document.getElementById('access-detail').classList.remove('active'); 
    currentDev = null;
    logData = [];
    currentLogViewRows = [];
    document.getElementById('breadcrumb').innerHTML = '<span>门禁控制</span>'; 
    // 返回列表恢复批量操作顶栏
    const batchBar = document.getElementById('batchTopbar');
    if (batchBar) batchBar.style.display = (isAccessPageActive() && canSetAccessDoorMode()) ? 'flex' : 'none';
    // 显示全局开门记录卡片
    const globalLogCard = document.getElementById('global-log-card');
    const canViewLogs = canViewAccessLogs();
    if (globalLogCard) globalLogCard.style.display = canViewLogs ? 'block' : 'none';
    renderAccessGrid(); 
    applyAccessPermissionUI();
    if (canViewLogs) {
        startGlobalLogAutoRefresh();
    }
    if (canViewLogs && globalLogData.length === 0) {
        const tbody = document.getElementById('globalLogBody');
        if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:20px"><span class="spinner"></span> 加载中...</td></tr>`;
        fetchGlobalLogDataAndProcess(true).catch(e => console.warn('初始加载全局日志失败:', e));
    }
}

// ========== 日志 ==========
async function fetchLogData() {
    if (!currentDev) throw new Error('未选择设备');
    if (!canViewAccessLogs()) return { code: 0, data: { records: [] }, skipped: true };
    let start = document.getElementById('logEnd').value;
    let end = document.getElementById('logEnd').value;
    if (!start) {
        start = getLocalDateString();
        document.getElementById('logEnd').value = start;
    }
    if (!end) {
        end = start;
        document.getElementById('logEnd').value = end;
    }
    return apiRequest('POST', '/api/log', currentDev, { start, end });
}

async function fetchLog() {
    if (!currentDev) return;
    if (!canViewAccessLogs()) return;
    const btn = document.querySelector('.btn-ghost[onclick="fetchLog()"]');
    if (btn) {
        const orig = btn.innerHTML; btn.innerHTML = '<span class="spinner"></span> 查询'; btn.disabled = true;
        try { await fetchLogDataAndProcess(false); } finally { btn.innerHTML = orig; btn.disabled = false; }
    } else { await fetchLogDataAndProcess(false); }
}

async function fetchLogDataAndProcess(silent = false) {
    if (!canViewAccessLogs()) return;
    try {
        const data = await fetchLogData();
        if (data.code === 0) {
            const newRecords = data.data.records || [];
            logData = newRecords;
            logData.sort((a, b) => new Date(b.time) - new Date(a.time));
            renderLog();
            if (!silent) {
                toast('info', newRecords.length ? `查询到 ${newRecords.length} 条开门记录` : '没有开门记录');
            }
        } else {
            throw new Error(data.msg || '请求失败');
        }
    } catch (e) {
        console.warn('获取日志失败:', e);
        const tbody = document.getElementById('logBody');
        tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state" style="padding:24px"><div class="empty-icon">⚠️</div><p>加载失败</p></div></td></tr>`;
        if (!silent) toast('error', '获取开门记录失败');
        throw e;
    }
}

async function doFetchLog(silent = false) {
    if (!currentDev) return;
    if (!canViewAccessLogs()) return;
    const refreshBtn = document.querySelector('#page-access .btn-ghost[onclick="fetchLog()"]');
    if (!silent && refreshBtn) {
        const originalHtml = refreshBtn.innerHTML;
        refreshBtn.innerHTML = '<span class="spinner"></span> 查询';
        refreshBtn.disabled = true;
        try {
            await fetchLogDataAndProcess(silent);
        } finally {
            refreshBtn.innerHTML = originalHtml;
            refreshBtn.disabled = false;
        }
    } else {
        await fetchLogDataAndProcess(silent);
    }
}

function filterLog() { 
    logFilter = document.getElementById('logSearch').value.toLowerCase(); 
    renderLog(); 
}

function renderLog() {
    const tbody = document.getElementById('logBody');
    let data = logFilter ? logData.filter(r => (r.name + r.user_id).toLowerCase().includes(logFilter)) : [...logData];
    data = data.filter(r => {
        const sameDevice = !r.device_id || !currentDev?.id || String(r.device_id) === String(currentDev.id);
        const sameDoor = Number(r.door) === Number(currentDoor);
        return sameDevice && sameDoor;
    });
    currentLogViewRows = data;
    if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state" style="padding:24px"><div class="empty-icon">📋</div><p>暂无开门记录</p></div></td></tr>`;
        return;
    }
    const mBadge = m => { const c={'人脸识别':'bb','刷卡':'by','远程开门':'bg','密码':'bgr'}; return `<span class="badge ${c[m]||'bgr'}">${escapeHtml(m)}</span>`; };
    tbody.innerHTML = data.map((r, idx) => `
        <tr>
            <td><span class="mono">${escapeHtml(r.time)}</span></td>
            <td><span class="mono">${escapeHtml(r.user_id||'—')}</span></td>
            <td>${r.name ? escapeHtml(r.name) : '<span style="color:var(--text3)">—</span>'}</td>
            <td>${mBadge(r.method)}</td>
            <td><span class="badge ${r.status==='成功'?'bg':'br'}">${escapeHtml(r.status)}</span></td>
            <td>${renderSnapshotCell('device', idx, r)}</td>
        </tr>
    `).join('');
}

async function silentRefreshLog() {
    if (!currentDev) return;
    if (!canViewAccessLogs()) return;
    const dev = currentDev;
    const token = accessDetailRequestToken;
    let startTime;
    if (logData.length > 0) {
        startTime = logData[0].time;
    } else {
        startTime = getLocalDateString() + ' 00:00:00';
    }
    const endTime = getLocalDateString() + ' 23:59:59';

    try {
        const data = await apiRequest('POST', '/api/log', dev, { start: startTime, end: endTime });
        if (token !== accessDetailRequestToken || !currentDev || String(currentDev.id) !== String(dev.id)) return;
        if (data.code === 0) {
            const newRecords = data.data.records || [];
            const existingKeys = new Set(logData.map(r => `${r.time}|${r.door}|${r.user_id}|${r.method}|${r.status}`));
            const uniqueNew = newRecords.filter(r => !existingKeys.has(`${r.time}|${r.door}|${r.user_id}|${r.method}|${r.status}`));
            if (uniqueNew.length > 0) {
                logData = [...uniqueNew, ...logData];
                logData.sort((a, b) => new Date(b.time) - new Date(a.time));
                renderLog();
            }
        }
    } catch (e) {
        console.warn('自动刷新日志失败:', e);
    }
}

// ---------- 门状态轮询 ----------
async function fetchDoorStatus() {
    if (!currentDev) return;
    const dev = currentDev;
    const door = currentDoor;
    const token = accessDetailRequestToken;
    try {
        const data = await apiRequest('POST', '/api/door/status', dev, { channel: door + 1 });
        if (token !== accessDetailRequestToken || !currentDev || String(currentDev.id) !== String(dev.id) || currentDoor !== door) return;
        if (data.code === 0) {
            updateDoorStatusUI(data.data.is_open);
        }
    } catch (e) {
        console.warn('查询门状态失败:', e);
    }
}

function updateDoorStatusUI(isOpen) {
    const icon = document.getElementById('doorStatusIcon');
    const text = document.getElementById('doorStatusText');
    if (!icon || !text) return;
    if (isOpen) {
        icon.textContent = '🔓';
        text.textContent = '门已开';
        text.style.color = 'var(--red2)';
    } else {
        icon.textContent = '🔒';
        text.textContent = '门已关';
        text.style.color = 'var(--accent)';
    }
}

function startAutoRefresh() {
    stopAutoRefresh();
    if (canViewAccessLogs()) {
        logRefreshTimer = setInterval(silentRefreshLog, 3000);
    }
    doorStatusTimer = setInterval(fetchDoorStatus, 3000);
}

function stopAutoRefresh() {
    if (logRefreshTimer) {
        clearInterval(logRefreshTimer);
        logRefreshTimer = null;
    }
    if (doorStatusTimer) {
        clearInterval(doorStatusTimer);
        doorStatusTimer = null;
    }
}

function openDetail(dev) {
    stopAutoRefresh();
    const token = ++accessDetailRequestToken;
    // 进入详情页隐藏批量操作顶栏
    const batchBar = document.getElementById('batchTopbar');
    if (batchBar) batchBar.style.display = 'none';
    currentDev = dev;
    currentDoor = 0;
    logFilter = '';
    document.getElementById('logSearch').value = '';
    document.getElementById('access-list').style.display = 'none';
    document.getElementById('access-detail').classList.add('active');
    // 隐藏全局开门记录卡片
    const globalLogCard = document.getElementById('global-log-card');
    if (globalLogCard) globalLogCard.style.display = 'none';
    document.getElementById('detail-name').textContent = dev.name;
    document.getElementById('detail-ip').textContent = `${dev.ip}:${dev.port}`;
    document.getElementById('breadcrumb').innerHTML = `<span style="cursor:pointer;color:var(--text3)" onclick="showAccessList()">门禁控制</span><span style="margin:0 4px">›</span><span style="color:var(--text2)">${escapeHtml(dev.name)}</span>`;
    applyAccessPermissionUI();
    updateDoorStatusUI(false);
    logData = [];
    const today = getLocalDateString();
    document.getElementById('logEnd').value = today;
    const tbody = document.getElementById('logBody');
    const tasks = [fetchDoorStatus()];
    const shouldLoadLogs = canViewAccessLogs();
    if (shouldLoadLogs) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:20px"><span class="spinner"></span> 加载中...</td></tr>`;
        tasks.unshift(fetchLogDataAndProcess(true));
    } else if (tbody) {
        tbody.innerHTML = '';
    }
    Promise.allSettled(tasks).then(results => {
        if (token !== accessDetailRequestToken || !currentDev || String(currentDev.id) !== String(dev.id)) return;
        // 任一失败都不要阻止门状态/日志的周期刷新
        const logFailed = shouldLoadLogs && results[0].status === 'rejected';
        if (logFailed) {
            tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state" style="padding:24px"><div class="empty-icon">⚠️</div><p>加载失败</p></div></td></tr>`;
        }
        startAutoRefresh();
    });
}

async function doOpenDoor() {
    if (!currentDev) return;
    if (typeof hasPermission === 'function' && !hasPermission('access.open_door')) {
        toast('warn', '当前账户没有远程开门权限');
        return;
    }
    const btn = document.getElementById('openDoorBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> &nbsp; 开门中…';
    try {
        const r = await apiRequest('POST', '/api/open', currentDev, { channel: currentDoor });
        if (r.code === 0) {
            toast('success', `✅ 远程开门成功（${currentDev.name}）`);
            if (canViewAccessLogs()) {
                fetchLogDataAndProcess(true).catch(e => console.warn('开门后刷新日志失败:', e));
            }
        } else {
            toast('error', `开门失败：${r.msg}`);
        }
    } catch(e) {
        toast('error', '无法连接服务，请检查 server.py 是否运行');
    }
    btn.disabled = false;
    btn.innerHTML = '🔓 &nbsp; 远程开门';
}

// 实时预览模态框内的远程开门按钮（同样作用于 currentDev）
async function doOpenDoorFromPreview() {
    if (!currentDev) return;
    if (typeof hasPermission === 'function' && !hasPermission('access.open_door')) {
        toast('warn', '当前账户没有远程开门权限');
        return;
    }
    const btn = document.getElementById('previewOpenDoorBtn');
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> &nbsp; 开门中…';
    try {
        const r = await apiRequest('POST', '/api/open', currentDev, { channel: currentDoor });
        if (r.code === 0) {
            toast('success', `✅ 远程开门成功（${currentDev.name}）`);
            if (canViewAccessLogs()) {
                fetchLogDataAndProcess(true).catch(e => console.warn('开门后刷新日志失败:', e));
            }
        } else {
            toast('error', `开门失败：${r.msg}`);
        }
    } catch(e) {
        toast('error', '无法连接服务，请检查 server.py 是否运行');
    }
    btn.disabled = false;
    btn.innerHTML = '🔓 &nbsp; 远程开门';
}

async function setDoorMode(mode) {
    if (!currentDev) return;
    if (typeof hasPermission === 'function' && !hasPermission('access.set_door_mode')) {
        toast('warn', '当前账户没有门模式设置权限');
        return;
    }
    const labelMap = { Normal: '正常', AlwaysOpen: '常开', AlwaysClose: '常闭' };
    const label = labelMap[mode] || mode;
    const btns = document.querySelectorAll('.btn-mode');
    btns.forEach(b => b.disabled = true);
    // 高亮被点击的按钮
    const clickedBtn = document.getElementById(`btn-mode-${mode.toLowerCase()}`);
    if (clickedBtn) clickedBtn.innerHTML = '<span class="spinner"></span>';
    try {
        const r = await apiRequest('POST', '/api/door/mode', currentDev, { mode });
        if (r.code === 0) {
            toast('success', `已设为${label}模式`);
            // 更新当前设备卡片 badge
            doorModeMap[currentDev.id] = mode;
            updateCardBadge(currentDev.id, mode);
            fetchDoorStatus();
        } else {
            toast('error', `设置失败：${r.msg}`);
        }
    } catch(e) {
        toast('error', '无法连接服务');
    }
    btns.forEach(b => b.disabled = false);
    document.getElementById('btn-mode-normal').innerHTML = '正常';
    document.getElementById('btn-mode-open').innerHTML = '常开';
    document.getElementById('btn-mode-close').innerHTML = '常闭';
}

// ================= 全局开门记录 =================
let globalLogData = [];
let globalLogFilter = '';
let globalLogRefreshTimer = null;
let globalLogLoading = false;
let accessSnapshotObjectUrl = null;

function buildAccessLogKey(r, includeDevice = false) {
    const parts = [r.time || '', r.door || '', r.user_id || '', r.method || '', r.status || ''];
    if (includeDevice) parts.unshift(r.device_id || r.device_name || '');
    return parts.join('|');
}

function mergeLogRows(existingRows, newRows, includeDevice = false) {
    const existingKeys = new Set(existingRows.map(r => buildAccessLogKey(r, includeDevice)));
    const uniqueNew = newRows.filter(r => !existingKeys.has(buildAccessLogKey(r, includeDevice)));
    if (!uniqueNew.length) return existingRows;
    return [...uniqueNew, ...existingRows].sort((a, b) => new Date(b.time) - new Date(a.time));
}

function startGlobalLogAutoRefresh() {
    if (globalLogRefreshTimer || !canViewAccessLogs()) return;
    globalLogRefreshTimer = setInterval(() => {
        if (isAccessPageActive() && canViewAccessLogs()) {
            fetchGlobalLogDataAndProcess(true).catch(e => console.warn('自动刷新全局日志失败:', e));
        }
    }, 15000);
}

function stopGlobalLogAutoRefresh() {
    if (globalLogRefreshTimer) {
        clearInterval(globalLogRefreshTimer);
        globalLogRefreshTimer = null;
    }
}

async function fetchGlobalLog() {
    const btn = document.querySelector('.btn-ghost[onclick="fetchGlobalLog()"]');
    if (btn) {
        const orig = btn.innerHTML;
        btn.innerHTML = '<span class="spinner"></span> 查询';
        btn.disabled = true;
        try {
            await fetchGlobalLogDataAndProcess(false);
        } finally {
            btn.innerHTML = orig;
            btn.disabled = false;
        }
    } else {
        await fetchGlobalLogDataAndProcess(false);
    }
}

async function fetchGlobalLogDataAndProcess(silent = false) {
    if (!canViewAccessLogs()) {
        const card = document.getElementById('global-log-card');
        if (card) card.style.display = 'none';
        stopGlobalLogAutoRefresh();
        return;
    }
    if (globalLogLoading) return;
    globalLogLoading = true;
    try {
        let end = document.getElementById('globalLogEnd').value;
        if (!end) {
            end = getLocalDateString();
            document.getElementById('globalLogEnd').value = end;
        }
        
        // 手动查询查选中日期全天；初始加载/自动刷新查打开页面以来的增量
        let start;
        if (silent) {
            if (globalLogData.length > 0) {
                start = globalLogData[0].time;
            } else {
                start = userLoginTime || (getLocalDateString() + ' 00:00:00');
            }
        } else {
            start = end + ' 00:00:00';
        }
        
        const data = await fetch(`${API}/api/log/all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ start, end, cache: silent ? 1 : 0 })
        }).then(r => r.json());
        
        if (data.code === 0) {
            const newRecords = data.data.records || [];
            const failedDevices = data.data.failed_devices || [];
            if (silent) {
                const merged = mergeLogRows(globalLogData, newRecords, true);
                globalLogData = merged;
                renderGlobalLog();
            } else {
                globalLogData = newRecords;
                globalLogData.sort((a, b) => new Date(b.time) - new Date(a.time));
                renderGlobalLog();
                toast('info', newRecords.length ? `查询到 ${newRecords.length} 条开门记录` : '没有开门记录');
                if (failedDevices.length > 0) {
                    const names = failedDevices.slice(0, 3).map(d => d.name || d.ip || '未知设备').join('、');
                    const more = failedDevices.length > 3 ? ` 等 ${failedDevices.length} 台` : '';
                    toast('warn', `${failedDevices.length} 台设备日志获取失败：${names}${more}`);
                }
            }
        } else {
            throw new Error(data.msg || '请求失败');
        }
    } catch (e) {
        console.warn('获取全局日志失败:', e);
        if (!silent) {
            const tbody = document.getElementById('globalLogBody');
            if (tbody) tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state" style="padding:24px"><div class="empty-icon">⚠️</div><p>加载失败</p></div></td></tr>`;
            toast('error', '获取开门记录失败');
        } else if (globalLogData.length === 0) {
            renderGlobalLog();
        }
    } finally {
        globalLogLoading = false;
    }
}

function filterGlobalLog() {
    globalLogFilter = document.getElementById('globalLogSearch').value.toLowerCase();
    renderGlobalLog();
}

function renderGlobalLog() {
    const tbody = document.getElementById('globalLogBody');
    let data = globalLogFilter 
        ? globalLogData.filter(r => (r.name + r.user_id + r.device_name).toLowerCase().includes(globalLogFilter))
        : [...globalLogData];
    currentGlobalLogViewRows = data;
    
    if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state" style="padding:24px"><div class="empty-icon">📋</div><p>暂无开门记录</p></div></td></tr>`;
        return;
    }
    
    const mBadge = m => {
        const c = {'人脸识别':'bb','刷卡':'by','远程开门':'bg','密码':'bgr'};
        return `<span class="badge ${c[m]||'bgr'}">${escapeHtml(m)}</span>`;
    };
    
    tbody.innerHTML = data.map((r, idx) => `
        <tr>
            <td><span class="mono">${escapeHtml(r.time)}</span></td>
            <td>${escapeHtml(r.device_name || '—')}</td>
            <td><span class="mono">${escapeHtml(r.user_id||'—')}</span></td>
            <td>${r.name ? escapeHtml(r.name) : '<span style="color:var(--text3)">—</span>'}</td>
            <td>${mBadge(r.method)}</td>
            <td><span class="badge ${r.status==='成功'?'bg':'br'}">${escapeHtml(r.status)}</span></td>
            <td>${renderSnapshotCell('global', idx, r)}</td>
        </tr>
    `).join('');
}

async function openAccessSnapshot(source, index) {
    const requestId = ++accessSnapshotRequestId;
    const rows = source === 'global' ? currentGlobalLogViewRows : currentLogViewRows;
    const rowIndex = Number(index);
    const row = source === 'global'
        ? (rows[rowIndex] || null)
        : (rows[rowIndex] || null);
    if (!row) {
        toast('error', '通行记录不存在');
        return;
    }

    const device = source === 'global'
        ? { id: row.device_id }
        : currentDev;
    if (!device || !device.id) {
        toast('error', '缺少设备信息，无法获取抓拍');
        return;
    }

    setAccessSnapshotLoading(row);
    openModal('accessSnapshotModal');
    try {
        const resp = await apiRequest('POST', '/api/log/snapshot', device, {
            time: row.time,
            user_id: row.user_id || '',
            name: row.name || '',
            door: row.door,
            method: row.method || '',
            status: row.status || ''
        });
        if (resp.code !== 0) throw new Error(resp.msg || '查询抓拍失败');
        const info = resp.data || {};
        if (requestId !== accessSnapshotRequestId) return;
        if (!info.has_image || !info.image_url) {
            setAccessSnapshotMessage(info.message || '该通行记录没有抓拍图片');
            return;
        }
        await loadAccessSnapshotImage(info.image_url, requestId);
    } catch (e) {
        if (requestId !== accessSnapshotRequestId) return;
        console.warn('抓拍预览失败:', e);
        setAccessSnapshotMessage(e.message || '抓拍预览失败');
    }
}

function setAccessSnapshotLoading(row) {
    const title = document.getElementById('accessSnapshotTitle');
    const status = document.getElementById('accessSnapshotStatus');
    const img = document.getElementById('accessSnapshotImg');
    if (title) title.textContent = `通行抓拍 · ${row.time || ''}`;
    if (status) {
        status.style.display = 'flex';
        status.innerHTML = '<span class="spinner"></span><span>正在获取抓拍…</span>';
    }
    if (img) {
        img.style.display = 'none';
        img.removeAttribute('src');
    }
    if (accessSnapshotObjectUrl) {
        URL.revokeObjectURL(accessSnapshotObjectUrl);
        accessSnapshotObjectUrl = null;
    }
}

function setAccessSnapshotMessage(message) {
    const status = document.getElementById('accessSnapshotStatus');
    const img = document.getElementById('accessSnapshotImg');
    if (img) {
        img.style.display = 'none';
        img.removeAttribute('src');
    }
    if (status) {
        status.style.display = 'flex';
        status.textContent = message || '暂无抓拍图片';
    }
}

async function loadAccessSnapshotImage(url, requestId) {
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) throw new Error(`图片加载失败 HTTP ${res.status}`);
    const blob = await res.blob();
    if (requestId !== accessSnapshotRequestId) return;
    if (accessSnapshotObjectUrl) URL.revokeObjectURL(accessSnapshotObjectUrl);
    accessSnapshotObjectUrl = URL.createObjectURL(blob);
    const img = document.getElementById('accessSnapshotImg');
    const status = document.getElementById('accessSnapshotStatus');
    if (img) {
        img.src = accessSnapshotObjectUrl;
        img.style.display = 'block';
    }
    if (status) status.style.display = 'none';
}

window.closeAccessSnapshot = closeAccessSnapshot;
function closeAccessSnapshot() {
    accessSnapshotRequestId++;
    closeModal('accessSnapshotModal');
    const img = document.getElementById('accessSnapshotImg');
    if (img) {
        img.style.display = 'none';
        img.removeAttribute('src');
    }
    if (accessSnapshotObjectUrl) {
        URL.revokeObjectURL(accessSnapshotObjectUrl);
        accessSnapshotObjectUrl = null;
    }
}

// 页面加载时初始化全局日志结束时间输入框
document.addEventListener('DOMContentLoaded', function() {
    const today = getLocalDateString();
    const globalLogEndInput = document.getElementById('globalLogEnd');
    if (globalLogEndInput) {
        globalLogEndInput.value = today;
    }
    if (typeof onTabClose === 'function') {
        onTabClose('access', () => {
            stopAutoRefresh();
            stopGlobalLogAutoRefresh();
            closeAccessSnapshot();
        });
    }
});

// ========== 批量操作 ==========
// 存储选中设备 ID 的 Set
const selectedDeviceIds = new Set();

function onDeviceCheckChange(deviceId, checked) {
    if (checked) {
        selectedDeviceIds.add(deviceId);
    } else {
        selectedDeviceIds.delete(deviceId);
    }
    // 更新卡片选中态
    const card = document.querySelector(`.device-card[data-device-id="${deviceId}"]`);
    if (card) card.classList.toggle('selected', checked);
    // 更新分组 checkbox 状态
    const area = getDeviceArea(deviceId);
    if (area) updateAreaCheckboxState(area);
    updateBatchTopbar();
}

function onAreaCheckChange(areaName, checked) {
    const areaDevices = getAreaDevices(areaName);
    areaDevices.forEach(d => {
        // 离线设备不可选中
        if (d.online !== true) return;
        const id = String(d.id);
        if (checked) {
            selectedDeviceIds.add(id);
        } else {
            selectedDeviceIds.delete(id);
        }
        // 更新卡片 UI
        const card = document.querySelector(`.device-card[data-device-id="${id}"]`);
        if (card) {
            card.classList.toggle('selected', checked);
            const cb = card.querySelector('.card-checkbox input');
            if (cb) cb.checked = checked;
        }
    });
    updateAreaCheckboxState(areaName);
    updateBatchTopbar();
}

function getDeviceArea(deviceId) {
    const d = getAccessDevices().find(dev => String(dev.id) === String(deviceId));
    return d ? (d.area || '未分组') : null;
}

function getAreaDevices(areaName) {
    return getAccessDevices().filter(d => (d.area || '未分组') === areaName);
}

function updateAreaCheckboxState(areaName) {
    const areaDevices = getAreaDevices(areaName);
    const onlineDevices = areaDevices.filter(d => d.online === true);
    const section = document.querySelector(`.area-section[data-area="${escapeHtml(areaName)}"]`);
    if (!section) return;
    const areaCheckbox = section.querySelector('.area-title .area-checkbox input');
    if (!areaCheckbox) return;
    const checkedCount = areaDevices.filter(d => selectedDeviceIds.has(String(d.id))).length;
    if (checkedCount === 0) {
        areaCheckbox.checked = false;
        areaCheckbox.indeterminate = false;
        section.querySelector('.area-title').classList.remove('partial');
    } else if (checkedCount === onlineDevices.length) {
        areaCheckbox.checked = true;
        areaCheckbox.indeterminate = false;
        section.querySelector('.area-title').classList.remove('partial');
    } else {
        areaCheckbox.checked = false;
        areaCheckbox.indeterminate = true;
        section.querySelector('.area-title').classList.add('partial');
    }
}

function updateBatchTopbar() {
    const countEl = document.getElementById('batchCount');
    const count = selectedDeviceIds.size;
    if (countEl) countEl.textContent = `已选择 ${count} 个设备`;
}

function getSelectedDevices() {
    return getAccessDevices().filter(d => selectedDeviceIds.has(String(d.id)));
}

function clearSelection() {
    // 清除所有卡片选中态
    document.querySelectorAll('.device-card.selected').forEach(c => c.classList.remove('selected'));
    document.querySelectorAll('.card-checkbox input:checked').forEach(cb => cb.checked = false);
    // 清除所有分组 checkbox
    document.querySelectorAll('.area-title .area-checkbox input').forEach(cb => {
        cb.checked = false;
        cb.indeterminate = false;
    });
    document.querySelectorAll('.area-title.partial').forEach(t => t.classList.remove('partial'));
    selectedDeviceIds.clear();
    updateBatchTopbar();
}

async function batchSetDoorMode(mode) {
    if (!canSetAccessDoorMode()) return;
    const selected = getSelectedDevices();
    if (selected.length === 0) {
        toast('warn', '请先选择设备');
        return;
    }
    const labelMap = { Normal: '恢复正常', AlwaysOpen: '一键常开', AlwaysClose: '一键常闭' };
    const label = labelMap[mode] || mode;
    setConfirm('批量门模式', `确定对 <span class="confirm-hi">${selected.length}</span> 个设备执行「${escapeHtml(label)}」操作？`, async () => {
        let success = 0, fail = 0;
        const failedNames = [];
        for (let i = 0; i < selected.length; i++) {
            const dev = selected[i];
            try {
                const r = await apiRequest('POST', '/api/door/mode', dev, { mode });
                if (r.code === 0) {
                    success++;
                    // 更新该设备卡片 badge
                    doorModeMap[dev.id] = mode;
                    updateCardBadge(dev.id, mode);
                } else {
                    fail++;
                    failedNames.push(dev.name || dev.ip || `设备${dev.id}`);
                    console.warn(`[batch] ${dev.name} 失败: ${r.msg}`);
                }
            } catch (e) {
                fail++;
                failedNames.push(dev.name || dev.ip || `设备${dev.id}`);
                console.warn(`[batch] ${dev.name} 异常:`, e);
            }
            // 更新进度
            const countEl = document.getElementById('batchCount');
            if (countEl) countEl.textContent = `执行中 ${i + 1}/${selected.length}（成功 ${success}，失败 ${fail}）`;
        }
        let msg = `批量${label}完成：成功 ${success} 个，失败 ${fail} 个`;
        if (failedNames.length > 0) {
            const names = failedNames.slice(0, 3).join('、');
            const more = failedNames.length > 3 ? ` 等 ${failedNames.length} 个` : '';
            msg += `；失败：${names}${more}`;
        }
        toast(success > 0 && fail > 0 ? 'warn' : (success > 0 ? 'success' : 'error'), msg);
        clearSelection();
    });
}
