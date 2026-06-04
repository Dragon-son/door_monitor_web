// ========== 人员管理模块（全部使用服务端人员 API）==========
let deviceUserPage = 1;
let deviceUserPageSize = 20;
let currentViewMode = 'local';
let persons = [];
let personsTotal = 0;        // 服务端分页总数
let personsLoading = false;  // 防止并发请求
let currentSearch = '';      // 当前搜索关键词（翻页时保持）
let personSortKey = '';
let personSortDir = 'asc';
let _selectedSyncDeviceIds = new Set();

function getPersonDeviceList() {
    return typeof getAccessDevices === 'function'
        ? getAccessDevices()
        : devices.filter(d => (d.device_type || 'access') === 'access');
}

// ---------- 从设备 RPC2 获取真实人脸状态并更新本地数据 ----------
async function enrichFaceFromDevice(deviceId, personList) {
    if (!deviceId || !personList || personList.length === 0) return;
    try {
        const uids = personList.map(p => p.user_id);
        const resp = await fetch(`${API}/api/device/${deviceId}/face-status`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({user_ids: uids})
        });
        const data = await resp.json();
        if (data.code === 0 && data.data.source === 'device') {
            const deviceFaces = data.data.faces;
            let updated = 0;
            for (const p of personList) {
                const uid = p.user_id;
                if (uid in deviceFaces) {
                    const deviceHas = deviceFaces[uid];
                    const didStr = String(deviceId);
                    // 确保 has_face 是 dict
                    if (!p.has_face || typeof p.has_face !== 'object') p.has_face = {};
                    p.has_face[didStr] = deviceHas;
                    updated++;
                }
            }
            if (updated > 0) {
                console.log(`[RPC2] 从设备更新了 ${updated} 人的人脸状态`);
            }
        }
    } catch (e) {
        console.warn('[RPC2] 获取设备人脸状态失败（可忽略）:', e);
    }
}

// ---------- 从设备 RPC2 拉取人脸照片并保存到本地 faces/ ----------
async function fetchDeviceFacePhotos(deviceId, userIds = null, force = false) {
    if (!deviceId) return null;
    try {
        const body = userIds ? {user_ids: userIds, force: force} : {force: force};
        const resp = await fetch(`${API}/api/device/${deviceId}/face-photos`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        let data = null;
        try {
            data = await resp.json();
        } catch (jsonErr) {
            data = {code: -1, msg: `HTTP ${resp.status}`};
        }
        if (!resp.ok || !data || data.code !== 0) {
            console.warn(`[RPC2] 拉取设备 ${deviceId} 人脸照片失败: HTTP ${resp.status}`, data);
            return data;
        }
        if (data.data) {
            console.log(`[RPC2] 从设备 ${deviceId} 拉取了 ${data.data.saved}/${data.data.total} 张人脸照片`);
        }
        return data;
    } catch (e) {
        console.warn(`[RPC2] 拉取设备 ${deviceId} 人脸照片失败（可忽略）:`, e);
        return null;
    }
}

// ---------- 从服务端加载当前设备人员（本地数据）----------
// ---------- 服务端分页加载 ----------
async function fetchPersonPage(page) {
    if (personsLoading) return;
    personsLoading = true;
    try {
        const params = new URLSearchParams({ page, per_page: deviceUserPageSize });
        if (currentPDevObj) params.set('device_id', currentPDevObj.id);
        if (currentSearch) params.set('search', currentSearch);
        const resp = await fetch(`${API}/api/persons?${params}`);
        const data = await resp.json();
        if (data.code === 0) {
            persons = data.data.items;
            personsTotal = data.data.total;
            deviceUserPage = page;
            renderPersonTable();
            updatePaginationUI();
        }
    } catch (e) {
        toast('error', '加载人员列表失败');
    } finally {
        personsLoading = false;
    }
}

async function refreshPersonList(resetPage = true) {
    if (!currentPDevObj) return;
    await fetchPersonPage(resetPage ? 1 : deviceUserPage);
}

async function loadAllPersons(resetPage = true) {
    await fetchPersonPage(resetPage ? 1 : deviceUserPage);
}

// ---------- 渲染当前页的本地人员表格（支持搜索过滤）----------
// 'yes' / 'no' / 'unknown' 三态:用于 UI 区分"明确有/明确无/字段缺失"。
// 与 hasFaceOnDevice(返回 boolean)解耦,后者保持原契约供其他调用方使用。
function faceStatusOnDevice(hasFace, did) {
    if (hasFace === null || hasFace === undefined) return 'unknown';
    if (typeof hasFace === 'object') {
        const key = String(did);
        if (key in hasFace) {
            return hasFaceOnDevice(hasFace, did) ? 'yes' : 'no';
        }
        if (did in hasFace) {
            return hasFaceOnDevice(hasFace, did) ? 'yes' : 'no';
        }
        return 'unknown';
    }
    // 旧的 flat 格式:整个 hasFace 是 bool/字符串,适用于所有设备
    return hasFaceOnDevice(hasFace, did) ? 'yes' : 'no';
}

function deviceEnd(p) {
    // 设备视角 → 该设备的 valid_end；全部设备视角 → 全局值（已由 _person_base_row 取 min）
    return currentPDevObj
        ? (p.valid_end_map?.[currentPDevObj.id] ?? p.valid_end)
        : p.valid_end;
}

function renderPersonTable(filter = '', customData = null) {
    const tbody = document.getElementById('personBody');
    let data = customData ? [...customData] : [...persons];
    if (filter) data = data.filter(p => (p.name + p.user_id).toLowerCase().includes(filter));
    data = sortPersonRows(data);
    updatePersonSortHeaders();

    let pageData;
    if (!customData) {
        // 服务端分页：persons 已包含当前页数据，无需 client 切片
        pageData = data;
    } else {
        pageData = data;
    }

    if (!pageData.length) {
        tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state"><p>暂无人员</p></div></td></tr>`;
        return;
    }

    tbody.innerHTML = pageData.map(p => {
        const hf = p.has_face;
        // 当前设备视角下:'yes'/'no'/'unknown'
        const curFace = currentPDevObj ? faceStatusOnDevice(hf, currentPDevObj.id) : null;
        const getDidStatus = (did) => {
            const s = p.status;
            if (s && typeof s === 'object') return s[String(did)] ?? 0;
            return typeof s === 'number' ? s : 0;
        };
        let displayStatus;
        if (currentPDevObj) {
            displayStatus = getDidStatus(currentPDevObj.id);
        } else {
            const myIds = new Set(getPersonDeviceList().map(d => String(d.id)));
            displayStatus = p.status && typeof p.status === 'object'
                ? ([...myIds].some(id => (p.status[id] ?? 0) !== 0) ? 1 : 0)
                : (typeof p.status === 'number' ? p.status : 0);
        }
        const myFaceDeviceIds = getPersonDeviceList().map(d => String(d.id));
        // 在所有管辖设备中有任意一个明确为 yes,就视为整体"已有人脸"。
        const anyDeviceHasFace = myFaceDeviceIds.some(id => faceStatusOnDevice(hf, id) === 'yes');
        const allDevicesKnownNoFace = myFaceDeviceIds.length > 0
            && myFaceDeviceIds.every(id => faceStatusOnDevice(hf, id) === 'no');

        let showFaceBtn;
        if (currentPDevObj) {
            showFaceBtn = curFace !== 'yes';
        } else {
            showFaceBtn = myFaceDeviceIds.length > 0 && !anyDeviceHasFace;
        }
        let faceHtml = '';
        const uid = p.user_id;
        const encodedUid = encodeURIComponent(uid);
        const safeNameForJs = escapeJsString(p.name || '');
        const hasPhoto = currentPDevObj ? curFace === 'yes' : anyDeviceHasFace;
        const knownNoPhoto = currentPDevObj ? curFace === 'no' : allDevicesKnownNoFace;
        if (hasPhoto) {
            faceHtml = `<img class="face-thumb" src="/api/face/${encodedUid}" onclick="openFacePreview('${encodedUid}', '${safeNameForJs}')" onerror="replaceFaceThumbWithPlaceholder(this)">`;
        } else if (knownNoPhoto) {
            faceHtml = facePlaceholderHtml('无');
        } else {
            faceHtml = facePlaceholderHtml('?');
        }

        return `
        <tr>
            <td><span class="mono">${escapeHtml(p.user_id)}</span></td>
            <td><strong style="color:var(--text)">${escapeHtml(p.name)}</strong></td>
            <td><span class="mono" style="font-size:11.5px">${escapeHtml(safeDate(deviceEnd(p), '—'))}</span></td>
            <td><span class="badge ${displayStatus === 0 ? 'bg' : 'br'}">${displayStatus === 0 ? '正常' : '冻结'}</span></td>
            <td class="person-face-col">${faceHtml}</td>
            <td><div class="dev-actions">
                <button class="btn btn-ghost btn-sm" onclick="openPersonModal('${escapeHtml(p.user_id)}')">编辑</button>
                ${displayStatus === 0
                    ? `<button class="btn btn-freeze btn-sm" onclick="togglePersonFreeze('${escapeHtml(p.user_id)}', true)">冻结</button>`
                    : `<button class="btn btn-blue btn-sm" onclick="togglePersonFreeze('${escapeHtml(p.user_id)}', false)">解冻</button>`}
                ${showFaceBtn ? `<button class="btn btn-blue btn-sm" onclick="openFaceModal('${escapeHtml(p.user_id)}')">录入人脸</button>` : ''}
                <button class="btn btn-danger btn-sm" onclick="confirmDelPerson('${escapeHtml(p.user_id)}')">删除</button>
            </div></td>
        </tr>`;
    }).join('');
}

function facePlaceholderHtml(text = '无') {
    return `<span class="face-placeholder">${escapeHtml(text)}</span>`;
}

function replaceFaceThumbWithPlaceholder(img, text = '无') {
    if (!img) return;
    img.onerror = null;
    img.outerHTML = facePlaceholderHtml(text);
}

function escapeJsString(str) {
    return String(str ?? '')
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/\r/g, '\\r')
        .replace(/\n/g, '\\n')
        .replace(/</g, '\\x3C')
        .replace(/>/g, '\\x3E')
        .replace(/&/g, '\\x26');
}

function sortPersonRows(data) {
    if (!personSortKey) return data;
    const dir = personSortDir === 'desc' ? -1 : 1;
    return [...data].sort((a, b) => {
        const av = getPersonSortValue(a, personSortKey);
        const bv = getPersonSortValue(b, personSortKey);
        if (av === bv) {
            return String(a.user_id || '').localeCompare(String(b.user_id || ''), 'zh-Hans-CN', { numeric: true }) * dir;
        }
        if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
        return String(av ?? '').localeCompare(String(bv ?? ''), 'zh-Hans-CN', { numeric: true }) * dir;
    });
}

function getPersonSortValue(person, key) {
    if (key === 'user_id') return String(person.user_id || '');
    if (key === 'valid_end') {
        const value = safeDate(person.valid_end, '');
        const time = value ? Date.parse(value) : Number.POSITIVE_INFINITY;
        return Number.isNaN(time) ? Number.POSITIVE_INFINITY : time;
    }
    return '';
}

function setPersonSort(key) {
    if (personSortKey === key) {
        personSortDir = personSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        personSortKey = key;
        personSortDir = 'asc';
    }
    deviceUserPage = 1;
    renderPersonTable();
    updatePaginationUI();
}

function updatePersonSortHeaders() {
    document.querySelectorAll('[data-person-sort]').forEach(btn => {
        const key = btn.getAttribute('data-person-sort');
        const icon = btn.querySelector('.sort-icon');
        if (!icon) return;
        icon.textContent = personSortKey === key ? (personSortDir === 'asc' ? '▲' : '▼') : '↕';
        btn.classList.toggle('active', personSortKey === key);
    });
}

// ---------- 设备选择框填充 ----------
function fillPersonDevSel() {
    const sel = document.getElementById('personDevSel');
    const prev = sel.value;
    sel.innerHTML = '<option value="">-- 全部设备 --</option>';
    getPersonDeviceList().forEach(d => {
        const o = document.createElement('option');
        o.value = d.id;
        o.textContent = `${d.name}  (${d.ip})`;
        sel.appendChild(o);
    });
    if (prev && getPersonDeviceList().some(d => d.id == prev)) sel.value = prev;
    else sel.value = '';
}

