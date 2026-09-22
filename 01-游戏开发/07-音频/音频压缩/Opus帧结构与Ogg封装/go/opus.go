// Package opus 实现 Opus 包结构解析（RFC 6716）与 Ogg 封装的时间轴换算（RFC 7845）。
package opus

import (
	"encoding/binary"
	"errors"
	"math"
)

// InvalidPacket 表示包结构非法。
var InvalidPacket = errors.New("invalid opus packet")

// Bandwidths 是 Table 1：名称 -> (音频带宽, 有效采样率)。
var Bandwidths = map[string][2]int{
	"NB":  {4000, 8000},
	"MB":  {6000, 12000},
	"WB":  {8000, 16000},
	"SWB": {12000, 24000},
	"FB":  {20000, 48000},
}

// Config 描述一个 TOC 配置号。
type Config struct {
	Mode      string
	Bandwidth string
	FrameMS   float64
}

var silkSizes = [4]float64{10, 20, 40, 60}
var celtSizes = [4]float64{2.5, 5, 10, 20}
var hybridSizes = [2]float64{10, 20}

// Configs 是 Table 2 的全部 32 个配置。
var Configs = buildConfigs()

func buildConfigs() map[int]Config {
	out := map[int]Config{}
	for i := 0; i < 4; i++ {
		out[i] = Config{"SILK-only", "NB", silkSizes[i]}
		out[4+i] = Config{"SILK-only", "MB", silkSizes[i]}
		out[8+i] = Config{"SILK-only", "WB", silkSizes[i]}
		out[16+i] = Config{"CELT-only", "NB", celtSizes[i]}
		out[20+i] = Config{"CELT-only", "WB", celtSizes[i]}
		out[24+i] = Config{"CELT-only", "SWB", celtSizes[i]}
		out[28+i] = Config{"CELT-only", "FB", celtSizes[i]}
	}
	for i := 0; i < 2; i++ {
		out[12+i] = Config{"Hybrid", "SWB", hybridSizes[i]}
		out[14+i] = Config{"Hybrid", "FB", hybridSizes[i]}
	}
	return out
}

// ParseTOC 拆 TOC 字节：config(5) | s(1) | c(2)。
func ParseTOC(b byte) (int, int, int) {
	return int(b>>3) & 0x1F, int(b>>2) & 1, int(b) & 3
}

// BuildTOC 组装 TOC 字节。
func BuildTOC(config int, stereo bool, code int) byte {
	s := 0
	if stereo {
		s = 1
	}
	return byte(((config & 0x1F) << 3) | (s << 2) | (code & 3))
}

// ConfigInfo 查配置表。
func ConfigInfo(config int) (Config, bool) {
	c, ok := Configs[config]
	return c, ok
}

// DecodeLength 解码 1~2 字节的帧长度（0=DTX，252..255 需第二字节）。
func DecodeLength(data []byte, pos int) (int, int, error) {
	if pos >= len(data) {
		return 0, 0, InvalidPacket
	}
	first := data[pos]
	if first < 252 {
		return int(first), 1, nil
	}
	if pos+1 >= len(data) {
		return 0, 0, InvalidPacket
	}
	return int(data[pos+1])*4 + int(first), 2, nil
}

// EncodeLength 编码帧长度（最大 1275）。
func EncodeLength(length int) ([]byte, error) {
	if length < 0 || length > 1275 {
		return nil, InvalidPacket
	}
	if length < 252 {
		return []byte{byte(length)}, nil
	}
	return []byte{byte(252 + (length & 3)), byte((length - 252) >> 2)}, nil
}

// Packet 是解析结果。
type Packet struct {
	Config   int
	Stereo   bool
	Code     int
	Frames   []int
	VBR      bool
	Padding  int
	CountByte byte
}

// DurationMS 返回包内音频时长。
func (p Packet) DurationMS() float64 {
	c, _ := Configs[p.Config]
	n := 0
	for _, f := range p.Frames {
		if f > 0 {
			n++
		}
	}
	return float64(n) * c.FrameMS
}

