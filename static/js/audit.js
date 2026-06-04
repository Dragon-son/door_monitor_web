// ========== 审计日志查询 ==========
let auditCurrentPage = 1;
let auditTotalPages = 1;

function auditEscapeAttr(value) {
    return escapeHtml(String(value ?? ''));
}

function auditFindDeviceByEndpoint(ip, port) {
    if (!ip || !Array.isArray(devices)) return null;
    const matches = devices.filter(d => String(d.ip || '') === String(ip));
    if (!matches.length) return null;
    if (port) {
        const portText = String(port);
        const exact = matches.find(d => String(d.port || 37777) === portText);
        if (exact) return exact;
    }
    return matches[0];
}

function auditFindDeviceById(deviceId) {
    if (!Array.isArray(devices)) return null;
    return devices.find(d => String(d.id) === String(deviceId)) || null;
}

function auditRenderDeviceTarget(rawTarget, dev, mainText) {
    const endpoint = `${dev.ip}${dev.port ? `:${dev.port}` : ''}`;
    return `<div title="${auditEscapeAttr(rawTarget)}" style="line-height:1.25;">
        <div style="font-size:12px;color:var(--text);font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(mainText || dev.name || rawTarget)}</div>
        <div style="font-size:11px;color:var(--text3);font-family:monospace;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(endpoint)}</div>
    </div>`;
}

