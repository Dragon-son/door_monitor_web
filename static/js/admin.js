// ========== 管理员：用户管理模块 ==========
let assignAllDevs = [];  // 所有可用设备缓存
let assignFilteredDevs = []; // 搜索过滤后的设备
let assignSelectedDevIds = new Set();
let assignCurrentUserChannels = {};
let assignSelectedChannels = {};
let assignExpandedNvrs = new Set();  // 用户主动展开的 NVR id 集合(搜索时强制展开)
let assignReadOnly = false;

const ADMIN_PERMISSION_LABELS = {
    'page.access': '门禁控制',
    'page.persons': '人员管理',
    'page.monitor': '视频监控',
    'page.playback': '视频回放',
    'page.devices': '设备管理',
    'page.admin': '用户管理',
    'page.audit': '操作日志',
    'access.preview': '实时预览',
    'access.open_door': '远程开门',
    'access.set_door_mode': '门模式设置',
    'access.view_logs': '开门记录查看',
};

function getAdminPermissionCheckboxes() {
    return Array.from(document.querySelectorAll('#au-page-permissions input[type="checkbox"], #au-access-permissions input[type="checkbox"]'));
}

function collectAdminPermissions() {
    return getAdminPermissionCheckboxes().filter(cb => cb.checked).map(cb => cb.value);
}

function setAdminPermissionChecks(permissions) {
    const allowed = new Set(permissions || []);
    getAdminPermissionCheckboxes().forEach(cb => { cb.checked = allowed.has(cb.value); });
}

function syncAdminPermissionRoleState() {
    const role = document.getElementById('au-role')?.value || 'user';
    const isSuper = currentUsername === 'admin';
    const currentAllowed = new Set(currentPermissions || []);
    getAdminPermissionCheckboxes().forEach(cb => {
        const isSelfServiceOnly = adminEditUsername === currentUsername && currentUsername !== 'admin';
        const outsideOwnScope = !isSuper && !currentAllowed.has(cb.value);
        const disabledByRole = isSelfServiceOnly || (!isSuper && role === 'admin') || adminEditUsername === 'admin';
        cb.disabled = disabledByRole || outsideOwnScope;
        cb.closest('label')?.classList.toggle('permission-out-of-scope', outsideOwnScope || isSelfServiceOnly);
    });
}

function syncAdminDepartmentScopesVisibility() {
    const row = document.getElementById('au-department-scopes-row');
    const input = document.getElementById('au-department-scopes');
    const role = document.getElementById('au-role')?.value || 'user';
    const visible = currentUsername === 'admin' && role === 'admin';
    if (row) row.style.display = visible ? '' : 'none';
    if (input) input.disabled = !visible;
}

function parseDepartmentScopes(value) {
    return Array.from(new Set(String(value || '').split(/[，,、]/).map(s => s.trim()).filter(Boolean))).sort();
}

function renderPermissionSummary(permissions, role, username) {
    if (username === 'admin') return '<span class="badge by">全部权限</span>';
    const list = (permissions || []).filter(p => ADMIN_PERMISSION_LABELS[p]);
    if (!list.length) return '<span style="color:var(--text3);font-size:12px">无</span>';
    const main = list.slice(0, 3).map(p => ADMIN_PERMISSION_LABELS[p]).join('、');
    const more = list.length > 3 ? ` +${list.length - 3}` : '';
    return `<span title="${escapeHtml(list.map(p => ADMIN_PERMISSION_LABELS[p]).join('、'))}">${escapeHtml(main)}${more}</span>`;
}

function renderDepartmentScopes(user) {
    if (user.username === 'admin') return '<span class="badge by">全部部门</span>';
    if (user.role !== 'admin') return '<span style="color:var(--text3);font-size:12px">—</span>';
    const scopes = user.department_scopes || [];
    if (!scopes.length) return '<span style="color:#e6a817;font-size:12px">未配置</span>';
    return `<span title="${escapeHtml(scopes.join('、'))}">${escapeHtml(scopes.slice(0, 3).join('、'))}${scopes.length > 3 ? ` +${scopes.length - 3}` : ''}</span>`;
}

