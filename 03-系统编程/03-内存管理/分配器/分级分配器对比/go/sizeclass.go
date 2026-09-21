// Package main —— jemalloc / tcmalloc / mimalloc 尺寸分级的 Go 镜像。
//
// 常量来源与 python 版一致（jemalloc(3) man page / TCMalloc Design Doc /
// mimalloc include/mimalloc/types.h）。无 Go 工具链，仅做人工审查 +
// bracket_check / go_sanity / go_crossref；运行证据见 python/selfcheck_alloc.py。
package main

const (
	KiB = 1024
	MiB = 1024 * 1024

	// ---- jemalloc（4 KiB 页、16 字节 quantum）----
	JemQuantum = 16
	JemPage    = 4 * KiB

	JemNArenasPerCPU = 4
	JemOversizeDef   = 8 * MiB
	JemLgExtentFit   = 6 // 2^6 = 64
	JemDirtyDecayMs  = 10000
	JemMuzzyDecayMs  = 0 // 默认关闭

	// ---- mimalloc（dev3、64 位、默认 MI_SECURE）----
	MISizeShift  = 3
	MISizeSize   = 1 << MISizeShift
	MISliceShift = 13 + MISizeShift

	MISmallPageSize  = 1 << MISliceShift          // 64 KiB
	MIMediumPageSize = 8 * MISmallPageSize        // 512 KiB
	MILargePageSize  = MISizeSize * MIMediumPageSize
	MIBlockAlign2    = 4 * KiB

	MISmallMaxObjSize  = (MISmallPageSize - MIBlockAlign2) / 6
	MIMediumMaxObjSize = (MIMediumPageSize - MIBlockAlign2) / 6
	MILargeMaxObjSize  = MILargePageSize / 8

	MIBinHuge  = 73
	MIBinFull  = MIBinHuge + 1
	MIBinCount = MIBinFull + 1
	MIAlignMax = 16
	MIPadding  = 0

	// ---- TCMalloc ----
	PtrSize  = 8
	HdrBytes = 24 // 每档 header：数组起点 + 容量 + 当前位置
)

// JemSizeClasses 按 Table 1 的递推规则生成尺寸档位：
// 初始 [8] 加 quantum 的 1..8 倍，其后每个 spacing S 产生 [5S,6S,7S,8S]，S 每次翻倍。
func JemSizeClasses(maxSize int) []int {
	out := []int{8}
	s := JemQuantum
	for k := 1; k <= 8; k++ {
		out = append(out, s*k)
	}
	s *= 2
	for {
		for _, k := range []int{5, 6, 7, 8} {
			v := s * k
			if v > maxSize {
				return out
			}
			out = append(out, v)
		}
		s *= 2
	}
}

// JemNArenas 返回 opt.narenas 与 opt.percpu_arena 组合后的 arena 数。
func JemNArenas(ncpu int, percpuArena string) int {
	base := 1
	if ncpu > 1 {
		base = JemNArenasPerCPU * ncpu
	}
	switch percpuArena {
	case "percpu":
		return ncpu
	case "phycpu":
		if ncpu/2 < 1 {
			return 1
		}
		return ncpu / 2
	}
	return base
}

// JemArenaOf 计算「线程当前所在 CPU」对应的 arena 编号。
func JemArenaOf(cpu, threadsPerCore int, percpuArena string, ncpu int) int {
	switch percpuArena {
	case "percpu":
		return cpu
	case "phycpu":
		return cpu / threadsPerCore
	}
	n := JemNArenas(ncpu, "disabled")
	if n < 1 {
		return 0
	}
	return cpu % n
}

// JemDecayRate 返回 sigmoidal 衰减的瞬时 purge 速率（两端为 0，中点最大）。
func JemDecayRate(u float64) float64 {
	if u <= 0 || u >= 1 {
		return 0
	}
	return 6 * u * (1 - u)
}

// JemDecayCumulative 返回已 purge 的累积比例（smoothstep）。
func JemDecayCumulative(u float64) float64 {
	if u <= 0 {
		return 0
	}
	if u >= 1 {
		return 1
	}
	return 3*u*u - 2*u*u*u
}

// JemPurgeFraction 返回 t 时刻的 (比例, 页数)；decayMs=0 立即全清，-1 永不。
func JemPurgeFraction(tMs, decayMs, unusedPages int) (float64, int) {
	if decayMs < 0 {
		return 0, 0
	}
	if decayMs == 0 {
		return 1, unusedPages
	}
	u := float64(tMs) / float64(decayMs)
	if u > 1 {
		u = 1
	}
	f := JemDecayCumulative(u)
	return f, int(float64(unusedPages) * f)
}

// TcmRoundUpSmall 按 align(8 或 16) 向上取整，并保底 16 字节。
func TcmRoundUpSmall(size, align int) int {
	v := (size + align - 1) / align * align
	if v < 16 {
		return 16
	}
	return v
}