// ---------- 切换设备时自动加载该设备的人员 ----------
function onPersonDeviceChange() {
    const devId = parseInt(document.getElementById('personDevSel').value);
    currentPDevObj = getPersonDeviceList().find(d => d.id === devId) || null;
    const badge = document.getElementById('personDevBadge');
    document.getElementById('personContent').style.display = 'block';
    document.getElementById('personPH').style.display = 'none';
    currentViewMode = 'local';
    document.getElementById('searchUserId').value = '';
    document.getElementById('searchUserName').value = '';
    currentSearch = '';

    if (!currentPDevObj) {
        badge.innerHTML = '<span class="badge bg">全部设备</span>';
        loadAllPersons();
        return;
    }

    badge.innerHTML = `<span class="badge bb">已选择</span>`;
    refreshPersonList().then(() => {
        const cachedTotal = localStorage.getItem(`dh_total_${currentPDevObj.id}`);
        deviceUserTotal = cachedTotal ? parseInt(cachedTotal) : persons.length;
        deviceUserPage = 1;
        updatePaginationUI();
    });
}

// ---------- 分页加载设备人员（自动导入到服务端）----------
async function loadDeviceUsersPage(page, event) {
    if (!currentPDevObj) { toast('error', '请先选择设备'); return; }

    const btn = event?.target;
    const originalText = btn ? btn.innerHTML : '📥 加载设备人员';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> 加载中';
    }

    try {
        const data = await apiRequest('POST', '/api/device/users/all', currentPDevObj, {
            page: page,
            page_size: deviceUserPageSize
        });

        if (data.code === 0 && data.data) {
            const total = data.data.total;
            const users = data.data.items;

            localStorage.setItem(`dh_total_${currentPDevObj.id}`, total);
            deviceUserTotal = total;
            deviceUserPage = page;

            const personsToImport = users.map(u => ({
                user_id: u.user_id,
                name: u.name,
                valid_begin: safeDate(u.valid_begin, '2000-01-01'),
                valid_end: safeDate(u.valid_end, '2037-12-31'),
                status: u.status || 0
            }));

            await fetch(`${API}/api/persons/import`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: currentPDevObj.id,
                    persons: personsToImport,
                    // 分页加载只拿到当前页，不是设备全量；避免清理当前设备其他页人员。
                    no_purge: true,
                    // 不预设人脸状态，RPC2 查询会异步更新真实状态
                    default_has_face: false
                })
            });

            await refreshPersonList(false);

            const userIds = users.map(u => String(u.user_id)).filter(Boolean);
            const pageData = persons.filter(p => userIds.includes(String(p.user_id)));

            // 同步后从设备查真实人脸状态 + 拉取照片；照片保存后再渲染，避免仍显示旧占位。
            await enrichFaceFromDevice(currentPDevObj.id, pageData);
            await fetchDeviceFacePhotos(currentPDevObj.id, userIds);
            renderPersonTable('', pageData);
            updatePaginationUI();

            toast('success', `第 ${page} 页，共 ${total} 人`);
        } else {
            toast('error', data.msg || '加载失败');
        }
    } catch (e) {
        console.error(e);
        toast('error', '网络错误');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

// ---------- 分页控件更新（基于 persons 总长度）----------
function updatePaginationUI() {
    const total = personsTotal;
    const totalPages = Math.ceil(total / deviceUserPageSize) || 1;
    document.getElementById('personTotal').textContent = total;
    document.getElementById('personCurrentPage').textContent = deviceUserPage;
    document.getElementById('personTotalPages').textContent = totalPages;
    document.getElementById('personPrevBtn').disabled = (deviceUserPage <= 1);
    document.getElementById('personNextBtn').disabled = (deviceUserPage >= totalPages);
}

function prevPersonPage() {
    if (deviceUserPage > 1) fetchPersonPage(deviceUserPage - 1);
}
function nextPersonPage() {
    const totalPages = Math.ceil(personsTotal / deviceUserPageSize);
    if (deviceUserPage < totalPages) fetchPersonPage(deviceUserPage + 1);
}
function jumpPersonPage() {
    const input = document.getElementById('personJumpPage');
    const page = parseInt(input.value);
    const totalPages = Math.ceil(personsTotal / deviceUserPageSize);
    if (!isNaN(page) && page >= 1 && page <= totalPages) fetchPersonPage(page);
    input.value = '';
}

function openSyncDeviceModal() {
    if (!getPersonDeviceList().length) {
        toast('error', '暂无可同步设备');
        return;
    }
    // 重置选中集合并清空搜索框
    _selectedSyncDeviceIds.clear();
    const searchInput = document.getElementById('syncDeviceSearchInput');
    if (searchInput) searchInput.value = '';
    const selectAllCb = document.getElementById('syncDeviceSelectAll');
    if (selectAllCb) {
        selectAllCb.checked = false;
        selectAllCb.indeterminate = false;
    }

    // 保存所有设备的原始数据（用于过滤）
    window._allSyncDevices = getPersonDeviceList().map(d => ({
        id: d.id,
        name: d.name,
        ip: d.ip,
        port: d.port,
        online: d.online === true
    }));

    renderSyncDeviceList(window._allSyncDevices);
    updateSyncDeviceSelectionState();

    // 绑定搜索事件（仅一次）
    if (searchInput && !searchInput.dataset.boundSyncFilter) {
        searchInput.dataset.boundSyncFilter = 'true';
        searchInput.addEventListener('input', filterSyncDeviceList);
    }
    openModal('syncDeviceModal');
}

// 全选/全不选
function setSyncDeviceAll(checked) {
    const listEl = document.getElementById('sync-device-list');
    if (!listEl) return;
    const visibleRows = Array.from(listEl.querySelectorAll('.sync-device-row')).filter(row => row.style.display !== 'none');
    const visibleCheckboxes = visibleRows.map(row => row.querySelector('.sync-device-check'));
    visibleCheckboxes.forEach(cb => {
        const devId = parseInt(cb.value);
        if (checked) {
            _selectedSyncDeviceIds.add(devId);
            cb.checked = true;
        } else {
            _selectedSyncDeviceIds.delete(devId);
            cb.checked = false;
        }
    });
    updateSyncDeviceSelectionState();
}

// 渲染同步设备列表
function renderSyncDeviceList(devicesToRender) {
    const listEl = document.getElementById('sync-device-list');
    if (!listEl) return;
    if (!devicesToRender.length) {
        listEl.innerHTML = '<div style="padding: 24px; text-align: center; color: var(--text3);">未找到匹配的设备</div>';
        return;
    }

    listEl.innerHTML = '';
    devicesToRender.forEach(dev => {
        const row = document.createElement('div');
        row.className = 'sync-device-row';
        // 添加行点击事件
        row.addEventListener('click', (e) => {
            // 防止点击复选框时重复触发（因为复选框自身也有 change 事件，且会冒泡）
            if (e.target.type === 'checkbox') {
                return;
            }
            const cb = row.querySelector('.sync-device-check');
            if (cb) {
                cb.checked = !cb.checked;
                // 手动触发 change 事件，以便更新选中状态集合
                const changeEvent = new Event('change', { bubbles: true });
                cb.dispatchEvent(changeEvent);
            }
        });

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'sync-device-check';
        cb.value = dev.id;
        cb.checked = _selectedSyncDeviceIds.has(dev.id);
        cb.addEventListener('change', (e) => {
            e.stopPropagation(); // 避免再次触发行的 click
            if (cb.checked) {
                _selectedSyncDeviceIds.add(dev.id);
            } else {
                _selectedSyncDeviceIds.delete(dev.id);
            }
            updateSyncDeviceSelectionState();
        });

        const mainDiv = document.createElement('div');
        mainDiv.className = 'sync-device-main';
        const nameSpan = document.createElement('div');
        nameSpan.className = 'sync-device-name';
        nameSpan.textContent = dev.name || `设备${dev.id}`;
        const metaSpan = document.createElement('div');
        metaSpan.className = 'sync-device-meta';
        metaSpan.textContent = `${dev.ip}:${dev.port}`;
        mainDiv.appendChild(nameSpan);
        mainDiv.appendChild(metaSpan);

        const statusSpan = document.createElement('span');
        statusSpan.className = `badge ${dev.online ? 'bg' : 'br'} sync-device-status`;
        statusSpan.textContent = dev.online ? '在线' : '离线';

        row.appendChild(cb);
        row.appendChild(mainDiv);
        row.appendChild(statusSpan);
        listEl.appendChild(row);
    });
    updateSyncDeviceSelectionState();
}

// 过滤设备列表
function filterSyncDeviceList() {
    const keyword = document.getElementById('syncDeviceSearchInput').value.trim().toLowerCase();
    if (!window._allSyncDevices) return;
    const filtered = keyword
        ? window._allSyncDevices.filter(dev =>
            dev.name.toLowerCase().includes(keyword) ||
            dev.ip.toLowerCase().includes(keyword)
          )
        : window._allSyncDevices.slice();
    renderSyncDeviceList(filtered);
}

// 更新同步设备选择状态（全选、计数）
function updateSyncDeviceSelectionState() {
    const listEl = document.getElementById('sync-device-list');
    if (!listEl) return;
    const allCheckboxes = Array.from(listEl.querySelectorAll('.sync-device-check'));
    const visibleCheckboxes = allCheckboxes.filter(cb => cb.closest('.sync-device-row')?.style.display !== 'none');
    const visibleChecked = visibleCheckboxes.filter(cb => cb.checked).length;
    const totalVisible = visibleCheckboxes.length;

    const allCb = document.getElementById('syncDeviceSelectAll');
    const hintSpan = document.getElementById('syncDeviceSelectHint');
    if (allCb) {
        allCb.checked = totalVisible > 0 && visibleChecked === totalVisible;
        allCb.indeterminate = visibleChecked > 0 && visibleChecked < totalVisible;
        allCb.disabled = totalVisible === 0;
    }
    if (hintSpan) {
        hintSpan.textContent = totalVisible ? `已选 ${visibleChecked} / ${totalVisible}` : '当前无设备';
    }
    const syncBtn = document.getElementById('syncDeviceBtn');
    if (syncBtn) syncBtn.disabled = visibleChecked === 0;
}

async function submitDeviceSyncSelection() {
    const selectedIds = Array.from(_selectedSyncDeviceIds);
    if (selectedIds.length === 0) {
        toast('error', '请至少选择一台设备');
        return;
    }
    const targetDevices = getPersonDeviceList().filter(d => selectedIds.includes(Number(d.id)));
    if (!targetDevices.length) {
        toast('error', '未找到选中的设备');
        return;
    }
    const btn = document.getElementById('syncDeviceBtn');
    const hint = document.getElementById('syncDeviceSelectHint');
    await runDeviceUsersSync(targetDevices, btn, hint);
    closeModal('syncDeviceModal');
    _selectedSyncDeviceIds.clear(); // 清理选中状态
}

async function syncDeviceUsersToLocal(dev) {
    const data = await apiRequest('POST', '/api/device/users/all', dev, {
        page: 1,
        page_size: 0
    });
    if (data.code !== 0 || !data.data) {
        throw new Error(data.msg || '加载失败');
    }

    const users = Array.isArray(data.data.items) ? data.data.items : [];
    const personsToImport = users.map(u => ({
        user_id: u.user_id,
        name: u.name,
        valid_begin: safeDate(u.valid_begin, '2000-01-01'),
        valid_end: safeDate(u.valid_end, '2037-12-31'),
        status: u.status || 0
    }));
    // 同步严格镜像设备数据:
    //   no_purge=false → 设备没返回的人员关系在本地一并删除;若一个人员所有设备引用都没了,主行也删除。
    //   detect_orphans=true → 告知前端这一轮被 purge 的 uid 数量,用于汇总提示。
    //   detect_conflicts=true → 检测同 uid 在不同设备间 name/valid 不一致并返回告警。
    const importResp = await fetch(`${API}/api/persons/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            device_id: dev.id,
            persons: personsToImport,
            no_purge: false,
            default_has_face: false,
            detect_orphans: true,
            detect_conflicts: true
        })
    });
    const importData = await importResp.json();
    if (importData.code !== 0) {
        throw new Error(importData.msg || '本地数据写入失败');
    }
    return {
        count: users.length,
        userIds: users.map(u => String(u.user_id)).filter(Boolean),
        purged: Array.isArray(importData.data?.orphans) ? importData.data.orphans : [],
        conflicts: Array.isArray(importData.data?.conflicts) ? importData.data.conflicts : []
    };
}

async function runDeviceUsersSync(targetDevices, btn = null, progressEl = null) {
    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> 同步中';
    }

    let successDevices = 0;
    let failDevices = 0;
    let totalUsers = 0;
    const syncedUserIdsByDevice = new Map();
    const purgedByDevice = new Map();
    const allConflicts = [];
    const errors = [];
    try {
        for (let i = 0; i < targetDevices.length; i++) {
            const dev = targetDevices[i];
            if (btn) btn.innerHTML = `<span class="spinner"></span> ${i + 1}/${targetDevices.length}`;
            if (progressEl) progressEl.textContent = `正在同步 ${dev.name || `设备${dev.id}`}（${i + 1}/${targetDevices.length}）`;
            try {
                const syncResult = await syncDeviceUsersToLocal(dev);
                const count = typeof syncResult === 'number' ? syncResult : (syncResult?.count || 0);
                const userIds = Array.isArray(syncResult?.userIds) ? syncResult.userIds : [];
                const purged = Array.isArray(syncResult?.purged) ? syncResult.purged : [];
                const conflicts = Array.isArray(syncResult?.conflicts) ? syncResult.conflicts : [];
                if (conflicts.length) allConflicts.push({ dev: dev.name || `设备${dev.id}`, conflicts });
                successDevices++;
                totalUsers += count;
                syncedUserIdsByDevice.set(Number(dev.id), userIds);
                if (purged.length) purgedByDevice.set(Number(dev.id), { dev, purged });
            } catch (e) {
                failDevices++;
                errors.push(`${dev.name || `设备${dev.id}`}：${e?.message || '同步失败'}`);
            }
        }

        if (successDevices > 0) {
            const syncedDeviceIds = new Set(Array.from(syncedUserIdsByDevice.keys()).map(id => Number(id)));
            const faceDeviceJobs = targetDevices
                .filter(dev => syncedDeviceIds.has(Number(dev.id)))
                .map(dev => ({ dev, userIds: syncedUserIdsByDevice.get(Number(dev.id)) || [] }));

            if (currentPDevObj) {
                await refreshPersonList(false);
            } else {
                await loadAllPersons(false);
            }

            // 同步后从每台成功设备查真实人脸状态 + 拉取照片。
            // 关键：照片拉取必须传同步得到的 userIds；不传参会走“拉全部有脸用户”，设备可能只返回状态/不稳定返回照片。
            for (const { dev, userIds } of faceDeviceJobs) {
                const targetPersons = userIds.length
                    ? persons.filter(p => userIds.includes(String(p.user_id)))
                    : persons;
                await enrichFaceFromDevice(dev.id, targetPersons);
                await fetchDeviceFacePhotos(dev.id, userIds.length ? userIds : null);
            }
            renderPersonTable();
            const totalPurged = Array.from(purgedByDevice.values())
                .reduce((sum, item) => sum + (item.purged?.length || 0), 0);
            let msg = `已同步 ${successDevices}/${targetDevices.length} 台设备，共 ${totalUsers} 名人员`;
            if (totalPurged > 0) {
                msg += `；清理了 ${totalPurged} 条本地多余记录(设备已不存在)`;
            }
            toast('success', msg);
// 检测到同 uid 跨设备信息不一致 → 弹出警告
            if (allConflicts.length > 0) {
                const totalConflictUids = new Set();
                let conflictHtml = '<div style="max-height:360px;overflow:auto;text-align:left;line-height:1.7">';
                for (const { dev, conflicts } of allConflicts) {
                    const cByUid = {};
                    for (const c of conflicts) {
                        const uid = c.user_id;
                        totalConflictUids.add(uid);
                        if (!cByUid[uid]) cByUid[uid] = [];
                        cByUid[uid].push(`${escapeHtml(c.field)}: ${escapeHtml(c.incoming)} ≠ ${escapeHtml(c.existing)}`);
                    }
                    conflictHtml += `<div style="margin-bottom:10px"><strong>${escapeHtml(dev)}</strong>`;
                    Object.entries(cByUid).forEach(([uid, fields]) => {
                        conflictHtml += `<div style="margin-left:12px"><span class="mono">${escapeHtml(uid)}</span>：${fields.join('；')}</div>`;
                    });
                    conflictHtml += '</div>';
                }
                conflictHtml += '</div><p style="margin-top:8px;color:var(--text3)">以上冲突已按本次同步结果覆盖本地数据。</p>';
                toast('warn', `检测到 ${totalConflictUids.size} 名人员信息冲突，详情已弹出`);
                if (typeof showResultModal === 'function') {
                    showResultModal('同步冲突提示', conflictHtml);
                }
            }
        }
        if (failDevices > 0) {
            const detail = errors.slice(0, 2).join('；');
            toast('error', `${failDevices} 台设备同步失败${detail ? `：${detail}` : ''}`);
        }
        return { successDevices, failDevices, totalUsers };
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
        if (progressEl) updateSyncDeviceSelectionState();
    }
}

// ---------- 一次性加载设备全部人员并更新到本地文件 ----------
async function loadAllDeviceUsers(event) {
    if (!currentPDevObj) {
        openSyncDeviceModal();
        return;
    }
    const btn = event?.currentTarget || event?.target || null;
    await runDeviceUsersSync([currentPDevObj], btn);
}

// ---------- 搜索功能 ----------
async function searchById(event) {
    const userId = document.getElementById('searchUserId').value.trim();
    if (!userId) { toast('error', '请输入用户ID'); return; }

    if (!currentPDevObj) {
        // 服务端分页后 persons 只有当前页，改为 API 搜索全部数据
        currentSearch = userId;  // 记录搜索词，翻页时保持
        const resp = await fetch(`${API}/api/persons?page=1&per_page=50&search=${encodeURIComponent(userId)}`);
        const data = await resp.json();
        if (data.code === 0 && data.data.items.length > 0) {
            persons = data.data.items;  // 更新全局 persons 数组
            personsTotal = data.data.total;
            deviceUserPage = 1;
            renderPersonTable('', persons);
            updatePaginationUI();
            toast('success', `本地找到 ${personsTotal} 个用户`);
        } else {
            persons = [];
            personsTotal = 0;
            deviceUserPage = 1;
            renderPersonTable('', []);
            updatePaginationUI();
            toast('error', '本地未找到该用户');
        }
        return;
    }

    const btn = event?.target;
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 搜索中';
    currentSearch = userId;  // 记录搜索词，翻页时保持

    try {
        const data = await apiRequest('POST', `/api/device/user/id/${encodeURIComponent(userId)}`, currentPDevObj);

        if (data.code === 0 && data.data) {
            const u = data.data;
            await fetch(`${API}/api/persons/import`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: currentPDevObj.id,
                    persons: [{
                        user_id: u.user_id,
                        name: u.name,
                        valid_begin: safeDate(u.valid_begin, '2000-01-01'),
                        valid_end: safeDate(u.valid_end, '2037-12-31'),
                        status: u.status || 0
                    }],
                    // 单人搜索只合并命中的人员，不能按"全量同步"清理当前设备其他人员。
                    no_purge: true,
                    // 查询接口拿不到设备人脸状态，不能把本地人脸状态改成已录入。
                    default_has_face: false
                })
            });

            // 搜索后从设备查此人脸状态 + 拉取照片（API 搜索避免分页遗漏）
            // 注意：不调用 refreshPersonList，避免竞态条件覆盖搜索结果
            const searchResp = await fetch(`${API}/api/persons?page=1&per_page=1&search=${encodeURIComponent(userId)}&search_mode=exact&device_id=${encodeURIComponent(currentPDevObj.id)}`);
            const searchData = await searchResp.json();
            const localPerson = (searchData.code === 0 && searchData.data.items.length > 0) ? searchData.data.items[0] : null;
            if (localPerson) {
                await enrichFaceFromDevice(currentPDevObj.id, [localPerson]);
                await fetchDeviceFacePhotos(currentPDevObj.id, [userId], true);  // force=true 强制覆盖本地照片
                persons = [localPerson];  // 更新全局 persons 数组
                personsTotal = 1;
                renderPersonTable('', persons);
                toast('success', `当前设备存在用户：${localPerson.name} (${localPerson.user_id})`);
            } else {
                toast('error', '未找到该用户（可能数据同步异常）');
            }
        } else {
            // 设备未找到 → API 搜本地缓存（避免分页遗漏）
            const cachedResp = await fetch(`${API}/api/persons?page=1&per_page=1&search=${encodeURIComponent(userId)}&search_mode=exact&device_id=${encodeURIComponent(currentPDevObj.id)}`);
            const cachedData = await cachedResp.json();
            const cachedPerson = (cachedData.code === 0 && cachedData.data.items.length > 0) ? cachedData.data.items[0] : null;
            // 设备明确返回 "用户不存在" 时携带 reason=not_found;不再依赖中文文案匹配,
            // 避免文案改动 / 国际化时清理本地关系的兜底逻辑失效。
            const isNotFound = data.reason === 'not_found';
            if (isNotFound && cachedPerson && cachedPerson.doors && cachedPerson.doors.includes(currentPDevObj.id)) {
                await fetch(`${API}/api/persons/${encodeURIComponent(userId)}/devices/${currentPDevObj.id}`, { method: 'DELETE' });
                await refreshPersonList(false);
                toast('info', '设备确认用户不存在，已移除本地关联');
            } else {
                toast('error', data.msg || '当前设备未找到该用户');
            }
        }
    } catch (e) {
        console.error(e);
        toast('error', '搜索失败，请检查网络');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

async function searchByName(event) {
    const keyword = document.getElementById('searchUserName').value.trim();
    if (!keyword) { toast('error', '请输入姓名关键词'); return; }

    // 服务端分页后改为 API 搜索，不再用客户端 persons.filter()
    currentSearch = keyword;  // 记录搜索词，翻页时保持
    try {
        const params = new URLSearchParams({ page: 1, per_page: deviceUserPageSize, search: keyword });
        if (currentPDevObj) params.set('device_id', currentPDevObj.id);
        const resp = await fetch(`${API}/api/persons?${params}`);
        const data = await resp.json();
        if (data.code === 0 && data.data.items.length > 0) {
            persons = data.data.items;
            personsTotal = data.data.total;
            deviceUserPage = 1;
            renderPersonTable();
            updatePaginationUI();
            toast('success', `找到 ${data.data.total} 条匹配记录`);
        } else {
            persons = [];
            personsTotal = 0;
            deviceUserPage = 1;
            renderPersonTable('', []);
            updatePaginationUI();
            toast('error', '未找到匹配人员');
        }
    } catch (e) {
        console.error(e);
        toast('error', '搜索失败');
    }
}

function showAllLocalPersons() {
    document.getElementById('searchUserId').value = '';
    document.getElementById('searchUserName').value = '';
    currentSearch = '';
    if (!currentPDevObj) {
        loadAllPersons();
        return;
    }
    refreshPersonList();
}

// ---------- 批量添加人员----------
async function handleBatchFolder(files) {
    if (!files.length) return;
    const formData = new FormData();
    for (let f of files) formData.append('files', f);

    const btn = document.querySelector('.btn-blue[onclick*="batchFolderInput"]');
    const origText = btn ? btn.innerHTML : '📂 批量导入';
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 导入中...'; }

    try {
        const resp = await fetch(`${API}/api/batch_import`, { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.code === 0) {
            const r = data.data;
            let resultHtml = `<div style="margin-bottom:16px;color:var(--text2);">
                <p>📊 处理：${r.total} 条 | ✅ 成功：${r.success} 人 | ❌ 失败：${r.fail} 条</p>
                <p>📷 人脸下发：成功 ${r.face_success || 0} / 失败 ${r.face_fail || 0}</p>
            </div>`;
            if (r.details && r.details.length > 0) {
                resultHtml += `<div style="max-height:300px;overflow-y:auto;margin-bottom:12px;">
                    <table style="width:100%;font-size:12px;color:var(--text2);">
                        <thead><tr><th>姓名</th><th>用户编号</th><th>目标门</th><th>添加结果</th><th>人脸下发</th></tr></thead><tbody>`;
                r.details.forEach(d => {
                    const statusColor = d.status === '成功' ? 'var(--accent2)' : (d.status === '失败' ? 'var(--red2)' : 'var(--yellow2)');
                    const faceColor = d.face_result.includes('成功') && !d.face_result.includes('部分') ? 'var(--accent2)' : (d.face_result === '失败' ? 'var(--red2)' : 'var(--text2)');
                    resultHtml += `<tr style="border-bottom:1px solid var(--border);">
                        <td>${escapeHtml(d.name)}</td><td>${escapeHtml(d.user_id)}</td><td>${escapeHtml(d.doors)}</td>
                        <td style="color:${statusColor}">${d.status}</td><td style="color:${faceColor}">${d.face_result}</td></tr>`;
                });
                resultHtml += '</tbody></table></div>';
            }
            showResultModal('批量导入结果', resultHtml, async () => {
                if (currentPDevObj) { await refreshPersonList(); } else { await loadAllPersons(); }
            });
        } else {
            showResultModal('批量导入结果', `<p style="color:var(--red2);">导入失败：${escapeHtml(data.msg || '未知错误')}</p>`, null);
        }
    } catch (e) {
        showResultModal('批量导入结果', '<p style="color:var(--red2);">网络请求失败，请检查服务器</p>', null);
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = origText; }
        document.getElementById('batchFolderInput').value = '';
    }
}

function downloadTemplate() {
    window.open(`${API}/download/template`, '_blank');
}

// ---------- 添加人员（支持多选设备）----------
function fillPersonDeviceSelect() {
    const container = document.getElementById('device-checkbox-list');
    if (!container) return;
    container.innerHTML = '';

    // 获取搜索关键词
    const searchInput = document.getElementById('device-search-input');
    const keyword = searchInput ? searchInput.value.trim().toLowerCase() : '';

    // 过滤设备列表
    const filteredDevices = keyword
        ? getPersonDeviceList().filter(d =>
            String(d.name || '').toLowerCase().includes(keyword) ||
            String(d.ip || '').toLowerCase().includes(keyword)
          )
        : getPersonDeviceList();

    if (filteredDevices.length === 0) {
        container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text3);">未找到匹配的设备</div>';
        updatePersonDeviceSelectionState();
        return;
    }

    filteredDevices.forEach(d => {
        const label = document.createElement('label');
        label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;border-bottom:1px solid var(--border);';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = d.id;
        cb.style.accentColor = 'var(--accent)';

        const span = document.createElement('span');
        span.textContent = `${d.name} (${d.ip})`; // textContent 自动转义，安全
        span.style.fontSize = '13px';
        span.style.color = 'var(--text)';

        label.appendChild(cb);
        label.appendChild(span);
        container.appendChild(label);
    });
    restorePersonDeviceChecks();
    updatePersonDeviceSelectionState();
}

function restorePersonDeviceChecks() {
    const container = document.getElementById('device-checkbox-list');
    if (!container) return;
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        const savedState = window._deviceCheckboxStates?.[cb.value];
        if (savedState !== undefined) cb.checked = savedState;
    });
}

function setPersonDeviceAll(checked) {
    const container = document.getElementById('device-checkbox-list');
    if (!container) return;
    if (!window._deviceCheckboxStates) window._deviceCheckboxStates = {};
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = checked;
        window._deviceCheckboxStates[cb.value] = checked;
    });
    updatePersonDeviceSelectionState();
}

function updatePersonDeviceSelectionState() {
    const container = document.getElementById('device-checkbox-list');
    const visibleChecks = container ? Array.from(container.querySelectorAll('input[type="checkbox"]')) : [];
    const visibleChecked = visibleChecks.filter(cb => cb.checked).length;
    const all = document.getElementById('personDeviceSelectAll');
    const hint = document.getElementById('personDeviceSelectHint');
    if (all) {
        all.checked = visibleChecks.length > 0 && visibleChecked === visibleChecks.length;
        all.indeterminate = visibleChecked > 0 && visibleChecked < visibleChecks.length;
        all.disabled = visibleChecks.length === 0;
    }
    if (hint) {
        const totalChecked = Object.values(window._deviceCheckboxStates || {}).filter(Boolean).length;
        hint.textContent = visibleChecks.length
            ? `当前结果 ${visibleChecked}/${visibleChecks.length}，已选 ${totalChecked} 台`
            : '当前结果 0 台';
    }
}

function initDeviceSearchListener() {
    const searchInput = document.getElementById('device-search-input');
    const container = document.getElementById('device-checkbox-list');
    if (searchInput && !searchInput.dataset.boundPersonDeviceSearch) {
        searchInput.dataset.boundPersonDeviceSearch = '1';
        searchInput.addEventListener('input', fillPersonDeviceSelect);
    }
    if (container && !container.dataset.boundPersonDeviceChecks) {
        container.dataset.boundPersonDeviceChecks = '1';
        container.addEventListener('change', function(e) {
            if (e.target.type === 'checkbox') {
                if (!window._deviceCheckboxStates) window._deviceCheckboxStates = {};
                window._deviceCheckboxStates[e.target.value] = e.target.checked;
                updatePersonDeviceSelectionState();
            }
        });
    }
}

function setFreezeDeviceAll(checked) {
    const list = document.getElementById('freeze-device-list');
    if (!list) return;
    Array.from(list.querySelectorAll('label'))
        .filter(label => label.style.display !== 'none')
        .forEach(label => {
            const cb = label.querySelector('input[name="freezeDevs"]');
            if (cb) cb.checked = checked;
        });
    updateFreezeDeviceSelectionState();
}

function updateFreezeDeviceSelectionState() {
    const list = document.getElementById('freeze-device-list');
    const all = document.getElementById('freezeDeviceSelectAll');
    const hint = document.getElementById('freezeDeviceSelectHint');
    if (!list) return;
    const visibleChecks = Array.from(list.querySelectorAll('label'))
        .filter(label => label.style.display !== 'none')
        .map(label => label.querySelector('input[name="freezeDevs"]'))
        .filter(Boolean);
    const visibleChecked = visibleChecks.filter(cb => cb.checked).length;
    if (all) {
        all.checked = visibleChecks.length > 0 && visibleChecked === visibleChecks.length;
        all.indeterminate = visibleChecked > 0 && visibleChecked < visibleChecks.length;
        all.disabled = visibleChecks.length === 0;
    }
    if (hint) {
        const totalChecked = document.querySelectorAll('input[name="freezeDevs"]:checked').length;
        hint.textContent = visibleChecks.length
            ? `当前结果 ${visibleChecked}/${visibleChecks.length}，已选 ${totalChecked} 台`
            : '当前结果 0 台';
    }
}

let _pendingFreezeDeviceAction = null;

function renderFreezeDeviceList(targetDevs, deviceStatus, shouldFreeze, defaultCheckedIds = null) {
    const list = document.getElementById('freeze-device-list');
    if (!list) return;
    const defaultChecked = defaultCheckedIds ? new Set(defaultCheckedIds.map(Number)) : null;
    list.innerHTML = '';
    targetDevs.forEach(d => {
        const isFrozen = deviceStatus[String(d.id)] === 1;
        const label = document.createElement('label');
        label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;border-bottom:1px solid var(--border);';
        label.setAttribute('data-device-name', d.name || '');
        label.setAttribute('data-device-ip', d.ip || '');

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.name = 'freezeDevs';
        cb.value = d.id;
        cb.checked = defaultChecked ? defaultChecked.has(Number(d.id)) : (shouldFreeze ? false : isFrozen);
        cb.style.accentColor = 'var(--accent)';
        cb.addEventListener('change', updateFreezeDeviceSelectionState);

        const span = document.createElement('span');
        span.textContent = `${d.name || `设备${d.id}`} (${d.ip || ''})`;
        span.style.fontSize = '13px';
        span.style.color = 'var(--text)';

        label.appendChild(cb);
        label.appendChild(span);
        list.appendChild(label);
    });
    updateFreezeDeviceSelectionState();
}

function filterFreezeDeviceList() {
    const input = document.getElementById('freeze-device-search');
    const list = document.getElementById('freeze-device-list');
    if (!input || !list) return;
    const keyword = input.value.trim().toLowerCase();
    const labels = Array.from(list.querySelectorAll('label'));
    labels.forEach(label => {
        const deviceName = (label.getAttribute('data-device-name') || '').toLowerCase();
        const deviceIp = (label.getAttribute('data-device-ip') || '').toLowerCase();
        const matches = !keyword || deviceName.includes(keyword) || deviceIp.includes(keyword);
        label.style.display = matches ? 'flex' : 'none';
    });

    const oldHint = list.querySelector('.no-match-hint');
    if (oldHint) oldHint.remove();
    const visibleLabels = labels.filter(label => label.style.display !== 'none');
    if (visibleLabels.length === 0) {
        const hint = document.createElement('div');
        hint.className = 'no-match-hint';
        hint.style.cssText = 'padding:20px;text-align:center;color:var(--text3);';
        hint.textContent = '未找到匹配的设备';
        list.appendChild(hint);
    }
    updateFreezeDeviceSelectionState();
}

function openFreezeDeviceModal(person, shouldFreeze, path, defaultCheckedIds = null) {
    const personDoors = person.doors || [];
    const targetDevs = personDoors.map(did => getPersonDeviceList().find(d => d.id === did)).filter(Boolean);
    if (targetDevs.length === 0) {
        toast('error', '该人员没有关联任何设备');
        return;
    }

    const actionText = shouldFreeze ? '冻结' : '解冻';
    _pendingFreezeDeviceAction = { uid: person.user_id, shouldFreeze, path };
    document.getElementById('freezeDeviceModalTitle').textContent = `${actionText}人员`;
    document.getElementById('freezeDevicePrompt').textContent = `选择要${actionText}的设备：${person.name || person.user_id}（ID: ${person.user_id}）`;
    const search = document.getElementById('freeze-device-search');
    if (search) {
        search.value = '';
        if (!search.dataset.boundFreezeDeviceSearch) {
            search.dataset.boundFreezeDeviceSearch = '1';
            search.addEventListener('input', filterFreezeDeviceList);
        }
    }
    renderFreezeDeviceList(targetDevs, person.status || {}, shouldFreeze, defaultCheckedIds);
    const okBtn = document.getElementById('freezeDeviceOkBtn');
    if (okBtn) {
        okBtn.disabled = false;
        okBtn.textContent = `确认${actionText}`;
        okBtn.className = shouldFreeze ? 'btn btn-danger' : 'btn btn-primary';
    }
    openModal('freezeDeviceModal');
}

async function submitFreezeDeviceSelection() {
    const action = _pendingFreezeDeviceAction;
    if (!action) {
        closeModal('freezeDeviceModal');
        return;
    }
    const checkedBoxes = document.querySelectorAll('input[name="freezeDevs"]:checked');
    if (checkedBoxes.length === 0) {
        toast('error', '请至少选择一个设备');
        return;
    }

    const actionText = action.shouldFreeze ? '冻结' : '解冻';
    const okBtn = document.getElementById('freezeDeviceOkBtn');
    if (okBtn) {
        okBtn.disabled = true;
        okBtn.innerHTML = '<span class="spinner"></span> 处理中';
    }

    let success = 0, fail = 0, offlineSkip = 0;
    const successDeviceIds = [];
    try {
        for (const cb of checkedBoxes) {
            const did = parseInt(cb.value);
            const dev = getPersonDeviceList().find(d => d.id === did);
            if (!dev) { fail++; continue; }
            if (!dev.online) { offlineSkip++; continue; }
            try {
                const result = await apiRequest('POST', action.path, dev);
                if (result.code === 0) {
                    success++;
                    successDeviceIds.push(did);
                } else {
                    fail++;
                    toast('error', `设备「${dev.name}」${actionText}失败：${result.msg}`);
                }
            } catch (e) {
                fail++;
                toast('error', `设备「${dev.name}」网络错误`);
            }
        }

        if (success > 0) {
            const newStatus = action.shouldFreeze ? 1 : 0;
            const statusPayload = {};
            for (const did of successDeviceIds) {
                statusPayload[String(did)] = newStatus;
            }
            let localStatusFailed = false;
            try {
                const statusResp = await fetch(`${API}/api/persons/${encodeURIComponent(action.uid)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: statusPayload })
                });
                const statusResult = await statusResp.json();
                localStatusFailed = !statusResp.ok || statusResult.code !== 0;
            } catch (e) {
                localStatusFailed = true;
            }
            let freezeMsg = `已完成：${success} 成功，${fail} 失败`;
            if (offlineSkip > 0) freezeMsg += `，${offlineSkip}台离线设备已跳过`;
            if (localStatusFailed) freezeMsg += '，本地状态更新失败，请刷新后核对';
            toast(localStatusFailed || fail > 0 ? 'info' : 'success', freezeMsg);
            closeModal('freezeDeviceModal');
            _pendingFreezeDeviceAction = null;
            
            // 根据当前视图刷新
            if (currentPDevObj) {
                await refreshPersonList(false);
            } else {
                await loadAllPersons(false);
            }
        } else if (offlineSkip > 0) {
            toast('info', `${offlineSkip}台离线设备已跳过`);
        } else {
            toast('error', '所有操作均失败');
        }
    } finally {
        if (okBtn) {
            okBtn.disabled = false;
            okBtn.textContent = `确认${actionText}`;
        }
    }
}

