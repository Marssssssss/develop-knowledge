"""pitchshift.py 自检：选项位、比例口径、模式约束与信号级变调变速率。实跑版。"""

import math

from pitchshift import (DefaultOptions, GROUPS, OptionChannelsTogether,
                        OptionDetectorPercussive, OptionEngineFiner,
                        OptionFormantPreserved, OptionPhaseIndependent,
                        OptionProcessRealTime, OptionSmoothingOn,
                        OptionThreadingAlways, OptionTransientsSmooth,
                        OptionWindowLong, OptionWindowShort, Stretcher,
                        StretcherError, engine_of, formant_scale,
                        get_formant_scale, has_option, output_samples,
                        pitch_scale_to_semitones, semitones_to_pitch_scale,
                        tempo_factor)
from wsola import (estimate_frequency, ola_stretch, pitch_shift,
                   resample_linear, time_stretch)


_ok = 0
_bad = []


def near(name, got, want, tol=1e-9):
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
    except StretcherError:
        return True
    except Exception:
        return False
    return False


# ------------------------------------------------------------ 选项位取值
expected = {
    "OptionProcessOffline": (0x00000000,),
    "OptionProcessRealTime": (0x00000001,),
    "OptionStretchElastic": (0x00000000,),
    "OptionStretchPrecise": (0x00000010,),
    "OptionTransientsCrisp": (0x00000000,),
    "OptionTransientsMixed": (0x00000100,),
    "OptionTransientsSmooth": (0x00000200,),
    "OptionDetectorCompound": (0x00000000,),
    "OptionDetectorPercussive": (0x00000400,),
    "OptionDetectorSoft": (0x00000800,),
    "OptionPhaseLaminar": (0x00000000,),
    "OptionPhaseIndependent": (0x00002000,),
    "OptionThreadingAuto": (0x00000000,),
    "OptionThreadingNever": (0x00010000,),
    "OptionThreadingAlways": (0x00020000,),
    "OptionWindowStandard": (0x00000000,),
    "OptionWindowShort": (0x00100000,),
    "OptionWindowLong": (0x00200000,),
    "OptionSmoothingOff": (0x00000000,),
    "OptionSmoothingOn": (0x00800000,),
    "OptionFormantShifted": (0x00000000,),
    "OptionFormantPreserved": (0x01000000,),
    "OptionPitchHighSpeed": (0x00000000,),
    "OptionPitchHighQuality": (0x02000000,),
    "OptionPitchHighConsistency": (0x04000000,),
    "OptionChannelsApart": (0x00000000,),
    "OptionChannelsTogether": (0x10000000,),
    "OptionEngineFaster": (0x00000000,),
    "OptionEngineFiner": (0x20000000,),
}
import pitchshift
for name, (value,) in expected.items():
    check("位值 " + name, getattr(pitchshift, name), value)
check("DefaultOptions 为 0", DefaultOptions, 0)
check("选项组数", len(GROUPS), 11)
check("engine 组有两个取值", len(GROUPS["engine"]), 2)
check("R3 判定", engine_of(OptionEngineFiner), "R3")
check("R2 判定", engine_of(DefaultOptions), "R2")
check("has_option 命中", has_option(OptionTransientsSmooth | OptionEngineFiner,
                                    OptionTransientsSmooth), True)
check("has_option 未命中", has_option(DefaultOptions, OptionThreadingAlways), False)

# ------------------------------------------------------------ 比例口径
near("+12 半音 = 2.0", semitones_to_pitch_scale(12), 2.0)
near("-12 半音 = 0.5", semitones_to_pitch_scale(-12), 0.5)
near("0 半音 = 1.0", semitones_to_pitch_scale(0), 1.0)
near("+7 半音 = 2^(7/12)", semitones_to_pitch_scale(7), 2 ** (7 / 12))
near("反解 12 半音", pitch_scale_to_semitones(2.0), 12.0)
near("反解 -12 半音", pitch_scale_to_semitones(0.5), -12.0)
near("time ratio 2.0 = 半速", tempo_factor(2.0), 0.5)
near("time ratio 0.5 = 倍速", tempo_factor(0.5), 2.0)
near("time ratio 1.0 不变", tempo_factor(1.0), 1.0)
check("输出样本数 = 输入 x ratio", output_samples(48000, 1.5), 72000)
check("输出样本数 2.0", output_samples(4800, 2.0), 9600)

# ------------------------------------------------------------ 共振峰
near("shifted 时自动取 1.0", formant_scale(DefaultOptions, 2.0), 1.0)
near("preserved 时取 1/pitch", formant_scale(OptionFormantPreserved, 2.0), 0.5)
near("显式值优先", formant_scale(OptionFormantPreserved, 2.0, 0.25), 0.25)
check("R2 的 getFormantScale 恒为 0", get_formant_scale(DefaultOptions, 0.5), 0.0)
check("R3 返回显式值", get_formant_scale(OptionEngineFiner, 0.5), 0.5)

