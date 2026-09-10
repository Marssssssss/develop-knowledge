"""ima_adpcm.py — IMA ADPCM(自适应差分脉冲编码)最小实现

演示内容:
  1. 16-bit PCM -> 4-bit IMA ADPCM 流式编解码(压缩比 4:1)
  2. 单 nibble 误码的传播与自恢复特性
  3. 块结构(4 字节头部 + nibble 数据)支持的随机访问解码

算法依据:
  - Davis Yen Pan, "Digital Audio Compression",
    Digital Technical Journal Vol.5 No.2, Spring 1993(第 3 节 IMA ADPCM)
  - RFC 3551 Section 4.5.1(DVI4 = IMA ADPCM 的 RTP 封装)
"""

import math
import struct

SAMPLE_RATE = 8000
NUM_SAMPLES = 2500  # 1000 静音段 + 500 突变段 + 1000 回落段
BLOCK_SAMPLES = 256
BLOCK_HEADER = 4  # int16 predictor + uint8 index + uint8 reserved

# 步长表:89 级,近似指数增长(每级约为前级 1.1 倍),7 -> 32767。
# 注:论文 Table 2 索引 84 处印作 22358,标准实现(ffmpeg/IMA 规范)为 22385,
# 此处以标准实现为准(见 README"注意事项")。
STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]

# 索引调整表:幅值小的码字(0-3 / 8-11)把索引 -1(步长收缩),
# 幅值大的码字(4-7 / 12-15)把索引 +2/+4/+6/+8(步长扩张)。
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def decode_sample(code, predictor, index):
    """解码单个 4-bit 码字,返回 (重建样本, 新 predictor, 新 index)。

    predictor 即"预测器"= 上一个重建样本(一阶延迟);
    step>>3 为半步长偏置,使重建值落在量化区间中部。
    """
    step = STEP_TABLE[index]
    diff = step >> 3
    if code & 4:
        diff += step
    if code & 2:
        diff += step >> 1
    if code & 1:
        diff += step >> 2
    if code & 8:
        diff = -diff
    predictor = clamp(predictor + diff, -32768, 32767)
    index = clamp(index + INDEX_TABLE[code], 0, 88)
    return predictor, predictor, index


def encode_sample(sample, predictor, index):
    """编码单个样本:符号位 + 三级阈值比较(论文图 3)。

    编码后立即用同一解码逻辑推进状态 —— 论文图 2a:
    编码器内嵌解码器,保证收发两端状态逐样本同步。
    """
    diff = sample - predictor
    code = 0
    if diff < 0:
        code = 8
        diff = -diff
    step = STEP_TABLE[index]
    if diff >= step:
        code |= 4
        diff -= step
    if diff >= step >> 1:
        code |= 2
        diff -= step >> 1
    if diff >= step >> 2:
        code |= 1
    _, predictor, index = decode_sample(code, predictor, index)
    return code, predictor, index


def encode_stream(pcm):
    """整段编码为连续 nibble 流(IMA WAV 打包惯例:偶数样本放低 4 位)。"""
    out = bytearray()
    predictor, index = 0, 0
    for i, s in enumerate(pcm):
        code, predictor, index = encode_sample(s, predictor, index)
        if i % 2 == 0:
            out.append(code)
        else:
            out[-1] |= code << 4
    return bytes(out)


