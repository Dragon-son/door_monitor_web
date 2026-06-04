// ========== 多通道预览模块 (1/4/9/16 格网格) ==========
// 基于 monitor.js 改造，支持多通道同时预览

let multiPreviewDevices = [];
let _multiPreviewLoaded = false;
let _multiPreviewSearchKeyword = '';
let _multiPreviewExpandedDevices = new Set();
let _multiPreviewSelectedDevice = null;  // 当前选中的 NVR 设备
let _multiPreviewAllChannels = [];       // 当前设备的所有通道列表
let _multiPreviewSelectedChannels = [];  // 已选中的通道列表 [{device, channel}]
let _multiPreviewCurrentPage = 0;        // 当前页码（0-indexed）

// 网格运行态
let _multiPreviewGrid = [];   // [{ ws, player, canvas, channelInfo, status }, ...]
let _multiPreviewGridLayout = 1;  // 当前布局：1/4/9/16
let _multiPreviewPingTimers = []; // 每个格子的心跳定时器

// 关闭 multi-preview tab 时停止所有流
(function _registerMultiPreviewTabClose() {
    if (typeof onTabClose === 'function') {
        onTabClose('multi-preview', stopMultiPreviewAll);
    }
})();

window.addEventListener('pagehide', () => {
    try { stopMultiPreviewAll(); } catch (e) {}
});

// ---------- 加载设备 ----------
async function loadMultiPreviewDevices() {
    const sidebar = document.getElementById('multi-sidebar');
    if (sidebar && !_multiPreviewLoaded) {
        sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><div class="spinner"></div><p>加载中…</p></div>`;
    }
    try {
        const resp = await fetch(`${API}/api/monitor/devices`);
        const data = await resp.json();
        if (data.code === 0) {
            multiPreviewDevices = data.data || [];
            _multiPreviewLoaded = true;
            renderMultiPreviewSidebar();
        } else {
            if (sidebar) sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><p>${escapeHtml(data.msg || '加载失败')}</p></div>`;
        }
    } catch (e) {
        if (sidebar) sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><p>无法连接服务器</p></div>`;
    }
}

function filterMultiPreviewChannels(kw) {
    _multiPreviewSearchKeyword = kw || '';
    renderMultiPreviewSidebar();
}

function _getFilteredMultiPreviewDevices() {
    const kw = (_multiPreviewSearchKeyword || '').trim().toLowerCase();
    if (!kw) return multiPreviewDevices;
    const result = [];
    multiPreviewDevices.forEach(dev => {
        const matched = (dev.channels || []).filter(ch => {
            const name = (ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`).toLowerCase();
            return name.includes(kw);
        });
        if (matched.length) result.push({ ...dev, channels: matched });
    });
    return result;
}

