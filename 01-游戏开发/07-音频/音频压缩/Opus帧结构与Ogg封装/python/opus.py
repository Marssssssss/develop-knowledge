"""Opus 帧结构（RFC 6716）与 Ogg 封装（RFC 7845）。

实读来源：
  * RFC 6716《Definition of the Opus Audio Codec》
    - TOC 字节：config 5 bit | s 1 bit | c 2 bit
    - Table 2 配置表：0..3 SILK NB / 4..7 SILK MB / 8..11 SILK WB（10,20,40,60 ms）
      12..13 Hybrid SWB / 14..15 Hybrid FB（10,20 ms）
      16..19 CELT NB / 20..23 CELT WB / 24..27 CELT SWB / 28..31 CELT FB（2.5,5,10,20 ms）
    - Table 1 带宽：NB 4kHz/8k、MB 6k/12k、WB 8k/16k、SWB 12k/24k、FB 20kHz(*)/48k
    - s=0 单声道 / 1 立体声；c: 0=1 帧, 1=2 帧等长, 2=2 帧不同长度, 3=帧数由字节给出
    - 帧长度编码：0 = 无帧(DTX)，1..251 = 长度，252..255 = 需要第二字节，总长 = 第二字节*4 + 第一字节
    - code 3 帧数字节：v(bit0/MSB) | p(bit1) | M(bits 2..7)；M 不得为 0，包内音频时长不得超过 120 ms
  * RFC 7845《Ogg Encapsulation for the Opus Audio Codec》
    - ID 头：'OpusHead' + version(1) + channel count + pre-skip(16) + input sample rate(32)
      + output gain(16, Q7.8 dB) + mapping family(8)
    - 输出增益应用：sample *= 10^(output_gain/(20*256))
    - granule position 以 48 kHz 样本计（与声道数无关）；'PCM sample position' = granule - pre-skip
    - ID 头页与 comment 头完成页的 granule 必须为 0；整页被单包跨越时 granule = -1
    - 20 ms 包在 48 kHz 下解码出 960 样本
"""

import struct

# ---------------------------------------------------------------- 配置表

BANDWIDTHS = {
    "NB": (4000, 8000),
    "MB": (6000, 12000),
    "WB": (8000, 16000),
    "SWB": (12000, 24000),
    "FB": (20000, 48000),
}

SILK_SIZES = [10.0, 20.0, 40.0, 60.0]
CELT_SIZES = [2.5, 5.0, 10.0, 20.0]
HYBRID_SIZES = [10.0, 20.0]

# (mode, bandwidth, frame sizes)
CONFIGS = {}
for i in range(4):
    CONFIGS[i] = ("SILK-only", "NB", SILK_SIZES, i)
    CONFIGS[4 + i] = ("SILK-only", "MB", SILK_SIZES, i)
    CONFIGS[8 + i] = ("SILK-only", "WB", SILK_SIZES, i)
for i in range(2):
    CONFIGS[12 + i] = ("Hybrid", "SWB", HYBRID_SIZES, i)
    CONFIGS[14 + i] = ("Hybrid", "FB", HYBRID_SIZES, i)
for i in range(4):
    CONFIGS[16 + i] = ("CELT-only", "NB", CELT_SIZES, i)
    CONFIGS[20 + i] = ("CELT-only", "WB", CELT_SIZES, i)
    CONFIGS[24 + i] = ("CELT-only", "SWB", CELT_SIZES, i)
    CONFIGS[28 + i] = ("CELT-only", "FB", CELT_SIZES, i)


class InvalidPacket(ValueError):
    pass


def parse_toc(byte):
    """TOC 字节 -> (config, stereo, code)。"""
    return (byte >> 3) & 0x1F, (byte >> 2) & 0x01, byte & 0x03


def build_toc(config, stereo, code):
    return ((config & 0x1F) << 3) | ((1 if stereo else 0) << 2) | (code & 0x03)


def config_info(config):
    """返回 (mode, bandwidth, frame_size_ms)。"""
    mode, bw, sizes, idx = CONFIGS[config]
    return mode, bw, sizes[idx]


def bandwidth_hz(name):
    """返回 (audio bandwidth, effective sample rate)。"""
    return BANDWIDTHS[name]


def frame_samples(frame_ms, sample_rate=48000):
    """帧长 -> 每声道样本数。"""
    return int(round(frame_ms * sample_rate / 1000.0))


# ---------------------------------------------------------------- 帧长度编码

def decode_length(data, pos):
    """解码 1~2 字节的帧长度，返回 (length, 消耗的字节数)。"""
    if pos >= len(data):
        raise InvalidPacket("truncated length")
    first = data[pos]
    if first < 252:
        return first, 1
    if pos + 1 >= len(data):
        raise InvalidPacket("truncated two-byte length")
    return data[pos + 1] * 4 + first, 2