let _pendingDeleteDeviceAction = null;

function setDeleteDeviceAll(checked) {
    const list = document.getElementById('delete-device-list');
    if (!list) return;
    Array.from(list.querySelectorAll('label'))
        .filter(label => label.style.display !== 'none')
        .forEach(label => {
            const cb = label.querySelector('input[name="deleteDevs"]');
            if (cb) cb.checked = checked;
        });
    updateDeleteDeviceSelectionState();
}

function updateDeleteDeviceSelectionState() {
    const list = document.getElementById('delete-device-list');
    const all = document.getElementById('deleteDeviceSelectAll');
    const hint = document.getElementById('deleteDeviceSelectHint');
    if (!list) return;
    const visibleChecks = Array.from(list.querySelectorAll('label'))
        .filter(label => label.style.display !== 'none')
        .map(label => label.querySelector('input[name="deleteDevs"]'))
        .filter(Boolean);
    const visibleChecked = visibleChecks.filter(cb => cb.checked).length;
    if (all) {
        all.checked = visibleChecks.length > 0 && visibleChecked === visibleChecks.length;
        all.indeterminate = visibleChecked > 0 && visibleChecked < visibleChecks.length;
        all.disabled = visibleChecks.length === 0;
    }
    if (hint) {
        const totalChecked = document.querySelectorAll('input[name="deleteDevs"]:checked').length;
        hint.textContent = visibleChecks.length
            ? `当前结果 ${visibleChecked}/${visibleChecks.length}，已选 ${totalChecked} 台`
            : '当前结果 0 台';
    }
}

