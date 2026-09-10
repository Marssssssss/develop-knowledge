// ima_adpcm.go — IMA ADPCM(自适应差分脉冲编码)最小实现
//
// 演示内容:
//   1. 16-bit PCM -> 4-bit IMA ADPCM 流式编解码(压缩比 4:1)
//   2. 单 nibble 误码的传播与自恢复特性
//   3. 块结构(4 字节头部 + nibble 数据)支持的随机访问解码
//
// 算法依据:
//   - Davis Yen Pan, "Digital Audio Compression",
//     Digital Technical Journal Vol.5 No.2, Spring 1993(第 3 节 IMA ADPCM)
//   - RFC 3551 Section 4.5.1(DVI4 = IMA ADPCM 的 RTP 封装)
package main

import (
	"encoding/binary"
	"fmt"
	"math"
)

const (
	sampleRate    = 8000
	numSamples    = 2500 // 1000 静音段 + 500 突变段 + 1000 回落段
	blockSamples  = 256
	blockHeader   = 4 // int16 predictor + uint8 index + uint8 reserved
)

// 步长表:89 级,近似指数增长(每级约为前级 1.1 倍),7 -> 32767。
// 注:论文 Table 2 索引 84 处印作 22358,标准实现(ffmpeg/IMA 规范)为 22385,
// 此处以标准实现为准(见 README"注意事项")。
var stepTable = [89]int16{
	7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
	19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
	50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
	130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
	337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
	876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
	2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
	5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
	15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
}

// 索引调整表:幅值小的码字(0-3 / 8-11)把索引 -1(步长收缩),
// 幅值大的码字(4-7 / 12-15)把索引 +2/+4/+6/+8(步长扩张)。
var indexTable = [16]int8{-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8}

// adpcmState:predictor 即"预测器"= 上一个重建样本;index 为步长表索引。
type adpcmState struct {
	predictor int32
	index     int
}

func (st *adpcmState) reset() {
	st.predictor = 0
	st.index = 0
}

func clamp(v, lo, hi int32) int32 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// decodeSample 解码单个 4-bit 码字并推进状态(论文图 2b 解码器)。
// step>>3 为半步长偏置:使重建值落在量化区间中部,消除系统性偏差。
func decodeSample(code uint8, st *adpcmState) int16 {
	step := int32(stepTable[st.index])
	diff := step >> 3
	if code&4 != 0 {
		diff += step
	}
	if code&2 != 0 {
		diff += step >> 1
	}
	if code&1 != 0 {
		diff += step >> 2
	}
	if code&8 != 0 {
		diff = -diff
	}
	st.predictor = clamp(st.predictor+diff, -32768, 32767)
	st.index += int(indexTable[code])
	if st.index < 0 {
		st.index = 0
	}
	if st.index > 88 {
		st.index = 88
	}
	return int16(st.predictor)
}

// encodeSample 编码单个样本:符号位 + 三级阈值比较(论文图 3 量化流程)。
// 编码器内嵌同一解码器推进状态 —— 论文图 2a:编码器复用解码器组件,
// 保证收发两端状态逐样本同步。
func encodeSample(sample int16, st *adpcmState) uint8 {
	diff := int32(sample) - st.predictor
	var code uint8
	if diff < 0 {
		code = 8
		diff = -diff
	}
	step := int32(stepTable[st.index])
	if diff >= step {
		code |= 4
		diff -= step
	}
	if diff >= step>>1 {
		code |= 2
		diff -= step >> 1
	}
	if diff >= step>>2 {
		code |= 1
	}
	decodeSample(code, st)
	return code
}

// encodeStream 整段编码为连续 nibble 流
// (IMA WAV 打包惯例:偶数样本放低 4 位;与 RTP DVI4 的高 4 位优先相反)。
func encodeStream(pcm []int16) []byte {
	out := make([]byte, (len(pcm)+1)/2)
	var st adpcmState
	st.reset()
	for i, s := range pcm {
		code := encodeSample(s, &st)
		if i%2 == 0 {
			out[i/2] = code
		} else {
			out[i/2] |= code << 4
		}
	}
	return out
}

func decodeStream(data []byte, n int) []int16 {
	out := make([]int16, n)
	var st adpcmState
	st.reset()
	for i := 0; i < n; i++ {
		var code uint8
		if i%2 == 0 {
			code = data[i/2] & 0x0F
		} else {
			code = data[i/2] >> 4
		}
		out[i] = decodeSample(code, &st)
	}
	return out
}

// encodeBlocks 分块编码:每块头部记录块首状态,支持随机访问。
//
// 头部布局(参照 RFC 3551 DVI4 块头):
//
//	[0..1] int16 predictor(小端)  [2] uint8 index  [3] uint8 reserved
func encodeBlocks(pcm []int16) []byte {
	var out []byte
	var st adpcmState
	st.reset()
	for off := 0; off < len(pcm); off += blockSamples {
		end := off + blockSamples
		if end > len(pcm) {
			end = len(pcm)
		}
		chunk := pcm[off:end]
		var hdr [4]byte
		binary.LittleEndian.PutUint16(hdr[:2], uint16(st.predictor))
		hdr[2] = byte(st.index)
		out = append(out, hdr[:]...)
		for i, s := range chunk {
			code := encodeSample(s, &st)
			if i%2 == 0 {
				out = append(out, code)
			} else {
				out[len(out)-1] |= code << 4
			}
		}
	}
	return out
}