async function loadAdminUsers() {
    const tbody = document.getElementById('adminUserBody');
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:20px"><span class="spinner"></span> 加载中...</td></tr>`;
    const createBtn = document.getElementById('adminCreateUserBtn');
    if (createBtn) createBtn.style.display = canManageUsers() ? '' : 'none';

    try {
        const resp = await fetch(`${API}/api/admin/users`);
        const data = await resp.json();
        if (data.code !== 0) { tbody.innerHTML = `<tr><td colspan="8"><div class="empty-state"><p>${escapeHtml(data.msg)}</p></div></td></tr>`; return; }
        const users = data.data;
        if (!users.length) { tbody.innerHTML = `<tr><td colspan="8"><div class="empty-state"><p>暂无用户</p></div></td></tr>`; return; }

        // 后端已按权限返回可见用户：管理员看可管理范围，普通用户只看自己
        const isCurrentSuper = currentUsername === 'admin';
        const visibleUsers = users;

        tbody.innerHTML = visibleUsers.map(u => {
            const isTargetAdmin = u.role === 'admin';
            const isTargetSuper = u.username === 'admin';
            const isSelf = u.username === currentUsername;
            const isSelfServiceOnly = isSelf && currentUsername !== 'admin';
            // 非超管只能自助编辑自己的密码；管理员按原规则管理普通用户；超管全管
            const canEdit = isCurrentSuper || isSelfServiceOnly || (!isSelf && !isTargetAdmin && !isTargetSuper && canManageUsers());
            const canAssignDevices = isCurrentSuper || isSelf || (!isSelf && !isTargetAdmin && !isTargetSuper && canManageUsers());
            const canEditAssignDevices = isCurrentSuper || (!isSelf && !isTargetAdmin && !isTargetSuper && canManageUsers());
            const canDelete = canEditAssignDevices && !isSelf && u.username !== 'admin';

            let roleBadge;
            if (isTargetSuper) roleBadge = '<span class="badge by">👑 超级管理员</span>';
            else if (isTargetAdmin) roleBadge = '<span class="badge bb">🛡️ 管理员</span>';
            else roleBadge = '<span class="badge bgr">👤 普通用户</span>';

            return `
            <tr>
                <td><strong style="color:var(--text)">${escapeHtml(u.username)}</strong></td>
                <td>${escapeHtml(u.department || '') || '<span style="color:var(--text3);font-size:12px">—</span>'}</td>
                <td>${escapeHtml(u.name || '') || '<span style="color:var(--text3);font-size:12px">—</span>'}</td>
                <td>${roleBadge}</td>
                <td>${renderDepartmentScopes(u)}</td>
                <td>${renderPermissionSummary(u.permissions || [], u.role, u.username)}</td>
                <td><span class="mono">${Number(u.device_count || 0)} 台</span></td>
                <td><div class="dev-actions">
                    ${canAssignDevices ? `<button class="btn btn-blue btn-sm" onclick="openAssignDevModal('${escapeHtml(u.username)}', ${isSelf ? 'true' : 'false'})">📡 ${isSelf ? '查看设备' : '分配设备'}</button>` : ''}
                    ${canEdit ? `<button class="btn btn-ghost btn-sm" onclick="openAdminUserModal('${escapeHtml(u.username)}', '${u.role}')">编辑</button>` : ''}
                    ${canDelete ? `<button class="btn btn-danger btn-sm" onclick="confirmDeleteAdminUser('${escapeHtml(u.username)}')">删除</button>` : ''}
                </div></td>
            </tr>`;
        }).join('');
    } catch(e) {
        tbody.innerHTML = `<tr><td colspan="8"><div class="empty-state"><p>加载失败，请刷新</p></div></td></tr>`;
    }
}