function renderDeleteDeviceList(targetDeviceIds, defaultCheckedIds) {
    const list = document.getElementById('delete-device-list');
    if (!list) return;
    const defaultChecked = new Set(defaultCheckedIds.map(Number));
    list.innerHTML = '';
    targetDeviceIds.forEach(did => {
        const dev = getPersonDeviceList().find(d => d.id === did);
        const label = document.createElement('label');
        label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;border-bottom:1px solid var(--border);';
        label.setAttribute('data-device-name', dev?.name || `设备${did}`);
        label.setAttribute('data-device-ip', dev?.ip || '');

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.name = 'deleteDevs';
        cb.value = did;
        cb.checked = defaultChecked.has(Number(did));
        cb.style.accentColor = 'var(--accent)';
        cb.addEventListener('change', updateDeleteDeviceSelectionState);

        const span = document.createElement('span');
        span.textContent = dev ? `${dev.name} (${dev.ip})` : `设备${did}`;
        span.style.fontSize = '13px';
        span.style.color = 'var(--text)';

        label.appendChild(cb);
        label.appendChild(span);
        list.appendChild(label);
    });
    updateDeleteDeviceSelectionState();
}

function filterDeleteDeviceList() {
    const input = document.getElementById('delete-device-search');
    const list = document.getElementById('delete-device-list');
    if (!input || !list) return;
    const keyword = input.value.trim().toLowerCase();
    const labels = Array.from(list.querySelectorAll('label'));
    labels.forEach(label => {
        const deviceName = (label.getAttribute('data-device-name') || '').toLowerCase();
        const deviceIp = (label.getAttribute('data-device-ip') || '').toLowerCase();
        const matches = !keyword || deviceName.includes(keyword) || deviceIp.includes(keyword);
        label.style.display = matches ? 'flex' : 'none';
    });

    const oldHint = list.querySelector('.no-match-hint');
    if (oldHint) oldHint.remove();
    const visibleLabels = labels.filter(label => label.style.display !== 'none');
    if (visibleLabels.length === 0) {
        const hint = document.createElement('div');
        hint.className = 'no-match-hint';
        hint.style.cssText = 'padding:20px;text-align:center;color:var(--text3);';
        hint.textContent = '未找到匹配的设备';
        list.appendChild(hint);
    }
    updateDeleteDeviceSelectionState();
}

