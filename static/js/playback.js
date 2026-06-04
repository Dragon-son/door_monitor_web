// ========== 视频回放模块 (sidebar + canvas + controls + timeline) ==========

let playbackDevices = [];
let _playbackDevicesLoaded = false;
let _playbackSelected = null;     // {device, channel}
let _playbackRecords = [];
let _playbackSearchKeyword = '';
let _playbackExpandedDevices = new Set();

// 回放运行态
let _playbackWs = null;
let _playbackPlayer = null;
let _playbackPaused = false;
let _playbackRange = null;        // {start: 'YYYY-MM-DD HH:MM:SS', end: ...} 当前播放区间
let _playbackPlayStartedAt = 0;   // 起播/恢复时刻(performance.now()),用于估算游标位置
let _playbackElapsedBeforePause = 0;
let _playbackPingTimer = null;    // 心跳定时器

// 时间轴渲染状态
let _timelineCanvas = null;
let _timelineDPR = 1;
let _timelineViewStartSec = 0;
let _timelineViewEndSec = 86400;

// ---------- tab close 钩子 ----------
(function _registerPlaybackTabClose() {
    if (typeof onTabClose === 'function') {
        onTabClose('playback', () => {
            if (typeof stopPlaybackIfActive === 'function') stopPlaybackIfActive();
        });
    }
})();

// ---------- 加载设备 ----------
async function loadPlaybackDevices() {
    const container = document.getElementById('playback-channels');
    if (container && !_playbackDevicesLoaded) {
        container.innerHTML = `<div class="empty-state" style="padding:20px"><div class="spinner"></div><p>加载中…</p></div>`;
    }
    try {
        const resp = await fetch(`${API}/api/monitor/devices`);
        const data = await resp.json();
        if (data.code === 0) {
            playbackDevices = data.data || [];
            _playbackDevicesLoaded = true;
            renderPlaybackChannels();
            refreshPlaybackDeviceStatus();
        } else {
            if (container) container.innerHTML = `<div class="empty-state" style="padding:20px"><p>${escapeHtml(data.msg || '加载失败')}</p></div>`;
        }
    } catch (e) {
        if (container) container.innerHTML = `<div class="empty-state" style="padding:20px"><p>无法连接服务器</p></div>`;
    }
    const dateEl = document.getElementById('playback-date');
    if (dateEl && !dateEl.value) dateEl.value = getLocalDateString();
}

async function refreshPlaybackDeviceStatus() {
    try {
        const resp = await fetch(`${API}/api/monitor/devices?refresh=1`);
        const data = await resp.json();
        if (data.code === 0) {
            playbackDevices = data.data || [];
            _playbackDevicesLoaded = true;
            renderPlaybackChannels();
        } else {
            console.warn('[playback] 状态刷新失败:', data.msg);
        }
    } catch (e) {
        console.warn('[playback] 状态刷新失败', e);
    }
}

function filterPlaybackChannels(kw) {
    _playbackSearchKeyword = kw || '';
    renderPlaybackChannels();
}