// ---------- 侧边栏树渲染 ----------
function renderMultiPreviewSidebar() {
    const sidebar = document.getElementById('multi-sidebar');
    if (!sidebar) return;
    const devices = _getFilteredMultiPreviewDevices();
    if (!devices.length) {
        const txt = _multiPreviewSearchKeyword
            ? `未找到匹配 "${escapeHtml(_multiPreviewSearchKeyword)}" 的通道`
            : '暂无可用监控通道';
        sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><div class="empty-icon">🎥</div><p>${txt}</p></div>`;
        return;
    }
    const searching = !!_multiPreviewSearchKeyword.trim();
    let html = '';
    devices.forEach(dev => {
        const online = dev.online === true;
        const expanded = searching || _multiPreviewExpandedDevices.has(String(dev.id));
        const icon = expanded ? '▼' : '▶';
        const channels = dev.channels || [];
        const onlineChannels = online
            ? channels.filter(ch => ch.online !== false).length
            : 0;
html += `<div class="vp-tree-nvr" data-device-id="${dev.id}">
            <div class="vp-tree-nvr-title ${online ? '' : 'offline'}">
                <span class="vp-tree-fold" onclick="event.stopPropagation();toggleMultiPreviewNvr('${dev.id}')">${icon}</span>
                <span onclick="autoFillMultiPreview(${dev.id})">🎥</span>
                <span style="flex:1;cursor:pointer" onclick="autoFillMultiPreview(${dev.id})">${escapeHtml(dev.name)}</span>
                <span class="vp-tree-count">${onlineChannels}/${channels.length}</span>
                ${online ? '' : '<span class="badge br" style="font-size:9.5px;padding:1px 5px;">离线</span>'}
            </div>
            <div class="vp-tree-children" style="display:${expanded ? 'block' : 'none'}">`;
        channels.forEach((ch, chIdx) => {
            const channelName = ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`;
            const chOffline = (ch.online === false);
            const klass = `vp-tree-channel ${(online && !chOffline) ? '' : 'offline'}`;
            const offlineBadge = (online && chOffline)
                ? '<span class="vp-channel-offline-dot" title="摄像头离线">●</span>'
                : '';
            // 检查该通道是否正在预览
            const isPreviewing = _multiPreviewSelectedChannels.some(
                item => item.device.id === dev.id && Number(item.channel.channel_no) === Number(ch.channel_no)
            );
            const previewDot = isPreviewing
                ? '<span class="vp-channel-preview-dot" title="正在预览">●</span>'
                : '';
            const click = online ? `onclick="selectMultiPreviewChannel(${dev.id}, ${ch.channel_no})"` : '';
            const dblclick = online ? `ondblclick="autoFillMultiPreview(${dev.id})"` : '';
            html += `<div class="${klass}" ${click} ${dblclick}>${offlineBadge}${previewDot}${escapeHtml(channelName)}</div>`;
        });
        html += `</div></div>`;
    });
    sidebar.innerHTML = html;
}

function toggleMultiPreviewNvr(deviceId) {
    const id = String(deviceId);
    if (_multiPreviewExpandedDevices.has(id)) _multiPreviewExpandedDevices.delete(id);
    else _multiPreviewExpandedDevices.add(id);
    renderMultiPreviewSidebar();
}

// ---------- 双击 NVR 自动填充 ----------
function autoFillMultiPreview(deviceId) {
    const dev = multiPreviewDevices.find(d => d.id === deviceId);
    if (!dev || dev.online !== true) return;

    // 切换设备：清空之前的通道
    if (_multiPreviewSelectedDevice && _multiPreviewSelectedDevice.id !== deviceId) {
        _multiPreviewSelectedChannels = [];
    }
    _multiPreviewSelectedDevice = dev;

    // 获取该设备所有在线通道
    _multiPreviewAllChannels = (dev.channels || [])
        .filter(ch => ch.online !== false)
        .map(ch => ({ device: dev, channel: ch }));

    // 自动填满当前布局的格子
    _multiPreviewCurrentPage = 0;
    _fillMultiPreviewGrid();
    renderMultiPreviewSidebar();
}

// ---------- 选通道 ----------
function selectMultiPreviewChannel(deviceId, channelNo) {
    const dev = multiPreviewDevices.find(d => d.id === deviceId);
    if (!dev || dev.online !== true) return;
    const ch = (dev.channels || []).find(c => Number(c.channel_no) === Number(channelNo));
    if (!ch) return;

    // 切换设备：清空之前的通道
    if (_multiPreviewSelectedDevice && _multiPreviewSelectedDevice.id !== deviceId) {
        _multiPreviewSelectedChannels = [];
        _multiPreviewAllChannels = [];
    }
    _multiPreviewSelectedDevice = dev;

    // 如果同一通道已选中，先移除（取消选中）
    const existingIdx = _multiPreviewSelectedChannels.findIndex(
        item => item.device.id === deviceId && Number(item.channel.channel_no) === Number(channelNo)
    );
    if (existingIdx >= 0) {
        _multiPreviewSelectedChannels.splice(existingIdx, 1);
    } else {
        // 添加新通道
        _multiPreviewSelectedChannels.push({ device: dev, channel: ch });
        // 同步更新 allChannels
        _multiPreviewAllChannels = _multiPreviewSelectedChannels.slice();
    }

    // 重置翻页
    _multiPreviewCurrentPage = 0;
    renderMultiPreviewSidebar();
    _fillMultiPreviewGrid();
}

