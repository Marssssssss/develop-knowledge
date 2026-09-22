"""实时变调与变速：Rubber Band 的选项位、比例口径与「拉伸+重采样」链路。

实读来源：
  * Rubber Band Library `rubberband/RubberBandStretcher.h`（breakfastquay/rubberband，default 分支）
    - Option 枚举的 32 个标志位取值（DefaultOptions = 0，同一组的默认值都是 0）
    - setTimeRatio：ratio = 拉伸后/拉伸前**时长**（不是速度），2.0 = 两倍长 = 半速
    - setPitchScale：目标/源频率比，半音 S 对应 pow(2, S/12)
    - setFormantScale：默认 0.0 表示自动；OptionFormantPreserved 时按 1/pitchScale，
      OptionFormantShifted 时按 1.0；getFormantScale 在 R2 下恒返回 0.0
    - Offline 模式下 time/pitch 比例在 study()/process() 之后不可再改；RealTime 可随时改
    - RealTime 模式不做自动填充：需 getPreferredStartPad() 补静音 + getStartDelay() 裁输出，
      Offline 模式下两者恒为 0
    - setFrequencyCutoff / getFrequencyCutoff / getInputIncrement 仅 R2 支持
    - OptionWindowLong 在 R3 下被忽略；OptionSmoothingOn 与 OptionWindowShort 同时给出时被忽略
  * Rubber Band README：命令行 `-t <timeratio> -p <semitones>`；R2(Faster)/R3(Finer) 两个引擎，
    以 `rubberband` 调用时默认 R2，以 `rubberband-r3` 调用时默认 R3
"""

import math

# ---------------------------------------------------------------- 选项位

OptionProcessOffline = 0x00000000
OptionProcessRealTime = 0x00000001

OptionStretchElastic = 0x00000000   # obsolete
OptionStretchPrecise = 0x00000010   # obsolete

OptionTransientsCrisp = 0x00000000
OptionTransientsMixed = 0x00000100
OptionTransientsSmooth = 0x00000200

OptionDetectorCompound = 0x00000000
OptionDetectorPercussive = 0x00000400
OptionDetectorSoft = 0x00000800

OptionPhaseLaminar = 0x00000000
OptionPhaseIndependent = 0x00002000

OptionThreadingAuto = 0x00000000
OptionThreadingNever = 0x00010000
OptionThreadingAlways = 0x00020000

OptionWindowStandard = 0x00000000
OptionWindowShort = 0x00100000
OptionWindowLong = 0x00200000

OptionSmoothingOff = 0x00000000
OptionSmoothingOn = 0x00800000

OptionFormantShifted = 0x00000000
OptionFormantPreserved = 0x01000000

OptionPitchHighSpeed = 0x00000000
OptionPitchHighQuality = 0x02000000
OptionPitchHighConsistency = 0x04000000

OptionChannelsApart = 0x00000000
OptionChannelsTogether = 0x10000000

OptionEngineFaster = 0x00000000
OptionEngineFiner = 0x20000000

DefaultOptions = 0x00000000

GROUPS = {
    "process": (OptionProcessOffline, OptionProcessRealTime),
    "transients": (OptionTransientsCrisp, OptionTransientsMixed,
                   OptionTransientsSmooth),
    "detector": (OptionDetectorCompound, OptionDetectorPercussive,
                 OptionDetectorSoft),
    "phase": (OptionPhaseLaminar, OptionPhaseIndependent),
    "threading": (OptionThreadingAuto, OptionThreadingNever,
                  OptionThreadingAlways),
    "window": (OptionWindowStandard, OptionWindowShort, OptionWindowLong),
    "smoothing": (OptionSmoothingOff, OptionSmoothingOn),
    "formant": (OptionFormantShifted, OptionFormantPreserved),
    "pitch": (OptionPitchHighSpeed, OptionPitchHighQuality,
              OptionPitchHighConsistency),
    "channels": (OptionChannelsApart, OptionChannelsTogether),
    "engine": (OptionEngineFaster, OptionEngineFiner),
}