async function openAdminUserModal(username = null, role = 'user') {
    adminEditUsername = username;
    adminEditOriginalRole = role;  // 保存原始角色，用于判断是否变更
    const isEdit = !!username;
    const isSelf = isEdit && username === currentUsername;
    const isSuper = currentUsername === 'admin';  // 判断当前是否超级管理员
    const isSelfServiceOnly = isSelf && currentUsername !== 'admin';
    document.getElementById('adminUserModalTitle').textContent = isEdit ? `编辑用户：${username}` : '新建用户';
    document.getElementById('au-username').value = username || '';
    document.getElementById('au-username').disabled = isEdit;
    const roleEl = document.getElementById('au-role');
    roleEl.value = role;
    roleEl.disabled = isSelfServiceOnly || !isSuper || isSelf; // 普通用户自助编辑时角色不可改
    roleEl.onchange = () => {
        if (!adminEditUsername) {
            setAdminPermissionChecks(roleEl.value === 'admin' ? Object.keys(ADMIN_PERMISSION_LABELS) : ['page.access', 'access.preview', 'access.open_door', 'access.view_logs']);
            if (roleEl.value === 'admin' && !document.getElementById('au-department-scopes').value.trim()) {
                document.getElementById('au-department-scopes').value = document.getElementById('au-department').value.trim();
            }
        }
        syncAdminPermissionRoleState();
        syncAdminDepartmentScopesVisibility();
    };
    document.getElementById('au-password').value = '';
    document.getElementById('au-department').value = '';
    document.getElementById('au-name').value = '';
    document.getElementById('au-department-scopes').value = '';
    document.getElementById('au-pass-hint').textContent = isEdit ? '（留空则不修改密码）' : '';
    setAdminPermissionChecks(isEdit ? [] : (role === 'admin' ? Object.keys(ADMIN_PERMISSION_LABELS) : ['page.access', 'access.preview', 'access.open_door', 'access.view_logs']));
    syncAdminPermissionRoleState();
    syncAdminDepartmentScopesVisibility();
    openModal('adminUserModal');
    if (isEdit) {
        try {
            const resp = await fetch(`${API}/api/admin/users`);
            const data = await resp.json();
            if (data.code === 0) {
                const user = (data.data || []).find(u => u.username === username);
                if (user) {
                    document.getElementById('au-department').value = user.department || '';
                    document.getElementById('au-name').value = user.name || '';
                    document.getElementById('au-department-scopes').value = (user.department_scopes || []).join(', ');
                    setAdminPermissionChecks(user.permissions || []);
                    syncAdminPermissionRoleState();
                    syncAdminDepartmentScopesVisibility();
                }
            }
        } catch(e) {}
    }
}

async function saveAdminUser() {
    const username = document.getElementById('au-username').value.trim();
    const password = document.getElementById('au-password').value;
    const department = document.getElementById('au-department').value.trim();
    const name = document.getElementById('au-name').value.trim();
    const role = document.getElementById('au-role').value;
    const departmentScopes = parseDepartmentScopes(document.getElementById('au-department-scopes').value || department);
    const permissions = collectAdminPermissions();
    const isEdit = !!adminEditUsername;
    const isSelfServiceOnly = isEdit && adminEditUsername === currentUsername && currentUsername !== 'admin';
    const btn = document.getElementById('adminUserSaveBtn');

    if (!isEdit && !username) { toast('error', '请填写用户名'); return; }
    if (!isEdit && !password) { toast('error', '请填写密码'); return; }
    if (isSelfServiceOnly && !password && !department && !name) { toast('error', '请输入新密码或修改资料'); return; }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 保存中';
    try {
        let resp, data;
        if (isEdit) {
            const body = {};
            if (isSelfServiceOnly) {
                if (password) body.password = password;
                body.name = name;
            } else {
                if (role !== adminEditOriginalRole) body.role = role;  // 仅角色变更时才发
                if (password) body.password = password;
                body.department = department;
                body.name = name;
                if (currentUsername === 'admin' && role === 'admin') body.department_scopes = departmentScopes;
                body.permissions = permissions;
            }
            resp = await fetch(`${API}/api/admin/users/${encodeURIComponent(adminEditUsername)}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
            });
            data = await resp.json();
            if (data.code === 0) { toast('success', `用户「${adminEditUsername}」已更新`); closeModal('adminUserModal'); loadAdminUsers(); }
            else toast('error', data.msg || '更新失败');
        } else {
            resp = await fetch(`${API}/api/admin/users`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password, role, department, name, department_scopes: currentUsername === 'admin' && role === 'admin' ? departmentScopes : [], permissions })
            });
            data = await resp.json();
            if (data.code === 0) { toast('success', `用户「${username}」已创建`); closeModal('adminUserModal'); loadAdminUsers(); }
            else toast('error', data.msg || '创建失败');
        }
    } catch(e) {
            toast('error', e?.message || '操作失败，请检查网络');
        }
    finally { btn.disabled = false; btn.innerHTML = '保存'; }
}

function confirmDeleteAdminUser(username) {
    setConfirm('删除用户', `确定删除用户 <span class="confirm-hi">「${escapeHtml(username)}」</span>？此操作不可恢复。`, async () => {
        try {
            const resp = await fetch(`${API}/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.code === 0) { toast('success', `用户「${username}」已删除`); loadAdminUsers(); }
            else toast('error', data.msg || '删除失败');
        } catch(e) {
            toast('error', e?.message || '操作失败，请检查网络');
        }
    });
}