function openDeleteDeviceModal(person, allowedDevices) {
    const defaultCheckedIds = currentPDevObj ? [currentPDevObj.id] : allowedDevices;
    _pendingDeleteDeviceAction = { uid: person.user_id };
    document.getElementById('deleteDevicePrompt').textContent = `选择要删除人员 ${person.name || person.user_id}（ID: ${person.user_id}）的设备`;
    const search = document.getElementById('delete-device-search');
    if (search) {
        search.value = '';
        if (!search.dataset.boundDeleteDeviceSearch) {
            search.dataset.boundDeleteDeviceSearch = '1';
            search.addEventListener('input', filterDeleteDeviceList);
        }
    }
    renderDeleteDeviceList(allowedDevices, defaultCheckedIds);
    const okBtn = document.getElementById('deleteDeviceOkBtn');
    if (okBtn) {
        okBtn.disabled = false;
        okBtn.textContent = '确认删除';
    }
    openModal('deleteDeviceModal');
}

async function submitDeleteDeviceSelection() {
    const action = _pendingDeleteDeviceAction;
    if (!action) {
        closeModal('deleteDeviceModal');
        return;
    }
    const checkedBoxes = document.querySelectorAll('input[name="deleteDevs"]:checked');
    if (checkedBoxes.length === 0) {
        toast('error', '请至少选择一个设备');
        return;
    }

    const okBtn = document.getElementById('deleteDeviceOkBtn');
    if (okBtn) {
        okBtn.disabled = true;
        okBtn.innerHTML = '<span class="spinner"></span> 删除中';
    }

    let success = 0, fail = 0, offlineSkip = 0;
    try {
        for (const cb of checkedBoxes) {
            const did = parseInt(cb.value);
            const dev = getPersonDeviceList().find(d => d.id === did);
            if (!dev) { fail++; continue; }
            if (!dev.online) { offlineSkip++; continue; }
            try {
                const hwResult = await apiRequest('DELETE', `/api/user/${encodeURIComponent(action.uid)}`, dev);
                if (hwResult.code === 0) {
                    const resp = await fetch(`${API}/api/persons/${encodeURIComponent(action.uid)}/devices/${did}`, { method: 'DELETE' });
                    if (resp.ok) {
                        success++;
                        toast('success', `已从设备「${dev.name}」移除`);
                    } else {
                        fail++;
                    }
                } else {
                    fail++;
                    toast('error', `设备「${dev.name}」删除失败：${hwResult.msg}`);
                }
            } catch (e) {
                fail++;
                toast('error', `设备「${dev.name}」网络错误`);
            }
        }

        if (success > 0) {
            let delMsg = `已完成：${success} 成功，${fail} 失败`;
            if (offlineSkip > 0) delMsg += `，${offlineSkip}台离线设备已跳过`;
            toast('success', delMsg);
            closeModal('deleteDeviceModal');
            _pendingDeleteDeviceAction = null;
        } else if (offlineSkip > 0) {
            toast('info', `${offlineSkip}台离线设备已跳过`);
        } else {
            toast('error', '所有操作均失败');
        }

        if (currentPDevObj) await refreshPersonList(false);
        else await loadAllPersons(false);
    } finally {
        if (okBtn) {
            okBtn.disabled = false;
            okBtn.textContent = '确认删除';
        }
    }
}

async function openPersonModal(uid = null) {
    if (!uid && !currentPDevObj && getPersonDeviceList().length === 0) {
        toast('error', '请先添加设备');
        return;
    }
    editPersonId = uid;
    document.getElementById('personModalTitle').textContent = uid ? '编辑人员' : '添加人员';
    document.getElementById('p-id').disabled = !!uid;
    
    // 清空搜索框和勾选状态缓存
    const searchInput = document.getElementById('device-search-input');
    if (searchInput) searchInput.value = '';
    window._deviceCheckboxStates = {};
    
    fillPersonDeviceSelect();
    initDeviceSearchListener();

    const container = document.getElementById('device-checkbox-list');
    const checkboxes = container ? container.querySelectorAll('input[type="checkbox"]') : [];
    const uploadRow = document.getElementById('face-upload-row');
    const hintRow = document.getElementById('face-hint-row');
    const updateRow = document.getElementById('face-update-row');

    if (uid) {
        if (uploadRow) uploadRow.style.display = 'none';
        if (updateRow) updateRow.style.display = '';
        let hasFace = false;
        try {
            const resp = await fetch(`${API}/api/face/${encodeURIComponent(uid)}/exists`);
            const data = await resp.json();
            hasFace = data.code === 0 ? !!data.data?.exists : !!data.exists;
        } catch (e) { }
        if (hintRow) hintRow.style.display = hasFace ? 'none' : '';
        // 服务端分页后改用 API 搜索，避免 persons.find() 跨页遗漏
        const findResp = await fetch(`${API}/api/persons?page=1&per_page=1&search=${encodeURIComponent(uid)}&search_mode=exact`);
        const findData = await findResp.json();
        const p = (findData.code === 0 && findData.data.items.length > 0) ? findData.data.items[0] : null;
        if (p) {
            document.getElementById('p-id').value = p.user_id;
            document.getElementById('p-name').value = p.name;
            // 设备视角 → 显示该设备的日期；全部设备视角 → 全局值（已经是 min end）
            const deviceEnd = currentPDevObj
                ? (p.valid_end_map?.[currentPDevObj.id] ?? p.valid_end)
                : p.valid_end;
            document.getElementById('p-end').value = safeDate(deviceEnd, '2037-12-31');
            checkboxes.forEach(cb => {
                cb.checked = p.doors && p.doors.includes(parseInt(cb.value));
                // 同步到勾选状态缓存
                window._deviceCheckboxStates[cb.value] = cb.checked;
            });
        }
    } else {
        if (uploadRow) uploadRow.style.display = '';
        if (hintRow) hintRow.style.display = 'none';
        if (updateRow) updateRow.style.display = 'none';
        ['p-id', 'p-name'].forEach(f => document.getElementById(f).value = '');
        document.getElementById('p-face').value = '';
        const pFaceKb = document.getElementById('p-face-kb');
        const pFaceW = document.getElementById('p-face-w');
        const pFaceH = document.getElementById('p-face-h');
        const pFaceQ = document.getElementById('p-face-q');
        if (pFaceKb) pFaceKb.value = '100';
        if (pFaceW) pFaceW.value = '0';
        if (pFaceH) pFaceH.value = '0';
        if (pFaceQ) pFaceQ.value = '0';
        document.getElementById('p-end').value = '2037-12-31';
        checkboxes.forEach(cb => {
            cb.checked = currentPDevObj && parseInt(cb.value) === currentPDevObj.id;
            // 同步到勾选状态缓存
            window._deviceCheckboxStates[cb.value] = cb.checked;
        });
    }
    updatePersonDeviceSelectionState();
    openModal('personModal');
}

