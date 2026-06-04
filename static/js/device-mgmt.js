// ========== 区域 API ==========
async function loadAreas() {
    try {
        const resp = await fetch(`${API}/api/areas`);
        const data = await resp.json();
        if (data.code === 0) {
            areas = data.data;
            renderAreaSelect();
            renderAreaList();
        } else {
            toast('error', '加载区域列表失败: ' + data.msg);
        }
    } catch (e) {
        console.error('loadAreas error:', e);
        toast('error', '无法连接服务器，请检查后端');
    }
}

async function addArea() {
    const name = document.getElementById('newAreaName').value.trim();
    if (!name) { toast('error', '请输入区域名称'); return; }
    try {
        const resp = await fetch(`${API}/api/areas`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
        const data = await resp.json();
        if (data.code === 0) {
            toast('success', `区域「${name}」已添加`);
            document.getElementById('newAreaName').value = '';
            await loadAreas();
            await loadDevicesFromServer();
        } else { toast('error', data.msg); }
    } catch (e) { toast('error', '添加失败'); }
}

async function deleteArea(areaName) {
    setConfirm('删除区域', `确定删除区域 <span class="confirm-hi">「${escapeHtml(areaName)}」</span>？区域下有设备时无法删除。`, async () => {
        try {
            const resp = await fetch(`${API}/api/areas/${encodeURIComponent(areaName)}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.code === 0) {
                toast('success', `区域「${areaName}」已删除`);
                await loadAreas();
                await loadDevicesFromServer();
            } else { toast('error', data.msg); }
        } catch (e) { toast('error', '删除失败'); }
    });
}

function renderAreaSelect() {
    const select = document.getElementById('d-area');
    if (!select) return;
    select.innerHTML = '';
    areas.forEach(area => { const o = document.createElement('option'); o.value = area; o.textContent = area; select.appendChild(o); });
    if (areas.length) select.value = areas[0];
}

function renderAreaList() {
    const tbody = document.getElementById('areaListBody');
    if (!tbody) return;
    if (!areas.length) { tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;padding:20px">暂无区域</td></tr>'; return; }
    tbody.innerHTML = areas.map(area => `<tr><td>${escapeHtml(area)}</td><td><button class="btn btn-danger btn-sm" onclick="deleteArea('${escapeHtml(area)}')">删除</button></td></tr>`).join('');
}

function openAreaModal() { renderAreaList(); openModal('areaModal'); }

// ========== 设备 API ==========
function applyDeviceList(nextDevices) {
    devices = nextDevices || [];
    _devicesLoaded = true;
    renderAccessGrid();
    renderDevTable();
    fillPersonDevSel();
    if (typeof renderMonitorGrid === 'function') renderMonitorGrid();
    // 设备类型决定哪些模块对当前用户可见，列表变更后同步主页卡片并关闭无依赖的已开 tab。
    if (typeof renderHomeCards === 'function') renderHomeCards();
    if (typeof closeOrphanTabs === 'function') closeOrphanTabs();
}

async function loadDevicesFromServer() {
    try {
        const resp = await fetch(`${API}/api/devices`);
        const data = await resp.json();
        if (data.code === 0) {
            applyDeviceList(data.data || []);
        }
        else toast('error', data.msg);
    } catch (e) { _devicesLoaded = true; toast('error', '无法连接服务器'); }
}

async function refreshDeviceStatusInBackground() {
    if (_deviceStatusRefreshing) return;
    _deviceStatusRefreshing = true;
    try {
        const resp = await fetch(`${API}/api/devices/status`);
        const data = await resp.json();
        if (data.code === 0) {
            applyDeviceList(data.data || []);
        } else {
            console.warn('[devices] 状态刷新失败:', data.msg);
        }
    } catch (e) {
        console.warn('[devices] 状态刷新失败', e);
    } finally {
        _deviceStatusRefreshing = false;
    }
}

function startDeviceStatusAutoRefresh(intervalMs = 30000) {
    if (deviceStatusTimer) return;
    deviceStatusTimer = setInterval(() => {
        refreshDeviceStatusInBackground();
    }, intervalMs);
}

async function refreshDevices() {
    await loadDevicesFromServer();
    refreshDeviceStatusInBackground();
}

// ========== 设备管理模块 ==========
function compareIP(ipA, ipB) {
    const partsA = ipA.split('.').map(Number);
    const partsB = ipB.split('.').map(Number);
    for (let i = 0; i < 4; i++) {
        if (partsA[i] !== partsB[i]) return partsA[i] - partsB[i];
    }
    return 0;
}

function sortDevices(devArray) {
    const areaOrder = {};
    areas.forEach((area, index) => { areaOrder[area] = index; });
    return [...devArray].sort((a, b) => {
        const orderA = areaOrder[a.area] ?? 9999;
        const orderB = areaOrder[b.area] ?? 9999;
        if (orderA !== orderB) return orderA - orderB;
        return compareIP(a.ip, b.ip);
    });
}

function getAccessDevices() {
    return devices.filter(d => (d.device_type || 'access') === 'access');
}

function getNvrDevices() {
    return devices.filter(d => d.device_type === 'nvr');
}

function deviceTypeLabel(d) {
    return d.device_type === 'nvr' ? '录像机' : '门禁';
}

function deviceTypeBadge(d) {
    if (d.device_type === 'nvr') {
        const count = Number(d.channel_count || 0);
        return `<span class="badge bb">录像机${count ? ` · ${count}路` : ''}</span>`;
    }
    return '<span class="badge bg">门禁</span>';
}

function renderDevTable(filter='') {
    const tbody = document.getElementById('devBody');
    const kw = String(filter || '').trim().toLowerCase();
    let data = kw ? devices.filter(d => String(d.name || '').toLowerCase().includes(kw) || String(d.ip || '').toLowerCase().includes(kw)) : devices;
    data = sortDevices(data);
    const onlineCount = devices.filter(d => d.online).length;
    document.getElementById('stat-total').textContent = devices.length;
    document.getElementById('stat-online').textContent = onlineCount;
    document.getElementById('stat-offline').textContent = devices.length - onlineCount;
    if (!data.length) {
        tbody.innerHTML = _devicesLoaded
            ? `<tr><td colspan="8"><div class="empty-state" style="padding:24px"><div class="empty-icon">📡</div><p>暂无设备</p></div></td></tr>`
            : `<tr><td colspan="8"><div class="empty-state" style="padding:24px"><span class="spinner"></span><p>正在加载设备…</p></div></td></tr>`;
        return;
    }
    tbody.innerHTML = data.map(d => `
        <tr>
            <td><strong style="color:var(--text)">${escapeHtml(d.name)}</strong>${d.note?`<br><span style="font-size:11.5px;color:var(--text3)">${escapeHtml(d.note)}</span>`:''}</td>
            <td>${deviceTypeBadge(d)}</td>
            <td><span class="mono">${escapeHtml(d.ip)}</span></td>
            <td><span class="mono">${d.port}</span></td>
            <td><span class="mono">${escapeHtml(d.username)}</span></td>
            <td>${d.device_type === 'nvr' ? '<span style="color:var(--text3);font-size:12px">—</span>' : `<span class="badge bgr">${escapeHtml(d.area)}</span>`}</td>
            <td><span class="badge ${d.online ? 'bg' : 'br'}">${d.online ? '在线' : '离线'}</span></td>
            <td><div class="dev-actions">${currentRole === 'admin' ? `<button class="btn btn-ghost btn-sm" onclick="openDeviceModal(${d.id})">⚙ 设置</button><button class="btn btn-danger btn-sm" onclick="confirmDelDev(${d.id})">删除</button>` : '<span style="color:var(--text3);font-size:12px">—</span>'}</div></td>
        </tr>
    `).join('');
}

function filterDevices(v) { renderDevTable(v); }

function openDeviceModal(id=null) {
    editDevId = id;
    document.getElementById('devModalTitle').textContent = id ? '编辑设备' : '添加设备';
    renderAreaSelect();
    if (id) {
        const d = devices.find(x=>x.id===id);
        if (d) {
            document.getElementById('d-name').value = d.name;
            document.getElementById('d-type').value = d.device_type || 'access';
            document.getElementById('d-type').disabled = true;
            document.getElementById('d-ip').value = d.ip;
            document.getElementById('d-port').value = d.port;
            const areaSel = document.getElementById('d-area');
            // 设备原区域已被管理员删除时,临时插入一个 option 让用户看见,避免静默改区域
            if (d.area && !areas.includes(d.area)) {
                const opt = document.createElement('option');
                opt.value = d.area;
                opt.textContent = `${d.area}(原区域,已删除)`;
                opt.dataset.orphan = '1';
                areaSel.appendChild(opt);
            }
            areaSel.value = d.area;
            areaSel.dataset.currentArea = d.area || '';
            document.getElementById('d-user').value = d.username;
            document.getElementById('d-pass').value = d.password || '';
            document.getElementById('d-note').value = d.note||'';
        }
    } else {
        ['d-name','d-ip','d-port','d-user','d-pass','d-note'].forEach(f=>document.getElementById(f).value='');
        document.getElementById('d-type').value = 'access';
        document.getElementById('d-type').disabled = false;
        document.getElementById('d-area').dataset.currentArea = '';
        if (areas.length) document.getElementById('d-area').value = areas[0];
    }
    updateDeviceTypeFields();
    openModal('devModal');
}

function updateDeviceTypeFields() {
    const typeEl = document.getElementById('d-type');
    const areaRow = document.getElementById('d-area-row');
    const areaEl = document.getElementById('d-area');
    if (!typeEl || !areaRow || !areaEl) return;
    const isNvr = typeEl.value === 'nvr';
    areaRow.style.display = isNvr ? 'none' : '';
    areaEl.required = !isNvr;
    if (!isNvr && !areaEl.value && areas.length) {
        areaEl.value = areas[0];
    }
}

async function saveDevice() {
    const name = document.getElementById('d-name').value.trim();
    const ip = document.getElementById('d-ip').value.trim();
    if (!name||!ip) { toast('error','请填写设备名称和 IP 地址'); return; }
    const deviceType = document.getElementById('d-type').value || 'access';
    const areaEl = document.getElementById('d-area');
    const area = deviceType === 'nvr' ? (areaEl.dataset.currentArea || areaEl.value || '未分组') : areaEl.value;
    if (deviceType !== 'nvr' && !area) { toast('error','请选择区域'); return; }
    const obj = {
        name, ip,
        device_type: deviceType,
        port: parseInt(document.getElementById('d-port').value) || 37777,
        area: area,
        username: document.getElementById('d-user').value || 'admin',
        password: document.getElementById('d-pass').value || '',
        note: document.getElementById('d-note').value || '',
    };
    const btn = document.getElementById('dev-save-btn');
    const originalLabel = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = editDevId ? '保存中…' : '添加中…'; }
    let res;
    try {
        if (editDevId) {
            res = await updateDeviceToServer(editDevId, obj);
            if (res.code===0) toast('success', `设备「${name}」已更新`);
            else toast('error', `更新失败：${res.msg}`);
        } else {
            res = await addDeviceToServer(obj);
            if (res.code===0) toast('success', `设备「${name}」已添加`);
            else toast('error', `添加失败：${res.msg}`);
        }
    } catch (e) {
        console.error('saveDevice error:', e);
        toast('error', '请求失败,请检查网络');
        return;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
    }
    if (res && res.code===0) {
        // 用响应数据局部刷新,避免再走一次全量并行 TCP 探测(每个设备 1.5s 超时)。
        // 后端返回的 payload 不含 password 且 online 可能为 None,保留本地旧值,
        // 然后后台异步触发一次完整刷新去更新在线状态。
        const payload = res.data || {};
        const idx = devices.findIndex(x => x.id === payload.id);
        if (idx >= 0) {
            const prev = devices[idx];
            devices[idx] = {
                ...prev,
                ...payload,
                password: obj.password || prev.password || '',
                online: payload.online != null ? payload.online : prev.online,
            };
        } else if (payload.id != null) {
            devices.push({ ...payload, password: obj.password || '', online: payload.online != null ? payload.online : false });
        }
        closeModal('devModal');
        renderAccessGrid();
        renderDevTable();
        fillPersonDevSel();
        if (typeof renderMonitorGrid === 'function') renderMonitorGrid();
        if (typeof renderHomeCards === 'function') renderHomeCards();
        // 后台静默刷新一次状态,把 online / 通道在线等带回最新值(用户不必等)
        setTimeout(() => { refreshDeviceStatusInBackground(); }, 0);
    }
}

async function addDeviceToServer(device) {
    const resp = await fetch(`${API}/api/devices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(device)
    });
    return resp.json();
}

async function updateDeviceToServer(id, device) {
    const resp = await fetch(`${API}/api/devices/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(device)
    });
    return resp.json();
}

async function deleteDeviceFromServer(id) {
    const resp = await fetch(`${API}/api/devices/${id}`, { method: 'DELETE' });
    return resp.json();
}

async function confirmDelDev(id) {
    const d = devices.find(x=>x.id===id);
    if (!d) return;
    setConfirm('删除设备', `确定删除设备 <span class="confirm-hi">「${escapeHtml(d.name)}」</span>（${escapeHtml(d.ip)}）？相关人员数据将一并清除。`, async () => {
        const res = await deleteDeviceFromServer(id);
        if (res.code===0) {
            toast('success', `已删除设备「${d.name}」`);
            localStorage.removeItem(`dh_p_${id}`);
            localStorage.removeItem(`dh_total_${id}`);
            await loadDevicesFromServer();
        } else {
            toast('error', `删除失败：${res.msg}`);
        }
    });
}