async function openAssignDevModal(username, readOnly = false) {
    assignTargetUsername = username;
    assignReadOnly = readOnly;
    assignExpandedNvrs = new Set();   // 重置 NVR 折叠状态(默认全部折叠)
    document.getElementById('assignTargetUser').textContent = username;
    const listEl = document.getElementById('assignDevList');
    const saveBtn = document.getElementById('assignDevSaveBtn');
    if (saveBtn) {
        saveBtn.disabled = readOnly;
        saveBtn.style.display = readOnly ? 'none' : '';
        saveBtn.innerHTML = readOnly ? '只读查看' : '保存分配';
    }
    // 清空搜索框
    document.getElementById('assignDevSearch').value = '';
    listEl.innerHTML = '<span class="spinner"></span> 加载中…';
    openModal('assignDevModal');

    try {
        const [myDevResp, userDevResp] = await Promise.all([
            fetch(`${API}/api/devices`),
            fetch(`${API}/api/admin/users/${encodeURIComponent(username)}/devices`)
        ]);
        const myDevData = await myDevResp.json();
        const userDevData = await userDevResp.json();
        const channelResp = await fetch(`${API}/api/admin/users/${encodeURIComponent(username)}/channels`);
        const channelData = await channelResp.json();

        const myDevs = myDevData.code === 0 ? myDevData.data : [];
        const userDevIds = new Set(userDevData.code === 0 ? userDevData.data : []);
        assignCurrentUserDevIds = userDevIds;
        assignSelectedDevIds = new Set(userDevIds);
        assignCurrentUserChannels = channelData.code === 0 ? channelData.data : {};
        assignSelectedChannels = {};
        Object.entries(assignCurrentUserChannels).forEach(([did, channels]) => {
            assignSelectedChannels[String(did)] = new Set((channels || []).map(Number));
        });

        assignAllDevs = myDevs;
        assignFilteredDevs = myDevs;

        if (!myDevs.length) { listEl.innerHTML = '<p style="color:var(--text3);font-size:13px;padding:8px">您尚未添加任何设备</p>'; return; }

        renderAssignDevList();
    } catch(e) { listEl.innerHTML = '<p style="color:var(--red2);font-size:13px;padding:8px">加载失败</p>'; }
}