// ---------- 填充网格（带翻页） ----------
function _fillMultiPreviewGrid() {
    const totalSlots = _multiPreviewGridLayout;
    const allChannels = _multiPreviewAllChannels;
    const totalPages = Math.max(1, Math.ceil(allChannels.length / totalSlots));

    // 确保当前页在有效范围内
    if (_multiPreviewCurrentPage >= totalPages) {
        _multiPreviewCurrentPage = totalPages - 1;
    }
    if (_multiPreviewCurrentPage < 0) {
        _multiPreviewCurrentPage = 0;
    }

    // 计算当前页的通道范围
    const startIdx = _multiPreviewCurrentPage * totalSlots;
    const endIdx = Math.min(startIdx + totalSlots, allChannels.length);
    const pageChannelsRaw = allChannels.slice(startIdx, endIdx);

    // 如果通道数少于格子数，用空位填充
    const pageChannels = pageChannelsRaw.concat(Array(totalSlots - pageChannelsRaw.length).fill(null));

    // 计算当前页的通道集合（用于判断哪些需要停止）
    const currentPageSet = new Set();
    for (let i = startIdx; i < endIdx; i++) {
        const ch = allChannels[i];
        currentPageSet.add(`${ch.device.id}:${Number(ch.channel.channel_no)}`);
    }

    _renderMultiPreviewGrid(pageChannels, totalPages, currentPageSet);
}

// ---------- 打印网格状态（调试用） ----------
function _dumpMultiGridState() {
    const info = _multiPreviewGrid.map((cell, i) => {
        if (!cell) return `[${i}]: null`;
        return `[${i}]: ${cell.channelInfo.device.id}:${cell.channelInfo.channelNo} ws=${cell.ws ? 'open' : 'null'} player=${cell.player ? 'ok' : 'null'}`;
    }).join(' | ');
    console.log('[multi-preview] grid state:', info);
}