def encode_length(length):
    """把长度编码成 1 或 2 字节（最大 1275）。"""
    if length < 0 or length > 1275:
        raise InvalidPacket("length out of range")
    if length < 252:
        return bytes([length])
    # 总长 = 第二字节*4 + 第一字节，第一字节取 252..255
    return bytes([252 + (length & 3), (length - 252) >> 2])


# ---------------------------------------------------------------- 包解析

class Packet(object):
    def __init__(self, config, stereo, code, frames, count_byte=None,
                 vbr=False, padding=0):
        self.config = config
        self.stereo = stereo
        self.code = code
        self.frames = frames            # 每帧压缩数据长度（字节）
        self.count_byte = count_byte
        self.vbr = vbr
        self.padding = padding

    def duration_ms(self):
        return len([f for f in self.frames if f > 0]) * config_info(self.config)[2]


def parse_packet(data):
    """解析 Opus 包，返回 Packet（含每帧长度）。"""
    if len(data) < 1:
        raise InvalidPacket("empty packet")
    config, stereo, code = parse_toc(data[0])
    if config not in CONFIGS:
        raise InvalidPacket("unknown configuration")
    n = len(data)
    if code == 0:
        return Packet(config, stereo, code, [n - 1])
    if code == 1:
        rest = n - 1
        if rest % 2 != 0:
            raise InvalidPacket("code 1 needs an even payload")
        return Packet(config, stereo, code, [rest // 2, rest // 2])
    if code == 2:
        if n < 2:
            raise InvalidPacket("code 2 needs at least 2 bytes")
        n1, used = decode_length(data, 1)
        rest = n - 1 - used - n1
        if rest < 0:
            raise InvalidPacket("N1 larger than remaining payload [R4]")
        return Packet(config, stereo, code, [n1, rest])
    # code 3
    if n < 2:
        raise InvalidPacket("code 3 needs at least 2 bytes [R6]")
    ch = data[1]
    count = ch & 0x3F
    padding_bit = (ch >> 6) & 1
    vbr = (ch >> 7) & 1
    if count == 0:
        raise InvalidPacket("M MUST NOT be zero")
    pos = 2
    padding_bytes = 0
    if padding_bit:
        pad_total = 0
        while True:
            if pos >= n:
                raise InvalidPacket("truncated padding")
            b = data[pos]
            pos += 1
            pad_total += 1
            if b == 255:
                pad_total += 254
                continue
            padding_bytes = pad_total + b
            break
    if vbr:
        frames = []
        for _ in range(count):
            length, used = decode_length(data, pos)
            pos += used
            frames.append(length)
            if pos + length > n - padding_bytes:
                raise InvalidPacket("VBR frame exceeds payload")
            pos += length
    else:
        rest = n - padding_bytes - pos
        if rest < 0 or rest % count != 0:
            raise InvalidPacket("CBR payload not divisible by M [R6]")
        frames = [rest // count] * count
    return Packet(config, stereo, code, frames, ch, bool(vbr), padding_bytes)


def duration_limit_ok(count, frame_ms):
    """包内音频时长不得超过 120 ms [R5]。"""
    return count * frame_ms <= 120.0


def max_frame_count(frame_ms):
    """由 120 ms 上限反推最大帧数。"""
    return int(120.0 // frame_ms)


# ---------------------------------------------------------------- Ogg 封装

class OpusHead(object):
    def __init__(self, channels=2, pre_skip=3840, input_rate=48000,
                 output_gain=0, mapping_family=0, version=1):
        self.version = version
        self.channels = channels
        self.pre_skip = pre_skip
        self.input_rate = input_rate
        self.output_gain = output_gain
        self.mapping_family = mapping_family

    def render(self):
        return (b"OpusHead" + struct.pack("<BBH", self.version, self.channels,
                                          self.pre_skip)
                + struct.pack("<I", self.input_rate)
                + struct.pack("<h", self.output_gain)
                + struct.pack("<B", self.mapping_family))

    @staticmethod
    def parse(data):
        if len(data) < 19 or data[:8] != b"OpusHead":
            raise InvalidPacket("bad ID header")
        version, channels, pre_skip = struct.unpack("<BBH", data[8:12])
        input_rate = struct.unpack("<I", data[12:16])[0]
        gain = struct.unpack("<h", data[16:18])[0]
        family = data[18]
        return OpusHead(channels, pre_skip, input_rate, gain, family, version)


def apply_output_gain(sample, output_gain):
    """RFC 7845：sample *= pow(10, output_gain/(20.0*256))。"""
    return sample * (10 ** (output_gain / (20.0 * 256.0)))


def pcm_sample_position(granule, pre_skip):
    """'PCM sample position' = granule position - pre-skip。"""
    return granule - pre_skip


def granule_after(granule, frame_ms, sample_rate=48000):
    """granule 以 48 kHz 样本计，与声道数无关。"""
    return granule + int(round(frame_ms * 48000 / 1000.0))
