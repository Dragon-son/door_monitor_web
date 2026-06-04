/**
 * Codec-agnostic WebSocket 视频播放器(H.264 + H.265,WebCodecs API)
 *
 * 工作流程:
 *  - 收到第一段 binary 时跑双套 NAL header 规则嗅探 codec
 *    H.264: nal_type = byte0 & 0x1F,SPS=7 PPS=8 IDR=5
 *    H.265: nal_type = (byte0 >> 1) & 0x3F,VPS=32 SPS=33 PPS=34 IDR=19/20
 *  - 嗅出后 configure 对应 VideoDecoder
 *  - 等到第一个 IDR 才送进 decoder,丢弃之前的 delta
 *  - 服务端拼好 access unit 才广播,每条 binary 已经是完整一帧(含 SPS/PPS/(VPS)+IDR)
 */

class VideoPlayer {
    constructor(canvas) {
        this.canvas = canvas;
        this.decoder = null;
        this.ws = null;
        this.codec = null;        // 'h264' | 'h265' | null
        this.frameCount = 0;
        this.isReady = false;
        this.gotKey = false;

        if (!('VideoDecoder' in window)) {
            console.error('[VideoPlayer] 浏览器不支持 WebCodecs API');
        }
    }

    static isSupported() {
        return 'VideoDecoder' in window;
    }

    init(ws) {
        this.ws = ws;
        ws.addEventListener('message', (event) => {
            if (event.data instanceof ArrayBuffer) {
                this._onBinary(event.data);
            }
        });
    }

    _onBinary(data) {
        if (this.codec === null) {
            const codec = this._sniffCodec(data);
            if (!codec) {
                return;  // 还没看到关键 NAL,继续等
            }
            this.codec = codec;
            console.log('[VideoPlayer] 嗅探 codec=' + codec);
            this._pendingBuffers = [];
            this._createDecoder(codec);
        }
        // decoder 是异步 configure,期间到达的 AU(包括含 IDR 的首帧 webcodecs_init)
        // 不能丢——先暂存,等 isReady 时一起喂进 decoder,否则要等下一个 IDR(可能 1~2 秒)。
        if (!this.isReady) {
            this._pendingBuffers.push(data);
            // 防御:configure 异常拖太久时,只保留最近 60 段,避免无限增长
            if (this._pendingBuffers.length > 60) {
                this._pendingBuffers.shift();
            }
            return;
        }
        this._decodeData(data);
    }

    _createDecoder(codec) {
        this.isReady = false;
        this.gotKey = false;
        if (this.decoder) {
            try { if (this.decoder.state !== 'closed') this.decoder.close(); } catch (e) {}
            this.decoder = null;
        }

        this.decoder = new VideoDecoder({
            output: (frame) => { this._renderFrame(frame); frame.close(); },
            error: (e) => {
                console.error('[VideoPlayer] 解码错误,将重建 decoder:', e);
                setTimeout(() => this._createDecoder(this.codec), 0);
            }
        });

        // hardwareAcceleration: 'prefer-hardware' —— Edge 默认对 hev1 走软解 fallback
        // 并把 1440p 内部降采样到 720p。明确请求硬解后,有硬件 HEVC 解码能力的机器
        // 会拿到原始分辨率;Chrome 本来就是硬解,这个标记对它无影响。
        const tryConfigs = codec === 'h265' ? [
            { codec: 'hev1.1.6.L150.B0', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'hev1.1.6.L120.B0', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'hev1.2.4.L120.B0', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'hvc1.1.6.L150.B0', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'hvc1.1.6.L120.B0', hardwareAcceleration: 'prefer-hardware' },
            // 兜底:让浏览器自选(no-preference),保住"能播"
            { codec: 'hev1.1.6.L150.B0' },
            { codec: 'hev1.1.6.L120.B0' },
        ] : [
            { codec: 'avc1.4D4032', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'avc1.640032', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'avc1.4D4028', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'avc1.640028', hardwareAcceleration: 'prefer-hardware' },
            { codec: 'avc1.4D4032' },
            { codec: 'avc1.640032' },
            { codec: 'avc1.4D4028' },
            { codec: 'avc1.640028' },
            { codec: 'avc1.42E01E' },
        ];

        const tryNext = (idx) => {
            if (idx >= tryConfigs.length) {
                console.error('[VideoPlayer] 所有 ' + codec + ' codec 都不支持');
                return;
            }
            VideoDecoder.isConfigSupported(tryConfigs[idx]).then((result) => {
                if (result.supported) {
                    this.decoder.configure(result.config);
                    this.isReady = true;
                    this._waitingFrames = 0;
                    console.log('[VideoPlayer] 解码器已就绪 codec=' + tryConfigs[idx].codec);
                    // 把 configure 期间排队的 binary 一次喂进去:首帧 webcodecs_init 含 IDR,
                    // 这里 flush 出去就能立即起播,不用再等下一个 IDR 周期。
                    const pending = this._pendingBuffers || [];
                    this._pendingBuffers = [];
                    for (const buf of pending) {
                        this._decodeData(buf);
                    }
                } else {
                    tryNext(idx + 1);
                }
            }).catch(() => tryNext(idx + 1));
        };

        tryNext(0);
    }