function renderAssignDevList() {
    const listEl = document.getElementById('assignDevList');
    if (!assignFilteredDevs.length) {
        listEl.innerHTML = '<p style="color:var(--text3);font-size:13px;padding:8px;text-align:center">无匹配设备</p>';
        return;
    }
    listEl.innerHTML = '';
    assignFilteredDevs.forEach(d => {
        const isNvr = d.device_type === 'nvr';
        if (!isNvr) {
            const label = document.createElement('label');
            label.className = 'assign-device-row';
            label.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 4px;cursor:pointer;border-bottom:1px solid var(--border)';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = d.id;
            cb.checked = assignSelectedDevIds.has(d.id);
            cb.disabled = assignReadOnly;
            cb.style.accentColor = 'var(--accent)';
            cb.onchange = () => {
                if (assignReadOnly) return;
                if (cb.checked) assignSelectedDevIds.add(d.id);
                else assignSelectedDevIds.delete(d.id);
            };
            const info = document.createElement('span');
            info.innerHTML = `<span style="color:var(--text);font-size:13px">${escapeHtml(d.name)}</span> <span class="badge bg" style="font-size:10.5px">门禁</span> <span class="mono" style="font-size:11.5px">${escapeHtml(d.ip)}</span> <span class="badge bgr" style="font-size:10.5px">${escapeHtml(d.area)}</span>`;
            label.appendChild(cb);
            label.appendChild(info);
            listEl.appendChild(label);
            return;
        }

        const row = document.createElement('div');
        row.className = 'assign-nvr-row';
        row.style.cssText = 'padding:8px 4px;border-bottom:1px solid var(--border)';
        const header = document.createElement('label');
        header.style.cssText = 'display:flex;align-items:center;gap:8px;cursor:pointer;';
        const channels = getAssignDeviceChannels(d);
        const selectedSet = getAssignSelectedChannelSet(d.id);

        // 搜索关键字
        const keyword = (document.getElementById('assignDevSearch').value || '').trim().toLowerCase();
        const searching = !!keyword;
        // 搜索时:通道名命中的子集;无命中通道(只是 name/ip 命中)→ 显示全通道
        let visibleChannels = channels;
        if (searching) {
            const matched = channels.filter(ch =>
                String(ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`).toLowerCase().includes(keyword)
            );
            if (matched.length > 0) visibleChannels = matched;
        }

        // 折叠图标
        const nvrId = String(d.id);
        const expanded = searching || assignExpandedNvrs.has(nvrId);
        const foldIcon = document.createElement('span');
        foldIcon.textContent = expanded ? '▼' : '▶';
        foldIcon.style.cssText = 'font-size:11px;color:var(--text3);width:14px;text-align:center;user-select:none;';
        foldIcon.onclick = (ev) => {
            ev.stopPropagation();
            ev.preventDefault();
            if (assignExpandedNvrs.has(nvrId)) assignExpandedNvrs.delete(nvrId);
            else assignExpandedNvrs.add(nvrId);
            renderAssignDevList();
        };

        const parent = document.createElement('input');
        parent.type = 'checkbox';
        parent.disabled = assignReadOnly;
        parent.style.accentColor = 'var(--accent)';
        updateAssignNvrParentState(parent, visibleChannels, selectedSet);
        parent.onchange = (ev) => {
            ev.stopPropagation();
            if (assignReadOnly) return;
            const set = getAssignSelectedChannelSet(d.id);
            if (searching) {
                // 搜索状态下只对命中的通道做增删,不影响其它已选
                if (parent.checked) visibleChannels.forEach(ch => set.add(Number(ch.channel_no)));
                else visibleChannels.forEach(ch => set.delete(Number(ch.channel_no)));
            } else {
                set.clear();
                if (parent.checked) channels.forEach(ch => set.add(Number(ch.channel_no)));
            }
            if (set.size) assignSelectedDevIds.add(d.id);
            else assignSelectedDevIds.delete(d.id);
            renderAssignDevList();
        };

        const title = document.createElement('span');
        title.innerHTML = `<span style="color:var(--text);font-size:13px">${escapeHtml(d.name)}</span> <span class="badge bb" style="font-size:10.5px">录像机 · ${channels.length}路</span> <span class="mono" style="font-size:11.5px">${escapeHtml(d.ip)}</span> <span class="badge bgr" style="font-size:10.5px">${escapeHtml(d.area)}</span>`;
        title.style.flex = '1';
        // 点击 title 区域切换折叠
        title.style.cursor = 'pointer';
        title.onclick = (ev) => {
            ev.stopPropagation();
            ev.preventDefault();
            if (assignExpandedNvrs.has(nvrId)) assignExpandedNvrs.delete(nvrId);
            else assignExpandedNvrs.add(nvrId);
            renderAssignDevList();
        };

        header.appendChild(foldIcon);
        header.appendChild(parent);
        header.appendChild(title);
        row.appendChild(header);

        const channelWrap = document.createElement('div');
        channelWrap.style.cssText = `display:${expanded ? 'grid' : 'none'};grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:6px;margin:8px 0 0 38px;`;
        visibleChannels.forEach(ch => {
            const label = document.createElement('label');
            label.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2);cursor:pointer;';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = ch.channel_no;
            cb.checked = selectedSet.has(Number(ch.channel_no));
            cb.disabled = assignReadOnly;
            cb.style.accentColor = 'var(--accent)';
            cb.onchange = () => {
                if (assignReadOnly) return;
                const set = getAssignSelectedChannelSet(d.id);
                if (cb.checked) set.add(Number(ch.channel_no));
                else set.delete(Number(ch.channel_no));
                if (set.size) assignSelectedDevIds.add(d.id);
                else assignSelectedDevIds.delete(d.id);
                updateAssignNvrParentState(parent, visibleChannels, set);
            };
            label.appendChild(cb);
            label.appendChild(document.createTextNode(ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`));
            channelWrap.appendChild(label);
        });
        row.appendChild(channelWrap);
        listEl.appendChild(row);
    });
}

