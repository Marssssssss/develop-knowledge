"""opus.py 自检：TOC / 配置表 / 四种 code / 长度编码 / Ogg 封装。实跑版。"""

import struct

from opus import (InvalidPacket, OpusHead, Packet, apply_output_gain,
                  bandwidth_hz, build_toc, config_info, decode_length,
                  duration_limit_ok, encode_length, granule_after,
                  max_frame_count, parse_packet, parse_toc, pcm_sample_position)

TOL = 1e-9
_ok = 0
_bad = []


def near(name, got, want, tol=TOL):
    global _ok
    if abs(got - want) <= tol:
        _ok += 1
    else:
        _bad.append("%s: got %r want %r" % (name, got, want))


def check(name, got, want):
    global _ok
    if got == want:
        _ok += 1
    else:
        _bad.append("%s: got %r want %r" % (name, got, want))


def raises(fn, *args):
    try:
        fn(*args)
    except (InvalidPacket, ValueError):
        return True
    return False


# ------------------------------------------------------------ TOC
check("TOC 0x78 -> config15 mono code0", parse_toc(0x78), (15, 0, 0))
check("TOC 0x7C -> config15 stereo", parse_toc(0x7C), (15, 1, 0))
check("TOC 0x01 -> config0 code1", parse_toc(0x01), (0, 0, 1))
check("TOC 0x02 -> code2", parse_toc(0x02), (0, 0, 2))
check("TOC 0x03 -> code3", parse_toc(0x03), (0, 0, 3))
check("TOC 往返", build_toc(9, True, 2), (9 << 3) | 4 | 2)
check("TOC 往返解析", parse_toc(build_toc(9, True, 2)), (9, 1, 2))

# ------------------------------------------------------------ 配置表
check("config 0 = SILK NB 10ms", config_info(0), ("SILK-only", "NB", 10.0))
check("config 3 = SILK NB 60ms", config_info(3), ("SILK-only", "NB", 60.0))
check("config 4 = SILK MB 10ms", config_info(4), ("SILK-only", "MB", 10.0))
check("config 8 = SILK WB 10ms", config_info(8), ("SILK-only", "WB", 10.0))
check("config 11 = SILK WB 60ms", config_info(11), ("SILK-only", "WB", 60.0))
check("config 12 = Hybrid SWB 10ms", config_info(12), ("Hybrid", "SWB", 10.0))
check("config 13 = Hybrid SWB 20ms", config_info(13), ("Hybrid", "SWB", 20.0))
check("config 14 = Hybrid FB 10ms", config_info(14), ("Hybrid", "FB", 10.0))
check("config 16 = CELT NB 2.5ms", config_info(16), ("CELT-only", "NB", 2.5))
check("config 19 = CELT NB 20ms", config_info(19), ("CELT-only", "NB", 20.0))
check("config 20 = CELT WB 2.5ms", config_info(20), ("CELT-only", "WB", 2.5))
check("config 24 = CELT SWB", config_info(24), ("CELT-only", "SWB", 2.5))
check("config 28 = CELT FB", config_info(28), ("CELT-only", "FB", 2.5))
check("config 31 = CELT FB 20ms", config_info(31), ("CELT-only", "FB", 20.0))

# ------------------------------------------------------------ 带宽表
check("NB 4k/8k", bandwidth_hz("NB"), (4000, 8000))
check("MB 6k/12k", bandwidth_hz("MB"), (6000, 12000))
check("WB 8k/16k", bandwidth_hz("WB"), (8000, 16000))
check("SWB 12k/24k", bandwidth_hz("SWB"), (12000, 24000))
check("FB 20k/48k", bandwidth_hz("FB"), (20000, 48000))

# ------------------------------------------------------------ 120 ms 上限
check("2.5ms 最多 48 帧", max_frame_count(2.5), 48)
check("20ms 最多 6 帧", max_frame_count(20.0), 6)
check("60ms 最多 2 帧", max_frame_count(60.0), 2)
near("2.5ms x 48 = 120ms", 48 * 2.5, 120.0)
check("2.5ms x 49 超限", duration_limit_ok(49, 2.5), False)
check("20ms x 6 通过", duration_limit_ok(6, 20.0), True)

# ------------------------------------------------------------ 长度编码
check("长度 0 单字节", encode_length(0), b"\x00")
check("长度 251 单字节", encode_length(251), b"\xfb")
check("长度 252 两字节", encode_length(252), b"\xfc\x00")
check("长度 300 = 12*4+252", encode_length(300), b"\xfc\x0c")
check("长度 1275 上限", encode_length(1275), b"\xff\xff")
check("越界报错", raises(encode_length, 1276), True)
check("解码单字节", decode_length(bytes([100]), 0), (100, 1))
check("解码两字节", decode_length(bytes([252, 12]), 0), (300, 2))
check("解码偏移", decode_length(bytes([9, 252, 12]), 1), (300, 2))
check("长度往返 700", decode_length(encode_length(700), 0)[0], 700)