function _getFilteredPlaybackDevices() {
    const kw = (_playbackSearchKeyword || '').trim().toLowerCase();
    if (!kw) return playbackDevices;
    const result = [];
    playbackDevices.forEach(dev => {
        const matched = (dev.channels || []).filter(ch => {
            const name = (ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`).toLowerCase();
            return name.includes(kw);
        });
        if (matched.length) result.push({ ...dev, channels: matched });
    });
    return result;
}

// ---------- 侧边栏渲染 ----------
function renderPlaybackChannels() {
    const container = document.getElementById('playback-channels');
    if (!container) return;
    const devices = _getFilteredPlaybackDevices();
    if (!devices.length) {
        const txt = _playbackSearchKeyword
            ? `未找到匹配 "${escapeHtml(_playbackSearchKeyword)}" 的通道`
            : '暂无可用监控通道';
        container.innerHTML = `<div class="empty-state" style="padding:20px"><div class="empty-icon">🎬</div><p>${txt}</p></div>`;
        return;
    }
    const searching = !!_playbackSearchKeyword.trim();
    let html = '';
    devices.forEach(dev => {
        const online = dev.online === true;
        const expanded = searching || _playbackExpandedDevices.has(String(dev.id));
        const icon = expanded ? '▼' : '▶';
        const channels = dev.channels || [];
        const onlineChannels = online
            ? channels.filter(ch => ch.online !== false).length
            : 0;
        html += `<div class="vp-tree-nvr" data-device-id="${dev.id}">
            <div class="vp-tree-nvr-title ${online ? '' : 'offline'}" onclick="togglePlaybackNvr('${dev.id}')">
                <span class="vp-tree-fold">${icon}</span>
                <span>🎥</span>
                <span style="flex:1">${escapeHtml(dev.name)}</span>
                <span class="vp-tree-count">${onlineChannels}/${channels.length}</span>
                ${online ? '' : '<span class="badge br" style="font-size:9.5px;padding:1px 5px;">离线</span>'}
            </div>
            <div class="vp-tree-children" style="display:${expanded ? 'block' : 'none'}">`;
        channels.forEach(ch => {
            const channelName = ch.channel_name || `通道 ${Number(ch.channel_no) + 1}`;
            const selected = _playbackSelected
                && _playbackSelected.device.id === dev.id
                && Number(_playbackSelected.channel.channel_no) === Number(ch.channel_no);
            const chOffline = (ch.online === false);
            const klass = `vp-tree-channel ${selected ? 'active' : ''} ${(online && !chOffline) ? '' : 'offline'}`;
            const offlineBadge = (online && chOffline)
                ? '<span class="vp-channel-offline-dot" title="摄像头离线">●</span>'
                : '';
            // 回放允许查询离线摄像头(只要 NVR 在线,SDK 仍能从硬盘读历史录像)
            const click = online ? `onclick="selectPlaybackChannel(${dev.id}, ${ch.channel_no})"` : '';
            html += `<div class="${klass}" ${click}>${offlineBadge}${escapeHtml(channelName)}</div>`;
        });
        html += `</div></div>`;
    });
    container.innerHTML = html;
}

function togglePlaybackNvr(deviceId) {
    const id = String(deviceId);
    if (_playbackExpandedDevices.has(id)) _playbackExpandedDevices.delete(id);
    else _playbackExpandedDevices.add(id);
    renderPlaybackChannels();
}

function selectPlaybackChannel(deviceId, channelNo) {
    const dev = playbackDevices.find(d => d.id === deviceId);
    if (!dev || dev.online !== true) return;
    const ch = (dev.channels || []).find(c => Number(c.channel_no) === Number(channelNo));
    if (!ch) return;
    _playbackSelected = { device: dev, channel: ch };
    renderPlaybackChannels();
    document.getElementById('playback-query-btn').disabled = false;
    // 切通道立即停旧播放并清掉旧录像段；不自动查询，等用户点"查询录像"按钮。
    playbackStop();
    _playbackRecords = [];
    _timelineViewStartSec = 0;
    _timelineViewEndSec = 86400;
    document.getElementById('playback-play-btn').disabled = true;
    _clearTimeline();
    _setPlaybackStatus('点击"查询录像"开始检索');
}

// ---------- 查询录像段 ----------
async function loadPlaybackRecords() {
    if (!_playbackSelected) { toast('error', '请先选择通道'); return; }
    const date = document.getElementById('playback-date').value;
    if (!date) { toast('error', '请选择日期'); return; }
    const stream = document.getElementById('playback-stream').value || 'main';

    _setPlaybackStatus('查询录像中…');
    _showTimelineEmpty('查询中…');

    try {
        const params = new URLSearchParams({
            device_id: _playbackSelected.device.id,
            channel_no: _playbackSelected.channel.channel_no,
            date,
            stream,
        });
        const resp = await fetch(`${API}/api/playback/records?${params}`);
        const data = await resp.json();
        if (data.code !== 0) {
            _showTimelineEmpty(data.msg || '查询失败');
            _setPlaybackStatus('查询失败');
            return;
        }
        _playbackRecords = (data.data && data.data.records) || [];
        if (!_playbackRecords.length) {
            _showTimelineEmpty('该日期没有录像');
            _setPlaybackStatus('该日期没有录像');
            document.getElementById('playback-play-btn').disabled = true;
            return;
        }
        _timelineViewStartSec = 0;
        _timelineViewEndSec = 86400;
        document.getElementById('playback-play-btn').disabled = false;
        _setPlaybackStatus(`共 ${_playbackRecords.length} 段录像,点时间轴定位播放`);
        renderPlaybackTimeline(date);
        // 自动播第一段；跨天录像段要裁剪到当前查询日期内，避免跳到前一天播放。
        const first = _playbackRecords[0];
        const last = _playbackRecords[_playbackRecords.length - 1];
        const startSec = Math.max(0, _timeToSec(date, first.start));
        const endSec = Math.min(86400, _timeToSec(date, last.end));
        const startStr = `${date} ${_secToTime(startSec)}`;
        const endStr = `${date} ${_secToTime(endSec)}`;
        _startPlaybackStream(startStr, endStr);
    } catch (e) {
        _showTimelineEmpty('查询失败: ' + e.message);
    }
}

// ---------- 时间轴渲染 ----------
function _showTimelineEmpty(msg) {
    const empty = document.getElementById('timeline-empty');
    const canvas = document.getElementById('playback-timeline-canvas');
    if (empty) { empty.textContent = msg; empty.style.display = 'flex'; }
    if (canvas) canvas.style.display = 'none';
}
function _clearTimeline() {
    _showTimelineEmpty('请选择通道和日期后查询录像');
}

/**
 * 把 'YYYY-MM-DD HH:MM:SS' 转成当天的秒数(0~86400)
 */
function _timeToSec(dateStr, fullStr) {
    if (!fullStr) return 0;
    const [fullDate, fullTime = '00:00:00'] = String(fullStr).split(' ');
    const parts = fullTime.split(':').map(Number);
    const secOfDay = (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
    if (!fullDate || !dateStr || fullDate === dateStr) return secOfDay;

    const base = Date.parse(`${dateStr}T00:00:00`);
    const current = Date.parse(`${fullDate}T00:00:00`);
    if (Number.isNaN(base) || Number.isNaN(current)) return secOfDay;
    return Math.round((current - base) / 1000) + secOfDay;
}
function _secToTime(sec) {
    sec = Math.max(0, Math.min(86400, Math.round(sec)));
    const h = Math.floor(sec / 3600).toString().padStart(2, '0');
    const m = Math.floor((sec % 3600) / 60).toString().padStart(2, '0');
    const s = (sec % 60).toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
}

function renderPlaybackTimeline(date) {
    const empty = document.getElementById('timeline-empty');
    const canvas = document.getElementById('playback-timeline-canvas');
    if (!canvas) return;
    if (empty) empty.style.display = 'none';
    canvas.style.display = 'block';
    _timelineCanvas = canvas;
    _resizeAndDrawTimeline(date);

    // 绑定鼠标 hover / 点击 (避免重复绑定,先 clone)
    const fresh = canvas.cloneNode(true);
    canvas.parentNode.replaceChild(fresh, canvas);
    _timelineCanvas = fresh;
    fresh.addEventListener('mousemove', e => _onTimelineHover(e, date));
    fresh.addEventListener('mouseleave', () => {
        const tip = document.getElementById('timeline-tooltip');
        if (tip) tip.style.display = 'none';
    });
    fresh.addEventListener('click', e => _onTimelineClick(e, date));
    fresh.addEventListener('wheel', e => _onTimelineWheel(e, date), { passive: false });
    fresh.addEventListener('dblclick', () => _resetTimelineZoom(date));

    // 启动游标动画(仅当正在播放)
    if (!window._playbackTimelineRafId) {
        const tick = () => {
            window._playbackTimelineRafId = requestAnimationFrame(tick);
            _drawPlayhead(date);
        };
        window._playbackTimelineRafId = requestAnimationFrame(tick);
    }
}

function _resizeAndDrawTimeline(date) {
    const canvas = _timelineCanvas;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    _timelineDPR = window.devicePixelRatio || 1;
    canvas.width = Math.floor(rect.width * _timelineDPR);
    canvas.height = Math.floor(rect.height * _timelineDPR);
    _drawTimelineBase(date);
    _drawPlayhead(date);
}

function _drawTimelineBase(date) {
    const canvas = _timelineCanvas;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const dpr = _timelineDPR;
    ctx.clearRect(0, 0, w, h);

    // 背景
    ctx.fillStyle = '#fafafa';
    ctx.fillRect(0, 0, w, h);

    const spanSec = Math.max(1, _timelineViewEndSec - _timelineViewStartSec);
    const padLeft = 12 * dpr;
    const padRight = 12 * dpr;
    const usableW = w - padLeft - padRight;

    // 录像段(蓝色色块) — 居中带条
    const stripeY = h * 0.42;
    const stripeH = h * 0.30;
    ctx.fillStyle = '#3370ff';
    (_playbackRecords || []).forEach(rec => {
        const startSec = _timeToSec(date, rec.start);
        const endSec = _timeToSec(date, rec.end);
        const clippedStart = Math.max(startSec, _timelineViewStartSec);
        const clippedEnd = Math.min(endSec, _timelineViewEndSec);
        if (clippedEnd <= clippedStart) return;
        const x1 = padLeft + ((clippedStart - _timelineViewStartSec) / spanSec) * usableW;
        const x2 = padLeft + ((clippedEnd - _timelineViewStartSec) / spanSec) * usableW;
        ctx.fillRect(x1, stripeY, Math.max(1, x2 - x1), stripeH);
    });

    // 刻度
    ctx.strokeStyle = '#d0d3d9';
    ctx.fillStyle = '#646a73';
    ctx.font = `${10 * dpr}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const tickStep = _getTimelineTickStep(spanSec);
    const firstTick = Math.ceil(_timelineViewStartSec / tickStep) * tickStep;
    for (let sec = firstTick; sec <= _timelineViewEndSec + 1; sec += tickStep) {
        const x = padLeft + ((sec - _timelineViewStartSec) / spanSec) * usableW;
        const major = sec % (tickStep * 2) === 0;
        ctx.beginPath();
        ctx.moveTo(x, h - (major ? 14 : 8) * dpr);
        ctx.lineTo(x, h - 2 * dpr);
        ctx.stroke();
        if (major || spanSec <= 3600) {
            ctx.fillText(_secToTime(sec).slice(0, 5), x, h - 26 * dpr);
        }
    }
}

function _getTimelineTickStep(spanSec) {
    if (spanSec <= 10 * 60) return 60;
    if (spanSec <= 30 * 60) return 5 * 60;
    if (spanSec <= 2 * 3600) return 15 * 60;
    if (spanSec <= 6 * 3600) return 30 * 60;
    return 2 * 3600;
}

function _timelineClientXToSec(clientX) {
    const canvas = _timelineCanvas;
    if (!canvas) return 0;
    const rect = canvas.getBoundingClientRect();
    const padLeft = 12;
    const padRight = 12;
    const usableW = Math.max(1, rect.width - padLeft - padRight);
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left - padLeft) / usableW));
    return _timelineViewStartSec + ratio * (_timelineViewEndSec - _timelineViewStartSec);
}

function _resetTimelineZoom(date) {
    _timelineViewStartSec = 0;
    _timelineViewEndSec = 86400;
    _resizeAndDrawTimeline(date);
}

function _onTimelineWheel(e, date) {
    if (!_timelineCanvas) return;
    e.preventDefault();
    const anchor = _timelineClientXToSec(e.clientX);
    const currentSpan = _timelineViewEndSec - _timelineViewStartSec;
    const minSpan = 5 * 60;
    const zoomFactor = e.deltaY < 0 ? 0.75 : 1.33;
    let nextSpan = Math.max(minSpan, Math.min(86400, currentSpan * zoomFactor));
    if (nextSpan >= 86400 - 1) {
        _timelineViewStartSec = 0;
        _timelineViewEndSec = 86400;
        _resizeAndDrawTimeline(date);
        return;
    }
    const anchorRatio = (anchor - _timelineViewStartSec) / currentSpan;
    let start = anchor - nextSpan * anchorRatio;
    let end = start + nextSpan;
    if (start < 0) {
        start = 0;
        end = nextSpan;
    }
    if (end > 86400) {
        end = 86400;
        start = end - nextSpan;
    }
    _timelineViewStartSec = start;
    _timelineViewEndSec = end;
    _resizeAndDrawTimeline(date);
}

function _drawPlayhead(date) {
    const canvas = _timelineCanvas;
    if (!canvas || !_playbackRange || !_playbackWs) return;
    // 重画基底
    _drawTimelineBase(date);

    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const dpr = _timelineDPR;
    const padLeft = 12 * dpr;
    const padRight = 12 * dpr;
    const usableW = w - padLeft - padRight;

    // 估算当前播放时刻:暂停前累计秒数 + 本次恢复后流过秒数 + range.start (固定1x正放)
    const runningSec = _playbackPaused ? 0 : (performance.now() - _playbackPlayStartedAt) / 1000;
    const elapsedSec = _playbackElapsedBeforePause + runningSec;
    const startSec = _timeToSec(date, _playbackRange.start);
    const endSec = _timeToSec(date, _playbackRange.end);
    let curSec = startSec + elapsedSec;
    if (curSec < startSec) curSec = startSec;
    if (curSec > endSec) curSec = endSec;

    const spanSec = Math.max(1, _timelineViewEndSec - _timelineViewStartSec);
    if (curSec < _timelineViewStartSec || curSec > _timelineViewEndSec) return;
    const x = padLeft + ((curSec - _timelineViewStartSec) / spanSec) * usableW;
    ctx.strokeStyle = '#f54a45';
    ctx.lineWidth = 2 * dpr;
    ctx.beginPath();
    ctx.moveTo(x, 2 * dpr);
    ctx.lineTo(x, h - 2 * dpr);
    ctx.stroke();
}

function _onTimelineHover(e, date) {
    const canvas = _timelineCanvas;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const sec = _timelineClientXToSec(e.clientX);
    const tip = document.getElementById('timeline-tooltip');
    if (!tip) return;
    tip.textContent = `${_secToTime(sec)} · 滚轮缩放，双击复位`;
    tip.style.left = (e.clientX - rect.left + 8) + 'px';
    tip.style.top = '4px';
    tip.style.display = 'block';
}

function _findPlaybackRecordAt(date, sec) {
    return (_playbackRecords || []).find(rec => {
        const startSec = _timeToSec(date, rec.start);
        const endSec = _timeToSec(date, rec.end);
        return sec >= startSec && sec <= endSec;
    }) || null;
}

function _onTimelineClick(e, date) {
    const canvas = _timelineCanvas;
    if (!canvas) return;
    const sec = Math.floor(_timelineClientXToSec(e.clientX));
    const rec = _findPlaybackRecordAt(date, sec);
    if (!rec) {
        toast('info', '该时间点没有录像');
        return;
    }
    const startStr = `${date} ${_secToTime(sec)}`;
    _startPlaybackStream(startStr, rec.end);
}

// ---------- 播放控制 ----------
function playbackPlayOrPause() {
    if (!_playbackWs) {
        if (!_playbackRecords.length) { toast('error', '没有录像'); return; }
        _startPlaybackStream(_playbackRecords[0].start, _playbackRecords[_playbackRecords.length - 1].end);
    } else {
        if (_playbackPaused) {
            _playbackWs.send('resume');
            _playbackPaused = false;
            _playbackPlayStartedAt = performance.now();  // 继续后的起点
            document.getElementById('playback-play-btn').textContent = '⏸ 暂停';
            _setPlaybackStatus('播放中');
        } else {
            _playbackWs.send('pause');
            _playbackPaused = true;
            _playbackElapsedBeforePause += (performance.now() - _playbackPlayStartedAt) / 1000;
            document.getElementById('playback-play-btn').textContent = '▶ 继续';
            _setPlaybackStatus('已暂停');
        }
    }
}

function playbackStop() {
    if (_playbackPlayer) {
        try {
            if (typeof _playbackPlayer.destroy === 'function') _playbackPlayer.destroy();
            else if (typeof _playbackPlayer.stop === 'function') _playbackPlayer.stop();
        } catch (e) {}
        _playbackPlayer = null;
    }
    if (_playbackWs) {
        try { _playbackWs.close(); } catch (e) {}
        _playbackWs = null;
    }
    if (_playbackPingTimer) {
        clearInterval(_playbackPingTimer);
        _playbackPingTimer = null;
    }
    if (window._playbackTimelineRafId) {
        cancelAnimationFrame(window._playbackTimelineRafId);
        window._playbackTimelineRafId = null;
    }
    _playbackPaused = false;
    _playbackRange = null;
    _playbackElapsedBeforePause = 0;

    document.getElementById('playback-play-btn').textContent = '▶ 播放';
    document.getElementById('playback-play-btn').disabled = !_playbackRecords.length;
    document.getElementById('playback-stop-btn').disabled = true;

    const canvas = document.getElementById('playback-canvas');
    if (canvas) {
        try {
            const ctx = canvas.getContext('2d');
            if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
        } catch (e) {}
    }
}

function stopPlaybackIfActive() {
    if (_playbackWs || _playbackPlayer) playbackStop();
}

function _useWebCodecs() {
    return typeof VideoPlayer !== 'undefined'
        && VideoPlayer.isSupported()
        && (window.isSecureContext === true);
}

function _startPlaybackStream(startStr, endStr) {
    if (!_playbackSelected) return;
    playbackStop();

    _playbackRange = { start: startStr, end: endStr };
    _playbackPlayStartedAt = performance.now();
    _playbackElapsedBeforePause = 0;
    const stream = document.getElementById('playback-stream').value || 'main';
    const wantWebCodecs = _useWebCodecs();

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const params = new URLSearchParams({
        start: startStr,
        end: endStr,
        stream,
        direction: 'forward',
        codec: wantWebCodecs ? 'h264' : 'mpeg1',
    });
    const wsUrl = `${protocol}//${location.host}/ws/playback/${_playbackSelected.device.id}/${_playbackSelected.channel.channel_no}?${params}`;
    console.log('[playback] connect', wsUrl);

    const canvas = document.getElementById('playback-canvas');
    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    _playbackWs = ws;

    ws.onopen = () => {
        _setPlaybackStatus('连接中…');
        document.getElementById('playback-play-btn').textContent = '⏸ 暂停';
        document.getElementById('playback-stop-btn').disabled = false;
    };
    ws.addEventListener('message', function onFirst(ev) {
        if (typeof ev.data === 'string') return;
        ws.removeEventListener('message', onFirst);
        _setPlaybackStatus('播放中');
        // 每20秒发心跳，防止服务端60秒超时断连
        _playbackPingTimer = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 20000);
    });
    ws.onclose = () => {
        if (_playbackWs !== ws) return;
        _setPlaybackStatus('已结束');
        if (_playbackPingTimer) { clearInterval(_playbackPingTimer); _playbackPingTimer = null; }
        if (_playbackPlayer) {
            try {
                if (typeof _playbackPlayer.destroy === 'function') _playbackPlayer.destroy();
                else if (typeof _playbackPlayer.stop === 'function') _playbackPlayer.stop();
            } catch (e) {}
        }
        _playbackPlayer = null;
        _playbackWs = null;
        _playbackPaused = false;
        _playbackRange = null;
        _playbackElapsedBeforePause = 0;
        document.getElementById('playback-play-btn').textContent = '▶ 播放';
        document.getElementById('playback-play-btn').disabled = !_playbackRecords.length;
        document.getElementById('playback-stop-btn').disabled = true;
    };
    ws.onerror = () => _setPlaybackStatus('连接错误');

    if (wantWebCodecs) {
        try {
            const player = new VideoPlayer(canvas);
            player.init(ws);
            _playbackPlayer = player;
        } catch (e) {
            console.error('[playback] VideoPlayer 初始化失败', e);
            playbackStop();
            return;
        }
    } else {
        _playbackPlayer = new jsmpeg(ws, { canvas, autoplay: true, loop: false });
    }
}

// ---------- 变速 / 倒放 ----------

function _setPlaybackStatus(text) {
    const el = document.getElementById('playback-status');
    if (el) el.textContent = text;
}

// 窗口尺寸变化时,重画时间轴
window.addEventListener('resize', () => {
    const dateEl = document.getElementById('playback-date');
    if (_timelineCanvas && _timelineCanvas.style.display !== 'none' && dateEl && dateEl.value) {
        _resizeAndDrawTimeline(dateEl.value);
    }
});
