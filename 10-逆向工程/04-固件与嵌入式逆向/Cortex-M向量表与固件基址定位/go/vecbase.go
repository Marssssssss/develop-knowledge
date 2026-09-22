// Package vecbase 对应 Python 侧的 vecbase.py。
//
// VTOR 位域取自 ARM-software/CMSIS_5 的 core_cm3.h：
//   SCB_VTOR_TBLBASE_Pos = 29，SCB_VTOR_TBLOFF_Pos = 7
// 向量表前 16 项的名字取自 zephyr 的 arch/arm/core/cortex_m/vector_table.S。
// core_cm0.h 的 SCB 里没有 VTOR，因此 ARMv6-M 的表固定在 0x0。
package vecbase

import "encoding/binary"

// VTOR 位域。
const (
	VTORTblbasePos    = 29
	VTORTbloffPos     = 7
	VTORTblbaseMask   = 1 << VTORTblbasePos
	VTORTbloffMask    = 0x1FFFFFF << VTORTbloffPos
	MinVectorAlign    = 1 << VTORTbloffPos // 128
	NVICExternalOffset = 16
)

// SystemExceptions 是 ARMv7-M/ARMv8-M mainline 的前 16 项。
var SystemExceptions = [16]string{
	"initial_sp", "reset", "nmi", "hard_fault",
	"mpu_fault", "bus_fault", "usage_fault", "secure_fault_or_reserved",
	"reserved7", "reserved8", "reserved9", "svc",
	"debug_monitor", "reserved13", "pendsv", "systick",
}

// ProfileHasVTOR 记录各架构档位有没有 VTOR 寄存器。
var ProfileHasVTOR = map[string]bool{
	"armv6-m":          false,
	"armv7-m":          true,
	"armv8-m-mainline": true,
}

// VectorTableSize 是含 n 个外部中断的表长。
func VectorTableSize(nExternal int) int { return (NVICExternalOffset + nExternal) * 4 }

// RequiredAlign 是不小于 (异常数 * 4) 的 2 的幂，且不低于 CMSIS 的 128。
func RequiredAlign(nVectors int) int {
	a := 1
	for a < nVectors*4 {
		a <<= 1
	}
	if a < MinVectorAlign {
		return MinVectorAlign
	}
	return a
}

// VTORValue 把基址编码进 VTOR。
func VTORValue(base uint32, inCodeRegion bool) uint32 {
	v := base & uint32(VTORTbloffMask)
	if inCodeRegion {
		v |= uint32(VTORTblbaseMask)
	}
	return v
}

// VTORTbloff 从 VTOR 里取出表基址。
func VTORTbloff(v uint32) uint32 { return v & uint32(VTORTbloffMask) }

// VTORRelocatable 判断该基址能否被 VTOR 指向。
func VTORRelocatable(base uint32, profile string) bool {
	if !ProfileHasVTOR[profile] {
		return base == 0
	}
	return base&(MinVectorAlign-1) == 0
}

// CandidateVectors 按小端读出候选向量表。
func CandidateVectors(image []byte, off, count int) []uint32 {
	out := []uint32{}
	for i := 0; i < count; i++ {
		p := off + i*4
		if p+4 > len(image) {
			break
		}
		out = append(out, binary.LittleEndian.Uint32(image[p:]))
	}
	return out
}

// ScoreBase 给候选基址打分：栈顶落 RAM 加 10，非零向量是奇数且落在镜像内加 3，
// 基址本身 128 对齐再加 1（用于打平分）。
func ScoreBase(vectors []uint32, base uint32, imageLen int,
	ramLo, ramHi uint32, thumbOnly bool) int {
	score := 0
	if len(vectors) == 0 {
		return 0
	}
	if base&(MinVectorAlign-1) == 0 {
		score++
	}
	sp := vectors[0]
	if sp >= ramLo && sp <= ramHi {
		score += 10
	}
	if sp != 0 && sp&3 == 0 {
		score += 2
	}
	for _, v := range vectors[1:] {
		if v == 0 {
			score++
			continue
		}
		if v < base {
			continue
		}
		rel := v - base
		if int(rel) >= imageLen {
			continue
		}
		if thumbOnly && v&1 == 0 {
			continue
		}
		score += 3
	}
	return score
}

// InferBase 枚举候选基址，返回得分最高的那个。
func InferBase(image []byte, ramLo, ramHi uint32, scan int) (uint32, int) {
	vecs := CandidateVectors(image, 0, scan)
	seeds := map[uint32]bool{}
	for _, v := range vecs[1:] {
		if v == 0 {
			continue
		}
		entry := v &^ 1
		for i := 1; i < len(vecs); i++ {
			cand := entry - uint32(i*4)
			if cand > 0 {
				seeds[cand] = true
			}
		}
	}
	best := uint32(0)
	bestScore := -1
	for s := range seeds {
		sc := ScoreBase(vecs, s, len(image), ramLo, ramHi, true)
		if sc > bestScore || (sc == bestScore && s < best) {
			best, bestScore = s, sc
		}
	}
	return best, bestScore
}

// EntryPoint 清掉 reset 向量的 Thumb 位。
func EntryPoint(vectors []uint32) uint32 {
	if len(vectors) < 2 {
		return 0
	}
	return vectors[1] &^ 1
}

// VectorNames 生成前 n 项的名字。
func VectorNames(n int) []string {
	out := make([]string, 0, n)
	for i := 0; i < n; i++ {
		if i < NVICExternalOffset {
			out = append(out, SystemExceptions[i])
		} else {
			out = append(out, "irq"+itoa(i-NVICExternalOffset))
		}
	}
	return out
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	digits := ""
	for i > 0 {
		digits = string(rune('0'+i%10)) + digits
		i /= 10
	}
	return digits
}