    _decodeData(data) {
        if (!this.isReady || !this.decoder || this.decoder.state !== 'configured') {
            return;
        }

        const isKey = this._containsIDR(data);

        if (!this.gotKey) {
            if (!isKey) {
                this._waitingFrames = (this._waitingFrames || 0) + 1;
                if (this._waitingFrames === 1 || this._waitingFrames % 30 === 0) {
                    console.log('[VideoPlayer] 等待 IDR,已丢弃 delta', this._waitingFrames);
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
                    console.error('[VideoPlayer] 首帧 decode 异常:', e);
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
            console.error('[VideoPlayer] decode 异常:', e,
                'codec=', this.codec, 'isKey=', isKey, 'size=', data.byteLength);
            this.gotKey = false;
        }
    }

    /**
     * 双套规则嗅探 codec。规则同后端 routes/codec_parsers.py 的 sniff_codec。
     * 哪边关键 NAL 命中且 >= 1 即返回该 codec;两边都没命中返回 null。
     */
    _sniffCodec(buf) {
        const view = new Uint8Array(buf);
        const n = view.length;
        let h264Hits = 0;
        let h265Hits = 0;
        let i = 0;
        while (i + 3 < n) {
            if (view[i] !== 0 || view[i + 1] !== 0) { i += 1; continue; }
            let payloadStart;
            if (view[i + 2] === 0 && i + 3 < n && view[i + 3] === 1) { payloadStart = i + 4; }
            else if (view[i + 2] === 1) { payloadStart = i + 3; }
            else { i += 1; continue; }
            if (payloadStart >= n) break;

            const t264 = view[payloadStart] & 0x1F;
            if (t264 === 5 || t264 === 7 || t264 === 8) h264Hits += 1;

            if (payloadStart + 1 < n) {
                const t265 = (view[payloadStart] >> 1) & 0x3F;
                if (t265 === 19 || t265 === 20 || t265 === 32 || t265 === 33 || t265 === 34) {
                    h265Hits += 1;
                }
            }
            i = payloadStart + 1;
        }
        if (h265Hits > h264Hits && h265Hits >= 1) return 'h265';
        if (h264Hits >= 1) return 'h264';
        return null;
    }

    /**
     * 是否含 IDR NAL。规则随 codec 切换:
     *   H.264: nal_type = byte0 & 0x1F,IDR = 5
     *   H.265: nal_type = (byte0 >> 1) & 0x3F,IDR = 19 / 20
     */
    _containsIDR(buf) {
        const view = new Uint8Array(buf);
        const n = view.length;
        let i = 0;
        const isH265 = this.codec === 'h265';
        while (i + 3 < n) {
            if (view[i] !== 0 || view[i + 1] !== 0) { i += 1; continue; }
            let payloadStart;
            if (view[i + 2] === 0 && i + 3 < n && view[i + 3] === 1) { payloadStart = i + 4; }
            else if (view[i + 2] === 1) { payloadStart = i + 3; }
            else { i += 1; continue; }
            if (payloadStart >= n) break;
            if (isH265) {
                const t = (view[payloadStart] >> 1) & 0x3F;
                if (t === 19 || t === 20) return true;
            } else {
                if ((view[payloadStart] & 0x1F) === 5) return true;
            }
            i = payloadStart + 1;
        }
        return false;
    }

    _renderFrame(frame) {
        if (!this._firstFrameLogged) {
            this._firstFrameLogged = true;
            console.log(`[VideoPlayer] 首帧已渲染 ${frame.displayWidth}x${frame.displayHeight}`);
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
        this.codec = null;
        this._pendingBuffers = null;
        if (this.decoder) {
            try { if (this.decoder.state !== 'closed') this.decoder.close(); }
            catch (e) { console.warn('[VideoPlayer] decoder close error:', e); }
            this.decoder = null;
        }
        this.ws = null;
        this.frameCount = 0;
        this._firstFrameLogged = false;
        const ctx = this.canvas.getContext('2d');
        if (ctx) ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }
}