def decode_stream(data, n):
    out = []
    predictor, index = 0, 0
    for i in range(n):
        code = (data[i // 2] & 0x0F) if i % 2 == 0 else (data[i // 2] >> 4)
        _, predictor, index = decode_sample(code, predictor, index)
        out.append(predictor)
    return out


def encode_blocks(pcm):
    """分块编码:每块头部记录块首状态,支持随机访问。

    头部布局(参照 RFC 3551 DVI4 块头):
      [0..1] int16 predictor(小端)  [2] uint8 index  [3] uint8 reserved
    """
    out = bytearray()
    predictor, index = 0, 0
    for off in range(0, len(pcm), BLOCK_SAMPLES):
        chunk = pcm[off:off + BLOCK_SAMPLES]
        out += struct.pack("<hBB", predictor, index, 0)
        for i, s in enumerate(chunk):
            code, predictor, index = encode_sample(s, predictor, index)
            if i % 2 == 0:
                out.append(code)
            else:
                out[-1] |= code << 4
        if len(chunk) % 2 == 1:
            pass  # 末块奇数个样本,高 4 位留 0
    return bytes(out)


def decode_blocks_from(blocks, start_block):
    """从指定块号开始解码(随机访问:仅依赖该块头部状态)。

    除末块外每块固定 128 字节数据(256 样本);末块可能不足。
    """
    out = []
    pos, block = 0, 0
    full_data = BLOCK_SAMPLES // 2
    while pos + BLOCK_HEADER <= len(blocks):
        body = len(blocks) - pos - BLOCK_HEADER
        data_bytes = full_data if body >= full_data else body
        cnt = data_bytes * 2
        if block >= start_block:
            predictor = struct.unpack_from("<h", blocks, pos)[0]
            index = blocks[pos + 2]
            base = pos + BLOCK_HEADER
            for i in range(cnt):
                code = (blocks[base + i // 2] & 0x0F) if i % 2 == 0 else (blocks[base + i // 2] >> 4)
                _, predictor, index = decode_sample(code, predictor, index)
                out.append(predictor)
        pos += BLOCK_HEADER + data_bytes
        block += 1
    return out


def error_stats(orig, dec, lo, hi):
    errs = [abs(orig[i] - dec[i]) for i in range(lo, hi)]
    return max(errs), sum(errs) / len(errs)


def gen_signal():
    pcm = []
    for i in range(NUM_SAMPLES):
        t = i / SAMPLE_RATE
        amp = 600.0 if i < 1000 else (12000.0 if i < 1500 else 600.0)
        pcm.append(int(clamp(amp * math.sin(2 * math.pi * 440.0 * t), -32768, 32767)))
    return pcm


def main():
    pcm = gen_signal()
    print("=== IMA ADPCM demo ===")
    print(f"采样率 {SAMPLE_RATE} Hz, 样本数 {NUM_SAMPLES} (PCM {NUM_SAMPLES * 2} 字节)")

    # [1] 流式编解码
    stream = bytearray(encode_stream(pcm))
    dec = decode_stream(stream, NUM_SAMPLES)
    maxe, meane = error_stats(pcm, dec, 0, NUM_SAMPLES)
    print("[1] 流式编解码")
    print(f"    编码后 {len(stream)} 字节, 压缩比 {NUM_SAMPLES * 2 / len(stream):.2f}:1 (理论 4:1)")
    print(f"    全段误差: 最大 {maxe}, 平均 {meane:.1f}")
    m1, _ = error_stats(pcm, dec, 0, 1000)
    m2, _ = error_stats(pcm, dec, 1000, 1500)
    m3, _ = error_stats(pcm, dec, 1500, NUM_SAMPLES)
    print(f"    分段最大误差: 静音段 {m1} / 突变段 {m2} / 回落段 {m3}")

    # [2] 抗误码:篡改中间 1 个 nibble
    stream[NUM_SAMPLES // 4] ^= 0x0F
    dec2 = decode_stream(stream, NUM_SAMPLES)
    p = NUM_SAMPLES // 2
    print(f"[2] 抗误码: 篡改位置 {p} 处 1 个 nibble")
    print(f"    损坏点误差: {abs(pcm[p] - dec2[p])}")
    print(f"    之后 100 样本: {abs(pcm[p + 100] - dec2[p + 100])} / "
          f"之后 500 样本: {abs(pcm[p + 500] - dec2[p + 500])}"
          "  (步长已收敛, 残差为恒定直流偏置)")
    stream[NUM_SAMPLES // 4] ^= 0x0F  # 还原

    # [3] 块随机访问
    blocks = encode_blocks(pcm)
    dec = decode_stream(stream, NUM_SAMPLES)  # 无损坏基准
    dec2 = decode_blocks_from(blocks, 5)
    same = dec2 == dec[5 * BLOCK_SAMPLES:5 * BLOCK_SAMPLES + len(dec2)]
    print(f"[3] 块结构: 共 {len(blocks)} 字节 (含 10 个块头), "
          f"压缩比 {NUM_SAMPLES * 2 / len(blocks):.2f}:1")
    print(f"    从第 5 块头部随机访问解码 {len(dec2)} 样本, 与全量解码一致: {'是' if same else '否'}")


if __name__ == "__main__":
    main()