// ---------- 渲染网格（DOM 原地更新，避免 innerHTML 销毁 canvas） ----------
function _renderMultiPreviewGrid(channels, totalPages, currentPageSet) {
    const gridEl = document.getElementById('multiPreviewGrid');
    if (!gridEl) return;

    const cols = Math.sqrt(_multiPreviewGridLayout);
    gridEl.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    gridEl.style.gridTemplateRows = `repeat(${cols}, 1fr)`;

    // 1️⃣ 移除多余的 DOM 元素（超出当前格子数的）
    while (gridEl.children.length > channels.length) {
        const child = gridEl.lastElementChild;
        if (child && child.classList.contains('multi-preview-pager')) break; // 保留翻页控件
        gridEl.removeChild(child);
    }

    // 2️⃣ 为每个通道更新或创建 cell DOM，**保留已有 canvas**
    channels.forEach((item, idx) => {
        const existingBtn = document.querySelector(`.multi-preview-cell[data-index="${idx}"]`);
        if (item === null) {
            // 空位：先停止旧播放器，再替换 DOM
            if (_multiPreviewGrid[idx]) {
                _stopMultiPreviewCell(idx);
            }
            if (existingBtn) {
                existingBtn.className = 'multi-preview-cell empty';
                // 保留 canvas 元素，只更新内容
                let body = existingBtn.querySelector('.multi-preview-cell-body');
                if (!body) {
                    body = existingBtn.querySelector('.multi-preview-cell-empty');
                    if (body) {
                        body.textContent = '空闲';
                    } else {
                        existingBtn.innerHTML = '<div class="multi-preview-cell-empty">空闲</div>';
                    }
                } else {
                    // 有 canvas 保留，只更新状态
                    let statusEl = body.querySelector('.multi-preview-cell-status');
                    if (statusEl) statusEl.textContent = '';
                    // 隐藏 canvas
                    let canvas = body.querySelector('canvas');
                    if (canvas) canvas.style.display = 'none';
                }
                existingBtn.removeAttribute('data-device-id');
                existingBtn.removeAttribute('data-channel-no');
            } else {
                const div = document.createElement('div');
                div.className = 'multi-preview-cell empty';
                div.innerHTML = '<div class="multi-preview-cell-empty">空闲</div>';
                gridEl.appendChild(div);
            }
            return;
        }

        const { device, channel } = item;
        const channelNo = Number(channel.channel_no);
        const channelName = channel.channel_name || `通道 ${channelNo + 1}`;

        if (existingBtn && existingBtn.dataset.deviceId === String(device.id) && Number(existingBtn.dataset.channelNo) === channelNo) {
            // ✅ 通道没变：保留 DOM，只更新状态文本
            const statusEl = document.getElementById(`multi-cell-status-${idx}`);
            if (statusEl && statusEl.textContent !== '直播中' && statusEl.textContent !== '连接中…') {
                statusEl.textContent = '直播中';
            }
        } else {
            // 通道变了（或新的 cell）：重建该 cell 的 DOM
            // 先停止旧播放器
            if (_multiPreviewGrid[idx]) {
                _stopMultiPreviewCell(idx);
            }

            const div = document.createElement('div');
            div.className = 'multi-preview-cell';
            div.dataset.index = idx;
            div.dataset.deviceId = device.id;
            div.dataset.channelNo = channelNo;
            div.innerHTML = `
                <div class="multi-preview-cell-header">
                    <span class="multi-preview-cell-title">${escapeHtml(device.name)} · ${escapeHtml(channelName)}</span>
                    <button class="multi-preview-cell-close" onclick="removeMultiPreviewChannel(${device.id}, ${channelNo})" title="移除">✕</button>
                </div>
                <div class="multi-preview-cell-body">
                    <canvas id="multi-cell-canvas-${idx}"></canvas>
                    <div class="multi-preview-cell-status" id="multi-cell-status-${idx}">连接中…</div>
                </div>`;

            if (existingBtn) {
                // 替换旧 cell
                gridEl.replaceChild(div, existingBtn);
            } else {
                // 追加新 cell
                gridEl.appendChild(div);
            }

            // 启动播放器
            _startMultiPreviewCell(idx, device, channel);
        }
    });

    // 3️⃣ 停止不在当前页的格子（翻页/布局切换时清理）
    for (let i = 0; i < _multiPreviewGrid.length; i++) {
        if (_multiPreviewGrid[i]) {
            const cell = _multiPreviewGrid[i];
            const key = `${cell.channelInfo.device.id}:${cell.channelInfo.channelNo}`;
            if (!currentPageSet.has(key)) {
                _stopMultiPreviewCell(i);
            }
        }
    }

    // 4️⃣ 渲染翻页控件
    _renderMultiPreviewPagination(totalPages);

    _updateMultiPreviewStatus();
}

// ---------- 翻页控件 ----------
function _renderMultiPreviewPagination(totalPages) {
    const gridEl = document.getElementById('multiPreviewGrid');
    if (!gridEl) return;

    // 移除旧的翻页控件
    const oldPager = gridEl.querySelector('.multi-preview-pager');
    if (oldPager) oldPager.remove();

    if (totalPages <= 1) return;

    const pager = document.createElement('div');
    pager.className = 'multi-preview-pager';
    pager.innerHTML = `
        <button class="pager-btn" onclick="prevMultiPreviewPage()" ${_multiPreviewCurrentPage === 0 ? 'disabled' : ''}>◀ 上一页</button>
        <span class="pager-info">第 ${_multiPreviewCurrentPage + 1} / ${totalPages} 页</span>
        <button class="pager-btn" onclick="nextMultiPreviewPage()" ${_multiPreviewCurrentPage >= totalPages - 1 ? 'disabled' : ''}>下一页 ▶</button>
    `;
    gridEl.appendChild(pager);
}