function getAssignDeviceChannels(device) {
    // 后端 /api/devices 已按当前登录用户的通道权限过滤了 NVR.channels：
    // 只要 channels 字段存在就以它为准（哪怕是空数组），不再用 channel_count 兜底，
    // 否则当前管理员的"无权限"通道会被还原回完整列表，导致越权分配。
    if (Array.isArray(device.channels)) return device.channels;
    const count = Number(device.channel_count || 0);
    return Array.from({ length: count }, (_, i) => ({ channel_no: i, channel_name: `通道 ${i + 1}` }));
}

function getAssignSelectedChannelSet(deviceId) {
    const key = String(deviceId);
    if (!assignSelectedChannels[key]) assignSelectedChannels[key] = new Set();
    return assignSelectedChannels[key];
}

function updateAssignNvrParentState(parent, channels, selectedSet) {
    const count = channels.length;
    const selectedCount = channels.filter(ch => selectedSet.has(Number(ch.channel_no))).length;
    parent.checked = count > 0 && selectedCount === count;
    parent.indeterminate = selectedCount > 0 && selectedCount < count;
}

function filterAssignDevList() {
    const keyword = document.getElementById('assignDevSearch').value.trim().toLowerCase();
    if (!keyword) {
        assignFilteredDevs = assignAllDevs;
    } else {
        assignFilteredDevs = assignAllDevs.filter(d =>
            d.name.toLowerCase().includes(keyword) ||
            d.ip.toLowerCase().includes(keyword) ||
            getAssignDeviceChannels(d).some(ch => String(ch.channel_name || '').toLowerCase().includes(keyword))
        );
    }
    renderAssignDevList();
}

async function saveAssignDevices() {
    if (!assignTargetUsername || assignReadOnly) return;
    const checked = [];
    const channelPermissions = {};
    assignAllDevs.forEach(d => {
        const did = Number(d.id);
        if (d.device_type === 'nvr') {
            const channels = Array.from(getAssignSelectedChannelSet(did)).sort((a, b) => a - b);
            if (channels.length > 0) {
                checked.push(did);
                channelPermissions[String(did)] = channels;
            }
        } else if (assignSelectedDevIds.has(did)) {
            checked.push(did);
        }
    });

    const all = assignAllDevs.map(d => Number(d.id));
    const toRemove = all.filter(id => !checked.includes(id) && assignCurrentUserDevIds.has(id));

    const btn = document.getElementById('assignDevSaveBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 保存中';

    try {
        if (checked.length > 0) {
            const resp = await fetch(`${API}/api/admin/users/${encodeURIComponent(assignTargetUsername)}/devices`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_ids: checked, channel_permissions: channelPermissions })
            });
            const data = await resp.json();
            if (data.code !== 0) { toast('error', '分配失败：' + data.msg); return; }
        }

        const removeErrors = [];
        for (const did of toRemove) {
            try {
                const resp = await fetch(`${API}/api/admin/users/${encodeURIComponent(assignTargetUsername)}/devices/${did}`, { method: 'DELETE' });
                const data = await resp.json();
                if (data.code !== 0) removeErrors.push(`设备${did}：${data.msg || '删除失败'}`);
            } catch (e) {
                removeErrors.push(`设备${did}：网络错误`);
            }
        }

        if (removeErrors.length > 0) {
            toast('error', `部分设备移除失败：${removeErrors.join('；')}`);
        } else {
            toast('success', `设备分配已更新，共分配 ${checked.length} 台`);
        }
        closeModal('assignDevModal');
        loadAdminUsers();
    } catch(e) {
            toast('error', e?.message || '操作失败，请检查网络');
        }
    finally {
        btn.disabled = false;
        btn.innerHTML = '保存分配';
        assignCurrentUserDevIds = new Set();
        assignReadOnly = false;
    }
}
