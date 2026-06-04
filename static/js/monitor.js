// ========== 视频监控模块 (sidebar + canvas 布局) ==========

let monitorDevices = [];
let _monitorLoaded = false;
let _monitorSearchKeyword = '';
let _monitorExpandedDevices = new Set();
let _monitorSelected = null;   // {device, channel}

// 播放运行态
let _monitorWs = null;
let _monitorPlayer = null;
let _monitorPingTimer = null;
let _monitorStreamToken = 0;

// 关闭 monitor tab 时停止流
(function _registerMonitorTabClose() {
    if (typeof onTabClose === 'function') {
        onTabClose('monitor', stopMonitorStream);
    }
})();

// 浏览器标签页关闭/刷新/进 bfcache 前主动断开 WS,
// 避免后端预览会话(SDK realplay + ffmpeg)残留到 TCP 超时才回收。
window.addEventListener('pagehide', () => {
    try { stopMonitorStream(); } catch (e) {}
});

// ---------- 加载设备 ----------
async function loadMonitorDevices() {
    const sidebar = document.getElementById('monitor-sidebar');
    if (sidebar && !_monitorLoaded) {
        sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><div class="spinner"></div><p>加载中…</p></div>`;
    }
    try {
        const resp = await fetch(`${API}/api/monitor/devices`);
        const data = await resp.json();
        if (data.code === 0) {
            monitorDevices = data.data || [];
            _monitorLoaded = true;
            _validateMonitorSelected();
            renderMonitorSidebar();
            refreshMonitorDeviceStatus();
        } else {
            if (sidebar) sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><p>${escapeHtml(data.msg || '加载失败')}</p></div>`;
        }
    } catch (e) {
        if (sidebar) sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><p>无法连接服务器</p></div>`;
    }
}

async function refreshMonitorDeviceStatus() {
    try {
        const resp = await fetch(`${API}/api/monitor/devices?refresh=1`);
        const data = await resp.json();
        if (data.code === 0) {
            monitorDevices = data.data || [];
            _monitorLoaded = true;
            _validateMonitorSelected();
            renderMonitorSidebar();
        } else {
            console.warn('[monitor] 状态刷新失败:', data.msg);
        }
    } catch (e) {
        console.warn('[monitor] 状态刷新失败', e);
    }
}

function filterMonitorChannels(kw) {
    _monitorSearchKeyword = kw || '';
    renderMonitorSidebar();
}

function _validateMonitorSelected() {
    if (!_monitorSelected) return;
    const dev = monitorDevices.find(d => String(d.id) === String(_monitorSelected.device.id));
    const ch = dev && (dev.channels || []).find(c => Number(c.channel_no) === Number(_monitorSelected.channel.channel_no));
    if (!dev || dev.online !== true || !ch) {
        stopMonitorStream();
        _monitorSelected = null;
        _setMonitorStatus('通道不可用');
    }
}

function _isCurrentMonitorStream(token, ws, dev, ch) {
    return token === _monitorStreamToken
        && _monitorWs === ws
        && _monitorSelected
        && String(_monitorSelected.device.id) === String(dev.id)
        && Number(_monitorSelected.channel.channel_no) === Number(ch.channel_no);
}

function _getFilteredMonitorDevices() {
    const kw = (_monitorSearchKeyword || '').trim().toLowerCase();
    if (!kw) return monitorDevices;
    const result = [];
    monitorDevices.forEach(dev => {
        const matched = (dev.channels || []).filter(ch => {
            const name = (ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`).toLowerCase();
            return name.includes(kw);
        });
        if (matched.length) result.push({ ...dev, channels: matched });
    });
    return result;
}