function prevMultiPreviewPage() {
    if (_multiPreviewCurrentPage > 0) {
        _multiPreviewCurrentPage--;
        _fillMultiPreviewGrid();
    }
}

function nextMultiPreviewPage() {
    const totalPages = Math.max(1, Math.ceil(_multiPreviewAllChannels.length / _multiPreviewGridLayout));
    if (_multiPreviewCurrentPage < totalPages - 1) {
        _multiPreviewCurrentPage++;
        _fillMultiPreviewGrid();
    } else if (_multiPreviewCurrentPage >= totalPages) {
        // 通道数减少导致当前页超出范围，回到最后一页
        _multiPreviewCurrentPage = Math.max(0, totalPages - 1);
        _fillMultiPreviewGrid();
    }
}

// ---------- 启动单个格子 ----------
function _startMultiPreviewCell(idx, device, channel) {
    const channelNo = Number(channel.channel_no);
    const canvas = document.getElementById(`multi-cell-canvas-${idx}`);
    const statusEl = document.getElementById(`multi-cell-status-${idx}`);

    // 如果该索引已有相同通道的预览，跳过
    if (_multiPreviewGrid[idx]) {
        const existing = _multiPreviewGrid[idx];
        if (existing.channelInfo.device.id === device.id && existing.channelInfo.channelNo === channelNo) {
            return; // 通道相同，不需要重建
        }
        // 通道不同，停止旧的
        _stopMultiPreviewCell(idx);
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wantH264 = _useH264ForMultiPreview();
    const wsUrl = `${protocol}//${location.host}/ws/monitor/preview/${device.id}/${channelNo}?stream=main&codec=${wantH264 ? 'h264' : 'mpeg1'}`;

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';

    _multiPreviewGrid[idx] = { ws, player: null, canvas, statusEl, channelInfo: { device, channelNo } };

    ws.onopen = () => {
        if (statusEl) statusEl.textContent = '连接中…';
    };

    ws.addEventListener('message', function onFirst(ev) {
        if (typeof ev.data === 'string') return;
        ws.removeEventListener('message', onFirst);
        if (statusEl) statusEl.textContent = '直播中';
        const pingTimer = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 20000);
        _multiPreviewPingTimers[idx] = pingTimer;
    });

    ws.onerror = () => {
        if (statusEl) statusEl.textContent = '连接错误';
    };

    ws.onclose = () => {
        if (statusEl) statusEl.textContent = '已断开';
        if (_multiPreviewPingTimers[idx]) {
            clearInterval(_multiPreviewPingTimers[idx]);
            _multiPreviewPingTimers[idx] = null;
        }
    };

    if (wantH264) {
        try {
            const player = new H264Player(canvas);
            player.init(ws);
            _multiPreviewGrid[idx].player = player;
        } catch (e) {
            console.error('[multi-preview] H264Player 初始化失败', e);
            _stopMultiPreviewCell(idx);
        }
    } else {
        _multiPreviewGrid[idx].player = new jsmpeg(ws, { canvas, autoplay: true, loop: false });
    }
}

function _stopMultiPreviewCell(idx) {
    if (_multiPreviewGrid[idx]) {
        const cell = _multiPreviewGrid[idx];
        if (cell.player) {
            try {
                if (typeof cell.player.destroy === 'function') cell.player.destroy();
                else if (typeof cell.player.stop === 'function') cell.player.stop();
            } catch (e) {}
            cell.player = null;
        }
        if (cell.ws) {
            try { cell.ws.close(); } catch (e) {}
            cell.ws = null;
        }
        if (_multiPreviewPingTimers[idx]) {
            clearInterval(_multiPreviewPingTimers[idx]);
            _multiPreviewPingTimers[idx] = null;
        }
        // 不尝试清理 canvas，因为 DOM 可能已被 innerHTML 清空，引用已失效
        _multiPreviewGrid[idx] = null;
    }
}

