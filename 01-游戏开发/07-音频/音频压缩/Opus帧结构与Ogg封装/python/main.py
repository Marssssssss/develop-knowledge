"""演示入口：TOC 解析、配置表查表、四种 code 的组包/解包与 Ogg 时间轴。"""

from opus import (OpusHead, apply_output_gain, bandwidth_hz, build_toc,
                  config_info, encode_length, granule_after, max_frame_count,
                  parse_packet, parse_toc, pcm_sample_position)


def main():
    print("== TOC 字节拆解（RFC 6716 §3.1）==")
    for toc in (0x78, 0x7C, 0x03, 0x4A):
        config, s, code = parse_toc(toc)
        mode, bw, size = config_info(config)
        print("  0x%02X -> config=%2d %-9s %-3s %5.1fms | s=%d(%s) | code=%d"
              % (toc, config, mode, bw, size, s, "立体声" if s else "单声道", code))

    print("\n== 配置表（mode / 带宽 / 帧长）==")
    for config in (0, 4, 8, 12, 14, 16, 20, 24, 28):
        mode, bw, size = config_info(config)
        audio_bw, rate = bandwidth_hz(bw)
        print("  config %2d..%2d: %-10s %-4s 音频带宽 %5d Hz / 采样率 %5d Hz / %4.1f ms"
              % (config, config + (3 if mode == "SILK-only" or mode == "CELT-only" else 1),
                 mode, bw, audio_bw, rate, size))

    print("\n== 120 ms 上限反推最大帧数 ==")
    for size in (2.5, 5.0, 10.0, 20.0, 40.0, 60.0):
        print("  %4.1f ms -> 最多 %2d 帧" % (size, max_frame_count(size)))

    print("\n== 帧长度编码（0=DTX，1..251 单字节，252..255 两字节）==")
    for length in (0, 251, 252, 300, 1275):
        print("  %5d -> %s" % (length, encode_length(length).hex()))

    print("\n== 四种 code 的组包 ==")
    toc = build_toc(15, False, 0)
    print("  code0 (1 帧):     " + str(parse_packet(bytes([toc]) + b"\x11" * 40).frames))
    print("  code1 (2 帧等长): " +
          str(parse_packet(bytes([build_toc(15, True, 1)]) + b"\x22" * 60).frames))
    print("  code2 (2 帧异长): " +
          str(parse_packet(bytes([build_toc(15, False, 2), 100]) + b"\x33" * 200).frames))
    print("  code3 (CBR M=3):  " +
          str(parse_packet(bytes([build_toc(15, False, 3), 0x03]) + b"\x44" * 90).frames))
    vbr = (bytes([build_toc(15, False, 3), 0x82, 10]) + b"\x66" * 10
           + bytes([20]) + b"\x77" * 20)
    print("  code3 (VBR M=2):  " + str(parse_packet(vbr).frames))

    print("\n== Ogg 时间轴（granule 以 48 kHz 样本计）==")
    head = OpusHead(channels=2, pre_skip=3840, input_rate=48000)
    print("  ID 头 %d 字节: %s" % (len(head.render()), head.render()))
    print("  pre-skip = %d 样本 = %.1f ms" % (head.pre_skip, head.pre_skip / 48.0))
    granule = 0
    for i in range(1, 4):
        granule = granule_after(granule, 20.0)
        pos = pcm_sample_position(granule, head.pre_skip)
        print("  第 %d 个 20ms 包后 granule=%6d, PCM 位置=%6d 样本 (%.3f s)%s"
              % (i, granule, pos, pos / 48000.0,
                 "  <- 仍在 pre-skip 内，需解码但丢弃" if pos < 0 else ""))
    print("  首包 granule 59971 / pre-skip 11971 的 PCM 位置 = %d（RFC 7845 示例）"
          % pcm_sample_position(59971, 11971))

    print("\n== 输出增益 Q7.8 ==")
    for gain in (-1536, -256, 0, 256, 1536):
        print("  raw %6d -> 系数 %.6f" % (gain, apply_output_gain(1.0, gain)))


if __name__ == "__main__":
    main()
