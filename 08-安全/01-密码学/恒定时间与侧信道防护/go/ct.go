// 常量时间原语与侧信道观测（对应 python/ct.py 与 python/leak.py）。
// Go 标准库已有 crypto/subtle，这里把它们摊开写一遍，看清掩码是怎么工作的。

package main

import "fmt"

const mask32 uint32 = 0xFFFFFFFF

// MSB 把最高位复制成全 1 或全 0：OpenSSL 写法是 `0 - (a >> 31)`。
func MSB(a uint32) uint32 { return 0 - (a >> 31) }

// IsZero 是 `msb(~a & (a-1))`：只有 a == 0 时 a-1 才回绕成全 1。
func IsZero(a uint32) uint32 { return MSB(^a & (a - 1)) }

// Eq 就是 IsZero(a ^ b)。
func Eq(a, b uint32) uint32 { return IsZero(a ^ b) }

// Lt 是 `msb(a ^ ((a^b) | ((a-b)^b)))`：借位要按同号修正。
func Lt(a, b uint32) uint32 { return MSB(a ^ ((a ^ b) | ((a - b) ^ b))) }

// Ge / Le 由 Lt 反推。
func Ge(a, b uint32) uint32 { return ^Lt(a, b) }

func Le(a, b uint32) uint32 { return Ge(b, a) }

// Select 是 `(m & a) | (~m & b)`。真实 OpenSSL 在两侧都套 value_barrier，
// Go 里 barrier 的等价物是 `//go:noinline` 加 volatile，这里显式标注。
//
//go:noinline
func barrier(a uint32) uint32 {
	return a
}

func Select(m, a, b uint32) uint32 {
	m = barrier(m)
	return (m & a) | (^m & b)
}

// ------------------------------------------------------------------ 观测器

// Observer 统计可观测事件：分支走向、内存访问索引、基本运算次数。
type Observer struct {
	Branches []bool
	Addr     []int
	Steps    int
}

func (o *Observer) Branch(taken bool) bool {
	o.Branches = append(o.Branches, taken)
	return taken
}

func (o *Observer) Access(i int) { o.Addr = append(o.Addr, i) }

func (o *Observer) Tick(n int) { o.Steps += n }

// ---------------------------------------------------- 朴素 vs 常量时间对照

// NaiveMemcmp 第一个字节不等就返回 —— 观测值直接暴露首个差异位置。
func NaiveMemcmp(o *Observer, x, y []byte) int {
	o.Tick(1)
	if len(x) != len(y) {
		return 0
	}
	for i := range x {
		o.Tick(1)
		if o.Branch(x[i] != y[i]) {
			return 0
		}
	}
	return 1
}

// CTMemcmp 不提前返回，把差异累积进累加器。
func CTMemcmp(o *Observer, x, y []byte) int {
	o.Tick(1)
	if len(x) != len(y) {
		return 0
	}
	o.Tick(len(x))
	acc := byte(0)
	for i := range x {
		acc |= x[i] ^ y[i]
	}
	if acc == 0 {
		return 1
	}
	return 0
}

// NaiveLookup 把索引送进缓存。
func NaiveLookup(o *Observer, table []byte, idx int) byte {
	o.Tick(1)
	o.Access(idx)
	return table[idx]
}

// CTLookup 读完整张表再用掩码挑 —— 访问序列与索引无关。
func CTLookup(o *Observer, table []byte, idx int) byte {
	out := uint32(0)
	for i, v := range table {
		o.Tick(1)
		o.Access(i)
		out |= Select(Eq(uint32(i), uint32(idx)), uint32(v), 0)
	}
	return byte(out)
}

// NaiveModexp 平方-乘：指数为 1 就多一次乘法。
func NaiveModexp(o *Observer, base, exp, mod uint32) (uint32, []int) {
	r := uint32(1) % mod
	b := base % mod
	bits := []int{}
	for e := exp; e > 0; e >>= 1 {
		o.Tick(1)
		bit := int(e & 1)
		bits = append(bits, bit)
		if o.Branch(bit == 1) {
			r = r * b % mod
			o.Tick(1)
		}
		b = b * b % mod
	}
	return r, bits
}

// LadderModexp Montgomery 阶梯：从最高位开始，每比特都算两个乘积再用掩码挑。
func LadderModexp(o *Observer, base, exp, mod uint32) (uint32, []int) {
	if exp == 0 {
		return 1 % mod, nil
	}
	s := fmt.Sprintf("%b", exp)
	bits := make([]int, 0, len(s))
	for _, c := range s {
		if c == '1' {
			bits = append(bits, 1)
		} else {
			bits = append(bits, 0)
		}
	}
	r0, r1 := uint32(1)%mod, base%mod
	for _, bit := range bits {
		o.Tick(1)
		m := uint32(0) - uint32(bit) // bit=1 → 全 1，bit=0 → 0，无分支
		p := r0 * r1 % mod
		q := r0 * r0 % mod
		s1 := r1 * r1 % mod
		r0 = Select(m, p, q)
		r1 = Select(m, s1, p)
		o.Tick(2)
	}
	return r0, bits
}

// CTPaddingCheck PKCS#7 去填充：全程掩码，观测序列与填充长度无关。
func CTPaddingCheck(o *Observer, data []byte) int {
	if len(data) < 16 {
		return -1
	}
	o.Tick(1)
	n := uint32(data[len(data)-1])
	acc := (Eq(n, 0) & 1) | (Lt(uint32(16), n) & 1)
	for i := 0; i < 16; i++ {
		o.Tick(1)
		inPad := Ge(uint32(i), 16-n) & 1
		bad := ^Eq(uint32(data[len(data)-16+i]), n) & 1
		acc |= inPad & bad
	}
	if acc == 0 {
		return int(n)
	}
	return -1
}
