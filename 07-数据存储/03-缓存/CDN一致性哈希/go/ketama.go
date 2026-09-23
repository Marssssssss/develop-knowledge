// libketama（RJ/ketama）一致性哈希环的 Go 转写。
// 对应 https://github.com/RJ/ketama/blob/master/libketama/ketama.c
//
// 语言差异（显式落地）：
//   - C 的 `float pct` / `floorf()` 是单精度；Go 里用 float32(x) 显式降级，
//     保证 7 台等权节点时 ks 仍是 40（全程 float64 会算成 39）。
//   - C 的 qsort 换成 sort.Slice。
package main

import (
	"crypto/md5"
	"encoding/binary"
	"math"
	"sort"
)

const MaxSearchSteps = 4096

type Mcs struct {
	Point uint32
	IP    string
}

func KetamaMd5Digest(s string) []byte {
	d := md5.Sum([]byte(s))
	return d[:]
}

// KetamaHashi 对应 ketama.c:335：md5 前 4 字节按小端拼成 uint32。
func KetamaHashi(s string) uint32 {
	d := KetamaMd5Digest(s)
	return binary.LittleEndian.Uint32(d[:4])
}

// KetamaKs 对应 ketama.c:424 的 `floorf(pct * 40.0 * (float)numservers)`。
func KetamaKs(mem, memory int, numservers int) int {
	pct := float32(float32(mem) / float32(memory))
	prod := float64(pct) * 40.0 * float64(float32(numservers))
	return int(math.Floor(float64(float32(prod))))
}

// KetamaKsDouble 是对照实现：全程 float64（与 C 的单精度路径会分叉）。
func KetamaKsDouble(mem, memory int, numservers int) int {
	pct := float64(mem) / float64(memory)
	return int(math.Floor(pct * 40.0 * float64(numservers)))
}

type Server struct {
	Addr   string
	Memory int
}

// KetamaCreateContinuum 对应 ketama.c:421-465。
func KetamaCreateContinuum(servers []Server) []Mcs {
	n := len(servers)
	if n < 1 {
		return nil
	}
	memory := 0
	for _, s := range servers {
		memory += s.Memory
	}
	if memory == 0 {
		return nil
	}

	continuum := make([]Mcs, 0, n*160)
	for _, s := range servers {
		ks := KetamaKs(s.Memory, memory, n)
		for k := 0; k < ks; k++ {
			digest := KetamaMd5Digest(s.Addr + "-" + itoa(k))
			for h := 0; h < 4; h++ {
				point := binary.LittleEndian.Uint32(digest[h*4 : h*4+4])
				continuum = append(continuum, Mcs{Point: point, IP: s.Addr})
			}
		}
	}

	sort.Slice(continuum, func(i, j int) bool {
		return continuum[i].Point < continuum[j].Point
	})
	return continuum
}

// KetamaGetServerH 对应 ketama.c:348 的二分查找（h 已知，便于断言注入）。
func KetamaGetServerH(h uint32, continuum []Mcs) *Mcs {
	numpoints := len(continuum)
	if numpoints == 0 {
		return nil
	}
	lowp, highp := 0, numpoints
	for step := 0; step < MaxSearchSteps; step++ {
		midp := (lowp + highp) / 2
		if midp == numpoints {
			return &continuum[0] // 走到末尾 → 回滚到第 0 个点
		}
		midval := continuum[midp].Point
		midval1 := uint32(0)
		if midp != 0 {
			midval1 = continuum[midp-1].Point
		}
		if h <= midval && h > midval1 {
			return &continuum[midp]
		}
		if midval < h {
			lowp = midp + 1
		} else {
			highp = midp - 1
		}
		if lowp > highp {
			return &continuum[0]
		}
	}
	panic("ketama 二分未收敛（不应发生）")
}

func KetamaGetServer(key string, continuum []Mcs) *Mcs {
	return KetamaGetServerH(KetamaHashi(key), continuum)
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	var buf []byte
	for i > 0 {
		buf = append([]byte{byte('0' + i%10)}, buf...)
		i /= 10
	}
	return string(buf)
}