# ------------------------------------------------------------ code 0
p0 = parse_packet(bytes([build_toc(15, False, 0)]) + b"\x11" * 40)
check("code0 单帧长度 = N-1", p0.frames, [40])
near("code0 时长 20ms", p0.duration_ms(), 20.0)
check("code0 无帧数字节", p0.count_byte, None)

# ------------------------------------------------------------ code 1
p1 = parse_packet(bytes([build_toc(15, True, 1)]) + b"\x22" * 60)
check("code1 两帧等长", p1.frames, [30, 30])
check("code1 奇数载荷非法",
      raises(parse_packet, bytes([build_toc(15, False, 1)]) + b"\x22" * 61), True)

# ------------------------------------------------------------ code 2
p2 = parse_packet(bytes([build_toc(15, False, 2), 100]) + b"\x33" * 200)
# N = 1(TOC) + 1(N1) + 200；第二帧 = 200 - 100 = 100
check("code2 解析 N1", p2.frames, [100, 100])
check("code2 一字节包非法", raises(parse_packet, bytes([build_toc(15, False, 2)])), True)
check("code2 N1 超载荷非法",
      raises(parse_packet, bytes([build_toc(15, False, 2), 250]) + b"\x33" * 10), True)

# ------------------------------------------------------------ code 3
c3 = bytes([build_toc(15, False, 3), 0x03]) + b"\x44" * 90
p3 = parse_packet(c3)
check("code3 M=3 CBR 等分", p3.frames, [30, 30, 30])
check("code3 vbr=0", p3.vbr, False)
check("code3 无 padding", p3.padding, 0)
check("code3 M=0 非法",
      raises(parse_packet, bytes([build_toc(15, False, 3), 0x00]) + b"\x00"), True)
check("code3 单字节包非法", raises(parse_packet, bytes([build_toc(15, False, 3)])), True)
check("code3 不能整除非法",
      raises(parse_packet, bytes([build_toc(15, False, 3), 0x03]) + b"\x44" * 91), True)
# padding 位
# padding 位：指示字节 10 表示「另有 10 字节填充」，P = 1 + 10 = 11
c3p = bytes([build_toc(15, False, 3), 0x43, 10]) + b"\x55" * 61 + b"\x00" * 10
p3p = parse_packet(c3p)
check("padding 位生效", p3p.padding, 11)
check("padding 后 CBR 等分", p3p.frames, [20, 20, 20])
# VBR（M=2）
# VBR 布局是「长度, 数据, 长度, 数据 ...」
vbr = (bytes([build_toc(15, False, 3), 0x82, 10]) + b"\x66" * 10
       + bytes([20]) + b"\x77" * 20)
pv = parse_packet(vbr)
check("VBR 位生效", pv.vbr, True)
check("VBR 各帧长度", pv.frames, [10, 20])

# ------------------------------------------------------------ Ogg 封装
head = OpusHead(channels=2, pre_skip=3840, input_rate=48000,
                output_gain=0, mapping_family=0)
raw = head.render()
check("ID 头长度", len(raw), 19)
check("Magic", raw[:8], b"OpusHead")
check("version/channels/pre-skip", struct.unpack("<BBH", raw[8:12]), (1, 2, 3840))
check("input rate", struct.unpack("<I", raw[12:16])[0], 48000)
check("output gain", struct.unpack("<h", raw[16:18])[0], 0)
back = OpusHead.parse(raw)
check("ID 头往返 channels", back.channels, 2)
check("ID 头往返 pre-skip", back.pre_skip, 3840)
check("坏 ID 头报错", raises(OpusHead.parse, b"OpusTag" + b"\x00" * 12), True)

near("输出增益 0dB 不变", apply_output_gain(1.0, 0), 1.0)
near("输出增益 +6dB 约 1.995 倍", apply_output_gain(1.0, int(6 * 256)),
     10 ** (6.0 / 20.0), 1e-6)
near("输出增益 -6dB", apply_output_gain(1.0, -int(6 * 256)), 10 ** (-6.0 / 20.0), 1e-6)

check("granule 减 pre-skip", pcm_sample_position(48000, 3840), 44160)
check("20ms 推进 960 样本", granule_after(0, 20.0) - 0, 960)
check("granule 与声道数无关（立体声同样 960）", granule_after(0, 20.0), 960)
check("2.5ms 推进 120", granule_after(0, 2.5), 120)
check("连续三包 20ms", granule_after(granule_after(granule_after(0, 20.0), 20.0), 20.0),
      2880)

print("assertions ok: %d, failed: %d" % (_ok, len(_bad)))
for line in _bad:
    print("FAILED", line)
if _bad:
    raise SystemExit(1)
print("ALL GREEN")
