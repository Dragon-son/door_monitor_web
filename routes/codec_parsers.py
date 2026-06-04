"""Codec-agnostic NAL 解析层。

支持 H.264 (AVC) 和 H.265 (HEVC) Annex-B 裸流的:
  - NAL 起始码扫描
  - SPS / PPS / VPS / IDR / VCL 识别
  - 新 picture 第一个 slice 判定(用于 access unit 边界)

两个 codec 的 NAL header 长度不同(H.264 1 字节 / H.265 2 字节),
NAL type 提取位也不同(H.264 byte0 & 0x1F / H.265 (byte0 >> 1) & 0x3F),
其余起始码扫描逻辑完全一致,故抽公共 _extract_nals_generic 复用。
"""


def _extract_nals_generic(buf, header_size):
    """扫描 Annex B 流,返回 [(nal_type, start, end), ...]

    - start 包含起始码
    - end 是下一个起始码起点或 buf 末尾
    - nal_type 由调用方根据 header_size 解析(本函数只定位,不解释类型)

    返回的 nal_type 用占位 -1,调用方需要自己根据 codec 规则重新计算;
    这样 H.265 才能用 2 字节 header 正确取出类型。
    """
    n = len(buf)
    nals = []
    i = 0
    while i + 3 < n:
        if buf[i] == 0 and buf[i + 1] == 0:
            if buf[i + 2] == 1:
                sc_len = 3
            elif buf[i + 2] == 0 and i + 3 < n and buf[i + 3] == 1:
                sc_len = 4
            else:
                i += 1
                continue
            nal_start = i
            payload_start = i + sc_len
            if payload_start + header_size > n:
                break
            # 留待 caller 解析 nal_type
            # 找下一个起始码
            j = payload_start + header_size
            while j + 2 < n:
                if buf[j] == 0 and buf[j + 1] == 0 and \
                        (buf[j + 2] == 1 or (j + 3 < n and buf[j + 2] == 0 and buf[j + 3] == 1)):
                    break
                j += 1
            end = j if j + 2 < n else n
            nals.append((payload_start, nal_start, end))
            i = end
            continue
        i += 1
    return nals


class H264Parser:
    codec_name = "h264"
    header_size = 1

    def extract_nals(self, buf):
        out = []
        for payload_start, nal_start, end in _extract_nals_generic(buf, self.header_size):
            nal_type = buf[payload_start] & 0x1F
            out.append((nal_type, nal_start, end))
        return out

    @staticmethod
    def is_vcl(t):
        return t in (1, 5)

    @staticmethod
    def is_idr(t):
        return t == 5

    @staticmethod
    def is_sps(t):
        return t == 7

    @staticmethod
    def is_pps(t):
        return t == 8

    @staticmethod
    def is_vps(t):
        return False

    @staticmethod
    def is_new_picture_first_slice(nal_bytes):
        """H.264: slice_header 第一个字段 first_mb_in_slice (ue(v)),
        紧跟在 1 字节 NAL header 后。Exp-Golomb 0 = bit '1',
        即该 byte 最高 bit 为 1。
        """
        n = len(nal_bytes)
        if n >= 4 and nal_bytes[0] == 0 and nal_bytes[1] == 0 \
                and nal_bytes[2] == 0 and nal_bytes[3] == 1:
            sh_idx = 5
        elif n >= 3 and nal_bytes[0] == 0 and nal_bytes[1] == 0 and nal_bytes[2] == 1:
            sh_idx = 4
        else:
            return False
        if sh_idx >= n:
            return False
        return (nal_bytes[sh_idx] & 0x80) != 0


class H265Parser:
    codec_name = "h265"
    header_size = 2

    def extract_nals(self, buf):
        out = []
        for payload_start, nal_start, end in _extract_nals_generic(buf, self.header_size):
            nal_type = (buf[payload_start] >> 1) & 0x3F
            out.append((nal_type, nal_start, end))
        return out

    @staticmethod
    def is_vcl(t):
        # H.265 VCL NAL 类型范围:0..31
        return 0 <= t <= 31

    @staticmethod
    def is_idr(t):
        # IDR_W_RADL = 19, IDR_N_LP = 20
        return t in (19, 20)

    @staticmethod
    def is_sps(t):
        return t == 33

    @staticmethod
    def is_pps(t):
        return t == 34

    @staticmethod
    def is_vps(t):
        return t == 32

    @staticmethod
    def is_new_picture_first_slice(nal_bytes):
        """H.265: slice_segment_header 第一个字段 first_slice_segment_in_pic_flag u(1),
        紧跟在 2 字节 NAL header 后。该 bit 即首个 byte 最高 bit。
        """
        n = len(nal_bytes)
        if n >= 5 and nal_bytes[0] == 0 and nal_bytes[1] == 0 \
                and nal_bytes[2] == 0 and nal_bytes[3] == 1:
            sh_idx = 6  # 4 起始码 + 2 NAL header
        elif n >= 4 and nal_bytes[0] == 0 and nal_bytes[1] == 0 and nal_bytes[2] == 1:
            sh_idx = 5  # 3 起始码 + 2 NAL header
        else:
            return False
        if sh_idx >= n:
            return False
        return (nal_bytes[sh_idx] & 0x80) != 0


def sniff_codec(buf):
    """扫一段 Annex-B 流,判定它是 H.264 还是 H.265。

    用 H.264 / H.265 两套 NAL header 规则各跑一遍 extract_nals,
    统计关键 NAL(SPS / PPS / IDR / H.265 VPS)的命中数。哪边命中且
    ≥1 即返回对应 codec;两边都没命中返回 None,表示还需要更多数据。
    """
    h264 = H264Parser()
    h265 = H265Parser()

    h264_nals = h264.extract_nals(buf)
    h265_nals = h265.extract_nals(buf)

    h264_hits = sum(
        1 for t, _, _ in h264_nals
        if h264.is_sps(t) or h264.is_pps(t) or h264.is_idr(t)
    )
    h265_hits = sum(
        1 for t, _, _ in h265_nals
        if h265.is_vps(t) or h265.is_sps(t) or h265.is_pps(t) or h265.is_idr(t)
    )

    if h265_hits > h264_hits and h265_hits >= 1:
        return "h265"
    if h264_hits >= 1:
        return "h264"
    return None


def make_parser(codec_name):
    if codec_name == "h265":
        return H265Parser()
    return H264Parser()