// ParsePacket 解析四种 code 的 Opus 包。
func ParsePacket(data []byte) (Packet, error) {
	if len(data) < 1 {
		return Packet{}, InvalidPacket
	}
	config, s, code := ParseTOC(data[0])
	if _, ok := Configs[config]; !ok {
		return Packet{}, InvalidPacket
	}
	p := Packet{Config: config, Stereo: s == 1, Code: code, CountByte: 0}
	n := len(data)
	switch code {
	case 0:
		p.Frames = []int{n - 1}
		return p, nil
	case 1:
		rest := n - 1
		if rest%2 != 0 {
			return Packet{}, InvalidPacket
		}
		p.Frames = []int{rest / 2, rest / 2}
		return p, nil
	case 2:
		if n < 2 {
			return Packet{}, InvalidPacket
		}
		n1, used, err := DecodeLength(data, 1)
		if err != nil {
			return Packet{}, err
		}
		rest := n - 1 - used - n1
		if rest < 0 {
			return Packet{}, InvalidPacket
		}
		p.Frames = []int{n1, rest}
		return p, nil
	}
	if n < 2 {
		return Packet{}, InvalidPacket
	}
	ch := data[1]
	p.CountByte = ch
	count := int(ch) & 0x3F
	paddingBit := (ch >> 6) & 1
	p.VBR = ((ch >> 7) & 1) == 1
	if count == 0 {
		return Packet{}, InvalidPacket
	}
	pos := 2
	if paddingBit == 1 {
		total := 0
		for {
			if pos >= n {
				return Packet{}, InvalidPacket
			}
			b := int(data[pos])
			pos++
			total++
			if b == 255 {
				total += 254
				continue
			}
			p.Padding = total + b
			break
		}
	}
	if p.VBR {
		for i := 0; i < count; i++ {
			length, used, err := DecodeLength(data, pos)
			if err != nil {
				return Packet{}, err
			}
			pos += used
			p.Frames = append(p.Frames, length)
			if pos+length > n-p.Padding {
				return Packet{}, InvalidPacket
			}
			pos += length
		}
		return p, nil
	}
	rest := n - p.Padding - pos
	if rest < 0 || rest%count != 0 {
		return Packet{}, InvalidPacket
	}
	for i := 0; i < count; i++ {
		p.Frames = append(p.Frames, rest/count)
	}
	return p, nil
}

// MaxFrameCount 由 120 ms 上限反推最大帧数。
func MaxFrameCount(frameMS float64) int {
	return int(120.0 / frameMS)
}

// DurationLimitOK 判断 count*frameMS 是否不超过 120 ms。
func DurationLimitOK(count int, frameMS float64) bool {
	return float64(count)*frameMS <= 120.0
}

// OpusHead 是 Ogg Opus 的 ID 头。
type OpusHead struct {
	Version       byte
	Channels      byte
	PreSkip       uint16
	InputRate     uint32
	OutputGain    int16
	MappingFamily byte
}

// Render 序列化 19 字节 ID 头（小端）。
func (h OpusHead) Render() []byte {
	out := append([]byte{}, "OpusHead"...)
	out = append(out, h.Version, h.Channels)
	buf := make([]byte, 2)
	binary.LittleEndian.PutUint16(buf, h.PreSkip)
	out = append(out, buf...)
	buf4 := make([]byte, 4)
	binary.LittleEndian.PutUint32(buf4, h.InputRate)
	out = append(out, buf4...)
	binary.LittleEndian.PutUint16(buf, uint16(h.OutputGain))
	out = append(out, buf...)
	return append(out, h.MappingFamily)
}

// ParseOpusHead 解析 ID 头。
func ParseOpusHead(data []byte) (OpusHead, error) {
	if len(data) < 19 || string(data[:8]) != "OpusHead" {
		return OpusHead{}, InvalidPacket
	}
	return OpusHead{
		Version:       data[8],
		Channels:      data[9],
		PreSkip:       binary.LittleEndian.Uint16(data[10:12]),
		InputRate:     binary.LittleEndian.Uint32(data[12:16]),
		OutputGain:    int16(binary.LittleEndian.Uint16(data[16:18])),
		MappingFamily: data[18],
	}, nil
}

// ApplyOutputGain 应用 Q7.8 输出增益：sample *= 10^(gain/(20*256))。
func ApplyOutputGain(sample float64, gain int) float64 {
	return sample * math.Pow(10, float64(gain)/(20.0*256.0))
}

// PCMSamplePosition 返回 granule - pre-skip。
func PCMSamplePosition(granule, preSkip int) int { return granule - preSkip }

// GranuleAfter 以 48 kHz 样本推进 granule。
func GranuleAfter(granule int, frameMS float64) int {
	return granule + int(math.Round(frameMS*48000/1000.0))
}
