/**
 * H.264 WebSocket 播放器
 * 使用 WebCodecs API 解码 H.264 裸流（Annex B 格式）
 *
 * 关键约束：
 *  - 在收到第一个 IDR(NAL type=5) 之前丢弃所有 P 帧。
 *  - encoder 下发的 Annex B 裸流直接喂 VideoDecoder（不带 description/AVCDecoderConfigurationRecord）。
 *  - codec 字符串优先用 High Profile 4.0，不支持时回退 Baseline 3.0。
 */

class H264Player {
    constructor(canvas) {
        this.canvas = canvas;
        this.decoder = null;
        this.ws = null;
        this.frameCount = 0;
        this.isReady = false;
        this.gotKey = false;

        if (!('VideoDecoder' in window)) {
            console.error('[H264Player] 浏览器不支持 WebCodecs API');
        }
    }

    static isSupported() {
        return 'VideoDecoder' in window;
    }

    init(ws) {
        this.ws = ws;
        this._createDecoder();

        ws.addEventListener('message', (event) => {
            if (event.data instanceof ArrayBuffer) {
                this.decodeData(event.data);
            }
        });
    }

    _createDecoder() {
        this.isReady = false;
        this.gotKey = false;
        if (this.decoder) {
            try { if (this.decoder.state !== 'closed') this.decoder.close(); } catch (e) {}
            this.decoder = null;
        }

        this.decoder = new VideoDecoder({
            output: (frame) => { this.renderFrame(frame); frame.close(); },
            error: (e) => {
                console.error('[H264Player] 解码错误，将重建 decoder:', e);
                setTimeout(() => this._createDecoder(), 0);
            }
        });

        const tryConfigs = [
            { codec: 'avc1.4D4032' }, // Main 5.0 (2560x1440)
            { codec: 'avc1.640032' }, // High 5.0
            { codec: 'avc1.4D4028' }, // Main 4.0 (1920x1080)
            { codec: 'avc1.640028' }, // High 4.0
            { codec: 'avc1.42E01E' }, // Baseline 3.0
        ];

        const tryNext = (idx) => {
            if (idx >= tryConfigs.length) {
                console.error('[H264Player] 所有 codec 都不支持');
                return;
            }
            VideoDecoder.isConfigSupported(tryConfigs[idx]).then((result) => {
                if (result.supported) {
                    this.decoder.configure(result.config);
                    this.isReady = true;
                    this._waitingFrames = 0;
                    console.log('[H264Player] 解码器已就绪 codec=' + tryConfigs[idx].codec);
                } else {
                    tryNext(idx + 1);
                }
            }).catch(() => tryNext(idx + 1));
        };

        tryNext(0);
    }

    /**
     * 解码 H.264 数据片
     */
    decodeData(data) {
        if (!this.isReady || !this.decoder || this.decoder.state !== 'configured') {
            return;
        }

        const isKey = this._containsIDR(data);

        if (!this.gotKey) {
            if (!isKey) {
                this._waitingFrames = (this._waitingFrames || 0) + 1;
                if (this._waitingFrames === 1 || this._waitingFrames % 30 === 0) {
                    console.log('[H264Player] 等待 IDR，已丢弃 delta', this._waitingFrames);
                }
            } else {
                try {
                    const chunk = new EncodedVideoChunk({
                        type: 'key',
                        timestamp: 0,
                        data: data,
                    });
                    this.decoder.decode(chunk);
                    this.gotKey = true;
                } catch (e) {
                    console.error('[H264Player] 首帧 decode 异常:', e);
                }
            }
            return;
        }

        try {
            const chunk = new EncodedVideoChunk({
                type: isKey ? 'key' : 'delta',
                timestamp: (performance.now() * 1000) | 0,
                data: data,
            });
            this.decoder.decode(chunk);
            if (isKey) this.gotKey = true;
        } catch (e) {
            console.error('[H264Player] decode 异常:', e,
                'isKey=', isKey, 'size=', data.byteLength);
            this.gotKey = false;
        }
    }

    _listNalTypes(buf) {
        const view = new Uint8Array(buf);
        const n = view.length, types = [];
        let i = 0;
        while (i + 3 < n) {
            if (view[i] !== 0 || view[i + 1] !== 0) { i += 1; continue; }
            let nalStart;
            if (view[i + 2] === 0 && i + 3 < n && view[i + 3] === 1) { nalStart = i + 4; }
            else if (view[i + 2] === 1) { nalStart = i + 3; }
            else { i += 1; continue; }
            if (nalStart < n) types.push(view[nalStart] & 0x1F);
            i = nalStart + 1;
        }
        return types;
    }

    _containsIDR(buf) {
        const view = new Uint8Array(buf);
        const n = view.length;
        let i = 0;
        while (i + 3 < n) {
            if (view[i] !== 0 || view[i + 1] !== 0) { i += 1; continue; }
            let nalStart;
            if (view[i + 2] === 0 && i + 3 < n && view[i + 3] === 1) { nalStart = i + 4; }
            else if (view[i + 2] === 1) { nalStart = i + 3; }
            else { i += 1; continue; }
            if (nalStart < n && (view[nalStart] & 0x1F) === 5) return true;
            i = nalStart + 1;
        }
        return false;
    }

    renderFrame(frame) {
        if (!this._firstFrameLogged) {
            this._firstFrameLogged = true;
            console.log(`[H264Player] 首帧已渲染 ${frame.displayWidth}x${frame.displayHeight}`);
        }
        if (this.canvas.width !== frame.displayWidth ||
            this.canvas.height !== frame.displayHeight) {
            this.canvas.width = frame.displayWidth;
            this.canvas.height = frame.displayHeight;
        }
        const ctx = this.canvas.getContext('2d');
        ctx.drawImage(frame, 0, 0, this.canvas.width, this.canvas.height);
        this.frameCount++;
    }

    destroy() {
        this.isReady = false;
        this.gotKey = false;
        if (this.decoder) {
            try { if (this.decoder.state !== 'closed') this.decoder.close(); }
            catch (e) { console.warn('[H264Player] decoder close error:', e); }
            this.decoder = null;
        }
        this.ws = null;
        this.frameCount = 0;
        const ctx = this.canvas.getContext('2d');
        if (ctx) ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }
}
