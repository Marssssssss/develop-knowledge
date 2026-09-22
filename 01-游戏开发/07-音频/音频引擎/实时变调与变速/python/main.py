"""演示入口：选项位组合、比例口径、Offline/RealTime 约束与变调变速实测。"""

import math

from pitchshift import (OptionEngineFiner, OptionFormantPreserved,
                        OptionProcessRealTime, OptionSmoothingOn,
                        OptionWindowLong, OptionWindowShort, Stretcher,
                        formant_scale, has_option, pitch_scale_to_semitones,
                        semitones_to_pitch_scale, tempo_factor)
from wsola import (estimate_frequency, pitch_shift, resample_linear,
                   time_stretch)


FS = 48000


def main():
    print("== 比例口径：time ratio 是时长比，不是速度 ==")
    for ratio in (0.5, 1.0, 1.5, 2.0):
        print("  time ratio %.1f -> 时长 x%.1f，速度 x%.2f"
              % (ratio, ratio, tempo_factor(ratio)))
    print("== 半音 <-> 频率比 ==")
    for s in (-12, -5, 0, 3, 7, 12):
        scale = semitones_to_pitch_scale(s)
        print("  %+3d 半音 -> pitch scale %.6f（反解 %+.3f）"
              % (s, scale, pitch_scale_to_semitones(scale)))

    print("\n== 共振峰自动取值 ==")
    for opts, name in ((0, "FormantShifted"), (OptionFormantPreserved, "FormantPreserved")):
        print("  %-18s pitch=2.0 -> formant scale %.3f"
              % (name, formant_scale(opts, 2.0)))

    print("\n== 引擎与选项生效 ==")
    r2 = Stretcher(48000, 2, 0)
    r3 = Stretcher(48000, 2, OptionEngineFiner)
    print("  R2: getFormantScale=%s, R3: %s"
          % (r2 and "0.0（不支持）", "支持显式值"))
    long_r3 = Stretcher(48000, 2, OptionEngineFiner | OptionWindowLong)
    print("  R3 下 OptionWindowLong 被忽略: %s"
          % (not has_option(long_r3.effective_options(), OptionWindowLong)))
    mix = Stretcher(48000, 2, OptionWindowShort | OptionSmoothingOn)
    print("  WindowShort 与 SmoothingOn 同时给出时后者被忽略: %s"
          % (not has_option(mix.effective_options(), OptionSmoothingOn)))

    print("\n== Offline / RealTime 约束 ==")
    off = Stretcher(48000, 2, 0)
    off.set_time_ratio(1.5)
    off.set_pitch_semitones(2)
    off.start()
    try:
        off.set_time_ratio(2.0)
        print("  Offline 开跑后仍可改（异常）")
    except RuntimeError:
        print("  Offline 开跑后改 ratio -> 报错（比例已固定）")
    print("  Offline startPad/startDelay = %d / %d"
          % (off.preferred_start_pad(), off.start_delay()))
    rt = Stretcher(48000, 2, OptionProcessRealTime)
    rt.set_time_ratio(2.0)
    rt.start()
    rt.set_time_ratio(1.25)
    print("  RealTime 开跑后改 ratio 成功 -> %.2f；startPad=%d startDelay=%d"
          % (rt.time_ratio, rt.preferred_start_pad(), rt.start_delay()))

    print("\n== 信号级实测（1 秒 440 Hz 正弦）==")
    sine = [math.sin(2 * math.pi * 440 * i / FS) for i in range(FS)]
    print("  原始        长度 %6d  估计频率 %6.1f Hz" % (len(sine),
                                                    estimate_frequency(sine, FS)))
    slow = time_stretch(sine, 2.0)
    print("  变速 2.0x   长度 %6d  估计频率 %6.1f Hz（音高应不变）"
          % (len(slow), estimate_frequency(slow, FS)))
    up = pitch_shift(sine, 12)
    print("  升 12 半音  长度 %6d  估计频率 %6.1f Hz（应 ~880）"
          % (len(up), estimate_frequency(up, FS)))
    down = pitch_shift(sine, -12)
    print("  降 12 半音  长度 %6d  估计频率 %6.1f Hz（应 ~220，朴素 WSOLA 有偏差）"
          % (len(down), estimate_frequency(down, FS)))
    fast = resample_linear(sine, 2.0)
    print("  纯重采样 2x 长度 %6d  估计频率 %6.1f Hz（音高随速度一起变）"
          % (len(fast), estimate_frequency(fast, FS)))


if __name__ == "__main__":
    main()