// ---------- 侧边栏树渲染 ----------
function renderMonitorSidebar() {
    const sidebar = document.getElementById('monitor-sidebar');
    if (!sidebar) return;
    const devices = _getFilteredMonitorDevices();
    if (!devices.length) {
        const txt = _monitorSearchKeyword
            ? `未找到匹配 "${escapeHtml(_monitorSearchKeyword)}" 的通道`
            : '暂无可用监控通道';
        sidebar.innerHTML = `<div class="empty-state" style="padding:20px"><div class="empty-icon">🎥</div><p>${txt}</p></div>`;
        return;
    }
    const searching = !!_monitorSearchKeyword.trim();
    let html = '';
    devices.forEach(dev => {
        const online = dev.online === true;
        const expanded = searching || _monitorExpandedDevices.has(String(dev.id));
        const icon = expanded ? '▼' : '▶';
        const channels = dev.channels || [];
        const onlineChannels = online
            ? channels.filter(ch => ch.online !== false).length
            : 0;
        html += `<div class="vp-tree-nvr" data-device-id="${dev.id}">
            <div class="vp-tree-nvr-title ${online ? '' : 'offline'}" onclick="toggleMonitorNvr('${dev.id}')">
                <span class="vp-tree-fold">${icon}</span>
                <span>🎥</span>
                <span style="flex:1">${escapeHtml(dev.name)}</span>
                <span class="vp-tree-count">${onlineChannels}/${channels.length}</span>
                ${online ? '' : '<span class="badge br" style="font-size:9.5px;padding:1px 5px;">离线</span>'}
            </div>
            <div class="vp-tree-children" style="display:${expanded ? 'block' : 'none'}">`;
        channels.forEach(ch => {
            const channelName = ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`;
            const selected = _monitorSelected
                && _monitorSelected.device.id === dev.id
                && Number(_monitorSelected.channel.channel_no) === Number(ch.channel_no);
            const chOffline = (ch.online === false);
            const klass = `vp-tree-channel ${selected ? 'active' : ''} ${(online && !chOffline) ? '' : 'offline'}`;
            // 通道左侧的离线标:NVR 在线但摄像头离线时显示
            const offlineBadge = (online && chOffline)
                ? '<span class="vp-channel-offline-dot" title="摄像头离线">●</span>'
                : '';
            // NVR 在线但摄像头离线 → 仍允许点击预览(可能能拿到最后帧或失败提示),保留 onclick
            const click = online ? `onclick="selectMonitorChannel(${dev.id}, ${ch.channel_no})"` : '';
            html += `<div class="${klass}" ${click}>${offlineBadge}${escapeHtml(channelName)}</div>`;
        });
        html += `</div></div>`;
    });
    sidebar.innerHTML = html;
}

function toggleMonitorNvr(deviceId) {
    const id = String(deviceId);
    if (_monitorExpandedDevices.has(id)) _monitorExpandedDevices.delete(id);
    else _monitorExpandedDevices.add(id);
    renderMonitorSidebar();
}

// ---------- 选通道 + 自动起播 ----------
function selectMonitorChannel(deviceId, channelNo) {
    const dev = monitorDevices.find(d => d.id === deviceId);
    if (!dev || dev.online !== true) return;
    const ch = (dev.channels || []).find(c => Number(c.channel_no) === Number(channelNo));
    if (!ch) return;
    _monitorSelected = { device: dev, channel: ch };
    renderMonitorSidebar();
    _setMonitorStatus('连接中…');
    _startMonitorStream(dev, ch);
}

function _useWebCodecs() {
    return typeof VideoPlayer !== 'undefined'
        && VideoPlayer.isSupported()
        && (window.isSecureContext === true);
}

function _startMonitorStream(dev, ch) {
    stopMonitorStream();
    const token = ++_monitorStreamToken;

    const canvas = document.getElementById('monitor-canvas');
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wantWebCodecs = _useWebCodecs();
    const wsUrl = `${protocol}//${location.host}/ws/monitor/preview/${dev.id}/${ch.channel_no}?stream=main&codec=${wantWebCodecs ? 'h264' : 'mpeg1'}`;
    console.log('[monitor] connect', wsUrl);

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    _monitorWs = ws;

    const btn = document.getElementById('monitor-retry-btn');
    if (btn) btn.style.display = 'none';

    ws.onopen = () => {
        if (_isCurrentMonitorStream(token, ws, dev, ch)) _setMonitorStatus('连接中…');
    };
    ws.addEventListener('message', function onFirst(ev) {
        if (!_isCurrentMonitorStream(token, ws, dev, ch)) return;
        if (typeof ev.data === 'string') return;
        ws.removeEventListener('message', onFirst);
        _setMonitorStatus('直播中');
        // 每20秒发心跳，防止服务端30秒超时断连
        _monitorPingTimer = setInterval(() => {
            if (_isCurrentMonitorStream(token, ws, dev, ch) && ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 20000);
    });
    ws.onerror = () => {
        if (_isCurrentMonitorStream(token, ws, dev, ch)) _setMonitorStatus('连接错误');
    };
    ws.onclose = () => {
        if (!_isCurrentMonitorStream(token, ws, dev, ch)) return;
        _setMonitorStatus('已断开');
        if (_monitorPingTimer) { clearInterval(_monitorPingTimer); _monitorPingTimer = null; }
        const retryBtn = document.getElementById('monitor-retry-btn');
        if (retryBtn && _monitorSelected) retryBtn.style.display = '';
    };

    if (wantWebCodecs) {
        try {
            const player = new VideoPlayer(canvas);
            player.init(ws);
            _monitorPlayer = player;
        } catch (e) {
            console.error('[monitor] VideoPlayer 初始化失败', e);
            _setMonitorStatus('播放器初始化失败');
            stopMonitorStream();
        }
    } else {
        _monitorPlayer = new jsmpeg(ws, { canvas, autoplay: true, loop: false });
    }
}

function stopMonitorStream() {
    _monitorStreamToken++;
    const btn = document.getElementById('monitor-retry-btn');
    if (btn) btn.style.display = 'none';
    if (_monitorPingTimer) { clearInterval(_monitorPingTimer); _monitorPingTimer = null; }
    if (_monitorPlayer) {
        try {
            if (typeof _monitorPlayer.destroy === 'function') _monitorPlayer.destroy();
            else if (typeof _monitorPlayer.stop === 'function') _monitorPlayer.stop();
        } catch (e) {}
        _monitorPlayer = null;
    }
    if (_monitorWs) {
        try { _monitorWs.close(); } catch (e) {}
        _monitorWs = null;
    }
    const canvas = document.getElementById('monitor-canvas');
    if (canvas) {
        try {
            const ctx = canvas.getContext('2d');
            if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
        } catch (e) {}
    }
}

function retryMonitorStream() {
    if (!_monitorSelected) {
        toast('error', '请先选择通道');
        return;
    }
    const { device, channel } = _monitorSelected;
    _setMonitorStatus('重新连接中…');
    _startMonitorStream(device, channel);
}

function _setMonitorStatus(text) {
    const el = document.getElementById('monitor-status');
    if (el) el.textContent = text;
}