function uniqueDeviceIds(ids) {
    return Array.from(new Set((ids || []).map(id => parseInt(id)).filter(id => !Number.isNaN(id))));
}

function getDeviceName(did) {
    const dev = getPersonDeviceList().find(d => Number(d.id) === Number(did));
    return dev?.name || `设备${did}`;
}

function formatDeviceNames(ids, limit = 4) {
    const names = uniqueDeviceIds(ids).map(getDeviceName);
    if (!names.length) return '';
    const shown = names.slice(0, limit).join('、');
    return names.length > limit ? `「${shown}」等${names.length}台` : `「${shown}」`;
}

function addUniqueDeviceId(list, did) {
    const id = parseInt(did);
    if (!Number.isNaN(id) && !list.includes(id)) list.push(id);
}

function formatDeviceFailures(failures, limit = 3) {
    const rows = (failures || []).slice(0, limit).map(item => {
        const msg = item.msg ? `：${item.msg}` : '';
        return `${getDeviceName(item.did)}${item.action || ''}失败${msg}`;
    });
    if ((failures || []).length > limit) rows.push(`等${failures.length}台失败`);
    return rows.join('；');
}

function buildPersonSaveToast({ personOps, faceSuccessIds, failures, actionLabel }) {
    const successParts = [];
    if (personOps.created.length) successParts.push(`下发人员${formatDeviceNames(personOps.created)}`);
    if (personOps.updated.length) successParts.push(`更新人员${formatDeviceNames(personOps.updated)}`);
    if (personOps.removed.length) successParts.push(`移除人员${formatDeviceNames(personOps.removed)}`);
    if (faceSuccessIds.length) successParts.push(`下发人脸${formatDeviceNames(faceSuccessIds)}`);

    const hasFailures = failures.length > 0;
    const prefix = hasFailures ? `${actionLabel}部分完成` : `${actionLabel}完成`;
    let msg = successParts.length ? `${prefix}：${successParts.join('，')}` : prefix;
    if (hasFailures) msg += `；${formatDeviceFailures(failures)}`;
    return { type: hasFailures ? 'error' : 'success', msg };
}

async function fetchLocalPersonById(uid) {
    try {
        // 改用精确搜索 API，避免服务端分页导致找不到用户
        const resp = await fetch(`${API}/api/persons?page=1&per_page=1&search=${encodeURIComponent(uid)}&search_mode=exact`);
        const data = await resp.json();
        if (data.code === 0 && data.data.items && data.data.items.length > 0) {
            return data.data.items[0];
        }
    } catch (e) {
        console.warn('读取本地人员状态失败:', e);
    }
    return null;
}