// ---------- 移除单个通道 ----------
function removeMultiPreviewChannel(deviceId, channelNo) {
    const idx = _multiPreviewSelectedChannels.findIndex(
        item => item && item.device.id === deviceId && Number(item.channel.channel_no) === Number(channelNo)
    );
    if (idx === -1) return;

    // 从选中列表中移除
    _multiPreviewSelectedChannels.splice(idx, 1);
    // 同步更新 allChannels
    _multiPreviewAllChannels = _multiPreviewSelectedChannels.slice();

    // 找到该通道在 _multiPreviewGrid 中的索引并停止
    const gridIdx = _multiPreviewGrid.findIndex(
        cell => cell && cell.channelInfo.device.id === deviceId && cell.channelInfo.channelNo === Number(channelNo)
    );
    if (gridIdx >= 0) {
        _stopMultiPreviewCell(gridIdx);
    }

    // 重新填充网格
    _fillMultiPreviewGrid();
    renderMultiPreviewSidebar();
}

// ---------- 布局切换 ----------
function switchMultiLayout(cols) {
    const layout = cols * cols;
    if (layout === _multiPreviewGridLayout) return;
    _multiPreviewGridLayout = layout;

    // 更新按钮状态
    const switchEl = document.getElementById('multiPreviewLayoutSwitch');
    if (switchEl) {
        switchEl.querySelectorAll('.layout-btn').forEach(btn => {
            btn.classList.toggle('active', Number(btn.dataset.cols) === cols);
        });
    }

    // 重置翻页
    _multiPreviewCurrentPage = 0;

    // 重新填充网格（保留当前选中的通道）
    _fillMultiPreviewGrid();
}

// ---------- 停止所有 ----------
function stopMultiPreviewAll() {
    for (let i = 0; i < _multiPreviewGrid.length; i++) {
        _stopMultiPreviewCell(i);
    }
    _multiPreviewGrid = [];
    _multiPreviewSelectedChannels = [];
    _multiPreviewAllChannels = [];
    _multiPreviewSelectedDevice = null;
    _multiPreviewGridLayout = 1;
    _multiPreviewCurrentPage = 0;
    _multiPreviewPingTimers = [];

    // 重置布局按钮
    const switchEl = document.getElementById('multiPreviewLayoutSwitch');
    if (switchEl) {
        switchEl.querySelectorAll('.layout-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.cols === '1');
        });
    }

    // 移除翻页控件
    const gridEl = document.getElementById('multiPreviewGrid');
    if (gridEl) {
        const pager = gridEl.querySelector('.multi-preview-pager');
        if (pager) pager.remove();
    }

    _updateMultiPreviewStatus();
}

// ---------- 状态更新 ----------
function _updateMultiPreviewStatus() {
    const el = document.getElementById('multiPreviewStatus');
    if (!el) return;
    const activeCount = _multiPreviewSelectedChannels.length;
    const totalPages = Math.max(1, Math.ceil(activeCount / _multiPreviewGridLayout));
    if (activeCount === 0) {
        el.textContent = '请从左侧选择通道（单击选中，双击自动填满）';
    } else if (totalPages > 1) {
        el.textContent = `${activeCount} 个通道 · 第 ${_multiPreviewCurrentPage + 1} / ${totalPages} 页`;
    } else {
        el.textContent = `${activeCount} 个通道预览中`;
    }
}

// ---------- H.264 检测 ----------
function _useH264ForMultiPreview() {
    return typeof H264Player !== 'undefined'
        && H264Player.isSupported()
        && (window.isSecureContext === true);
}

// ---------- 全屏 ----------
function toggleMultiPreviewFullscreen() {
    const container = document.getElementById('multi-preview-container');
    if (!container) return;
    if (!document.fullscreenElement) {
        container.requestFullscreen().catch(err => {
            console.error('全屏失败:', err);
        });
    } else {
        document.exitFullscreen();
    }
}