// decodeBlocksFrom 从指定块号开始解码(随机访问:仅依赖该块头部状态)。
// 除末块外每块固定 128 字节数据(256 样本);末块可能不足。
func decodeBlocksFrom(blocks []byte, startBlock int) []int16 {
	var out []int16
	pos, block := 0, 0
	fullData := blockSamples / 2
	for pos+blockHeader <= len(blocks) {
		body := len(blocks) - pos - blockHeader
		dataBytes := fullData
		if body < fullData {
			dataBytes = body
		}
		cnt := dataBytes * 2
		if block >= startBlock {
			st := adpcmState{
				predictor: int32(int16(binary.LittleEndian.Uint16(blocks[pos:]))),
				index:     int(blocks[pos+2]),
			}
			base := pos + blockHeader
			for i := 0; i < cnt; i++ {
				var code uint8
				if i%2 == 0 {
					code = blocks[base+i/2] & 0x0F
				} else {
					code = blocks[base+i/2] >> 4
				}
				out = append(out, decodeSample(code, &st))
			}
		}
		pos += blockHeader + dataBytes
		block++
	}
	return out
}

func errorStats(orig, dec []int16, lo, hi int) (int, float64) {
	maxAbs, sum := 0, 0.0
	for i := lo; i < hi; i++ {
		e := int(orig[i]) - int(dec[i])
		if e < 0 {
			e = -e
		}
		if e > maxAbs {
			maxAbs = e
		}
		sum += float64(e)
	}
	return maxAbs, sum / float64(hi-lo)
}

func genSignal() []int16 {
	pcm := make([]int16, numSamples)
	for i := range pcm {
		t := float64(i) / float64(sampleRate)
		// 幅度 600 -> 12000 -> 600:制造 20 倍瞬时跳变,逼出步长自适应
		amp := 600.0
		if i >= 1000 && i < 1500 {
			amp = 12000.0
		}
		v := amp * math.Sin(2*math.Pi*440.0*t)
		pcm[i] = int16(clamp(int32(v), -32768, 32767))
	}
	return pcm
}

func main() {
	pcm := genSignal()
	fmt.Println("=== IMA ADPCM demo ===")
	fmt.Printf("采样率 %d Hz, 样本数 %d (PCM %d 字节)\n", sampleRate, numSamples, numSamples*2)

	// [1] 流式编解码
	stream := encodeStream(pcm)
	dec := decodeStream(stream, numSamples)
	maxe, meane := errorStats(pcm, dec, 0, numSamples)
	fmt.Println("[1] 流式编解码")
	fmt.Printf("    编码后 %d 字节, 压缩比 %.2f:1 (理论 4:1)\n",
		len(stream), float64(numSamples*2)/float64(len(stream)))
	fmt.Printf("    全段误差: 最大 %d, 平均 %.1f\n", maxe, meane)
	m1, _ := errorStats(pcm, dec, 0, 1000)
	m2, _ := errorStats(pcm, dec, 1000, 1500)
	m3, _ := errorStats(pcm, dec, 1500, numSamples)
	fmt.Printf("    分段最大误差: 静音段 %d / 突变段 %d / 回落段 %d\n", m1, m2, m3)

	// [2] 抗误码:篡改中间 1 个 nibble
	stream[numSamples/4] ^= 0x0F
	dec2 := decodeStream(stream, numSamples)
	p := numSamples / 2
	fmt.Printf("[2] 抗误码: 篡改位置 %d 处 1 个 nibble\n", p)
	fmt.Printf("    损坏点误差: %d\n", abs(pcm[p]-dec2[p]))
	fmt.Printf("    之后 100 样本: %d / 之后 500 样本: %d",
		abs(pcm[p+100]-dec2[p+100]), abs(pcm[p+500]-dec2[p+500]))
	fmt.Println("  (步长已收敛, 残差为恒定直流偏置)")
	stream[numSamples/4] ^= 0x0F // 还原

	// [3] 块随机访问
	blocks := encodeBlocks(pcm)
	dec = decodeStream(stream, numSamples) // 无损坏基准
	dec2 = decodeBlocksFrom(blocks, 5)
	same := true
	for i := range dec2 {
		if dec2[i] != dec[5*blockSamples+i] {
			same = false
			break
		}
	}
	fmt.Printf("[3] 块结构: 共 %d 字节 (含 10 个块头), 压缩比 %.2f:1\n",
		len(blocks), float64(numSamples*2)/float64(len(blocks)))
	fmt.Printf("    从第 5 块头部随机访问解码 %d 样本, 与全量解码一致: %s\n",
		len(dec2), boolCN(same))
}

func abs(v int16) int {
	if v < 0 {
		return -int(v)
	}
	return int(v)
}

func boolCN(b bool) string {
	if b {
		return "是"
	}
	return "否"
}