async function _finalizeEditSave(ps) {
    const { uid, name, validEnd, validBegin, onlineChecked, removedDoors,
            existingPerson, personPayload, getDevStatus, checked,
            devicesNeedFace,
            devicesAlreadySaved, cachedFaceBlob, cachedFaceFileName, faceOptions,
            personCreatedDevices, faceSuccessDevices } = ps;

    let updatedDeviceIds = [];
    const personOps = { created: [], updated: [], removed: [] };
    const failures = [];
    const faceSuccessIds = uniqueDeviceIds(faceSuccessDevices || []);
    try {
        if (!devicesAlreadySaved) {
            updatedDeviceIds = [];
            const originalDoors = (existingPerson?.doors || []).map(Number);
            // 检查基础字段是否变更，只有变时才需要更新已有设备
            const oldName = (existingPerson?.name || '');
            const oldEnd = currentPDevObj
                ? safeDate(existingPerson?.valid_end_map?.[currentPDevObj.id] ?? existingPerson?.valid_end, '2037-12-31')
                : safeDate(existingPerson?.valid_end, '2037-12-31');
            const basicFieldsChanged = (name !== oldName) || (validEnd !== oldEnd);
            const alreadyCreatedIds = new Set(uniqueDeviceIds(personCreatedDevices || []));
            for (const did of onlineChecked) {
                const dev = getPersonDeviceList().find(d => d.id === did);
                if (!dev) continue;
                const isNewDevice = !originalDoors.includes(did);
                const alreadyCreated = alreadyCreatedIds.has(did);
                // 已有设备且基础字段没变 → 跳过，不需要操作
                if (!isNewDevice && !basicFieldsChanged) {
                    updatedDeviceIds.push(did);
                    continue;
                }
                if (isNewDevice && alreadyCreated) {
                    updatedDeviceIds.push(did);
                    addUniqueDeviceId(personOps.created, did);
                    continue;
                }
                try {
                    let deviceResult;
                    if (isNewDevice && !alreadyCreated) {
                        // 新增设备：调用 POST 创建人员
                        deviceResult = await apiRequest('POST', '/api/user', dev, {
                            user_id: uid,
                            name,
                            status: getDevStatus(did),
                            doors: [0],
                            valid_begin: validBegin,
                            valid_end: validEnd,
                        });
                    } else {
                        // 已有设备：调用 PUT 更新人员
                        deviceResult = await apiRequest('PUT', `/api/user/${encodeURIComponent(uid)}`, dev, {
                            name,
                            status: getDevStatus(did),
                            doors: [0],
                            valid_begin: validBegin,
                            valid_end: validEnd,
                        });
                    }
                    if (deviceResult.code === 0) {
                        updatedDeviceIds.push(did);
                        addUniqueDeviceId(isNewDevice ? personOps.created : personOps.updated, did);
                    } else {
                        failures.push({
                            did,
                            action: isNewDevice ? '下发人员' : '更新人员',
                            msg: deviceResult.msg || '未知错误'
                        });
                    }
                } catch (e) {
                    failures.push({
                        did,
                        action: isNewDevice ? '下发人员' : '更新人员',
                        msg: e?.message || '网络错误'
                    });
                }
            }
            const failedOnlineChecked = onlineChecked.filter(did => !updatedDeviceIds.includes(did));
            const stillCheckedFailedOriginal = failedOnlineChecked.filter(did => existingPerson?.doors?.includes(did));
            const stillCheckedFailedNew = failedOnlineChecked.filter(did => !existingPerson?.doors?.includes(did));
            if (stillCheckedFailedNew.length > 0) {
                personPayload.doors = personPayload.doors.filter(did => !stillCheckedFailedNew.includes(did));
                for (const did of stillCheckedFailedNew) {
                    const key = String(did);
                    delete personPayload.status[key];
                    if (personPayload.has_face && typeof personPayload.has_face === 'object') {
                        delete personPayload.has_face[key];
                    }
                    delete personPayload.valid_begin_map?.[key];
                    delete personPayload.valid_end_map?.[key];
                }
            }
            for (const did of stillCheckedFailedOriginal) {
                if (!personPayload.doors.includes(did)) personPayload.doors.push(did);
                if (!(String(did) in personPayload.status)) {
                    personPayload.status[String(did)] = getDevStatus(did);
                }
            }
            for (const did of removedDoors) {
                const dev = getPersonDeviceList().find(d => d.id === did);
                if (!dev) continue;
                if (!dev.online) {
                    if (!personPayload.doors.includes(did)) personPayload.doors.push(did);
                    personPayload.status[String(did)] = getDevStatus(did);
                    continue;
                }
                try {
                    const removeResult = await apiRequest('DELETE', `/api/user/${encodeURIComponent(uid)}`, dev);
                    if (removeResult.code === 0) {
                        personPayload.doors = personPayload.doors.filter(d => d !== did);
                        const key = String(did);
                        delete personPayload.status[key];
                        if (personPayload.has_face && typeof personPayload.has_face === 'object') {
                            delete personPayload.has_face[key];
                        }
                        delete personPayload.valid_begin_map?.[key];
                        delete personPayload.valid_end_map?.[key];
                        addUniqueDeviceId(personOps.removed, did);
                    } else {
                        if (!personPayload.doors.includes(did)) personPayload.doors.push(did);
                        personPayload.status[String(did)] = getDevStatus(did);
                        failures.push({ did, action: '移除人员', msg: removeResult.msg || '未知错误' });
                    }
                } catch (e) {
                    if (!personPayload.doors.includes(did)) personPayload.doors.push(did);
                    personPayload.status[String(did)] = getDevStatus(did);
                    failures.push({ did, action: '移除人员', msg: e?.message || '网络错误' });
                }
            }
            personPayload.doors = Array.from(new Set(personPayload.doors));

            // 构造 has_face：保留已有记录 + 标记下发成功的设备
            if (personCreatedDevices && faceSuccessDevices && faceSuccessDevices.length > 0) {
                const mergedFace = { ...(existingPerson?.has_face || {}) };
                for (const did of faceSuccessDevices) {
                    mergedFace[String(did)] = true;
                }
                personPayload.has_face = mergedFace;
            }

            const localResp = await fetch(`${API}/api/persons/${encodeURIComponent(uid)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(personPayload)
            });
            const localData = await localResp.json();
            if (localData.code !== 0) {
                toast('error', localData.msg || '本地人员信息更新失败');
                return;
            }
        }

        if (cachedFaceBlob && devicesNeedFace && devicesNeedFace.length > 0) {
            const faceTargetIds = uniqueDeviceIds(devicesNeedFace)
                .filter(did => updatedDeviceIds.includes(did) && personPayload.doors.includes(did));
            for (const did of faceTargetIds) {
                const dev = getPersonDeviceList().find(d => d.id === did);
                if (!dev) {
                    failures.push({ did, action: '下发人脸', msg: '未找到设备' });
                    continue;
                }
                const fd = new FormData();
                fd.append('file', cachedFaceBlob, cachedFaceFileName || `${uid}.jpg`);
                fd.append('force', '1');
                fd.append('max_kb', faceOptions?.max_kb || '0');
                fd.append('width', faceOptions?.width || '0');
                fd.append('height', faceOptions?.height || '0');
                fd.append('quality', faceOptions?.quality || '0');
                fd.append('device_id', dev.id);
                try {
                    const devRes = await fetch(`${API}/api/face/${encodeURIComponent(uid)}`, { method: 'POST', body: fd });
                    const devResult = await devRes.json();
                    if (devResult.code === 0) {
                        addUniqueDeviceId(faceSuccessIds, did);
                    } else {
                        failures.push({ did, action: '下发人脸', msg: devResult.msg || '未知错误' });
                    }
                } catch (e) {
                    failures.push({ did, action: '下发人脸', msg: e?.message || '网络错误' });
                }
            }
        }

        const finalToast = buildPersonSaveToast({
            personOps,
            faceSuccessIds,
            failures,
            actionLabel: editPersonId ? '编辑人员' : '新增人员'
        });
        toast(finalToast.type, finalToast.msg);
        closeModal('personModal');

        if (currentPDevObj) {
            if (checked.includes(currentPDevObj.id)) {
                await refreshPersonList(false);
            } else if (removedDoors.includes(currentPDevObj.id)) {
                await refreshPersonList(false);
            }
        } else {
            await loadAllPersons(false);
        }
    } catch (e) {
        console.error(e);
        toast('error', e?.message || '编辑人员失败，请检查设备连接或用户状态');
    }
}

async function savePerson() {
    const container = document.getElementById('device-checkbox-list');
    const checkedState = window._deviceCheckboxStates || {};
    let checked = Object.entries(checkedState)
        .filter(([, isChecked]) => isChecked)
        .map(([id]) => parseInt(id))
        .filter(id => getPersonDeviceList().some(d => Number(d.id) === id));
    if (checked.length === 0 && container) {
        checked = Array.from(container.querySelectorAll('input[type="checkbox"]:checked'))
            .map(cb => parseInt(cb.value));
    }
    if (!checked.length) {
        toast('error', '请至少勾选一个设备');
        return;
    }

    const uid = document.getElementById('p-id').value.trim();
    const name = document.getElementById('p-name').value.trim();
    const validEnd = document.getElementById('p-end').value;
    if (!uid || !name) {
        toast('error', '请填写用户ID和姓名');
        return;
    }
    if (!validEnd) {
        toast('error', '请填写有效期截止日期');
        return;
    }

    const existingPerson = editPersonId ? await getPersonById(editPersonId) : null;
    const originalDoors = Array.isArray(existingPerson?.doors) ? existingPerson.doors.map(Number) : [];
    const removedDoors = editPersonId ? originalDoors.filter(id => !checked.includes(id)) : [];
    // 设备视角 → 取该设备的开始日期；全部设备视角 → 全局值
    const validBegin = currentPDevObj
        ? safeDate(existingPerson?.valid_begin_map?.[currentPDevObj.id] ?? existingPerson?.valid_begin, '2000-01-01')
        : safeDate(existingPerson?.valid_begin, '2000-01-01');
    const status = existingPerson?.status ?? {};
    const getDevStatus = (did) => {
        if (typeof status === 'object' && status !== null) return status[String(did)] ?? 0;
        return typeof status === 'number' ? status : 0;
    };
    if (editPersonId && existingPerson) {
        const oldName = existingPerson.name || '';
        // 设备视角 → 比较该设备的日期；全部设备视角 → 比较全局值（min）
        const oldEnd = currentPDevObj
            ? safeDate(existingPerson.valid_end_map?.[currentPDevObj.id] ?? existingPerson.valid_end, '2037-12-31')
            : safeDate(existingPerson.valid_end, '2037-12-31');
        const doorsSame = originalDoors.length === checked.length
            && originalDoors.every(d => checked.includes(d));
        if (name === oldName && doorsSame && validEnd === oldEnd) {
            toast('info', '信息未变更，无需保存');
            return;
        }
    }

    // 只对明确在线的设备操作，离线或未检测的设备一律跳过
    let onlineChecked = checked.filter(did => {
        const dev = getPersonDeviceList().find(d => d.id === did);
        return dev?.online === true;
    });

    const keptOfflineChecked = editPersonId
        ? checked.filter(did => originalDoors.includes(did) && !onlineChecked.includes(did))
        : [];
    const localDoors = editPersonId
        ? Array.from(new Set([...onlineChecked, ...keptOfflineChecked]))
        : [...onlineChecked];

    const personPayload = {
        user_id: uid,
        name: name,
        valid_begin: validBegin,
        valid_end: validEnd,
        status: status,
        doors: localDoors
    };
    // per-device 日期：表单日期对所有勾选设备生效
    if (editPersonId && existingPerson) {
        personPayload.valid_begin_map = { ...(existingPerson.valid_begin_map || {}) };
        personPayload.valid_end_map = { ...(existingPerson.valid_end_map || {}) };
    } else {
        personPayload.valid_begin_map = {};
        personPayload.valid_end_map = {};
    }
    for (const did of localDoors) {
        const key = String(did);
        if (!(key in personPayload.valid_begin_map)) {
            personPayload.valid_begin_map[key] = validBegin;
        }
        personPayload.valid_end_map[key] = validEnd;
    }
    for (const did of keptOfflineChecked) {
        const key = String(did);
        personPayload.valid_end_map[key] = validEnd;
        if (!(key in personPayload.valid_begin_map)) {
            personPayload.valid_begin_map[key] = validBegin;
        }
    }
    if (typeof personPayload.status !== 'object' || personPayload.status === null || Array.isArray(personPayload.status)) {
        personPayload.status = {};
    }
    for (const did of onlineChecked) {
        if (!(String(did) in personPayload.status)) {
            personPayload.status[String(did)] = 0;
        }
    }
    for (const did of keptOfflineChecked) {
        if (!(String(did) in personPayload.status)) {
            personPayload.status[String(did)] = getDevStatus(did);
        }
    }
    if (editPersonId) {
        for (const did of removedDoors) {
            delete personPayload.status[String(did)];
        }
    }
    if (editPersonId && existingPerson?.has_face && removedDoors.length > 0) {
        const cleanedFace = (typeof existingPerson.has_face === 'object' && existingPerson.has_face !== null && !Array.isArray(existingPerson.has_face))
            ? {...existingPerson.has_face}
            : {};
        for (const did of removedDoors) {
            delete cleanedFace[String(did)];
        }
        personPayload.has_face = cleanedFace;
    }

    const btn = document.getElementById('personSaveBtn');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 保存中';

    try {
        if (editPersonId) {
            const localPerson = await fetchLocalPersonById(uid);
            const hf2 = localPerson?.has_face || existingPerson?.has_face || {};
            const newOnlineDevices = onlineChecked.filter(did => !originalDoors.includes(did));
            const devicesNeedFace = newOnlineDevices.filter(did => hasFaceOnDevice(hf2, did) === false);
            if (devicesNeedFace.length > 0) {
                let hasLocalFace = false;
                let faceBlob = null;
                let faceFileName = `${uid}.jpg`;
                try {
                    const faceResp = await fetch(`${API}/api/face/${encodeURIComponent(uid)}`);
                    if (faceResp.ok) {
                        hasLocalFace = true;
                        faceBlob = await faceResp.blob();
                    }
                } catch (e) { }
                if (!hasLocalFace) {
                    _pendingPersonSave = {
                        uid, name, validEnd, validBegin, onlineChecked, removedDoors,
                        existingPerson, personPayload, getDevStatus, checked,
                        devicesNeedFace,
                        devicesAlreadySaved: false
                    };
                    closeModal('personModal', true);
                    btn.disabled = false;
                    btn.innerHTML = originalText;
                    openFaceModal(uid, devicesNeedFace);
                    return;
                }
                _pendingPersonSave = {
                    uid, name, validEnd, validBegin, onlineChecked, removedDoors,
                    existingPerson, personPayload, getDevStatus, checked,
                    devicesNeedFace,
                    devicesAlreadySaved: false,
                    cachedFaceBlob: faceBlob,
                    cachedFaceFileName: faceFileName
                };
                const ps = _pendingPersonSave;
                _pendingPersonSave = null;
                await _finalizeEditSave(ps);
                return;
            }

            _pendingPersonSave = {
                uid, name, validEnd, validBegin, onlineChecked, removedDoors,
                existingPerson, personPayload, getDevStatus, checked,
                devicesNeedFace,
                devicesAlreadySaved: false
            };
            const ps = _pendingPersonSave;
            _pendingPersonSave = null;
            await _finalizeEditSave(ps);
            return;
        } else {
            if (onlineChecked.length === 0) {
                toast('error', '所有设备离线，无法添加人员');
                btn.disabled = false;
                btn.innerHTML = originalText;
                return;
            }
            const addSuccessDevices = [];
            const addFailures = [];
            const addFaceSuccessDevices = [];
            for (const did of onlineChecked) {
                const dev = getPersonDeviceList().find(d => d.id === did);
                if (!dev) continue;
                try {
                    const createResult = await apiRequest('POST', '/api/user', dev, {
                        user_id: uid,
                        name,
                        valid_begin: validBegin,
                        valid_end: validEnd,
                        status: getDevStatus(did),
                        doors: [0]
                    });
                    if (createResult.code === 0) {
                        addSuccessDevices.push(did);
                    } else {
                        addFailures.push({ did, action: '下发人员', msg: createResult.msg || '未知错误' });
                    }
                } catch (e) {
                    addFailures.push({ did, action: '下发人员', msg: e?.message || '网络错误' });
                }
            }
            if (addSuccessDevices.length === 0) {
                throw new Error('所有在线设备新增人员均失败');
            }
            const addedPersonPayload = {
                ...personPayload,
                doors: addSuccessDevices,
                status: Object.fromEntries(addSuccessDevices.map(did => [String(did), getDevStatus(did)]))
            };
            personPayload.doors = addSuccessDevices;
            personPayload.status = addedPersonPayload.status;
            onlineChecked = addSuccessDevices;

            const importResp = await fetch(`${API}/api/persons/import`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_id: addSuccessDevices[0], persons: [addedPersonPayload], no_purge: true })
            });
            const importData = await importResp.json();
            if (importData.code !== 0) {
                toast('error', importData.msg || '本地人员信息写入失败');
                return;
            }

            const faceFileInput = document.getElementById('p-face');
            if (faceFileInput && faceFileInput.files.length > 0) {
                const faceFile = faceFileInput.files[0];
                const fd = new FormData();
                fd.append('file', faceFile);
                fd.append('force', '1');
                fd.append('max_kb', document.getElementById('p-face-kb')?.value || '0');
                fd.append('width', document.getElementById('p-face-w')?.value || '0');
                fd.append('height', document.getElementById('p-face-h')?.value || '0');
                fd.append('quality', document.getElementById('p-face-q')?.value || '0');
                const faceSuccessDevices = [];
                for (const did of onlineChecked) {
                    const dev = getPersonDeviceList().find(d => d.id === did);
                    if (!dev) {
                        addFailures.push({ did, action: '下发人脸', msg: '未找到设备' });
                        continue;
                    }
                    fd.set('device_id', dev.id);
                    try {
                        const faceResp = await fetch(`${API}/api/face/${encodeURIComponent(uid)}`, { method: 'POST', body: fd });
                        const faceData = await faceResp.json();
                        if (faceData.code === 0) { 
                            faceSuccessDevices.push(did);
                            addUniqueDeviceId(addFaceSuccessDevices, did);
                        }
                        else {
                            addFailures.push({ did, action: '下发人脸', msg: faceData.msg || '未知错误' });
                        }
                    } catch (e) {
                        addFailures.push({ did, action: '下发人脸', msg: e?.message || '网络错误' });
                    }
                }
                // 更新本地 has_face 状态：标记人脸上传成功的设备
                if (faceSuccessDevices.length > 0) {
                    const updatedHasFace = { ...(addedPersonPayload.has_face || {}) };
                    for (const did of faceSuccessDevices) {
                        updatedHasFace[String(did)] = true;
                    }
                    try {
                        const updateResp = await fetch(`${API}/api/persons/${encodeURIComponent(uid)}`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ has_face: updatedHasFace })
                        });
                        const updateData = await updateResp.json();
                        if (updateData.code !== 0) {
                            console.warn('更新 has_face 状态失败:', updateData.msg);
                        }
                    } catch (e) {
                        console.error('更新 has_face 状态异常:', e);
                    }
                }
            }

            const finalToast = buildPersonSaveToast({
                personOps: { created: addSuccessDevices, updated: [], removed: [] },
                faceSuccessIds: addFaceSuccessDevices,
                failures: addFailures,
                actionLabel: '新增人员'
            });
            toast(finalToast.type, finalToast.msg);
        }
        closeModal('personModal');

        if (currentPDevObj) {
            if (checked.includes(currentPDevObj.id)) {
                await refreshPersonList(false);
                if (!editPersonId) {
                    const idx = persons.findIndex(p => p.user_id === uid);
                    if (idx > -1) {
                        const newUser = persons.splice(idx, 1)[0];
                        persons.unshift(newUser);
                    }
                    deviceUserPage = 1;
                    renderPersonTable();
                    updatePaginationUI();
                }
            } else if (editPersonId && removedDoors.includes(currentPDevObj.id)) {
                await refreshPersonList(false);
            }
        } else {
            await loadAllPersons(false);
        }
    } catch (e) {
        console.error(e);
        toast('error', e?.message || (editPersonId ? '编辑人员失败，请检查设备连接或用户状态' : '操作失败，请检查设备连接'));
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

async function getPersonById(uid) {
    const resp = await fetch(`${API}/api/persons?page=1&per_page=1&search=${encodeURIComponent(uid)}&search_mode=exact`);
    const data = await resp.json();
    return (data.code === 0 && data.data.items.length > 0) ? data.data.items[0] : null;
}

async function openValidityModal(uid) {
    if (!currentPDevObj) {
        toast('error', '请先选择设备');
        return;
    }
    const person = await getPersonById(uid);
    if (!person) {
        toast('error', '未找到人员信息');
        return;
    }
    document.getElementById('validityUid').textContent = uid;
    const vb = currentPDevObj
        ? (person.valid_begin_map?.[currentPDevObj.id] ?? person.valid_begin)
        : person.valid_begin;
    const ve = currentPDevObj
        ? (person.valid_end_map?.[currentPDevObj.id] ?? person.valid_end)
        : person.valid_end;
    document.getElementById('validity-begin').value = safeDate(vb, '2000-01-01');
    document.getElementById('validity-end').value = safeDate(ve, '2037-12-31');
    openModal('validityModal');
}

async function submitValidityUpdate() {
    if (!currentPDevObj) {
        toast('error', '请先选择设备');
        return;
    }

    const uid = document.getElementById('validityUid').textContent.trim();
    const validBegin = document.getElementById('validity-begin').value;
    const validEnd = document.getElementById('validity-end').value;
    if (!uid || !validBegin || !validEnd) {
        toast('error', '请完整填写有效期');
        return;
    }
    if (validBegin > validEnd) {
        toast('error', '开始日期不能晚于截止日期');
        return;
    }

    const btn = document.getElementById('validitySaveBtn');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 保存中';

    try {
        const result = await apiRequest('PUT', `/api/user/${encodeURIComponent(uid)}/validity`, currentPDevObj, {
            valid_begin: validBegin,
            valid_end: validEnd
        });
        if (result.code !== 0) {
            toast('error', result.msg || '有效期更新失败');
            return;
        }

        const localResp = await fetch(`${API}/api/persons/${encodeURIComponent(uid)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                valid_begin: validBegin,
                valid_end: validEnd,
                valid_begin_map: { [String(currentPDevObj.id)]: validBegin },
                valid_end_map: { [String(currentPDevObj.id)]: validEnd }
            })
        });
        const localData = await localResp.json();
        if (localData.code !== 0) {
            toast('error', localData.msg || '本地数据更新失败');
            return;
        }

        toast('success', `人员「${uid}」有效期已更新`);
        closeModal('validityModal');
        await refreshPersonList(false);
    } catch (e) {
        console.error(e);
        toast('error', '更新有效期失败，请检查设备连接');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

async function togglePersonFreeze(uid, shouldFreeze) {
    const person = await getPersonById(uid);
    if (!person) {
        toast('error', '未找到人员信息');
        return;
    }

    const path = shouldFreeze ? `/api/user/${encodeURIComponent(uid)}/freeze` : `/api/user/${encodeURIComponent(uid)}/unfreeze`;

    if (!currentPDevObj) {
        openFreezeDeviceModal(person, shouldFreeze, path);
        return;
    }

    openFreezeDeviceModal(person, shouldFreeze, path, [currentPDevObj.id]);
}

// ---------- 删除人员 ----------
async function confirmDelPerson(uid) {
    const p = persons.find(x => x.user_id === uid);
    if (!p) return;

    let userDeviceIds = [];
    try {
        const resp = await fetch(`${API}/api/devices`);
        const data = await resp.json();
        if (data.code === 0) {
            userDeviceIds = data.data
                .filter(d => (d.device_type || 'access') === 'access')
                .map(d => d.id);
        }
    } catch (e) { toast('error', '获取设备列表失败'); return; }

    const personDoors = p.doors || [];
    const allowedDevices = personDoors.filter(did => userDeviceIds.includes(did));

    if (allowedDevices.length === 0) {
        toast('error', '该人员没有关联您管理的设备');
        return;
    }

    openDeleteDeviceModal(p, allowedDevices);
}

// ---------- 人脸操作 ----------
function hasFaceOnDevice(hasFace, did) {
    if (typeof hasFace === 'object' && hasFace !== null) {
        const v = hasFace[String(did)] ?? hasFace[did];
        if (typeof v === 'boolean') return v;
        if (typeof v === 'number') return v !== 0;
        if (typeof v === 'string') return v === 'true' || v === '1';
        return !!v;
    }
    if (typeof hasFace === 'string') return hasFace === 'true' || hasFace === '1';
    return !!hasFace;
}

// ---------- 人脸照片预览 ----------
function openFacePreview(uid, name) {
    const modal = document.getElementById('facePreviewModal');
    if (!modal) return;
    const img = modal.querySelector('.face-preview-img');
    const label = modal.querySelector('.face-preview-label');
    const decodedUid = decodeURIComponent(uid || '');
    if (img) {
        img.onerror = () => toast('error', '人脸照片加载失败');
        img.src = `/api/face/${encodeURIComponent(decodedUid)}?t=${Date.now()}`;
    }
    if (label) label.textContent = name ? `${name} (${decodedUid})` : decodedUid;
    openModal('facePreviewModal');
}

window.closeFacePreview = closeFacePreview;
function closeFacePreview() {
    const img = document.querySelector('#facePreviewModal .face-preview-img');
    if (img) {
        img.onerror = null;
        img.src = '';
    }
    closeModal('facePreviewModal');
}

function openFaceModal(uid, pendingDevices = null) {
    faceUid = uid;
    facePendingDevices = pendingDevices;
    document.getElementById('faceUid').textContent = uid;
    document.getElementById('faceFile').value = '';
    document.getElementById('face-kb').value = (pendingDevices && pendingDevices.length > 0) ? '100' : '0';
    openModal('faceModal');
}

async function submitFace() {
    const fi = document.getElementById('faceFile');
    if (!fi.files.length) { toast('error', '请选择图片'); return; }
    const btn = document.getElementById('faceBtn');
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 下发中';

    try {
        let doors, hf, isEditContext;
        if (facePendingDevices && facePendingDevices.length > 0) {
            isEditContext = true;
            if (_pendingPersonSave) {
                const ps = _pendingPersonSave;
                _pendingPersonSave = null;
                ps.cachedFaceBlob = fi.files[0];
                ps.cachedFaceFileName = fi.files[0].name || `${faceUid}.jpg`;
                ps.faceOptions = {
                    max_kb: document.getElementById('face-kb').value || '0',
                    width: document.getElementById('face-w').value || '0',
                    height: document.getElementById('face-h').value || '0',
                    quality: document.getElementById('face-q').value || '0'
                };
                facePendingDevices = null;
                closeModal('faceModal', true);
                await _finalizeEditSave(ps);
                return;
            }
            doors = facePendingDevices;
            const localPerson = await fetchLocalPersonById(faceUid);
            hf = localPerson?.has_face || {};
        } else {
            isEditContext = false;
            // 改用精确搜索 API，避免服务端分页导致找不到用户
            const resp = await fetch(`${API}/api/persons?page=1&per_page=1&search=${encodeURIComponent(faceUid)}&search_mode=exact`);
            const data = await resp.json();
            const person = (data.code === 0 && data.data.items.length > 0) ? data.data.items[0] : null;
            const doors2 = person ? (person.doors || []) : [];
            hf = person?.has_face || {};
            if (doors2.length === 0 && currentPDevObj) {
                doors2.push(currentPDevObj.id);
            }
            doors = doors2;
        }
        if (doors.length === 0) {
            toast('error', '该人员没有关联任何设备，请先选择设备');
            return;
        }

        let successCount = 0;
        let failCount = 0;
        let skipCount = 0;
        let offlineSkip = 0;
        const faceSuccessDevices = [];  // 跟踪人脸下发成功的设备

        for (const did of doors) {
            if (!isEditContext && hasFaceOnDevice(hf, did)) { skipCount++; continue; }

            const dev = getPersonDeviceList().find(d => d.id === did);
            if (!dev) { failCount++; continue; }
            if (!dev.online) { offlineSkip++; continue; }

            const fd = new FormData();
            fd.append('file', fi.files[0]);
            fd.append('max_kb', document.getElementById('face-kb').value || '0');
            fd.append('width', document.getElementById('face-w').value || '0');
            fd.append('height', document.getElementById('face-h').value || '0');
            fd.append('quality', document.getElementById('face-q').value || '0');
            fd.append('device_id', dev.id);
            if (isEditContext) { fd.append('force', '1'); }

            try {
                const devRes = await fetch(`${API}/api/face/${encodeURIComponent(faceUid)}`, { method: 'POST', body: fd });
                const devResult = await devRes.json();
                if (devResult.code === 0) {
                    successCount++;
                    faceSuccessDevices.push(did);
                } else {
                    failCount++;
                    console.warn(`设备 ${dev.name} 失败: ${devResult.msg}`);
                }
            } catch (e) {
                failCount++;
                console.warn(`设备 ${dev.name} 网络错误`);
            }
        }

        if (successCount > 0 || skipCount > 0) {
            facePendingDevices = null;

            if (_pendingPersonSave) {
                const ps = _pendingPersonSave;
                _pendingPersonSave = null;
                ps.faceSuccess = successCount;
                ps.faceFail = failCount;
                ps.faceSuccessDevices = faceSuccessDevices;
                closeModal('faceModal', true);
                await _finalizeEditSave(ps);
                return;
            }

            if (currentPDevObj) { await refreshPersonList(false); } else { loadAllPersons(false); }

            let msgParts = [];
            if (successCount > 0) msgParts.push(`已下发 ${successCount} 台`);
            if (skipCount > 0) msgParts.push(`跳过 ${skipCount} 台（已有）`);
            if (offlineSkip > 0) msgParts.push(`${offlineSkip} 台离线已跳过`);
            if (failCount > 0) msgParts.push(`${failCount} 台失败`);
            const toastType = failCount > 0 || offlineSkip > 0 ? 'info' : 'success';
            toast(toastType, `人脸操作完成：${msgParts.join('，')}`);
            closeModal('faceModal');
        } else {
            toast('error', '所有设备下发均失败');
        }
    } catch (e) {
        console.error(e);
        toast('error', '网络错误');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '下发人脸';
    }
}

async function updatePersonFace() {
    const uid = document.getElementById('p-id').value;
    const fileInput = document.getElementById('p-face-update');
    const btn = document.getElementById('updateFaceBtn');
    
    if (!uid) {
        toast('error', '用户ID不能为空');
        return;
    }
    
    if (!fileInput || !fileInput.files.length) {
        toast('error', '请选择照片');
        return;
    }
    
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '更新中...';
    
    try {
        // 获取当前人员的所有设备（精确搜索，避免模糊匹配到其他用户）
        const findResp = await fetch(`${API}/api/persons?page=1&per_page=1&search=${encodeURIComponent(uid)}&search_mode=exact`);
        const findData = await findResp.json();
        if (findData.code !== 0 || !findData.data.items.length) {
            toast('error', '未找到该人员');
            return;
        }
        
        const person = findData.data.items[0];
        const devices = person.doors || [];
        
        if (devices.length === 0) {
            toast('error', '该人员没有所属设备');
            return;
        }
        
        const maxKb = document.getElementById('p-face-kb')?.value || '0';
        const width = document.getElementById('p-face-w')?.value || '0';
        const height = document.getElementById('p-face-h')?.value || '0';
        const quality = document.getElementById('p-face-q')?.value || '0';
        
        let successCount = 0;
        let failCount = 0;
        let offlineCount = 0;
        
        for (const did of devices) {
            const dev = getPersonDeviceList().find(d => d.id === did);
            if (!dev) {
                failCount++;
                continue;
            }
            if (!dev.online) {
                offlineCount++;
                continue;
            }
            
            const fd = new FormData();
            fd.append('file', fileInput.files[0]);
            fd.append('max_kb', maxKb);
            fd.append('width', width);
            fd.append('height', height);
            fd.append('quality', quality);
            fd.append('device_id', dev.id);
            
            try {
                const resp = await fetch(`${API}/api/face/${encodeURIComponent(uid)}`, {
                    method: 'PUT',
                    body: fd
                });
                const result = await resp.json();
                if (result.code === 0) {
                    successCount++;
                } else {
                    failCount++;
                    console.warn(`设备 ${dev.name} 更新失败: ${result.msg}`);
                }
            } catch (e) {
                failCount++;
                console.warn(`设备 ${dev.name} 网络错误`);
            }
        }
        
        let msgParts = [];
        if (successCount > 0) msgParts.push(`已更新 ${successCount} 台`);
        if (offlineCount > 0) msgParts.push(`${offlineCount} 台离线已跳过`);
        if (failCount > 0) msgParts.push(`${failCount} 台失败`);
        
        if (successCount > 0) {
            const toastType = failCount > 0 || offlineCount > 0 ? 'info' : 'success';
            toast(toastType, `人脸更新完成：${msgParts.join('，')}`);
            fileInput.value = '';
        } else {
            toast('error', `人脸更新失败：${msgParts.join('，')}`);
        }
    } catch (e) {
        console.error(e);
        toast('error', e?.message || '网络错误');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}