class StretcherError(RuntimeError):
    pass


def has_option(options, flag):
    return (options & flag) == flag


def engine_of(options):
    return "R3" if has_option(options, OptionEngineFiner) else "R2"


# ---------------------------------------------------------------- 比例口径

def semitones_to_pitch_scale(semitones):
    """原文：pow(2.0, S/12.0)。"""
    return 2.0 ** (semitones / 12.0)


def pitch_scale_to_semitones(scale):
    return 12.0 * math.log2(scale)


def tempo_factor(time_ratio):
    """time ratio 是时长比，速度是它的倒数。"""
    return 1.0 / time_ratio


def output_samples(input_samples, time_ratio):
    return int(round(input_samples * time_ratio))


def formant_scale(options, pitch_scale, explicit=None):
    """默认 0.0 = 自动：preserved 用 1/pitchScale，shifted 用 1.0。"""
    if explicit is not None and explicit != 0.0:
        return explicit
    if has_option(options, OptionFormantPreserved):
        return 1.0 / pitch_scale
    return 1.0


def get_formant_scale(options, explicit=None):
    """R2 下恒返回 0.0（不支持）。"""
    if engine_of(options) == "R2":
        return 0.0
    return explicit if explicit is not None else 0.0


# ---------------------------------------------------------------- 引擎模型

class Stretcher(object):
    """RubberBandStretcher 的语义模型（比例/模式/延迟/支持性）。"""

    def __init__(self, sample_rate, channels, options=DefaultOptions,
                 realtime=None):
        realtime = has_option(options, OptionProcessRealTime) if realtime is None \
            else realtime
        self.sample_rate = sample_rate
        self.channels = channels
        self.options = options
        self.realtime = realtime
        self.engine = engine_of(options)
        self.time_ratio = 1.0
        self.pitch_scale = 1.0
        self.explicit_formant = 0.0
        self.started = False

    # --- 比例设置
    def set_time_ratio(self, ratio):
        if self.started and not self.realtime:
            raise StretcherError("Offline: ratio fixed after study()/process()")
        self.time_ratio = ratio

    def set_pitch_scale(self, scale):
        if self.started and not self.realtime:
            raise StretcherError("Offline: pitch fixed after study()/process()")
        self.pitch_scale = scale

    def set_pitch_semitones(self, semitones):
        self.set_pitch_scale(semitones_to_pitch_scale(semitones))

    def set_formant_scale(self, scale):
        if self.engine == "R2":
            raise StretcherError("setFormantScale is R3-only")
        self.explicit_formant = scale

    def start(self):
        self.started = True

    # --- 延迟与填充
    def preferred_start_pad(self):
        if not self.realtime:
            return 0
        return int(round(1024 * max(1.0, self.time_ratio)))

    def start_delay(self):
        if not self.realtime:
            return 0
        return int(round(512 * max(1.0, self.time_ratio)))

    # --- R2 专用
    def frequency_cutoff(self, n):
        if self.engine != "R2":
            raise StretcherError("frequency cutoff is R2-only")
        return [600.0, 4000.0][n]

    def input_increment(self):
        if self.engine != "R2":
            raise StretcherError("getInputIncrement is R2-only")
        return 512

    # --- 选项生效判定
    def effective_options(self):
        opts = self.options
        if self.engine == "R3" and has_option(opts, OptionWindowLong):
            opts &= ~OptionWindowLong           # R3 忽略 WindowLong
            opts |= OptionWindowStandard
        if has_option(opts, OptionWindowShort) and has_option(opts, OptionSmoothingOn):
            opts &= ~OptionSmoothingOn          # 与 Short 同时给出时被忽略
        return opts

    def formant(self):
        return formant_scale(self.options, self.pitch_scale,
                             self.explicit_formant or None)


# ---------------------------------------------------------------- 信号级模型
