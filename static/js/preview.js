
// ========== 视频预览模块 ==========
// 依赖：
//   - jsmpg.js（MPEG-1 over WebSocket，HTTP 降级路径）
//   - video-player.js(WebCodecs,HTTPS / localhost 优先路径,H.264 + H.265)

let _previewPlayer = null;     // 当前播放器实例（jsmpeg 或 VideoPlayer）
let _previewMode = null;       // 'h264' | 'mpeg1'
let _previewWs = null;         // 视频 WebSocket 实例
let _previewAudioWs = null;    // 音频 WebSocket 实例
let _previewDeviceId = null;   // 当前预览的设备ID
let _previewAudioPath = null;  // 当前预览的音频 WS 路径
let _previewAudioAvailable = true;
let _previewPingTimer = null;  // 心跳定时器
let _previewStatusTimer = null;// "连接中→直播中" 容错定时器
let _previewStatusFinal = false; // 状态是否已确认为"直播中"
let _monitorPreviewContext = null;
let _monitorPreviewStream = 'main';

// ---- 音频相关 ----
let _audioCtx = null;          // AudioContext（需用户交互后创建）
let _audioEnabled = false;     // 用户是否开启音频
let _audioNextTime = 0;        // 下一次播放的调度时间
let _audioGain = null;         // 音量控制节点
let _audioWsConnected = false; // 音频 WS 是否已连接上

/**
 * 打开预览 Modal，连接 WebSocket，启动 jsmpeg 播放
 * @param {object} device  设备对象 {id, name, ip, port, ...}
 */
function openPreview(device) {
    if (typeof hasPermission === 'function' && !hasPermission('access.preview')) {
        toast('warn', '当前账户没有实时预览权限');
        return;
    }
    _previewDeviceId = device.id;
    _monitorPreviewContext = null;
    _setMonitorStreamControl(false);

    // 清除旧画布，防止残留上一个门的画面
    const canvas = document.getElementById('previewCanvas');
    try {
        const ctx = canvas.getContext('2d');
        if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    } catch (e) {
        canvas.width = canvas.width;
    }

    // 更新 Modal 标题和状态
    document.getElementById('previewModalTitle').textContent = `📹 ${device.name} 实时预览`;
    document.getElementById('previewStatus').textContent = '连接中…';
    document.getElementById('previewStatus').style.color = 'var(--text3)';
    _previewStatusFinal = false;

    // 重置音频状态
    _audioEnabled = false;
    _audioNextTime = 0;
    _audioWsConnected = false;
    const audioBtn = document.getElementById('audioToggleBtn');
    if (audioBtn) { audioBtn.textContent = '🔇 音频'; audioBtn.disabled = true; audioBtn.style.display = ''; }

    // 门禁预览：显示远程开门按钮
    const openBtn = document.getElementById('previewOpenDoorBtn');
    if (openBtn) {
        openBtn.style.display = '';
        openBtn.disabled = false;
        openBtn.innerHTML = '🔓 &nbsp; 远程开门';
    }

    openModal('previewModal');
    _startPreviewStream(device.id, { audio: true });
}

function openMonitorPreview(device, channel, stream = 'main') {
    _previewDeviceId = null;
    const channelNo = Number(channel.channel_no);
    const channelName = channel.channel_name || `通道 ${channelNo + 1}`;
    _monitorPreviewStream = stream === 'sub' ? 'sub' : 'main';
    _monitorPreviewContext = { device, channelNo, channelName };

    const canvas = document.getElementById('previewCanvas');
    try {
        const ctx = canvas.getContext('2d');
        if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    } catch (e) {
        canvas.width = canvas.width;
    }

    document.getElementById('previewModalTitle').textContent = `📹 ${device.name} · ${channelName}`;
    document.getElementById('previewStatus').textContent = '连接中…';
    document.getElementById('previewStatus').style.color = 'var(--text3)';
    _previewStatusFinal = false;

    _audioEnabled = false;
    _audioNextTime = 0;
    _audioWsConnected = false;
    const audioBtn = document.getElementById('audioToggleBtn');
    if (audioBtn) { audioBtn.textContent = '🔇 音频'; audioBtn.disabled = true; audioBtn.style.display = 'none'; }
    _setMonitorStreamControl(true, _monitorPreviewStream);

    // 视频监控预览：隐藏远程开门按钮（非门禁场景）
    const openBtn = document.getElementById('previewOpenDoorBtn');
    if (openBtn) openBtn.style.display = 'none';

    openModal('previewModal');
    _startMonitorPreviewStream();
}