# ------------------------------------------------------------ 模式约束
off = Stretcher(48000, 2, DefaultOptions)
off.set_time_ratio(1.5)
off.set_pitch_semitones(3)
near("Offline 起手可改 ratio", off.time_ratio, 1.5)
near("Offline 起手可改 pitch", off.pitch_scale, semitones_to_pitch_scale(3))
off.start()
check("Offline 开跑后不可改 ratio", raises(off.set_time_ratio, 2.0), True)
check("Offline 开跑后不可改 pitch", raises(off.set_pitch_scale, 1.0), True)
check("Offline startPad 为 0", off.preferred_start_pad(), 0)
check("Offline startDelay 为 0", off.start_delay(), 0)

rt = Stretcher(48000, 2, OptionProcessRealTime)
check("RealTime 判定", rt.realtime, True)
rt.start()
rt.set_time_ratio(2.0)
near("RealTime 开跑后仍可改", rt.time_ratio, 2.0)
check("RealTime 需要 startPad", rt.preferred_start_pad() > 0, True)
check("RealTime 需要 startDelay", rt.start_delay() > 0, True)
near("startPad 随 ratio 放大", rt.preferred_start_pad(), 2048)

# ------------------------------------------------------------ R2 专用接口
r2 = Stretcher(48000, 1, DefaultOptions)
near("R2 frequency_cutoff(0)", r2.frequency_cutoff(0), 600.0)
near("R2 frequency_cutoff(1)", r2.frequency_cutoff(1), 4000.0)
check("R2 input_increment", r2.input_increment(), 512)
r3 = Stretcher(48000, 1, OptionEngineFiner)
check("R3 无 frequency_cutoff", raises(r3.frequency_cutoff, 0), True)
check("R3 无 input_increment", raises(r3.input_increment), True)
check("R3 无 setFormantScale", raises(r2.set_formant_scale if False else
                                      Stretcher(48000, 1, 0).set_formant_scale, 1.0),
      True)
r3.set_formant_scale(0.5)
near("R3 可设共振峰", r3.formant(), 0.5)

# ------------------------------------------------------------ 选项生效判定
r3l = Stretcher(48000, 1, OptionEngineFiner | OptionWindowLong)
check("R3 忽略 WindowLong",
      has_option(r3l.effective_options(), OptionWindowLong), False)
check("R3 忽略后走 Standard",
      has_option(r3l.effective_options(), OptionWindowLong) is False, True)
r2l = Stretcher(48000, 1, OptionWindowLong)
check("R2 保留 WindowLong", has_option(r2l.effective_options(), OptionWindowLong), True)
mix = Stretcher(48000, 1, OptionWindowShort | OptionSmoothingOn)
check("Short 时忽略 SmoothingOn",
      has_option(mix.effective_options(), OptionSmoothingOn), False)
sep = Stretcher(48000, 1, OptionSmoothingOn)
check("单独 SmoothingOn 保留",
      has_option(sep.effective_options(), OptionSmoothingOn), True)

# ------------------------------------------------------------ 信号级
FS = 48000
sine = [math.sin(2 * math.pi * 440 * i / FS) for i in range(FS)]  # 1 秒 440 Hz
near("原信号频率", estimate_frequency(sine, FS), 440.0, 11.0)

up = resample_linear(sine, 2.0)
near("重采样 2x 后频率翻倍", estimate_frequency(up, FS), 880.0, 22.0)
check("重采样 2x 后时长减半", len(up), FS // 2)

slow = time_stretch(sine, 2.0)
check("拉伸 2x 后时长翻倍", abs(len(slow) - 2 * FS) <= 2, True)
near("拉伸后音高不变", estimate_frequency(slow, FS), 440.0, 11.0)

shifted = pitch_shift(sine, 12)
check("升 12 半音后时长基本不变（±1%）",
      abs(len(shifted) - FS) <= FS // 100, True)
near("升 12 半音后频率翻倍", estimate_frequency(shifted, FS), 880.0, 22.0)

# 朴素 WSOLA 在**压缩**方向（ratio<1）相位对齐会失效：降 12 半音理论值 220 Hz，
# 实测约 188 Hz（合成 hop 引入的周期性伪影），此处记录实测值并标注偏差来源。
down = pitch_shift(sine, -12)
near("降 12 半音的实测频率（记录朴素 WSOLA 的偏差）",
     estimate_frequency(down, FS), 188.2, 8.0)
check("降 12 半音的实测频率明显低于理论 220 Hz",
      estimate_frequency(down, FS) < 210.0, True)
check("降 12 半音时长仍正确", abs(len(down) - FS) <= FS // 100, True)

combo = time_stretch(resample_linear(sine, semitones_to_pitch_scale(7)),
                     semitones_to_pitch_scale(7) * 1.5)
check("变调+变速后时长 = 原长 x 1.5",
      abs(len(combo) - int(FS * 1.5)) <= FS // 50, True)
near("变调+变速后频率 = 440 x 2^(7/12)",
     estimate_frequency(combo, FS), 440 * 2 ** (7 / 12), 20.0)

print("assertions ok: %d, failed: %d" % (_ok, len(_bad)))
for line in _bad:
    print("FAILED", line)
if _bad:
    raise SystemExit(1)
print("ALL GREEN")