function auditRenderTarget(item) {
    const rawTarget = String(item.target ?? '').trim();
    if (!rawTarget) {
        return '<span style="color:var(--text3);">—</span>';
    }

    const deviceIdMatch = rawTarget.match(/\bdevice_id=(\d+)\b/);
    if (deviceIdMatch) {
        const dev = auditFindDeviceById(deviceIdMatch[1]);
        if (dev) {
            const mainText = rawTarget.replace(deviceIdMatch[0], dev.name || deviceIdMatch[0]).trim();
            return auditRenderDeviceTarget(rawTarget, dev, mainText);
        }
    }

    const endpointMatch = rawTarget.match(/(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?/);
    if (!endpointMatch) {
        return `<span title="${auditEscapeAttr(rawTarget)}">${escapeHtml(rawTarget)}</span>`;
    }

    const ip = endpointMatch[1];
    const port = endpointMatch[2] || '';
    const dev = auditFindDeviceByEndpoint(ip, port);
    if (!dev) {
        return `<span title="${auditEscapeAttr(rawTarget)}">${escapeHtml(rawTarget)}</span>`;
    }

    const before = rawTarget.slice(0, endpointMatch.index).replace(/@$/, '').trim();
    const after = rawTarget.slice(endpointMatch.index + endpointMatch[0].length).replace(/^@/, '').trim();
    const doorName = dev.name || rawTarget;
    const mainText = [before, doorName, after].filter(Boolean).join(' @ ');
    return auditRenderDeviceTarget(rawTarget, dev, mainText);
}

async function loadAuditLogs(page) {
    auditCurrentPage = page || 1;
    if ((!Array.isArray(devices) || devices.length === 0) && typeof loadDevicesFromServer === 'function') {
        try { await loadDevicesFromServer(); } catch (e) { console.warn('[audit] 加载设备列表失败', e); }
    }
    const action = document.getElementById('auditActionFilter').value;
    const username = document.getElementById('auditUsernameFilter').value.trim();
    const startDate = document.getElementById('auditStartDate').value;
    const endDate = document.getElementById('auditEndDate').value;

    let params = `?page=${auditCurrentPage}&page_size=50`;
    if (action) params += `&action=${encodeURIComponent(action)}`;
    if (username) params += `&username=${encodeURIComponent(username)}`;
    if (startDate) params += `&start_time=${encodeURIComponent(startDate + 'T00:00:00')}`;
    if (endDate) params += `&end_time=${encodeURIComponent(endDate + 'T23:59:59')}`;

    try {
        const resp = await fetch(`${API}/api/audit_logs${params}`);
        const data = await resp.json();
        if (data.code !== 0) {
            toast('error', data.msg || '获取审计日志失败');
            return;
        }
        renderAuditLogTable(data.data);
        auditCurrentPage = data.data.page || 1;
        if (data.data.limited) {
            toast('warn', `审计日志较多，仅扫描最近 ${data.data.scan_limit} 条，请缩小日期范围`);
        }
    } catch (e) {
        toast('error', '网络错误');
    }
}

function renderAuditLogTable(data) {
    const items = data.items || [];
    const total = data.total || 0;
    const page = data.page || 1;
    const pageSize = data.page_size || 50;
    auditTotalPages = Math.max(1, Math.ceil(total / pageSize));

    const tbody = document.getElementById('auditLogBody');
    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text3);padding:40px;">暂无操作日志</td></tr>';
    } else {
        tbody.innerHTML = items.map(item => {
            const ts = (item.timestamp || '').slice(0, 19).replace('T', ' ');
            const actionLabel = {
                open_door: '🚪 开门', door_mode: '🚪 门模式',
                login: '🔑 登录', logout: '🔑 登出', register: '👤 注册',
                import_persons: '📥 同步人员', delete_person: '🗑️ 删除人员', update_person: '✏️ 更新人员',
                remove_person_device: '🔗 移除设备',
                add_face: '📸 上传人脸', batch_import: '📦 批量导入',
                add_device: '📟 添加设备', update_device: '📟 修改设备', delete_device: '📟 删除设备',
                add_area: '🏠 添加区域', delete_area: '🏠 删除区域',
                admin_create_user: '👤 创建用户', admin_update_user: '👤 修改用户', admin_update_user_permissions: '🔐 修改用户权限', admin_delete_user: '👤 删除用户',
                admin_assign_devices: '📟 分配设备', admin_remove_user_device: '📟 移除设备',
                device_add_user: '🛠️ 添加人员', device_update_user: '🛠️ 修改人员', device_delete_user: '🛠️ 删除人员',
                device_freeze_user: '❄️ 冻结', device_unfreeze_user: '🔥 解冻', device_update_validity: '📅 更新有效期'
            }[item.action] || escapeHtml(item.action || '');

            const resultBadge = item.result === 'success'
                ? '<span style="color:var(--accent);font-weight:500;">✅ 成功</span>'
                : `<span style="color:var(--red2);font-weight:500;">❌ 失败</span>`;

            const detail = item.detail ? String(item.detail) : '';
            const error = item.error ? String(item.error) : '';
            let detailStr = detail ? (detail.length > 60 ? detail.slice(0, 60) + '…' : detail) : '';
            let errorStr = error ? (error.length > 60 ? error.slice(0, 60) + '…' : error) : '';
            let detailHtml = detailStr
                ? `<span title="${auditEscapeAttr(detail)}">${escapeHtml(detailStr)}</span>`
                : '';
            if (errorStr) {
                detailHtml += `<span style="color:var(--red2);display:block;font-size:11px;" title="${auditEscapeAttr(error)}">⚠️ ${escapeHtml(errorStr)}</span>`;
            }
            if (!detailHtml) detailHtml = '<span style="color:var(--text3);">—</span>';
            const targetHtml = auditRenderTarget(item);

            return `<tr>
                <td style="color:var(--text3);font-size:11px;">${escapeHtml(String(item.id ?? ''))}</td>
                <td style="font-size:12px;white-space:nowrap;">${escapeHtml(ts)}</td>
                <td>${escapeHtml(item.username || '—')}</td>
                <td style="font-family:monospace;font-size:12px;">${escapeHtml(item.ip || '—')}</td>
                <td style="font-size:13px;">${actionLabel}</td>
                <td style="font-size:12px;max-width:220px;overflow:hidden;text-overflow:ellipsis;">${targetHtml}</td>
                <td>${resultBadge}</td>
                <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;">${detailHtml}</td>
            </tr>`;
        }).join('');
    }

    document.getElementById('auditTotal').textContent = total;
    document.getElementById('auditCurrentPage').textContent = page;
    document.getElementById('auditTotalPages').textContent = auditTotalPages;
    document.getElementById('auditPrevBtn').disabled = page <= 1;
    document.getElementById('auditNextBtn').disabled = page >= auditTotalPages;
}

function auditChangePage(delta) {
    const newPage = auditCurrentPage + delta;
    if (newPage < 1 || newPage > auditTotalPages) return;
    loadAuditLogs(newPage);
}

function auditJumpToPage() {
    const input = document.getElementById('auditJumpPage');
    const page = parseInt(input.value, 10);
    if (page >= 1 && page <= auditTotalPages) {
        loadAuditLogs(page);
        input.value = '';
        return;
    }
    toast('error', '页码超出范围');
    input.value = '';
}

function clearAuditFilters() {
    document.getElementById('auditActionFilter').value = '';
    document.getElementById('auditUsernameFilter').value = '';
    document.getElementById('auditStartDate').value = '';
    document.getElementById('auditEndDate').value = '';
    loadAuditLogs(1);
}