function setMonitorPreviewStream(stream) {
    if (!_monitorPreviewContext) return;
    const nextStream = stream === 'sub' ? 'sub' : 'main';
    if (nextStream === _monitorPreviewStream) return;
    _monitorPreviewStream = nextStream;
    _setMonitorStreamControl(true, _monitorPreviewStream);
    document.getElementById('previewStatus').textContent = '连接中…';
    document.getElementById('previewStatus').style.color = 'var(--text3)';
    _previewStatusFinal = false;
    _startMonitorPreviewStream();
}

function _startMonitorPreviewStream() {
    if (!_monitorPreviewContext) return;
    const { device, channelNo } = _monitorPreviewContext;
    _startPreviewStream(device.id, {
        audio: false,
        path: `/ws/monitor/preview/${device.id}/${channelNo}?stream=${encodeURIComponent(_monitorPreviewStream)}`,
    });
}

function _setMonitorStreamControl(visible, stream = 'main') {
    const switchEl = document.getElementById('previewStreamSwitch');
    if (!switchEl) return;
    switchEl.style.display = visible ? 'inline-flex' : 'none';
    switchEl.querySelectorAll('[data-stream]').forEach(btn => {
        const active = btn.dataset.stream === stream;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
}

/**
 * 关闭预览 Modal，断开连接，销毁播放器
 */
window.closePreview = closePreview;
function closePreview() {
    _stopAudio();
    _stopPreviewStream();
    _monitorPreviewContext = null;
    _setMonitorStreamControl(false);
    closeModal('previewModal');
}

/**
 * 是否走 WebCodecs 直通路径
 *   - 浏览器要支持 VideoDecoder
 *   - 页面必须是 Secure Context（HTTPS / localhost）
 */
function _useWebCodecs() {
    return typeof VideoPlayer !== 'undefined'
        && VideoPlayer.isSupported()
        && (window.isSecureContext === true);
}

/**
 * 建立 WebSocket + 初始化对应播放器
 */
function _startPreviewStream(deviceId, options = {}) {
    _stopPreviewStream(); // 先清理旧连接

    const canvas = document.getElementById('previewCanvas');
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const basePath = options.path || `/ws/preview/${deviceId}`;
    const wantWebCodecs = _useWebCodecs();
    _previewMode = wantWebCodecs ? 'h264' : 'mpeg1';

    // 通过查询参数告诉后端要哪种码流
    const sep = basePath.includes('?') ? '&' : '?';
    const videoPath = wantWebCodecs ? `${basePath}${sep}codec=h264` : basePath;
    const wsUrl = `${protocol}//${location.host}${videoPath}`;

    _previewAudioAvailable = options.audio !== false;
    _previewAudioPath = _previewAudioAvailable ? (options.audioPath || `/ws/preview/${deviceId}/audio`) : null;

    console.log(`[preview] 使用 ${_previewMode} 路径: ${wsUrl}`);

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    _previewWs = ws;

    ws.onopen = () => {
        console.log('[preview] 视频 WebSocket 已连接');
        const audioBtn = document.getElementById('audioToggleBtn');
        if (audioBtn && _previewAudioAvailable) {
            audioBtn.disabled = false;
        }
    };

    ws.onclose = () => {
        console.log('[preview] 视频 WebSocket 已断开');
        _updatePreviewStatus('连接已断开', 'var(--red2)');
        _stopPing();
    };

    ws.onerror = () => {
        console.log('[preview] 视频 WebSocket 错误');
        _updatePreviewStatus('连接错误', 'var(--red2)');
    };

    // 首帧到达 → 更新状态
    ws.addEventListener('message', function onFirstFrame() {
        ws.removeEventListener('message', onFirstFrame);
        _markPreviewLive();
    });

    // 容错：3 秒后若首帧都没到，强制更新为直播中
    _previewStatusTimer = setTimeout(() => {
        if (!_previewStatusFinal) _markPreviewLive();
    }, 3000);

    if (wantWebCodecs) {
        // WebCodecs 路径
        try {
            const player = new VideoPlayer(canvas);
            player.init(ws);
            _previewPlayer = player;
        } catch (e) {
            console.error('[preview] VideoPlayer 初始化失败，回退 jsmpg', e);
            try { ws.close(); } catch (_) {}
            _previewMode = 'mpeg1';
            _fallbackToJsmpg(deviceId, options);
            return;
        }
    } else {
        // jsmpg 路径（向后兼容）
        _previewPlayer = new jsmpeg(ws, {
            canvas: canvas,
            autoplay: true,
            loop: false,
            onload: () => {
                console.log('[preview] 视频流已加载 (onload)');
                _markPreviewLive();
            },
            onfinished: () => {
                console.log('[preview] 流结束');
                _updatePreviewStatus('流已结束', 'var(--yellow2)');
            },
        });
    }

    // 容错：如果 WebSocket 已经是 OPEN 状态，立即启用音频按钮
    setTimeout(() => {
        if (ws.readyState === WebSocket.OPEN) {
            const audioBtn = document.getElementById('audioToggleBtn');
            if (audioBtn && _previewAudioAvailable && audioBtn.disabled) {
                audioBtn.disabled = false;
            }
        }
    }, 100);
}

/**
 * 启动失败时强制走 jsmpg 重试一次
 */
function _fallbackToJsmpg(deviceId, options) {
    const canvas = document.getElementById('previewCanvas');
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const videoPath = options.path || `/ws/preview/${deviceId}`;
    const wsUrl = `${protocol}//${location.host}${videoPath}`;
    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    _previewWs = ws;
    _previewPlayer = new jsmpeg(ws, {
        canvas: canvas,
        autoplay: true,
        loop: false,
        onload: () => _markPreviewLive(),
    });
}

/**
 * 停止预览：停止播放器，关闭 WS，清理心跳
 */
function _stopPreviewStream() {
    _stopPing();
    if (_previewStatusTimer) {
        clearTimeout(_previewStatusTimer);
        _previewStatusTimer = null;
    }
    if (_previewPlayer) {
        try {
            if (typeof _previewPlayer.destroy === 'function') {
                _previewPlayer.destroy();      // VideoPlayer
            } else if (typeof _previewPlayer.stop === 'function') {
                _previewPlayer.stop();          // jsmpeg
            }
        } catch (e) {}
        _previewPlayer = null;
    }
    if (_previewWs) {
        try { _previewWs.close(); } catch (e) {}
        _previewWs = null;
    }
    _previewMode = null;
    _previewDeviceId = null;
    _previewAudioPath = null;
    _previewAudioAvailable = true;
    _previewStatusFinal = false;
    // 清除画布，防止残留画面
    const canvas = document.getElementById('previewCanvas');
    if (canvas) {
        try {
            const ctx = canvas.getContext('2d');
            if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
        } catch (e) {
            canvas.width = canvas.width;
        }
    }
}

// ==================== 音频模块 ====================

/**
 * 切换音频开关
 */
function toggleAudio() {
    const btn = document.getElementById('audioToggleBtn');
    if (!btn) return;

    if (_audioEnabled) {
        // 关闭音频
        _stopAudio();
        btn.textContent = '🔇 音频';
        _audioEnabled = false;
        return;
    }

    if (!_previewAudioAvailable || !_previewAudioPath) {
        toast('error', '当前预览不支持音频');
        return;
    }

    // 开启音频前，确保 _previewDeviceId 有值
    if (!_previewDeviceId && currentDev && currentDev.id) {
        _previewDeviceId = currentDev.id;
        console.log('[audio] 从 currentDev 恢复 device_id:', _previewDeviceId);
    }

    if (!_previewDeviceId) {
        toast('error', '无法获取设备ID，请重新打开预览');
        return;
    }

    // 开启音频
    if (!_audioCtx) {
        try {
            _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            _audioGain = _audioCtx.createGain();
            _audioGain.gain.value = 1.0;
            _audioGain.connect(_audioCtx.destination);
        } catch (e) {
            toast('error', '浏览器不支持音频: ' + e.message);
            return;
        }
    }

    // 如果 AudioContext 被 suspend（浏览器自动暂停），resume 它
    if (_audioCtx.state === 'suspended') {
        _audioCtx.resume().catch(e => console.warn('[audio] resume失败', e));
    }

    _audioNextTime = _audioCtx.currentTime + 0.1; // 100ms 预缓冲
    _audioEnabled = true;
    btn.textContent = '🔊 音频';
    _startAudioStream();
}

/**
 * 连接音频 WebSocket 并开始播放 PCM 数据
 */
function _startAudioStream() {
    if (_previewAudioWs) return; // 已连接

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}${_previewAudioPath}`;

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    _previewAudioWs = ws;

    // 心跳定时器
    let audioPingTimer = null;

    ws.onopen = () => {
        console.log('[audio] 音频 WebSocket 已连接');
        _audioWsConnected = true;
        
        // 启动心跳：每 15 秒发送一次 ping
        audioPingTimer = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                try {
                    ws.send('ping');
                } catch (e) {
                    console.warn('[audio] 心跳发送失败', e);
                }
            }
        }, 15000);
    };

    ws.onclose = () => {
        console.log('[audio] 音频 WebSocket 已断开');
        _audioWsConnected = false;
        if (audioPingTimer) {
            clearInterval(audioPingTimer);
            audioPingTimer = null;
        }
        if (_previewAudioWs === ws) _previewAudioWs = null;
        if (_audioEnabled) {
            const btn = document.getElementById('audioToggleBtn');
            if (btn) btn.textContent = '🔇 音频断连';
        }
    };

    ws.onerror = () => {
        console.log('[audio] 音频 WebSocket 错误');
    };

    ws.onmessage = (event) => {
        if (!_audioEnabled || !_audioCtx || !_audioGain) return;

        const pcmData = new Int16Array(event.data);
        if (pcmData.length === 0) return;

        // Int16Array → Float32Array (PCM s16le to float)
        const floatData = new Float32Array(pcmData.length);
        for (let i = 0; i < pcmData.length; i++) {
            floatData[i] = pcmData[i] / 32768.0;
        }

        try {
            const buffer = _audioCtx.createBuffer(1, floatData.length, 8000);
            buffer.copyToChannel(floatData, 0);

            const source = _audioCtx.createBufferSource();
            source.buffer = buffer;
            source.connect(_audioGain);

            const now = _audioCtx.currentTime;
            // 如果调度时间落后于当前时间，追上来
            if (_audioNextTime < now) {
                _audioNextTime = now + 0.05;
            }
            source.start(_audioNextTime);
            _audioNextTime += buffer.duration;
        } catch (e) {
            console.warn('[audio] 播放错误:', e);
        }
    };
}

/**
 * 停止音频播放，断开音频 WS
 */
function _stopAudio() {
    _audioEnabled = false;
    _audioWsConnected = false;
    if (_previewAudioWs) {
        try { _previewAudioWs.close(); } catch (e) {}
        _previewAudioWs = null;
    }
    // 不销毁 AudioContext，下次开音频可复用
}

/**
 * 心跳：每 20 秒发一次 ping，防止 WS 被代理超时断开
 */
function _startPing() {
    _stopPing();
    _previewPingTimer = setInterval(() => {
        if (_previewWs && _previewWs.readyState === WebSocket.OPEN) {
            _previewWs.send('ping');
        }
    }, 20000);
}

function _stopPing() {
    if (_previewPingTimer) {
        clearInterval(_previewPingTimer);
        _previewPingTimer = null;
    }
}

/**
 * 更新预览状态文字
 */
function _updatePreviewStatus(text, color) {
    const el = document.getElementById('previewStatus');
    if (el) {
        el.textContent = text;
        el.style.color = color || 'var(--text3)';
    }
}

/**
 * 标记预览已进入直播状态（防重复触发）
 */
function _markPreviewLive() {
    if (_previewStatusFinal) return;
    _previewStatusFinal = true;
    if (_previewStatusTimer) {
        clearTimeout(_previewStatusTimer);
        _previewStatusTimer = null;
    }
    _updatePreviewStatus('直播中', 'var(--accent2)');
    _startPing();
}

/**
 * 全屏预览
 */
function togglePreviewFullscreen() {
    const canvas = document.getElementById('previewCanvas');
    if (!document.fullscreenElement) {
        canvas.requestFullscreen().catch(e => toast('error', '全屏失败: ' + e.message));
    } else {
        document.exitFullscreen();
    }
}

// 标签页关闭/刷新/进入 bfcache 前主动断开 WS,
// 避免后端预览会话(SDK realplay + ffmpeg)残留等到 TCP 超时才回收。
// pagehide 比 beforeunload 更可靠(后者在移动端/bfcache 经常不触发)。
window.addEventListener('pagehide', () => {
    try { _stopAudio(); } catch (e) {}
    try { _stopPreviewStream(); } catch (e) {}
});
