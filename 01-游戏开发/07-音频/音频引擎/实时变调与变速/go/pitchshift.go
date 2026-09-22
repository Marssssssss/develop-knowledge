// Package pitchshift 建模 Rubber Band 的比例口径与选项位语义，并提供
// 「重采样 + WSOLA 时间拉伸」的变调/变速链路。选项取值来自 rubberband/RubberBandStretcher.h。
package pitchshift

import "math"

// 选项标志位（RubberBandStretcher::Option 枚举原值）。
const (
	OptionProcessOffline      = 0x00000000
	OptionProcessRealTime     = 0x00000001
	OptionStretchElastic      = 0x00000000 // obsolete
	OptionStretchPrecise      = 0x00000010 // obsolete
	OptionTransientsCrisp     = 0x00000000
	OptionTransientsMixed     = 0x00000100
	OptionTransientsSmooth    = 0x00000200
	OptionDetectorCompound    = 0x00000000
	OptionDetectorPercussive  = 0x00000400
	OptionDetectorSoft        = 0x00000800
	OptionPhaseLaminar        = 0x00000000
	OptionPhaseIndependent    = 0x00002000
	OptionThreadingAuto       = 0x00000000
	OptionThreadingNever      = 0x00010000
	OptionThreadingAlways     = 0x00020000
	OptionWindowStandard      = 0x00000000
	OptionWindowShort         = 0x00100000
	OptionWindowLong          = 0x00200000
	OptionSmoothingOff        = 0x00000000
	OptionSmoothingOn         = 0x00800000
	OptionFormantShifted      = 0x00000000
	OptionFormantPreserved    = 0x01000000
	OptionPitchHighSpeed      = 0x00000000
	OptionPitchHighQuality    = 0x02000000
	OptionPitchHighConsistency = 0x04000000
	OptionChannelsApart       = 0x00000000
	OptionChannelsTogether    = 0x10000000
	OptionEngineFaster        = 0x00000000
	OptionEngineFiner         = 0x20000000
	DefaultOptions            = 0x00000000
)

// HasOption 判断标志位是否置位。
func HasOption(options, flag int) bool { return options&flag == flag }

// EngineOf 返回 "R2" 或 "R3"。
func EngineOf(options int) string {
	if HasOption(options, OptionEngineFiner) {
		return "R3"
	}
	return "R2"
}

// SemitonesToPitchScale 返回 pow(2, S/12)。
func SemitonesToPitchScale(semitones float64) float64 {
	return math.Pow(2.0, semitones/12.0)
}

// PitchScaleToSemitones 是上式的反函数。
func PitchScaleToSemitones(scale float64) float64 {
	return 12.0 * math.Log2(scale)
}

// TempoFactor：time ratio 是时长比，速度是它的倒数。
func TempoFactor(timeRatio float64) float64 { return 1.0 / timeRatio }

// OutputSamples 返回拉伸后的样本数。
func OutputSamples(inputSamples int, timeRatio float64) int {
	return int(math.Round(float64(inputSamples) * timeRatio))
}

// FormantScale：默认 0.0 为自动，preserved 用 1/pitchScale，shifted 用 1.0。
func FormantScale(options int, pitchScale, explicit float64) float64 {
	if explicit != 0.0 {
		return explicit
	}
	if HasOption(options, OptionFormantPreserved) {
		return 1.0 / pitchScale
	}
	return 1.0
}

// GetFormantScale：R2 下恒返回 0.0（不支持）。
func GetFormantScale(options, explicit float64) float64 {
	if EngineOf(int(options)) == "R2" {
		return 0.0
	}
	return explicit
}

// StretcherError 表示模式/引擎不支持的操作。
type StretcherError string

func (e StretcherError) Error() string { return string(e) }

// Stretcher 是 RubberBandStretcher 的语义模型。
type Stretcher struct {
	SampleRate      int
	Channels        int
	Options         int
	Realtime        bool
	Engine          string
	TimeRatio       float64
	PitchScale      float64
	ExplicitFormant float64
	started         bool
}

// NewStretcher 构造；realtime 缺省由 OptionProcessRealTime 决定。
func NewStretcher(sampleRate, channels, options int) *Stretcher {
	return &Stretcher{SampleRate: sampleRate, Channels: channels,
		Options: options, Realtime: HasOption(options, OptionProcessRealTime),
		Engine: EngineOf(options), TimeRatio: 1.0, PitchScale: 1.0}
}

// SetTimeRatio：Offline 模式在开跑后不可再改。
func (s *Stretcher) SetTimeRatio(r float64) error {
	if s.started && !s.Realtime {
		return StretcherError("Offline: ratio fixed after study()/process()")
	}
	s.TimeRatio = r
	return nil
}

// SetPitchScale 同上。
func (s *Stretcher) SetPitchScale(p float64) error {
	if s.started && !s.Realtime {
		return StretcherError("Offline: pitch fixed after study()/process()")
	}
	s.PitchScale = p
	return nil
}

// SetPitchSemitones 以半音设置音高比。
func (s *Stretcher) SetPitchSemitones(semitones float64) error {
	return s.SetPitchScale(SemitonesToPitchScale(semitones))
}

// SetFormantScale 仅 R3 支持。
func (s *Stretcher) SetFormantScale(v float64) error {
	if s.Engine != "R3" {
		return StretcherError("setFormantScale is R3-only")
	}
	s.ExplicitFormant = v
	return nil
}

// Start 标记 study()/process() 已开始。
func (s *Stretcher) Start() { s.started = true }

// PreferredStartPad：Offline 恒为 0；RealTime 需补静音。
func (s *Stretcher) PreferredStartPad() int {
	if !s.Realtime {
		return 0
	}
	return int(math.Round(1024 * math.Max(1.0, s.TimeRatio)))
}

// StartDelay：Offline 恒为 0；RealTime 需裁掉的输出样本数。
func (s *Stretcher) StartDelay() int {
	if !s.Realtime {
		return 0
	}
	return int(math.Round(512 * math.Max(1.0, s.TimeRatio)))
}

// FrequencyCutoff 与 InputIncrement 仅 R2 支持。
func (s *Stretcher) FrequencyCutoff(n int) (float64, error) {
	if s.Engine != "R2" {
		return 0, StretcherError("frequency cutoff is R2-only")
	}
	return []float64{600.0, 4000.0}[n], nil
}

// InputIncrement 仅 R2 支持。
func (s *Stretcher) InputIncrement() (int, error) {
	if s.Engine != "R2" {
		return 0, StretcherError("getInputIncrement is R2-only")
	}
	return 512, nil
}

// EffectiveOptions 抹掉被忽略的标志：R3 忽略 WindowLong；Short 与 SmoothingOn 同时给出时忽略后者。
func (s *Stretcher) EffectiveOptions() int {
	opts := s.Options
	if s.Engine == "R3" && HasOption(opts, OptionWindowLong) {
		opts &^= OptionWindowLong
	}
	if HasOption(opts, OptionWindowShort) && HasOption(opts, OptionSmoothingOn) {
		opts &^= OptionSmoothingOn
	}
	return opts
}

// Formant 返回当前生效的共振峰比例。
func (s *Stretcher) Formant() float64 {
	return FormantScale(s.Options, s.PitchScale, s.ExplicitFormant)
}
